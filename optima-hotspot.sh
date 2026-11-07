#!/bin/bash
# Optima - moteur de point d'acces (hostapd + dnsmasq + NAT + normalisation
# TTL). Concu pour tourner en root (service systemd).
set -e

AP_IFACE="ap0"
RUN_DIR="/run/optima"
mkdir -p "$RUN_DIR"

detect_uplink() {
    nmcli -t -f DEVICE,TYPE,STATE device status 2>/dev/null \
        | awk -F: '$2=="wifi" && $3=="connected" {print $1; exit}'
}

detect_channel() {
    local iface="$1"
    iw dev "$iface" info 2>/dev/null | awk '/channel/{print $2; exit}'
}

stop_all() {
    if [ -f "$RUN_DIR/hostapd.pid" ]; then
        kill -9 "$(cat "$RUN_DIR/hostapd.pid")" 2>/dev/null || true
    fi
    if [ -f "$RUN_DIR/dnsmasq.pid" ]; then
        kill -9 "$(cat "$RUN_DIR/dnsmasq.pid")" 2>/dev/null || true
    fi
    # Filet de securite si le pidfile est absent/perime (le motif doit
    # correspondre a la ligne de commande reelle : "-B" separe "hostapd"
    # du chemin du fichier de conf).
    pkill -9 -f "hostapd -B $RUN_DIR/hostapd.conf" 2>/dev/null || true
    pkill -9 -f "dnsmasq --conf-file=$RUN_DIR/dnsmasq.conf" 2>/dev/null || true
    rm -f "$RUN_DIR/hostapd.pid" "$RUN_DIR/dnsmasq.pid"
    tc qdisc del dev "$AP_IFACE" root 2>/dev/null || true
    sleep 1

    # Le retrait de l'interface peut echouer juste apres avoir tue hostapd
    # (le driver n'a pas encore relache l'etat) : on retente.
    for _ in 1 2 3; do
        iw dev "$AP_IFACE" del 2>/dev/null && break
        sleep 1
    done

    local uplink
    uplink=$(detect_uplink)
    if [ -n "$uplink" ]; then
        iptables -t nat -D POSTROUTING -o "$uplink" -j MASQUERADE 2>/dev/null || true
        iptables -D FORWARD -j OPTIMA_CUT 2>/dev/null || true
        iptables -D FORWARD -j OPTIMA_ACCT 2>/dev/null || true
        iptables -D FORWARD -i "$AP_IFACE" -o "$uplink" -j ACCEPT 2>/dev/null || true
        iptables -D FORWARD -i "$uplink" -o "$AP_IFACE" -m state --state RELATED,ESTABLISHED -j ACCEPT 2>/dev/null || true
        iptables -t mangle -D POSTROUTING -o "$uplink" -j TTL --ttl-set 65 2>/dev/null || true
        iptables -t mangle -D PREROUTING -i "$uplink" -j TTL --ttl-set 65 2>/dev/null || true
    fi
    # Regle d'isolement (independante du lien montant), retiree dans tous les cas.
    while iptables -D FORWARD -i "$AP_IFACE" -o "$AP_IFACE" -j DROP 2>/dev/null; do :; done
    # Chaines dediees a la coupure d'acces et au comptage de consommation
    # par appareil (donnees perdues volontairement a chaque arret : la
    # consommation affichee couvre la session de partage en cours).
    iptables -F OPTIMA_CUT 2>/dev/null || true
    iptables -X OPTIMA_CUT 2>/dev/null || true
    iptables -F OPTIMA_ACCT 2>/dev/null || true
    iptables -X OPTIMA_ACCT 2>/dev/null || true
}

start_ap() {
    local ssid="$1"
    local password="$2"
    local max_clients="${3:-20}"
    local bandwidth_mbps="${4:-0}"
    # 06/11/2026 : reglages supplementaires (reseau masque, isolement, DNS)
    local hidden="${5:-0}"
    local isolate="${6:-0}"
    local dns="${7:-auto}"
    [ "$hidden" = 1 ] || hidden=0
    [ "$isolate" = 1 ] || isolate=0
    local dns_clients
    case "$dns" in
        cloudflare) dns_clients="1.1.1.1,1.0.0.1" ;;
        google) dns_clients="8.8.8.8,8.8.4.4" ;;
        quad9) dns_clients="9.9.9.9,149.112.112.112" ;;
        *) dns_clients="10.42.0.1,8.8.8.8" ;;
    esac

    case "$max_clients" in ''|*[!0-9]*) max_clients=20 ;; esac
    case "$bandwidth_mbps" in ''|*[!0-9]*) bandwidth_mbps=0 ;; esac
    [ "$max_clients" -lt 1 ] && max_clients=1
    [ "$max_clients" -gt 240 ] && max_clients=240

    if [ ${#password} -lt 8 ]; then
        echo "ERREUR: le mot de passe doit faire au moins 8 caracteres" >&2
        exit 1
    fi

    local uplink
    uplink=$(detect_uplink)
    if [ -z "$uplink" ]; then
        echo "ERREUR: aucune connexion Wi-Fi active detectee (rien a partager)" >&2
        exit 1
    fi

    local channel
    channel=$(detect_channel "$uplink")
    [ -z "$channel" ] && channel=1

    # 07/11/2026 : la carte n'a qu'une radio, le point d'acces doit emettre sur le canal
    # de la connexion Wi-Fi du PC. Canal > 14 = bande 5 GHz -> hw_mode=a (avec g, hostapd
    # refuse un canal 5 GHz et ne demarre pas). Le 5 GHz est « IR-CONCURRENT » sur ce
    # pilote : autorise seulement tant que le PC reste connecte sur ce meme canal.
    local hw_mode=g
    [ "$channel" -gt 14 ] 2>/dev/null && hw_mode=a

    # 07/11/2026 : verifier AVANT de lancer hostapd que la carte a le droit d'emettre
    # un point d'acces sur ce canal, et sinon expliquer pourquoi (au lieu d'un echec muet).
    # Canaux « Radar detection » (5 GHz, 52 a 144 en France) ou desactives : interdits.
    local phy_check flags reseau
    phy_check=$(iw dev "$uplink" info | awk '/wiphy/{print "phy"$2; exit}')
    flags=$(iw phy "$phy_check" channels 2>/dev/null | awk -v c="[$channel]" '
        $0 ~ /^[[:space:]]*\*/ { dedans = (index($0, c) > 0); if (dedans && $0 ~ /disabled/) print "disabled"; next }
        dedans { print }')
    reseau=$(iw dev "$uplink" info | sed -n 's/^[[:space:]]*ssid //p' | head -1)
    if printf '%s' "$flags" | grep -qiE 'radar|disabled'; then
        echo "MESSAGE: Optima ne peut pas partager « ${reseau:-ce Wi-Fi} » : il emet sur le canal $channel (5 GHz), un canal reserve aux radars ou la carte Wi-Fi n'a pas le droit de creer un point d'acces. Connecte le PC a un Wi-Fi en 2,4 GHz (ou en 5 GHz sur les canaux 36 a 48), puis relance le partage." >&2
        exit 2
    fi

    stop_all
    sleep 2

    # Conserve la liste de blocage existante (persiste entre demarrages tant
    # que /run n'est pas efface, donc jusqu'au prochain redemarrage du PC).
    touch "$RUN_DIR/denylist"

    # 06/11/2026 (Optima v2) : socket de controle de hostapd lisible par le groupe de
    # l'utilisateur qui a lance Optima -> l'interface lit sans root le debit, le signal
    # et la duree de connexion de chaque appareil (hostapd_cli all_sta).
    local ctrl_group=0
    if [ -n "${PKEXEC_UID:-}" ]; then
        ctrl_group=$(id -g "$PKEXEC_UID" 2>/dev/null || echo 0)
    fi

    cat > "$RUN_DIR/hostapd.conf" <<EOF
interface=$AP_IFACE
driver=nl80211
ssid=$ssid
hw_mode=$hw_mode
channel=$channel
wpa=2
wpa_passphrase=$password
wpa_key_mgmt=WPA-PSK
rsn_pairwise=CCMP
ieee80211n=1
ctrl_interface=$RUN_DIR/hostapd_ctrl
ctrl_interface_group=$ctrl_group
macaddr_acl=0
deny_mac_file=$RUN_DIR/denylist
ignore_broadcast_ssid=$hidden
ap_isolate=$isolate
EOF

    local phy essai hostapd_ok=false
    phy=$(iw dev "$uplink" info | awk '/wiphy/{print "phy"$2; exit}')

    # hostapd plante parfois (bug driver/etat transitoire) juste apres la
    # creation de l'interface virtuelle : jusqu'a 2 tentatives completes.
    for essai in 1 2; do
        iw dev "$AP_IFACE" del 2>/dev/null || true
        sleep 1
        # 05/11/2026 (d'apres la note Optima de septembre) : adresse MAC aleatoire a chaque
        # creation. Reutiliser toujours la meme MAC degradait l'etat du pilote apres
        # plusieurs cycles -> segfault de hostapd au demarrage.
        local mac_ap
        mac_ap=$(printf '02:%02x:%02x:%02x:%02x:%02x' $((RANDOM%256)) $((RANDOM%256)) $((RANDOM%256)) $((RANDOM%256)) $((RANDOM%256)))
        iw phy "$phy" interface add "$AP_IFACE" type __ap addr "$mac_ap"

        # Doit se faire immediatement : NetworkManager tente sinon de gerer
        # cette interface des sa creation, ce qui empeche hostapd de la
        # configurer (course entre les deux, echec sinon).
        nmcli device set "$AP_IFACE" managed no 2>/dev/null || true
        sleep 1

        rm -f "$RUN_DIR/hostapd.pid"
        hostapd -B "$RUN_DIR/hostapd.conf" -P "$RUN_DIR/hostapd.pid" || true
        sleep 2

        if [ -f "$RUN_DIR/hostapd.pid" ] && [ -d "/proc/$(cat "$RUN_DIR/hostapd.pid")" ]; then
            hostapd_ok=true
            break
        fi
        echo "Tentative $essai echouee, nouvel essai..." >&2
    done

    if [ "$hostapd_ok" != true ]; then
        echo "ERREUR: impossible de demarrer le point d'acces (hostapd a echoue deux fois)" >&2
        exit 1
    fi

    ip addr flush dev "$AP_IFACE"
    ip addr add 10.42.0.1/24 dev "$AP_IFACE"

    local dhcp_fin=$((10 + max_clients - 1))
    [ "$dhcp_fin" -gt 250 ] && dhcp_fin=250

    cat > "$RUN_DIR/dnsmasq.conf" <<EOF
interface=$AP_IFACE
bind-interfaces
except-interface=lo
dhcp-range=10.42.0.10,10.42.0.$dhcp_fin,255.255.255.0,12h
dhcp-option=3,10.42.0.1
dhcp-option=6,$dns_clients
dhcp-leasefile=$RUN_DIR/dnsmasq.leases
EOF

    dnsmasq --conf-file="$RUN_DIR/dnsmasq.conf" --pid-file="$RUN_DIR/dnsmasq.pid"

    sysctl -w net.ipv4.ip_forward=1 >/dev/null

    # Limite de debit partagee (0 = illimite) : plafonne le trafic sortant
    # vers les appareils connectes (download cote clients).
    if [ "$bandwidth_mbps" -gt 0 ]; then
        tc qdisc add dev "$AP_IFACE" root tbf rate "${bandwidth_mbps}mbit" burst 32kbit latency 400ms 2>/dev/null || true
    fi

    # Chaine de coupure d'acces (par IP, ciblee par appareil) : toujours
    # evaluee en premier, pour qu'un appareil "coupe" ne ressorte jamais
    # vers Internet meme s'il reste associe au Wi-Fi.
    iptables -N OPTIMA_CUT
    # Chaine de comptage de consommation par appareil : une paire de regles
    # (upload/download) par IP connue, sans cible ("fall through"), donc
    # sans effet sur le routage — seulement des compteurs lus par "sync".
    iptables -N OPTIMA_ACCT

    iptables -t nat -A POSTROUTING -o "$uplink" -j MASQUERADE
    iptables -A FORWARD -j OPTIMA_CUT
    iptables -A FORWARD -j OPTIMA_ACCT
    # Isolement : les appareils connectes ne se voient pas entre eux.
    if [ "$isolate" = 1 ]; then
        iptables -A FORWARD -i "$AP_IFACE" -o "$AP_IFACE" -j DROP
    fi
    iptables -A FORWARD -i "$AP_IFACE" -o "$uplink" -j ACCEPT
    iptables -A FORWARD -i "$uplink" -o "$AP_IFACE" -m state --state RELATED,ESTABLISHED -j ACCEPT

    # Normalisation TTL : uniformise le TTL des paquets entrants et sortants
    # sur l'interface montante, avant la decision de routage.
    iptables -t mangle -A PREROUTING -i "$uplink" -j TTL --ttl-set 65
    iptables -t mangle -A POSTROUTING -o "$uplink" -j TTL --ttl-set 65

    # Reapplique les coupures d'acces internet deja demandees pour des
    # appareils qui se reconnectent apres un redemarrage du partage.
    touch "$RUN_DIR/cutlist"
    sync_state "$uplink"

    echo "OK"
}

# Recalibre les chaines OPTIMA_CUT / OPTIMA_ACCT sur la liste actuelle des
# baux DHCP : ajoute les regles de comptage manquantes pour les nouveaux
# appareils, et reapplique les coupures d'acces internet demandees pour
# les appareils de cutlist qui ont (re)obtenu une IP. N'efface jamais une
# regle existante (les compteurs de trafic doivent survivre a chaque appel).
sync_state() {
    local uplink="$1"
    [ -z "$uplink" ] && uplink=$(detect_uplink)
    [ -z "$uplink" ] && return 0
    [ -f "$RUN_DIR/dnsmasq.leases" ] || return 0

    while read -r _exp mac ip _host _clientid; do
        [ -z "$mac" ] && continue
        mac=$(echo "$mac" | tr 'A-Z' 'a-z')

        if ! iptables -C OPTIMA_ACCT -s "$ip" -o "$uplink" -m comment --comment "up-$mac" 2>/dev/null; then
            iptables -A OPTIMA_ACCT -s "$ip" -o "$uplink" -m comment --comment "up-$mac"
        fi
        if ! iptables -C OPTIMA_ACCT -d "$ip" -i "$uplink" -m comment --comment "down-$mac" 2>/dev/null; then
            iptables -A OPTIMA_ACCT -d "$ip" -i "$uplink" -m comment --comment "down-$mac"
        fi

        if [ -f "$RUN_DIR/cutlist" ] && grep -qix "$mac" "$RUN_DIR/cutlist" 2>/dev/null; then
            if ! iptables -C OPTIMA_CUT -s "$ip" -j DROP -m comment --comment "cut-$mac" 2>/dev/null; then
                iptables -A OPTIMA_CUT -s "$ip" -j DROP -m comment --comment "cut-$mac"
            fi
            if ! iptables -C OPTIMA_CUT -d "$ip" -j DROP -m comment --comment "cut-$mac" 2>/dev/null; then
                iptables -A OPTIMA_CUT -d "$ip" -j DROP -m comment --comment "cut-$mac"
            fi
        fi
    done < "$RUN_DIR/dnsmasq.leases"
}

# Lit les compteurs de la chaine OPTIMA_ACCT (via iptables-save, seul
# format stable a parser) et les agrege par adresse MAC.
lire_consommation() {
    local sortie mac sens paquets octets
    declare -A montant_up montant_down
    sortie=$(iptables-save -c -t filter 2>/dev/null | grep ' -A OPTIMA_ACCT ')
    while read -r ligne; do
        [ -z "$ligne" ] && continue
        paquets=$(echo "$ligne" | sed -n 's/^\[\([0-9]*\):.*/\1/p')
        octets=$(echo "$ligne" | sed -n 's/^\[[0-9]*:\([0-9]*\)\].*/\1/p')
        mac=$(echo "$ligne" | sed -n 's/.*--comment "\{0,1\}\(up\|down\)-\([0-9a-f:]*\).*/\2/p')
        sens=$(echo "$ligne" | sed -n 's/.*--comment "\{0,1\}\(up\|down\)-[0-9a-f:]*.*/\1/p')
        [ -z "$mac" ] && continue
        if [ "$sens" = "up" ]; then
            montant_up["$mac"]=$(( ${montant_up["$mac"]:-0} + ${octets:-0} ))
        else
            montant_down["$mac"]=$(( ${montant_down["$mac"]:-0} + ${octets:-0} ))
        fi
    done <<< "$sortie"

    local premier=true resultat="["
    local macs
    macs=$(printf '%s\n%s\n' "${!montant_up[@]}" "${!montant_down[@]}" | sort -u)
    for mac in $macs; do
        [ -z "$mac" ] && continue
        [ "$premier" = true ] && premier=false || resultat="$resultat,"
        resultat="$resultat{\"mac\":\"$mac\",\"up\":${montant_up[$mac]:-0},\"down\":${montant_down[$mac]:-0}}"
    done
    resultat="$resultat]"
    echo "$resultat"
}

# Commande privilegiee appelee periodiquement par l'interface : maintient
# les regles de comptage/coupure a jour puis renvoie la consommation
# cumulee (en octets) de chaque appareil connu.
sync_and_report() {
    local uplink
    uplink=$(detect_uplink)
    if [ -f "$RUN_DIR/hostapd.pid" ] && [ -d "/proc/$(cat "$RUN_DIR/hostapd.pid" 2>/dev/null)" ]; then
        sync_state "$uplink"
        lire_consommation
    else
        echo "[]"
    fi
}

# Deconnecte un appareil (deauth 802.11 immediat) et l'empeche de se
# reconnecter en l'ajoutant a la deny_mac_file de hostapd (rechargee a
# chaud). Sans le blocage, l'appareil se reassocierait aussitot puisqu'il
# connait toujours le mot de passe.
kick_mac() {
    local mac
    mac=$(echo "${1:-}" | tr 'A-Z' 'a-z')
    if [ -z "$mac" ]; then
        echo "ERREUR: adresse MAC manquante" >&2
        exit 1
    fi

    touch "$RUN_DIR/denylist"
    if ! grep -qix "$mac" "$RUN_DIR/denylist" 2>/dev/null; then
        echo "$mac" >> "$RUN_DIR/denylist"
    fi

    if [ -S "$RUN_DIR/hostapd_ctrl/$AP_IFACE" ]; then
        hostapd_cli -p "$RUN_DIR/hostapd_ctrl" -i "$AP_IFACE" reload >/dev/null 2>&1 || true
        hostapd_cli -p "$RUN_DIR/hostapd_ctrl" -i "$AP_IFACE" deauthenticate "$mac" >/dev/null 2>&1 || true
    fi

    # Retire aussi le bail DHCP pour que le statut ne le montre plus comme
    # connecte (le lien radio est de toute facon coupe par le deauth).
    if [ -f "$RUN_DIR/dnsmasq.leases" ]; then
        grep -vi " $mac " "$RUN_DIR/dnsmasq.leases" > "$RUN_DIR/dnsmasq.leases.tmp" 2>/dev/null || true
        mv "$RUN_DIR/dnsmasq.leases.tmp" "$RUN_DIR/dnsmasq.leases" 2>/dev/null || true
    fi
    echo "OK"
}

unblock_mac() {
    local mac
    mac=$(echo "${1:-}" | tr 'A-Z' 'a-z')
    if [ -z "$mac" ]; then
        echo "ERREUR: adresse MAC manquante" >&2
        exit 1
    fi

    if [ -f "$RUN_DIR/denylist" ]; then
        grep -vix "$mac" "$RUN_DIR/denylist" > "$RUN_DIR/denylist.tmp" 2>/dev/null || true
        mv "$RUN_DIR/denylist.tmp" "$RUN_DIR/denylist"
    fi
    if [ -S "$RUN_DIR/hostapd_ctrl/$AP_IFACE" ]; then
        hostapd_cli -p "$RUN_DIR/hostapd_ctrl" -i "$AP_IFACE" reload >/dev/null 2>&1 || true
    fi
    echo "OK"
}

# Coupe l'acces Internet d'un appareil SANS le deconnecter du Wi-Fi (il
# reste associe, garde son bail DHCP, mais plus aucun paquet ne traverse
# vers l'exterieur). Contrairement a kick_mac, aucun deauth n'est envoye.
cut_internet() {
    local mac ip uplink
    mac=$(echo "${1:-}" | tr 'A-Z' 'a-z')
    if [ -z "$mac" ]; then
        echo "ERREUR: adresse MAC manquante" >&2
        exit 1
    fi

    touch "$RUN_DIR/cutlist"
    if ! grep -qix "$mac" "$RUN_DIR/cutlist" 2>/dev/null; then
        echo "$mac" >> "$RUN_DIR/cutlist"
    fi

    uplink=$(detect_uplink)
    ip=$(awk -v m="$mac" 'tolower($2)==m{print $3}' "$RUN_DIR/dnsmasq.leases" 2>/dev/null | tail -1)
    if [ -n "$ip" ] && [ -n "$uplink" ]; then
        iptables -C OPTIMA_CUT -s "$ip" -j DROP -m comment --comment "cut-$mac" 2>/dev/null \
            || iptables -A OPTIMA_CUT -s "$ip" -j DROP -m comment --comment "cut-$mac"
        iptables -C OPTIMA_CUT -d "$ip" -j DROP -m comment --comment "cut-$mac" 2>/dev/null \
            || iptables -A OPTIMA_CUT -d "$ip" -j DROP -m comment --comment "cut-$mac"
    fi
    echo "OK"
}

# Retire la coupure d'acces Internet : supprime l'entree de cutlist et les
# regles DROP correspondantes (retrouvees par leur commentaire, supprimees
# en partant de la derniere ligne pour ne pas decaler les numeros pendant
# la boucle).
restore_internet() {
    local mac
    mac=$(echo "${1:-}" | tr 'A-Z' 'a-z')
    if [ -z "$mac" ]; then
        echo "ERREUR: adresse MAC manquante" >&2
        exit 1
    fi

    if [ -f "$RUN_DIR/cutlist" ]; then
        grep -vix "$mac" "$RUN_DIR/cutlist" > "$RUN_DIR/cutlist.tmp" 2>/dev/null || true
        mv "$RUN_DIR/cutlist.tmp" "$RUN_DIR/cutlist"
    fi

    local lignes ligne
    lignes=$(iptables -L OPTIMA_CUT --line-numbers -n 2>/dev/null | grep "cut-$mac\b" | awk '{print $1}' | sort -rn)
    for ligne in $lignes; do
        iptables -D OPTIMA_CUT "$ligne" 2>/dev/null || true
    done
    echo "OK"
}

status() {
    local uplink pid running=false blocked="[]"
    uplink=$(detect_uplink)

    if [ -f "$RUN_DIR/denylist" ] && [ -s "$RUN_DIR/denylist" ]; then
        blocked=$(awk 'NF{printf "%s\"%s\"", (NR>1?",":""), $1}' "$RUN_DIR/denylist")
        blocked="[$blocked]"
    fi

    # pgrep sur un process root peut echouer pour un appelant non-root
    # (hidepid) : on verifie plutot via le fichier pid + /proc/<pid>, une
    # simple existence de repertoire, toujours lisible.
    if [ -f "$RUN_DIR/hostapd.pid" ]; then
        pid=$(cat "$RUN_DIR/hostapd.pid" 2>/dev/null)
        [ -n "$pid" ] && [ -d "/proc/$pid" ] && running=true
    fi

    if [ "$running" = true ]; then
        local clients=0 devices="[]"
        if [ -f "$RUN_DIR/dnsmasq.leases" ]; then
            clients=$(wc -l < "$RUN_DIR/dnsmasq.leases")
            # cutlist et denylist sont de simples fichiers texte (pas besoin
            # de droits root pour les lire) : c'est ce qui permet a ce statut
            # de rester appelable sans pkexec.
            devices=$(awk -v cutlist="$RUN_DIR/cutlist" '
                BEGIN {
                    while ((getline ligne < cutlist) > 0) { if (ligne != "") coupe[tolower(ligne)] = 1 }
                    close(cutlist)
                }
                {
                    host = ($4 == "*" ? "Appareil inconnu" : $4)
                    gsub(/"/, "", host)
                    mac = tolower($2)
                    est_coupe = (mac in coupe) ? "true" : "false"
                    printf "%s{\"ip\":\"%s\",\"mac\":\"%s\",\"hostname\":\"%s\",\"coupe\":%s}", (NR>1?",":""), $3, mac, host, est_coupe
                }
            ' "$RUN_DIR/dnsmasq.leases")
            devices="[$devices]"
        fi
        echo "{\"running\": true, \"uplink\": \"${uplink:-null}\", \"clients\": $clients, \"devices\": $devices, \"blocked\": $blocked}"
    else
        echo "{\"running\": false, \"uplink\": \"${uplink:-null}\", \"clients\": 0, \"devices\": [], \"blocked\": $blocked}"
    fi
}

case "$1" in
    start) start_ap "$2" "$3" "$4" "$5" "$6" "$7" "$8" ;;
    stop) stop_all; echo "OK" ;;
    status) status ;;
    kick) kick_mac "$2" ;;
    unblock) unblock_mac "$2" ;;
    cut) cut_internet "$2" ;;
    restore) restore_internet "$2" ;;
    sync) sync_and_report ;;
    *) echo "Usage: $0 {start SSID PASSWORD [MAX_CLIENTS] [BANDWIDTH_MBPS]|stop|status|kick MAC|unblock MAC|cut MAC|restore MAC|sync}" >&2; exit 1 ;;
esac
