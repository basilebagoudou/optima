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
    pkill -9 -f "hostapd $RUN_DIR/hostapd.conf" 2>/dev/null || true
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
        iptables -D FORWARD -i "$AP_IFACE" -o "$uplink" -j ACCEPT 2>/dev/null || true
        iptables -D FORWARD -i "$uplink" -o "$AP_IFACE" -m state --state RELATED,ESTABLISHED -j ACCEPT 2>/dev/null || true
        iptables -t mangle -D POSTROUTING -o "$uplink" -j TTL --ttl-set 65 2>/dev/null || true
        iptables -t mangle -D PREROUTING -i "$uplink" -j TTL --ttl-set 65 2>/dev/null || true
    fi
}

start_ap() {
    local ssid="$1"
    local password="$2"
    local max_clients="${3:-20}"
    local bandwidth_mbps="${4:-0}"

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

    stop_all
    sleep 2

    cat > "$RUN_DIR/hostapd.conf" <<EOF
interface=$AP_IFACE
driver=nl80211
ssid=$ssid
hw_mode=g
channel=$channel
wpa=2
wpa_passphrase=$password
wpa_key_mgmt=WPA-PSK
rsn_pairwise=CCMP
ieee80211n=1
EOF

    local phy essai hostapd_ok=false
    phy=$(iw dev "$uplink" info | awk '/wiphy/{print "phy"$2; exit}')

    # hostapd plante parfois (bug driver/etat transitoire) juste apres la
    # creation de l'interface virtuelle : jusqu'a 2 tentatives completes.
    for essai in 1 2; do
        iw dev "$AP_IFACE" del 2>/dev/null || true
        sleep 1
        iw phy "$phy" interface add "$AP_IFACE" type __ap

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
dhcp-option=6,10.42.0.1,8.8.8.8
dhcp-leasefile=$RUN_DIR/dnsmasq.leases
EOF

    dnsmasq --conf-file="$RUN_DIR/dnsmasq.conf" --pid-file="$RUN_DIR/dnsmasq.pid"

    sysctl -w net.ipv4.ip_forward=1 >/dev/null

    # Limite de debit partagee (0 = illimite) : plafonne le trafic sortant
    # vers les appareils connectes (download cote clients).
    if [ "$bandwidth_mbps" -gt 0 ]; then
        tc qdisc add dev "$AP_IFACE" root tbf rate "${bandwidth_mbps}mbit" burst 32kbit latency 400ms 2>/dev/null || true
    fi

    iptables -t nat -A POSTROUTING -o "$uplink" -j MASQUERADE
    iptables -A FORWARD -i "$AP_IFACE" -o "$uplink" -j ACCEPT
    iptables -A FORWARD -i "$uplink" -o "$AP_IFACE" -m state --state RELATED,ESTABLISHED -j ACCEPT

    # Normalisation TTL : uniformise le TTL des paquets entrants et sortants
    # sur l'interface montante, avant la decision de routage.
    iptables -t mangle -A PREROUTING -i "$uplink" -j TTL --ttl-set 65
    iptables -t mangle -A POSTROUTING -o "$uplink" -j TTL --ttl-set 65

    echo "OK"
}

status() {
    local uplink pid running=false
    uplink=$(detect_uplink)

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
            devices=$(awk '{
                host = ($4 == "*" ? "Appareil inconnu" : $4)
                gsub(/"/, "", host)
                printf "%s{\"ip\":\"%s\",\"mac\":\"%s\",\"hostname\":\"%s\"}", (NR>1?",":""), $3, $2, host
            }' "$RUN_DIR/dnsmasq.leases")
            devices="[$devices]"
        fi
        echo "{\"running\": true, \"uplink\": \"${uplink:-null}\", \"clients\": $clients, \"devices\": $devices}"
    else
        echo "{\"running\": false, \"uplink\": \"${uplink:-null}\", \"clients\": 0, \"devices\": []}"
    fi
}

case "$1" in
    start) start_ap "$2" "$3" "$4" "$5" ;;
    stop) stop_all; echo "OK" ;;
    status) status ;;
    *) echo "Usage: $0 {start SSID PASSWORD [MAX_CLIENTS] [BANDWIDTH_MBPS]|stop|status}" >&2; exit 1 ;;
esac
