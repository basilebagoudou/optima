#!/bin/bash
# Partie systeme d'Optima (a lancer en root, appelee par install.sh) :
#  1. NetworkManager ne touche jamais a l'interface ap0 (sinon course au demarrage ->
#     hostapd et wpa_supplicant plantent en boucle, plus de Wi-Fi jusqu'au redemarrage) ;
#  2. lanceurs /usr/local/libexec/optima/<action> + regles polkit : la fenetre de mot de
#     passe affiche une phrase claire au lieu de la commande (et du mot de passe Wi-Fi).
# Usage : sudo bash installer-systeme.sh /home/<utilisateur>/.local/share/optima
set -e

[ "$(id -u)" -eq 0 ] || { echo "ERREUR: a lancer en root (sudo)." >&2; exit 1; }
DEST="${1:?Usage: installer-systeme.sh <dossier d installation d Optima>}"
[ -f "$DEST/optima-hotspot.sh" ] || { echo "ERREUR: $DEST/optima-hotspot.sh introuvable." >&2; exit 1; }
ICI="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

install -m 644 "$ICI/99-optima-ap0.conf" /etc/NetworkManager/conf.d/99-optima-ap0.conf
nmcli general reload conf 2>/dev/null || true

install -d -m 755 /usr/local/libexec/optima
for action in start stop cut restore kick unblock; do
    cat > "/usr/local/libexec/optima/$action" <<LANCEUR
#!/bin/bash
# Lanceur root d'Optima ($action) : polkit affiche un message lisible au lieu de la commande.
exec /bin/bash $DEST/optima-hotspot.sh $action "\$@"
LANCEUR
    chmod 755 "/usr/local/libexec/optima/$action"
done

install -m 644 "$ICI/local.optima.policy" /usr/share/polkit-1/actions/local.optima.policy
echo "Partie systeme d'Optima installee."
