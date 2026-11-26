#!/bin/bash
# Construit le paquet Debian/Ubuntu d'Optima (installation pour tout le systeme).
# Usage : bash packaging/build-deb.sh [VERSION] [DOSSIER_DE_SORTIE]
#   -> optima_VERSION_all.deb
# Installation : sudo apt install ./optima_VERSION_all.deb
set -e
umask 022

ICI="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
VERSION="${1:-3.0.0}"
SORTIE="${2:-$ICI/dist}"
TRAVAIL="$(mktemp -d)"
trap 'rm -rf "$TRAVAIL"' EXIT
R="$TRAVAIL/optima_${VERSION}_all"
ACTIONS="start stop cut restore kick unblock limit priorite dns filaire"

install -d "$R/DEBIAN" "$R/usr/lib/optima" "$R/usr/bin" "$R/usr/libexec/optima" \
    "$R/usr/share/applications" "$R/usr/share/pixmaps" "$R/usr/share/polkit-1/actions" \
    "$R/etc/NetworkManager/conf.d" "$R/usr/share/doc/optima"

install -m 644 "$ICI/optima_gui.py" "$R/usr/lib/optima/optima_gui.py"
install -m 755 "$ICI/optima-hotspot.sh" "$R/usr/lib/optima/optima-hotspot.sh"
install -m 644 "$ICI/optima-icon.png" "$R/usr/lib/optima/optima-icon.png"
install -m 644 "$ICI/optima-icon.png" "$R/usr/share/pixmaps/optima.png"
install -m 644 "$ICI/systeme/99-optima-ap0.conf" "$R/etc/NetworkManager/conf.d/99-optima-ap0.conf"
install -m 644 "$ICI/LICENSE" "$R/usr/share/doc/optima/copyright"

cat > "$R/usr/bin/optima" <<'EOF'
#!/bin/sh
exec python3 /usr/lib/optima/optima_gui.py "$@"
EOF
chmod 755 "$R/usr/bin/optima"

for action in $ACTIONS; do
    cat > "$R/usr/libexec/optima/$action" <<EOF
#!/bin/bash
# Lanceur root d'Optima ($action) : polkit affiche un message lisible au lieu de la commande.
exec /bin/bash /usr/lib/optima/optima-hotspot.sh $action "\$@"
EOF
    chmod 755 "$R/usr/libexec/optima/$action"
done

# Actions polkit : memes messages que l'installation locale, identifiants propres au paquet
sed -e 's/local\.optima\./org.optima./g' -e 's#/usr/local/libexec/optima/#/usr/libexec/optima/#g' \
    "$ICI/systeme/local.optima.policy" > "$R/usr/share/polkit-1/actions/org.optima.policy"

cat > "$R/usr/share/applications/optima.desktop" <<'EOF'
[Desktop Entry]
Type=Application
Name=Optima
Comment=Partage de connexion Wi-Fi
Exec=/usr/bin/optima
Icon=optima
StartupWMClass=Optima
Terminal=false
Categories=Network;
StartupNotify=false
EOF

TAILLE=$(du -sk "$R/usr" "$R/etc" | awk '{s+=$1} END {print s}')
cat > "$R/DEBIAN/control" <<EOF
Package: optima
Version: $VERSION
Section: net
Priority: optional
Architecture: all
Installed-Size: $TAILLE
Depends: python3, python3-tk, python3-pil, hostapd, dnsmasq-base, iw, iptables, iproute2, network-manager, pkexec | policykit-1, systemd
Recommends: python3-qrcode, python3-pystray, gir1.2-ayatanaappindicator3-0.1, python3-matplotlib, libnotify-bin
Maintainer: Basile BAGOUDOU <basile2bagoudou@gmail.com>
Homepage: https://github.com/basilebagoudou/optima
Description: Partage de connexion Wi-Fi, meme derriere un portail captif
 Optima cree un point d'acces Wi-Fi a cote de la connexion Wi-Fi du PC et la
 partage (normalisation TTL, portail captif, invites, limites de donnees et
 de debit, filtrage des sites, statistiques et rapport hebdomadaire).
EOF
echo "/etc/NetworkManager/conf.d/99-optima-ap0.conf" > "$R/DEBIAN/conffiles"

cat > "$R/DEBIAN/postinst" <<'EOF'
#!/bin/sh
set -e
# NetworkManager ne doit jamais gerer l'interface ap0 (voir 99-optima-ap0.conf)
if [ "$1" = configure ] && [ -z "$DPKG_ROOT" ] && command -v nmcli >/dev/null 2>&1; then
    nmcli general reload conf >/dev/null 2>&1 || true
fi
exit 0
EOF
cat > "$R/DEBIAN/postrm" <<'EOF'
#!/bin/sh
set -e
if [ "$1" = remove ] || [ "$1" = purge ]; then
    if [ -z "$DPKG_ROOT" ] && command -v nmcli >/dev/null 2>&1; then
        nmcli general reload conf >/dev/null 2>&1 || true
    fi
fi
exit 0
EOF
chmod 755 "$R/DEBIAN/postinst" "$R/DEBIAN/postrm"

chmod -R go-w "$R"
chmod 755 "$R"
mkdir -p "$SORTIE"
fakeroot dpkg-deb --build --root-owner-group "$R" "$SORTIE/optima_${VERSION}_all.deb" >/dev/null
echo "$SORTIE/optima_${VERSION}_all.deb"
