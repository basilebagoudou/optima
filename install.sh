#!/bin/bash
# Installe Optima pour l'utilisateur courant a partir de ce depot.
set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DEST="$HOME/.local/share/optima"
BIN_LANCEUR="$HOME/.local/bin/optima"
FICHIER_DESKTOP="$HOME/.local/share/applications/optima.desktop"

echo "=== Installation d'Optima ==="
echo ""

# 1. Verification des dependances systeme.
DEPS_MANQUANTES=()
for cmd in hostapd dnsmasq pkexec iw iptables nmcli; do
    command -v "$cmd" >/dev/null 2>&1 || DEPS_MANQUANTES+=("$cmd")
done

if [ ${#DEPS_MANQUANTES[@]} -gt 0 ]; then
    echo "Paquets manquants : ${DEPS_MANQUANTES[*]}"
    echo "Installation via apt (mot de passe administrateur demande)..."
    sudo apt-get update
    sudo apt-get install -y hostapd dnsmasq policykit-1 iw iptables network-manager
else
    echo "Toutes les dependances systeme sont deja presentes."
fi

command -v python3 >/dev/null 2>&1 || { echo "ERREUR: python3 est requis." >&2; exit 1; }
python3 -c "import tkinter" 2>/dev/null || {
    echo "Installation de python3-tk..."
    sudo apt-get install -y python3-tk
}

# 2. Copie des fichiers de l'application.
echo "Copie des fichiers dans $DEST ..."
mkdir -p "$DEST"
cp "$SCRIPT_DIR/optima_gui.py" "$DEST/"
cp "$SCRIPT_DIR/optima-hotspot.sh" "$DEST/"
cp "$SCRIPT_DIR/optima-icon.png" "$DEST/optima-icon.png"
chmod +x "$DEST/optima-hotspot.sh"

# 3. Lanceur en ligne de commande.
mkdir -p "$HOME/.local/bin"
cat > "$BIN_LANCEUR" <<EOF
#!/bin/bash
exec python3 "$DEST/optima_gui.py" "\$@"
EOF
chmod +x "$BIN_LANCEUR"

# 4. Entree dans le menu des applications.
mkdir -p "$HOME/.local/share/applications"
cat > "$FICHIER_DESKTOP" <<EOF
[Desktop Entry]
Type=Application
Name=Optima
Comment=Partage de connexion Wi-Fi
Exec=$BIN_LANCEUR
Icon=$DEST/optima-icon.png
StartupWMClass=Optima
Terminal=false
Categories=Network;Utility;
StartupNotify=true
EOF

update-desktop-database "$HOME/.local/share/applications" 2>/dev/null || true

echo ""
echo "=== Installation terminee ==="
echo "Lance Optima depuis le menu des applications, ou tape 'optima' dans un terminal."

case ":$PATH:" in
    *":$HOME/.local/bin:"*) ;;
    *) echo "Note : $HOME/.local/bin n'est pas dans le PATH. Deconnecte-toi/reconnecte-toi, ou lance directement $BIN_LANCEUR" ;;
esac
