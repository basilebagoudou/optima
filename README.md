# Optima

Application de bureau (Linux/GNOME, Python + Tkinter) pour transformer ta
carte Wi-Fi en point d'accès et partager ta connexion internet avec d'autres
appareils — utile quand ton réseau est derrière un portail captif et que tu
n'as pas d'équivalent Linux à des outils comme Connectify.

## Fonctionnalités

- Point d'accès Wi-Fi (`hostapd` + `dnsmasq` + NAT `iptables`) piloté depuis
  une interface graphique simple
- Normalisation TTL sur le trafic partagé
- QR code de connexion, génération de mot de passe, copier/coller rapide
- Icône dans la zone de notification, notifications système, historique des
  appareils connectés
- Limite du nombre d'appareils et du débit partagé
- Démarrage automatique à l'ouverture de session
- Système de licence **optionnel** (voir plus bas), désactivé par défaut

## Pré-requis

- Ubuntu/Debian avec GNOME (testé sur Ubuntu récent, Wayland)
- Une carte Wi-Fi dont le pilote supporte la création d'une interface
  virtuelle en mode AP (`iw list` → vérifier le mode `AP` dans les
  combinaisons supportées)
- `python3` + `python3-tk`
- `hostapd`, `dnsmasq`, `policykit-1` (pour `pkexec`), `iw`, `iptables`,
  `network-manager`

Optionnel (fonctionnalités qui se désactivent proprement si absentes) :
- `pillow` (icônes, QR code) — souvent déjà présent sur Ubuntu
- `qrcode` (`pip install --user qrcode`) pour le QR code de connexion
- `pystray` (`pip install --user pystray`) pour l'icône de zone de
  notification (nécessite `gir1.2-ayatanaappindicator3-0.1` sous Ubuntu/GNOME)

## Installation

```bash
git clone <url-de-ce-depot>
cd optima
./install.sh
```

Le script installe les dépendances système manquantes (`apt`, mot de passe
demandé), copie l'application dans `~/.local/share/optima`, crée un lanceur
(`optima`) et une entrée dans le menu des applications.

## Comment ça marche

Optima crée une interface virtuelle `ap0` à côté de ta carte Wi-Fi
principale (le pilote de la plupart des cartes ne supporte pas le mode
simultané client + point d'accès sur une seule interface). `hostapd` gère le
point d'accès sur `ap0`, `dnsmasq` distribue les IP, et des règles
`iptables` font le NAT vers ta connexion principale.

**Limite connue** : sur certaines cartes/pilotes, `hostapd` peut planter de
façon reproductible après de nombreux cycles rapides de démarrage/arrêt
(état du pilote dégradé). Un redémarrage de la machine résout le problème
si ça arrive.

## Système de licence (optionnel)

Par défaut, **aucune restriction** : l'application est entièrement
utilisable dès l'installation. Si tu veux distribuer un binaire compilé à
des tiers en exigeant une clé d'activation que toi seul peux générer :

```bash
# 1. Genere un secret que tu gardes prive (ne le commite jamais)
python3 -c "import secrets; print(secrets.token_hex(32))"

# 2. Exporte-le avant de lancer/compiler l'application
export OPTIMA_LICENSE_SECRET=<le_secret>

# 3. Cree ce fichier sur ta propre machine pour debloquer le panneau admin
#    (generation de cles) dans Parametres :
mkdir -p ~/.config/optima && touch ~/.config/optima/.admin
```

Sans `OPTIMA_LICENSE_SECRET`, ce mécanisme est entièrement désactivé (pas
d'écran d'activation, pas de panneau admin). C'est volontaire : ce dépôt
public ne contient aucun secret intégré.

À savoir : c'est du Python en clair, pas un système anti-piratage robuste —
ça filtre une copie occasionnelle, pas une personne qui lit le code source.

## Licence de ce dépôt

Aucune licence choisie pour l'instant. Ajoute un fichier `LICENSE` (par
exemple MIT) si tu veux clarifier les conditions de réutilisation du code.
