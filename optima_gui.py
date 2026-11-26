#!/usr/bin/env python3
"""Optima - point d'acces Wi-Fi partage, avec normalisation TTL.
Interface de bureau (Tkinter).

Version 2 (06/11/2026) : nouvelle interface (theme clair/sombre suivant GNOME,
cartes arrondies, gros bouton marche/arret), statistiques en direct (debit,
consommation de la session, graphique), gestion des appareils (surnom, couper
Internet, deconnecter et bloquer, debloquer), QR code integre, arret programme.
"""
import hashlib
import hmac
import json
import os
import queue
import re
import secrets
import string
import subprocess
import sys
import threading
import time
import tkinter as tk
import urllib.request
import tkinter.font as tkfont
from collections import deque
from datetime import date, datetime, timedelta
from pathlib import Path
from tkinter import messagebox

from PIL import Image, ImageDraw

try:
    import qrcode
    QR_DISPONIBLE = True
except ImportError:
    QR_DISPONIBLE = False

try:
    import pystray
    TRAY_DISPONIBLE = True
except ImportError:
    TRAY_DISPONIBLE = False

APP_DIR = Path(__file__).resolve().parent
HOTSPOT_SCRIPT = APP_DIR / "optima-hotspot.sh"
CONFIG_PATH = Path.home() / ".config" / "optima" / "config.json"
HISTORY_PATH = Path.home() / ".local" / "share" / "optima" / "historique.json"
CONSO_PATH = Path.home() / ".local" / "share" / "optima" / "conso.json"
CACHE_DIR = Path.home() / ".cache" / "optima"
ICONE_APP = APP_DIR / "optima-icon.png"
AUTOSTART_DIR = Path.home() / ".config" / "autostart"
AUTOSTART_FILE = AUTOSTART_DIR / "optima-autostart.desktop"
LANCEUR = Path.home() / ".local" / "bin" / "optima"
RUN_DIR = Path("/run/optima")
AP_IFACE = "ap0"

ADMIN_MARKER = Path.home() / ".config" / "optima" / ".admin"
ACTIVATION_PATH = Path.home() / ".config" / "optima" / "activation.json"

# Systeme de licence optionnel, desactive par defaut (voir README d'origine) :
# exporter OPTIMA_LICENSE_SECRET pour l'activer, ~/.config/optima/.admin pour
# le panneau de generation de cles.
_SECRET_HEX = os.environ.get("OPTIMA_LICENSE_SECRET")
LICENCE_ACTIVE = bool(_SECRET_HEX)
SECRET_LICENCE = bytes.fromhex(_SECRET_HEX) if _SECRET_HEX else b""

# -- themes ------------------------------------------------------------------
THEMES = {
    "clair": {
        "fond": "#f2f2f7", "carte": "#ffffff", "champ": "#f2f2f7",
        "bordure": "#e3e3e8", "texte": "#1d1d1f", "texte_att": "#6e6e73",
        "accent": "#34c759", "accent_survol": "#2fb350", "accent_texte": "#ffffff",
        "bleu": "#007aff", "rouge": "#ff3b30", "orange": "#ff9500",
        "bouton": "#e9e9ee", "bouton_survol": "#dcdce2", "eteint": "#c7c7cc",
        "graphe_bas": "#007aff", "graphe_haut": "#34c759",
    },
    "sombre": {
        "fond": "#1c1c1e", "carte": "#2c2c2e", "champ": "#1c1c1e",
        "bordure": "#3a3a3c", "texte": "#f5f5f7", "texte_att": "#98989d",
        "accent": "#30d158", "accent_survol": "#28b84c", "accent_texte": "#04210d",
        "bleu": "#0a84ff", "rouge": "#ff453a", "orange": "#ff9f0a",
        "bouton": "#3a3a3c", "bouton_survol": "#48484a", "eteint": "#48484a",
        "graphe_bas": "#0a84ff", "graphe_haut": "#30d158",
    },
}
POLICE = "Cantarell"


def _facteur_echelle():
    """Ecran HiDPI (Xwayland en mise a l'echelle native) : Tk agrandit les polices
    (Xft.dpi) mais pas les tailles en pixels -> on multiplie nous-memes."""
    try:
        sortie = subprocess.run(["xrdb", "-query"], capture_output=True, text=True, timeout=3).stdout
        m = re.search(r"Xft\.dpi:\s*([0-9.]+)", sortie)
        if m:
            return max(1.0, float(m.group(1)) / 96)
    except Exception:
        pass
    return 1.0


F = _facteur_echelle()


def px(v):
    if isinstance(v, tuple):
        return tuple(px(x) for x in v)
    return int(round(v * F))

CONFIG_PAR_DEFAUT = {
    "ssid": "Optima",
    "password": "",
    "auto_start": False,
    "max_clients": 20,
    "bandwidth_mbps": 0,
    "theme": "auto",          # auto | clair | sombre
    "notifier_appareils": True,
    "surnoms": {},            # mac -> nom choisi
    # 06/11/2026 : reglages supplementaires
    "partage_ouverture": False,   # demarrer le partage quand on ouvre Optima
    "fermeture": "reduire",       # reduire | quitter (croix de la fenetre)
    "reseau_masque": False,       # ne pas diffuser le nom du reseau
    "isoler_appareils": False,    # les appareils ne se voient pas entre eux
    "dns": "auto",                # auto | cloudflare | google | quad9
    "arret_inactif_min": 0,       # arret si aucun appareil depuis N min (0 = jamais)
    "confirmer_arret": True,      # demander avant d'arreter si des appareils sont connectes
    # 26/11/2026 : donnees, quotas, plages horaires, surveillance de la connexion
    "quota_jour_mo": 0,           # limite de donnees par jour, tous appareils (0 = aucune)
    "quota_mois_go": 0,           # limite par mois civil (0 = aucune)
    "quota_appareil_mo": 0,       # limite par appareil et par jour (0 = aucune)
    "quota_action": "alerter",    # alerter | bloquer (arret du partage / Internet coupe pour l'appareil)
    "planning_actif": False,      # demarrage et arret a heures fixes
    "planning_debut": "08:00",
    "planning_fin": "23:00",
    "planning_jours": [0, 1, 2, 3, 4, 5, 6],   # 0 = lundi
    "surveiller_connexion": True, # ping + detection du portail captif du reseau source
    # 26/11/2026 (2) : invites, liste blanche, debit par appareil, test de vitesse
    "invite": None,               # {"mdp", "debut", "expire", "macs"} pendant un acces invite
    "liste_blanche": False,       # seuls les appareils autorises peuvent se connecter
    "appareils_autorises": [],
    "limites_debit": {},          # mac -> kbit/s (debit maximum par appareil)
    "tests_vitesse": [],          # derniers resultats [{"date", "ping", "bas", "haut"}]
}

ERREURS_CONNUES = {
    "aucune connexion wi-fi active": (
        "Aucune connexion Wi-Fi active. Connecte-toi d'abord a un reseau, "
        "puis relance le partage."
    ),
    "hostapd a echoue deux fois": (
        "Le point d'acces n'a pas reussi a demarrer (bug hostapd connu sur "
        "cette machine). Reessaie une fois ; si ca persiste, redemarre le PC."
    ),
    "mot de passe doit faire au moins 8": "Le mot de passe doit faire au moins 8 caracteres.",
    "delai depasse": "L'operation a pris trop de temps et a ete interrompue. Reessaie.",
    "not authorized": "Autorisation refusee (mot de passe administrateur annule).",
    "request dismissed": "Autorisation annulee.",
}

ARRETS_PROGRAMMES = [0, 15, 30, 60, 120, 240]  # minutes, 0 = jamais


def message_convivial(sortie):
    # 07/11/2026 : le script peut fournir directement un message complet, a afficher tel quel.
    for ligne in (sortie or "").splitlines():
        if ligne.startswith("MESSAGE: "):
            return ligne[len("MESSAGE: "):]
    bas = (sortie or "").lower()
    for cle, message in ERREURS_CONNUES.items():
        if cle in bas:
            return message
    if "126" in bas or "error executing command" in bas:
        return "Autorisation annulee."
    return sortie or "Une erreur est survenue."


def charger_config():
    cfg = json.loads(json.dumps(CONFIG_PAR_DEFAUT))
    if CONFIG_PATH.exists():
        try:
            cfg.update(json.loads(CONFIG_PATH.read_text()))
        except (ValueError, json.JSONDecodeError):
            pass
    if not isinstance(cfg.get("surnoms"), dict):
        cfg["surnoms"] = {}
    for cle, type_ in (("limites_debit", dict), ("appareils_autorises", list), ("tests_vitesse", list)):
        if not isinstance(cfg.get(cle), type_):
            cfg[cle] = type_()
    if cfg.get("invite") is not None and not isinstance(cfg.get("invite"), dict):
        cfg["invite"] = None
    return cfg


def sauvegarder_config(cfg):
    CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    CONFIG_PATH.write_text(json.dumps(cfg, indent=2, ensure_ascii=False))
    try:
        os.chmod(CONFIG_PATH, 0o600)  # contient le mot de passe du reseau
    except OSError:
        pass


def charger_historique():
    if HISTORY_PATH.exists():
        try:
            return json.loads(HISTORY_PATH.read_text())
        except (ValueError, json.JSONDecodeError):
            pass
    return []


def ajouter_historique(entree):
    historique = charger_historique()
    historique.append(entree)
    historique = historique[-300:]
    HISTORY_PATH.parent.mkdir(parents=True, exist_ok=True)
    HISTORY_PATH.write_text(json.dumps(historique, indent=2, ensure_ascii=False))


def activer_autostart():
    AUTOSTART_DIR.mkdir(parents=True, exist_ok=True)
    AUTOSTART_FILE.write_text(
        "[Desktop Entry]\n"
        "Type=Application\n"
        "Name=Optima (demarrage auto)\n"
        f"Exec={LANCEUR} --auto\n"
        f"Icon={ICONE_APP}\n"
        "StartupWMClass=Optima\n"
        "X-GNOME-Autostart-enabled=true\n"
        "NoDisplay=true\n"
        "Comment=Demarre automatiquement le partage Optima a la connexion\n"
    )


def desactiver_autostart():
    AUTOSTART_FILE.unlink(missing_ok=True)


def envoyer_notification(titre, message):
    try:
        subprocess.Popen(
            ["notify-send", "-a", "Optima", "-i", str(ICONE_APP),
             "-h", "string:desktop-entry:optima", titre, message],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
    except FileNotFoundError:
        pass


def theme_systeme():
    try:
        sortie = subprocess.run(
            ["gsettings", "get", "org.gnome.desktop.interface", "color-scheme"],
            capture_output=True, text=True, timeout=3,
        ).stdout
        return "sombre" if "dark" in sortie else "clair"
    except Exception:
        return "clair"


# -- licence (inchange) ------------------------------------------------------
def _empreinte_licence(partie):
    return hmac.new(SECRET_LICENCE, partie.encode(), hashlib.sha256).hexdigest().upper()[:8]


def generer_cle_licence():
    alphabet = string.ascii_uppercase + string.digits
    partie = "".join(secrets.choice(alphabet) for _ in range(10))
    return f"{partie[:5]}-{partie[5:]}-{_empreinte_licence(partie)}"


def valider_cle_licence(cle):
    cle = (cle or "").strip().upper().replace(" ", "")
    morceaux = cle.split("-")
    if len(morceaux) != 3 or len(morceaux[0]) != 5 or len(morceaux[1]) != 5:
        return False
    return hmac.compare_digest(_empreinte_licence(morceaux[0] + morceaux[1]), morceaux[2])


def est_admin():
    return LICENCE_ACTIVE and ADMIN_MARKER.exists()


def est_active():
    if not LICENCE_ACTIVE or est_admin():
        return True
    if not ACTIVATION_PATH.exists():
        return False
    try:
        donnees = json.loads(ACTIVATION_PATH.read_text())
    except (ValueError, json.JSONDecodeError):
        return False
    return valider_cle_licence(donnees.get("cle", ""))


def activer_licence(cle):
    ACTIVATION_PATH.parent.mkdir(parents=True, exist_ok=True)
    ACTIVATION_PATH.write_text(json.dumps({"cle": cle.strip().upper(),
                                           "date": datetime.now().isoformat()}, indent=2))


# -- lecture de l'etat (sans droits root) ----------------------------------------
def echapper_wifi(valeur):
    return re.sub(r'([\\;,":])', r'\\\1', valeur)


LANCEURS_ROOT = Path("/usr/local/libexec/optima")


def commande_privilegiee(*args):
    """Passe par un lanceur dedie (/usr/local/libexec/optima/<action>) declare
    dans polkit : la fenetre de mot de passe affiche alors une phrase claire
    au lieu de la commande (et du mot de passe Wi-Fi)."""
    lanceur = LANCEURS_ROOT / args[0] if args else None
    if lanceur and lanceur.is_file():
        return ["pkexec", str(lanceur), *args[1:]]
    return ["pkexec", "bash", str(HOTSPOT_SCRIPT), *args]


def executer_privilegie(*args):
    """Lance le script hotspot en root via pkexec, renvoie (ok, sortie)."""
    env = os.environ.copy()
    env.setdefault("DISPLAY", ":0")
    env.setdefault("WAYLAND_DISPLAY", "wayland-0")
    env.setdefault("XDG_RUNTIME_DIR", f"/run/user/{os.getuid()}")
    try:
        resultat = subprocess.run(
            commande_privilegiee(*args),
            capture_output=True, text=True, timeout=60, env=env,
        )
        sortie = (resultat.stdout + resultat.stderr).strip()
        return resultat.returncode == 0, sortie
    except subprocess.TimeoutExpired:
        return False, "Delai depasse."


def lire_statut():
    try:
        resultat = subprocess.run(["bash", str(HOTSPOT_SCRIPT), "status"],
                                  capture_output=True, text=True, timeout=10)
        etat = json.loads(resultat.stdout)
    except Exception:
        etat = {"running": False, "uplink": None, "clients": 0, "devices": [], "blocked": []}
    etat.setdefault("blocked", [])
    return etat


def lire_stations():
    """Infos radio par appareil (debit consomme, signal, duree) via hostapd_cli.
    Accessible sans root seulement si le partage a ete demarre par Optima v2
    (groupe du socket de controle = groupe de l'utilisateur). Sinon : {}."""
    try:
        sortie = subprocess.run(
            ["hostapd_cli", "-p", str(RUN_DIR / "hostapd_ctrl"), "-i", AP_IFACE, "all_sta"],
            capture_output=True, text=True, timeout=4,
        ).stdout
    except Exception:
        return {}
    stations, courant = {}, None
    for ligne in sortie.splitlines():
        ligne = ligne.strip()
        if re.fullmatch(r"[0-9a-fA-F]{2}(:[0-9a-fA-F]{2}){5}", ligne):
            courant = stations.setdefault(ligne.lower(), {})
        elif courant is not None and "=" in ligne:
            cle, _, val = ligne.partition("=")
            courant[cle] = val
    return stations


def lire_compteurs_ap():
    base = Path("/sys/class/net") / AP_IFACE / "statistics"
    try:
        # cote point d'acces : tx = recu par les appareils, rx = envoye par eux
        return int((base / "tx_bytes").read_text()), int((base / "rx_bytes").read_text())
    except (OSError, ValueError):
        return None


def lire_ssid_en_service():
    try:
        for ligne in (RUN_DIR / "hostapd.conf").read_text().splitlines():
            if ligne.startswith("ssid="):
                return ligne[5:]
    except OSError:
        pass
    return None


def debut_du_partage():
    try:
        return (RUN_DIR / "hostapd.pid").stat().st_mtime
    except OSError:
        return None


def nom_reseau_source(iface):
    if not iface:
        return None
    try:
        sortie = subprocess.run(["nmcli", "-t", "-f", "GENERAL.CONNECTION", "device", "show", iface],
                                capture_output=True, text=True, timeout=4).stdout
        return sortie.split(":", 1)[1].strip() or None
    except Exception:
        return None


def format_octets(n):
    if n is None:
        return "—"
    for unite in ("o", "Ko", "Mo", "Go", "To"):
        if n < 1024 or unite == "To":
            return f"{n:.0f} {unite}" if unite in ("o", "Ko") else f"{n:.1f} {unite}"
        n /= 1024
    return f"{n:.1f} To"


def format_debit(octets_par_s):
    bits = octets_par_s * 8
    if bits < 1000:
        return f"{bits:.0f} bit/s"
    if bits < 1_000_000:
        return f"{bits / 1000:.0f} kbit/s"
    return f"{bits / 1_000_000:.1f} Mbit/s"


def format_duree(secondes):
    secondes = int(max(0, secondes))
    h, reste = divmod(secondes, 3600)
    m, s = divmod(reste, 60)
    if h:
        return f"{h} h {m:02d}"
    if m:
        return f"{m} min {s:02d}"
    return f"{s} s"


def barres_signal(dbm):
    try:
        dbm = int(dbm)
    except (TypeError, ValueError):
        return None
    if dbm >= -55:
        return 4
    if dbm >= -65:
        return 3
    if dbm >= -75:
        return 2
    return 1


MO = 1024 * 1024
GO = 1024 * MO
JOURS_COURTS = ("L", "M", "M", "J", "V", "S", "D")
JOURS_SEMAINE = ("lun.", "mar.", "mer.", "jeu.", "ven.", "sam.", "dim.")


# -- comptage des donnees (26/11/2026) -----------------------------------------------
class Compteur:
    """Cumule les octets partages par jour, au total (compteurs de ap0) et par
    appareil (compteurs hostapd). Ecrit dans conso.json ; garde aussi les derniers
    compteurs vus pour ne rien perdre si Optima est relance pendant le partage."""

    GARDER_JOURS = 400

    def __init__(self, chemin=CONSO_PATH):
        self.chemin = chemin
        self.donnees = {"jours": {}, "noms": {}, "alertes": {}, "session": {}}
        try:
            lu = json.loads(chemin.read_text())
            if isinstance(lu, dict):
                for cle in self.donnees:
                    if isinstance(lu.get(cle), dict):
                        self.donnees[cle] = lu[cle]
        except (OSError, ValueError):
            pass
        self._modifie = False
        self._derniere_ecriture = time.time()

    # -- enregistrement
    def _jour(self, cle=None):
        cle = cle or date.today().isoformat()
        return self.donnees["jours"].setdefault(cle, {"bas": 0, "haut": 0, "appareils": {}})

    def ajouter_total(self, compteurs, debut):
        """compteurs = (bas, haut) de ap0, debut = date de lancement du partage."""
        session = self.donnees["session"]
        if not compteurs or not debut:
            return
        bas, haut = compteurs
        if session.get("debut") == debut and bas >= session.get("bas", 0) and haut >= session.get("haut", 0):
            db, dh = bas - session.get("bas", 0), haut - session.get("haut", 0)
        else:
            db = dh = 0   # nouveau partage (ou premier lancement) : on part de maintenant
            if session.get("debut") not in (None, debut):
                session["stations"] = {}
        session.update({"debut": debut, "bas": bas, "haut": haut})
        if db or dh:
            j = self._jour()
            j["bas"] += db
            j["haut"] += dh
            self._modifie = True

    def ajouter_stations(self, stations, appareils):
        session = self.donnees["session"]
        prec = session.setdefault("stations", {})
        maintenant = time.time()
        vu = session.get("vu")   # derniere observation (None = tout premier lancement)
        noms = {a.get("mac"): a.get("hostname") for a in appareils or []}
        j = None
        for mac, st in stations.items():
            try:
                tx, rx = int(st.get("tx_bytes", 0)), int(st.get("rx_bytes", 0))
                ct = int(st.get("connected_time", 0))
            except ValueError:
                continue
            ancien = prec.get(mac)
            if ancien and tx >= ancien[0] and rx >= ancien[1] and ct >= ancien[2]:
                db, dh = tx - ancien[0], rx - ancien[1]
            elif ancien is None and (vu is None or ct > maintenant - vu + 15):
                db = dh = 0   # deja connecte avant qu'on observe : pas de rattrapage
            else:
                db, dh = tx, rx   # nouvelle association : compteurs repartis de zero
            prec[mac] = [tx, rx, ct]
            nom = noms.get(mac)
            if nom and nom not in ("*", "Appareil inconnu") and self.donnees["noms"].get(mac) != nom:
                self.donnees["noms"][mac] = nom
                self._modifie = True
            if db or dh:
                j = j or self._jour()
                a = j["appareils"].setdefault(mac, [0, 0])
                a[0] += db
                a[1] += dh
                self._modifie = True
        session["vu"] = maintenant
        for mac in list(prec):
            if mac not in stations:
                prec.pop(mac)

    def sauvegarder(self, forcer=False):
        if not (self._modifie or forcer):
            return
        if not forcer and time.time() - self._derniere_ecriture < 30:
            return
        limite = (date.today() - timedelta(days=self.GARDER_JOURS)).isoformat()
        for cle in [k for k in self.donnees["jours"] if k < limite]:
            self.donnees["jours"].pop(cle)
        for cle in [k for k in self.donnees["alertes"] if k.split("|")[1] < limite[:7]]:
            self.donnees["alertes"].pop(cle)
        try:
            self.chemin.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.chemin.with_suffix(".tmp")
            tmp.write_text(json.dumps(self.donnees, ensure_ascii=False))
            tmp.replace(self.chemin)
            self._modifie = False
            self._derniere_ecriture = time.time()
        except OSError:
            pass

    # -- lecture
    def jour(self, cle=None):
        j = self.donnees["jours"].get(cle or date.today().isoformat(), {})
        return j.get("bas", 0), j.get("haut", 0)

    def total_jour(self, cle=None):
        return sum(self.jour(cle))

    def total_mois(self, mois=None):
        mois = mois or date.today().isoformat()[:7]
        return sum(j.get("bas", 0) + j.get("haut", 0)
                   for k, j in self.donnees["jours"].items() if k.startswith(mois))

    def appareil_jour(self, mac, cle=None):
        a = self.donnees["jours"].get(cle or date.today().isoformat(), {}).get("appareils", {}).get(mac)
        return sum(a) if a else 0

    def serie(self, n):
        aujourdhui = date.today()
        jours = [aujourdhui - timedelta(days=i) for i in range(n - 1, -1, -1)]
        return [(d, *self.jour(d.isoformat())) for d in jours]

    def total_periode(self, n):
        return sum(b + h for _d, b, h in self.serie(n))

    def classement(self, n):
        debut = (date.today() - timedelta(days=n - 1)).isoformat()
        cumul = {}
        for k, j in self.donnees["jours"].items():
            if k >= debut:
                for mac, (b, h) in j.get("appareils", {}).items():
                    c = cumul.setdefault(mac, [0, 0])
                    c[0] += b
                    c[1] += h
        return sorted(cumul.items(), key=lambda e: -(e[1][0] + e[1][1]))

    def alerte_deja(self, cle):
        return cle in self.donnees["alertes"]

    def noter_alerte(self, cle):
        self.donnees["alertes"][cle] = True
        self._modifie = True


# -- qualite de la connexion source (26/11/2026) -------------------------------------
def mesurer_ping(iface, cible="1.1.1.1"):
    """Temps de reponse en ms via l'interface source, None si perdu."""
    commande = ["ping", "-n", "-q", "-c", "1", "-W", "2"]
    if iface:
        commande += ["-I", iface]
    try:
        r = subprocess.run(commande + [cible], capture_output=True, text=True, timeout=5)
    except Exception:
        return None
    m = re.search(r"= [0-9.]+/([0-9.]+)/", r.stdout)
    return float(m.group(1)) if r.returncode == 0 and m else None


def verifier_connectivite():
    """full | portal | limited | none | unknown (verification de NetworkManager)."""
    try:
        return subprocess.run(["nmcli", "networking", "connectivity", "check"],
                              capture_output=True, text=True, timeout=20).stdout.strip() or "unknown"
    except Exception:
        return "unknown"


# -- plages horaires (26/11/2026) -----------------------------------------------------
def lire_heure(texte):
    m = re.fullmatch(r"\s*([01]?\d|2[0-3])\s*[:hH]\s*([0-5]\d)?\s*", texte or "")
    if not m:
        return None
    return int(m.group(1)) * 60 + int(m.group(2) or 0)


def dans_plage(cfg, moment=None):
    moment = moment or datetime.now()
    debut, fin = lire_heure(cfg.get("planning_debut")), lire_heure(cfg.get("planning_fin"))
    jours = cfg.get("planning_jours") or []
    if debut is None or fin is None:
        return None
    m, j = moment.hour * 60 + moment.minute, moment.weekday()
    if debut == fin:
        return j in jours
    if debut < fin:
        return j in jours and debut <= m < fin
    # plage qui passe minuit (ex. 20:00 -> 02:00) : la nuit appartient au jour de depart
    return (j in jours and m >= debut) or ((j - 1) % 7 in jours and m < fin)


def prochain_changement(cfg, moment=None):
    """(datetime, demarre) du prochain debut ou fin de plage, sur 8 jours."""
    moment = (moment or datetime.now()).replace(second=0, microsecond=0)
    etat = dans_plage(cfg, moment)
    if etat is None:
        return None
    for i in range(1, 8 * 24 * 60):
        t = moment + timedelta(minutes=i)
        if dans_plage(cfg, t) != etat:
            return t, not etat
    return None


def format_moment(t):
    aujourdhui = date.today()
    if t.date() == aujourdhui:
        jour = "aujourd'hui"
    elif t.date() == aujourdhui + timedelta(days=1):
        jour = "demain"
    else:
        jour = JOURS_SEMAINE[t.weekday()]
    return f"{jour} à {t:%H:%M}"


# -- pilotage de hostapd sans root (socket de controle du groupe de l'utilisateur) ---------
INVITES_PSK = RUN_DIR / "invites.psk"
DEBITS_APPAREIL = [0, 1000, 2000, 5000, 10000]   # kbit/s, 0 = illimite


def hostapd_cmd(*args):
    try:
        r = subprocess.run(["hostapd_cli", "-p", str(RUN_DIR / "hostapd_ctrl"), "-i", AP_IFACE, *args],
                           capture_output=True, text=True, timeout=4)
    except Exception as erreur:
        return False, str(erreur)
    sortie = r.stdout.strip()
    return r.returncode == 0 and "FAIL" not in sortie and "UNKNOWN" not in sortie, sortie


def lister_stations_rapide():
    ok, sortie = hostapd_cmd("list_sta")
    if not ok:
        return None
    return [l.strip().lower() for l in sortie.splitlines()
            if re.fullmatch(r"[0-9a-fA-F]{2}(:[0-9a-fA-F]{2}){5}", l.strip())]


def keyid_station(mac):
    ok, sortie = hostapd_cmd("sta", mac)
    m = re.search(r"^keyid=(\S+)", sortie, re.M) if ok else None
    return m.group(1) if m else None


def invites_disponibles():
    """Le partage en cours a-t-il ete demarre avec le fichier des invites ?"""
    try:
        conf = (RUN_DIR / "hostapd.conf").read_text()
    except OSError:
        return False
    return "wpa_psk_file=" in conf and os.access(INVITES_PSK, os.W_OK)


def ecrire_invites(mdp):
    contenu = f"keyid=invite 00:00:00:00:00:00 {mdp}\n" if mdp else ""
    try:
        if INVITES_PSK.read_text() == contenu:
            return True
    except OSError:
        pass
    try:
        with open(INVITES_PSK, "w") as f:   # ecriture sur place : le dossier /run/optima est a root
            f.write(contenu)
    except OSError:
        return False
    return hostapd_cmd("reload_wpa_psk")[0]


def generer_mdp_invite():
    alphabet = "abcdefghjkmnpqrstuvwxyz23456789"
    return "".join(secrets.choice(alphabet) for _ in range(10))


def format_kbit(kbit):
    return f"{kbit / 1000:g} Mbit/s" if kbit >= 1000 else f"{kbit} kbit/s"


# -- test de vitesse (serveurs de Cloudflare, sans compte) --------------------------------
def test_descente(octets, rappel=None):
    req = urllib.request.Request(f"https://speed.cloudflare.com/__down?bytes={octets}",
                                 headers={"User-Agent": "Optima"})
    with urllib.request.urlopen(req, timeout=30) as r:
        recu, t0 = 0, None
        while True:
            morceau = r.read(65536)
            if not morceau:
                break
            if t0 is None:
                t0 = time.time()   # on ne compte pas l'etablissement de la connexion
                continue
            recu += len(morceau)
            if rappel:
                rappel(recu * 8 / max(0.001, time.time() - t0))
    duree = max(0.001, time.time() - (t0 or time.time()))
    return recu * 8 / duree


def test_montee(octets):
    donnees = secrets.token_bytes(octets)
    req = urllib.request.Request("https://speed.cloudflare.com/__up", data=donnees, method="POST",
                                 headers={"User-Agent": "Optima", "Content-Type": "application/octet-stream"})
    # connexion d'abord (petit envoi), puis mesure du gros envoi
    urllib.request.urlopen(urllib.request.Request(
        "https://speed.cloudflare.com/__up", data=b"0", method="POST",
        headers={"User-Agent": "Optima"}), timeout=15).read()
    t0 = time.time()
    with urllib.request.urlopen(req, timeout=60) as r:
        r.read()
    return octets * 8 / max(0.001, time.time() - t0)


def format_bits(bits_par_s):
    if bits_par_s >= 1_000_000:
        return f"{bits_par_s / 1_000_000:.1f} Mbit/s"
    return f"{bits_par_s / 1000:.0f} kbit/s"


def dossier_documents():
    try:
        d = subprocess.run(["xdg-user-dir", "DOCUMENTS"], capture_output=True, text=True,
                           timeout=3).stdout.strip()
        if d and Path(d).is_dir():
            return Path(d)
    except Exception:
        pass
    return Path.home()


def image_icone_tray(actif):
    # Point rond : vert quand le partage est actif, gris quand il est arrete
    # (dessine en 4x puis reduit pour des bords lisses).
    taille = 64
    couleur = (52, 199, 89, 255) if actif else (142, 142, 147, 255)
    grand = Image.new("RGBA", (taille * 4, taille * 4), (0, 0, 0, 0))
    d = ImageDraw.Draw(grand)
    r = 19 * 4
    c = taille * 2
    d.ellipse((c - r, c - r, c + r, c + r), fill=couleur)
    return grand.resize((taille, taille), Image.LANCZOS)


def _curseur_systeme(racine):
    """Tk (sans libXcursor) dessine ses curseurs nommes (main, barre en I...)
    avec les vieux curseurs X au lieu du theme Bibata. Curseur vide = herite
    de la fenetre racine -> on garde partout le curseur normal du systeme."""
    racine.option_add("*cursor", "")
    racine.configure(cursor="")


def _premier_plan(win):
    """Tk ne signale pas son lancement a GNOME -> la fenetre s'ouvrait derriere
    les autres. On la met au premier plan nous-memes juste apres l'ouverture."""
    try:
        win.deiconify()
        win.lift()
        win.attributes("-topmost", True)
        win.after(500, lambda: win.attributes("-topmost", False))
        win.focus_force()
    except tk.TclError:
        pass


# -- petits composants dessines ----------------------------------------------------
def rect_arrondi(canvas, x1, y1, x2, y2, r, **kw):
    r = max(0, min(r, (x2 - x1) / 2, (y2 - y1) / 2))
    points = [x1 + r, y1, x2 - r, y1, x2, y1, x2, y1 + r, x2, y2 - r, x2, y2,
              x2 - r, y2, x1 + r, y2, x1, y2, x1, y2 - r, x1, y1 + r, x1, y1]
    return canvas.create_polygon(points, smooth=True, splinesteps=24, **kw)


class Carte(tk.Canvas):
    """Carte aux coins arrondis ; le contenu se place dans self.corps."""

    def __init__(self, parent, t, rayon=px(18), marge=px(16), titre=None):
        super().__init__(parent, bg=t["fond"], highlightthickness=0, bd=0, height=10)
        self.t, self.rayon, self.marge = t, rayon, marge
        self.corps = tk.Frame(self, bg=t["carte"])
        if titre:
            tk.Label(self.corps, text=titre, font=(POLICE, 12, "bold"), bg=t["carte"],
                     fg=t["texte_att"], anchor="w").pack(fill="x", pady=px((0, 6)))
        self._fenetre = self.create_window(marge, marge, window=self.corps, anchor="nw")
        self.bind("<Configure>", self._redessiner)
        self.corps.bind("<Configure>", self._redessiner)

    def _redessiner(self, _e=None):
        largeur = self.winfo_width()
        hauteur = self.corps.winfo_reqheight() + 2 * self.marge
        if int(self.cget("height")) != hauteur:
            self.config(height=hauteur)
        self.itemconfig(self._fenetre, width=max(10, largeur - 2 * self.marge))
        self.delete("fond")
        rect_arrondi(self, 1, 1, largeur - 1, hauteur - 1, self.rayon, width=max(1, px(1)),
                     fill=self.t["carte"], outline=self.t["bordure"], tags="fond")
        self.tag_lower("fond")


class BoutonPilule(tk.Canvas):
    def __init__(self, parent, t, texte, commande, style="secondaire", taille=12,
                 hauteur=px(34), largeur=None, fond_parent=None):
        self.t = t
        self.texte, self.commande, self.style = texte, commande, style
        self.police = (POLICE, taille, "bold" if style == "principal" else "normal")
        self.actif = True
        bg = fond_parent or t["carte"]
        self.largeur_fixe = largeur
        l = largeur or self._largeur_texte(parent, texte)
        super().__init__(parent, width=l, height=hauteur, bg=bg, highlightthickness=0,
                         bd=0)
        self._survol = False
        self.bind("<Configure>", lambda _e: self._dessiner())
        self.bind("<Enter>", lambda _e: self._etat_survol(True))
        self.bind("<Leave>", lambda _e: self._etat_survol(False))
        self.bind("<ButtonRelease-1>", self._clic)
        self._dessiner()

    def _largeur_texte(self, widget, texte):
        return tkfont.Font(root=widget, font=self.police).measure(texte) + px(28)

    def _couleurs(self):
        t = self.t
        if self.style == "principal":
            return (t["accent_survol"] if self._survol else t["accent"]), t["accent_texte"]
        if self.style == "danger":
            return (t["bouton_survol"] if self._survol else t["bouton"]), t["rouge"]
        if self.style == "bleu":
            return (t["bouton_survol"] if self._survol else t["bouton"]), t["bleu"]
        return (t["bouton_survol"] if self._survol else t["bouton"]), t["texte"]

    def _dessiner(self):
        self.delete("all")
        w, h = self.winfo_width(), self.winfo_height()
        if w < 4:
            w, h = int(self.cget("width")), int(self.cget("height"))
        fond, texte = self._couleurs()
        if not self.actif:
            texte = self.t["texte_att"]
        rect_arrondi(self, 0, 0, w, h, h / 2, fill=fond, outline="")
        self.create_text(w / 2, h / 2, text=self.texte, font=self.police, fill=texte)

    def _etat_survol(self, survol):
        self._survol = survol and self.actif
        self._dessiner()

    def _clic(self, _e):
        if self.actif and self.commande:
            self.commande()

    def configurer(self, texte=None, actif=None, style=None):
        if texte is not None:
            self.texte = texte
            if not self.largeur_fixe:
                self.config(width=self._largeur_texte(self, texte))
        if actif is not None:
            self.actif = actif
            self.config(cursor="")
        if style is not None:
            self.style = style
        self._dessiner()


class Interrupteur(tk.Canvas):
    """Interrupteur facon macOS."""

    def __init__(self, parent, t, variable, fond_parent=None):
        super().__init__(parent, width=px(44), height=px(26), bg=fond_parent or t["carte"],
                         highlightthickness=0, bd=0)
        self.t, self.variable = t, variable
        self.bind("<ButtonRelease-1>", lambda _e: (variable.set(not variable.get()), self._dessiner()))
        self._dessiner()

    def _dessiner(self):
        self.delete("all")
        on = bool(self.variable.get())
        rect_arrondi(self, 0, 0, px(44), px(26), px(13), fill=self.t["accent"] if on else self.t["eteint"], outline="")
        x = px(31) if on else px(13)
        self.create_oval(x - px(11), px(2), x + px(11), px(24), fill="#ffffff", outline="")


class Jauge(tk.Canvas):
    """Barre de progression fine (quota) : verte, orange a 80 %, rouge a 100 %."""

    def __init__(self, parent, t):
        super().__init__(parent, height=px(6), bg=t["carte"], highlightthickness=0, bd=0)
        self.t, self.ratio = t, 0.0
        self.bind("<Configure>", lambda _e: self._dessiner())

    def definir(self, ratio):
        self.ratio = max(0.0, ratio)
        self._dessiner()

    def _dessiner(self):
        self.delete("all")
        w, h = self.winfo_width(), self.winfo_height()
        if w < 10:
            return
        rect_arrondi(self, 0, 0, w, h, h / 2, fill=self.t["bouton"], outline="")
        couleur = (self.t["rouge"] if self.ratio >= 1 else
                   self.t["orange"] if self.ratio >= 0.8 else self.t["accent"])
        largeur = max(h, w * min(1.0, self.ratio)) if self.ratio > 0 else 0
        if largeur:
            rect_arrondi(self, 0, 0, largeur, h, h / 2, fill=couleur, outline="")


class BoutonMarche(tk.Canvas):
    """Gros bouton rond marche/arret."""

    TAILLE = px(92)

    def __init__(self, parent, t, commande):
        super().__init__(parent, width=self.TAILLE, height=self.TAILLE, bg=t["carte"],
                         highlightthickness=0, bd=0)
        self.t, self.commande = t, commande
        self.etat = "arret"  # arret | marche | attente
        self._angle = 0
        self.bind("<ButtonRelease-1>", lambda _e: self.etat != "attente" and commande())
        self._dessiner()

    def definir(self, etat):
        if etat != self.etat:
            self.etat = etat
            if etat == "attente":
                self._tourner()
            self._dessiner()

    def _tourner(self):
        if self.etat != "attente":
            return
        self._angle = (self._angle + 12) % 360
        self._dessiner()
        self.after(40, self._tourner)

    def _dessiner(self):
        self.delete("all")
        t, s = self.t, self.TAILLE
        c = s / 2
        if self.etat == "marche":
            fond, trait = t["accent"], "#ffffff"
        elif self.etat == "attente":
            fond, trait = t["bouton"], t["texte_att"]
        else:
            fond, trait = t["bouton"], t["texte_att"]
        self.create_oval(px(4), px(4), s - px(4), s - px(4), fill=fond, outline="")
        if self.etat == "attente":
            self.create_arc(px(14), px(14), s - px(14), s - px(14), start=self._angle, extent=100,
                            style="arc", outline=t["accent"], width=px(5))
            return
        r = s * 0.2
        self.create_arc(c - r, c - r + px(3), c + r, c + r + px(3), start=120, extent=300,
                        style="arc", outline=trait, width=px(5))
        self.create_line(c, c - r - px(4), c, c + px(2), fill=trait, width=px(5), capstyle="round")


class Graphe(tk.Canvas):
    """Petit graphique du debit (60 dernieres secondes)."""

    def __init__(self, parent, t, hauteur=54):
        super().__init__(parent, height=px(hauteur), bg=t["carte"], highlightthickness=0, bd=0)
        self.t = t
        self.bas = deque([0.0] * 60, maxlen=60)
        self.haut = deque([0.0] * 60, maxlen=60)
        self.bind("<Configure>", lambda _e: self.dessiner())

    def ajouter(self, bas, haut):
        self.bas.append(bas)
        self.haut.append(haut)
        self.dessiner()

    def dessiner(self):
        self.delete("all")
        w, h = self.winfo_width(), self.winfo_height()
        if w < 10:
            return
        maxi = max(max(self.bas), max(self.haut), 1)
        for serie, couleur, remplir in ((self.bas, self.t["graphe_bas"], True),
                                        (self.haut, self.t["graphe_haut"], False)):
            pas = w / (len(serie) - 1)
            pts = []
            for i, v in enumerate(serie):
                pts += [i * pas, h - px(2) - (v / maxi) * (h - px(6))]
            if remplir:
                self.create_polygon([0, h] + pts + [w, h], fill=couleur, outline="",
                                    stipple="gray25")
            self.create_line(pts, fill=couleur, width=px(2), smooth=True)


# -- application -----------------------------------------------------------------
class OptimaApp(tk.Tk):
    LARGEUR, HAUTEUR = px(540), px(900)

    def __init__(self, mode_auto=False):
        super().__init__(className="Optima")
        _curseur_systeme(self)
        self.after(250, lambda: _premier_plan(self))
        self.title("Optima")
        self.minsize(px(480), px(600))
        try:
            self._image_icone = tk.PhotoImage(file=str(ICONE_APP))
            self.iconphoto(True, self._image_icone)
        except tk.TclError:
            pass

        self.config_actuelle = charger_config()
        self.etat = {"running": False, "devices": [], "blocked": []}
        self.stations = {}
        self.appareils_precedents = None
        self.icone_tray = None
        self._averti_reduction = False
        self._occupe = False
        self._compteurs_prec = None
        self._session_depart = None
        self.arret_programme = None   # timestamp
        self._sans_appareil_depuis = None
        self.duree_arret = 0
        self._nom_source = None
        self._qr_visible = False
        # 26/11/2026 : donnees, qualite de la connexion source, plages horaires
        self.compteur = Compteur()
        self._pings = deque(maxlen=12)       # 1 minute de mesures (une toutes les 5 s)
        self._connectivite = None
        self._niveau_qualite = None
        self._texte_qualite = ""
        self._qualite_en_cours = False
        self._derniere_verif = 0.0
        self._plage_prec = None
        self._cache_planning = (None, None)
        # 26/11/2026 (2) : liste blanche (demandes de connexion), invites, limites
        self._demandes = {}          # mac -> heure de la tentative
        self._refuses = set()        # deja refuses dans cette session (pas de nouvelle notif)
        self._garde_en_cours = False
        self._keyids = {}            # mac -> keyid (« invite ») lu dans hostapd
        self._ip_limitees = {}       # mac -> ip au moment ou la limite a ete appliquee
        # Les fils d'arriere-plan (statut, pkexec, icone de la barre) ne touchent
        # jamais Tk directement : ils deposent une fonction ici, executee par Tk.
        self._file = queue.Queue()
        self._vider_file()

        self._construire()
        self._centrer_fenetre()
        self._configurer_tray()
        self._boucle_rapide()
        self._boucle_statut()
        self.after(1500, self._boucle_qualite)
        self.after(2000, self._boucle_garde)

        if mode_auto:
            if TRAY_DISPONIBLE:
                self.withdraw()
            self.after(1000, self._demarrage_auto)
        elif self.config_actuelle.get("partage_ouverture"):
            def verifier():
                if not lire_statut().get("running"):
                    self._en_main(self._demarrage_auto)
            self.after(1000, lambda: threading.Thread(target=verifier, daemon=True).start())

    def _en_main(self, fonction):
        self._file.put(fonction)

    def _vider_file(self):
        try:
            while True:
                fonction = self._file.get_nowait()
                try:
                    fonction()
                except tk.TclError:
                    pass
        except queue.Empty:
            pass
        try:
            self._id_file = self.after(80, self._vider_file)
        except tk.TclError:
            pass

    def destroy(self):
        try:
            self.after_cancel(self._id_file)
        except (AttributeError, tk.TclError, ValueError):
            pass
        super().destroy()

    # -- construction ------------------------------------------------------------
    def _choisir_theme(self):
        choix = self.config_actuelle.get("theme", "auto")
        if choix not in THEMES:
            choix = theme_systeme()
        return THEMES[choix]

    def _construire(self):
        self.t = t = self._choisir_theme()
        self.configure(bg=t["fond"])

        # zone defilante
        self.toile = tk.Canvas(self, bg=t["fond"], highlightthickness=0, bd=0)
        self.toile.pack(fill="both", expand=True)
        self.contenu = tk.Frame(self.toile, bg=t["fond"])
        self._id_contenu = self.toile.create_window(0, 0, window=self.contenu, anchor="nw")
        self.contenu.bind("<Configure>", lambda _e: self.toile.configure(scrollregion=self.toile.bbox("all")))
        self.toile.bind("<Configure>", lambda e: self.toile.itemconfig(self._id_contenu, width=e.width))
        for seq, delta in (("<Button-4>", -2), ("<Button-5>", 2)):
            self.bind_all(seq, lambda e, d=delta: self._defiler(d, e))
        self.bind_all("<MouseWheel>", lambda e: self._defiler(-1 if e.delta > 0 else 1, e))

        c = self.contenu
        # en-tete
        tete = tk.Frame(c, bg=t["fond"])
        tete.pack(fill="x", padx=px(20), pady=px((18, 10)))
        tk.Label(tete, text="Optima", font=(POLICE, 22, "bold"), bg=t["fond"],
                 fg=t["texte"]).pack(side="left")
        BoutonPilule(tete, t, "Réglages", self._ouvrir_parametres, taille=11, hauteur=px(28),
                     fond_parent=t["fond"]).pack(side="right")
        BoutonPilule(tete, t, "Historique", self._ouvrir_historique, taille=11, hauteur=px(28),
                     fond_parent=t["fond"]).pack(side="right", padx=px((0, 8)))
        BoutonPilule(tete, t, "Statistiques", self._ouvrir_stats, taille=11, hauteur=px(28),
                     fond_parent=t["fond"]).pack(side="right", padx=px((0, 8)))
        BoutonPilule(tete, t, "Vitesse", self._ouvrir_vitesse, taille=11, hauteur=px(28),
                     fond_parent=t["fond"]).pack(side="right", padx=px((0, 8)))

        # -- carte etat
        carte = Carte(c, t)
        carte.pack(fill="x", padx=px(16), pady=px((0, 12)))
        haut = tk.Frame(carte.corps, bg=t["carte"])
        haut.pack(fill="x")
        self.bouton_marche = BoutonMarche(haut, t, self._basculer)
        self.bouton_marche.pack(side="left")
        infos = tk.Frame(haut, bg=t["carte"])
        infos.pack(side="left", fill="both", expand=True, padx=px((16, 0)))
        self.label_etat = tk.Label(infos, text="Vérification…", font=(POLICE, 17, "bold"),
                                   bg=t["carte"], fg=t["texte"], anchor="w")
        self.label_etat.pack(fill="x", pady=px((10, 0)))
        self.label_sous_etat = tk.Label(infos, text="", font=(POLICE, 12), bg=t["carte"],
                                        fg=t["texte_att"], anchor="w", justify="left",
                                        wraplength=px(350))
        self.label_sous_etat.pack(fill="x")
        self.label_source = tk.Label(infos, text="", font=(POLICE, 11), bg=t["carte"],
                                     fg=t["texte_att"], anchor="w", justify="left", wraplength=px(350))
        self.label_source.pack(fill="x", pady=px((2, 0)))
        ligne_q = tk.Frame(infos, bg=t["carte"])
        ligne_q.pack(fill="x", pady=px((2, 0)))
        self.point_qualite = tk.Label(ligne_q, text="●", font=(POLICE, 11), bg=t["carte"],
                                      fg=t["eteint"])
        self.point_qualite.pack(side="left", anchor="n")
        self.label_qualite = tk.Label(ligne_q, text="Mesure de la connexion…", font=(POLICE, 11),
                                      bg=t["carte"], fg=t["texte_att"], anchor="w", justify="left",
                                      wraplength=px(330))
        self.label_qualite.pack(side="left", fill="x", padx=px((4, 0)))
        self.bouton_portail = BoutonPilule(infos, t, "Ouvrir la page de connexion", self._ouvrir_portail,
                                           style="bleu", taille=11, hauteur=px(28))

        # statistiques
        stats = tk.Frame(carte.corps, bg=t["carte"])
        stats.pack(fill="x", pady=px((14, 4)))
        self.stat_vals = {}
        for i, (cle, titre) in enumerate((("appareils", "Appareils"),
                                          ("vitesse", "Vitesse"),
                                          ("session", "Cette session"))):
            stats.columnconfigure(i, weight=1, uniform="s")
            bloc = tk.Frame(stats, bg=t["carte"])
            bloc.grid(row=0, column=i, sticky="w")
            tk.Label(bloc, text=titre, font=(POLICE, 10), bg=t["carte"], fg=t["texte_att"],
                     anchor="w").pack(fill="x")
            v = tk.Label(bloc, text="—", font=(POLICE, 12, "bold"), bg=t["carte"],
                         fg=t["texte"], anchor="w", justify="left")
            v.pack(fill="x")
            self.stat_vals[cle] = v
        self.graphe = Graphe(carte.corps, t)
        self.graphe.pack(fill="x", pady=px((8, 0)))
        legende = tk.Frame(carte.corps, bg=t["carte"])
        legende.pack(fill="x", pady=px((4, 0)))
        for couleur, texte in ((t["graphe_bas"], "Reçu par les appareils"),
                               (t["graphe_haut"], "Envoyé")):
            p = tk.Canvas(legende, width=px(10), height=px(10), bg=t["carte"], highlightthickness=0)
            p.create_oval(px(1), px(1), px(9), px(9), fill=couleur, outline="")
            p.pack(side="left", padx=px((0, 4)))
            tk.Label(legende, text=texte, font=(POLICE, 10), bg=t["carte"],
                     fg=t["texte_att"]).pack(side="left", padx=px((0, 12)))

        # donnees consommees (jour / mois) + jauges de quota
        self.lignes_conso = {}
        for cle, titre in (("jour", "Données aujourd'hui"), ("mois", "Données ce mois-ci")):
            bloc = tk.Frame(carte.corps, bg=t["carte"])
            bloc.pack(fill="x", pady=px((10 if cle == "jour" else 6, 0)))
            l = tk.Frame(bloc, bg=t["carte"])
            l.pack(fill="x")
            tk.Label(l, text=titre, font=(POLICE, 12), bg=t["carte"], fg=t["texte"]).pack(side="left")
            valeur = tk.Label(l, text="—", font=(POLICE, 12, "bold"), bg=t["carte"], fg=t["texte"])
            valeur.pack(side="right")
            jauge = Jauge(bloc, t)
            self.lignes_conso[cle] = {"valeur": valeur, "jauge": jauge}

        # arret programme
        ligne_arret = tk.Frame(carte.corps, bg=t["carte"])
        ligne_arret.pack(fill="x", pady=px((12, 0)))
        tk.Label(ligne_arret, text="Arrêt automatique", font=(POLICE, 12), bg=t["carte"],
                 fg=t["texte"]).pack(side="left")
        self.bouton_arret_prog = BoutonPilule(ligne_arret, t, "Jamais", self._cycle_arret,
                                              taille=11, hauteur=px(28), largeur=px(130))
        self.bouton_arret_prog.pack(side="right")
        self.label_planning = tk.Label(carte.corps, text="", font=(POLICE, 11), bg=t["carte"],
                                       fg=t["texte_att"], anchor="w", justify="left")

        # -- carte reseau
        carte_r = Carte(c, t, titre="RÉSEAU PARTAGÉ")
        carte_r.pack(fill="x", padx=px(16), pady=px((0, 12)))
        corps = carte_r.corps
        self.ssid_var = self._champ(corps, "Nom du réseau", self.config_actuelle["ssid"])
        self.mdp_var, self.entree_mdp = self._champ(corps, "Mot de passe",
                                                    self.config_actuelle["password"], secret=True)
        self.label_aide_mdp = tk.Label(corps, text="", font=(POLICE, 10), bg=t["carte"],
                                       fg=t["texte_att"], anchor="w")
        self.label_aide_mdp.pack(fill="x", pady=px((2, 0)))
        outils = tk.Frame(corps, bg=t["carte"])
        outils.pack(fill="x", pady=px((10, 0)))
        self.bouton_voir = BoutonPilule(outils, t, "Afficher", self._basculer_mdp, taille=11, hauteur=px(28))
        self.bouton_voir.pack(side="left")
        BoutonPilule(outils, t, "Générer", self._generer_mdp, taille=11, hauteur=px(28)).pack(side="left", padx=px(6))
        self.bouton_copier = BoutonPilule(outils, t, "Copier", self._copier_mdp, taille=11, hauteur=px(28))
        self.bouton_copier.pack(side="left")
        self.bouton_qr = BoutonPilule(outils, t, "QR code", self._basculer_qr, taille=11, hauteur=px(28),
                                      style="bleu")
        self.bouton_qr.pack(side="right")
        ligne_inv = tk.Frame(corps, bg=t["carte"])
        ligne_inv.pack(fill="x", pady=px((10, 0)))
        self.bouton_invite = BoutonPilule(ligne_inv, t, "Accès invité", self._ouvrir_invite, taille=11,
                                          hauteur=px(28), style="bleu")
        self.bouton_invite.pack(side="left")
        self.label_invite = tk.Label(ligne_inv, text="", font=(POLICE, 11), bg=t["carte"],
                                     fg=t["texte_att"], anchor="w")
        self.label_invite.pack(side="left", padx=px((10, 0)))
        self.cadre_qr = tk.Frame(corps, bg=t["carte"])
        self.bouton_appliquer = BoutonPilule(corps, t, "Appliquer les changements (redémarre le partage)",
                                             self._on_demarrer, style="bleu", taille=11, hauteur=px(30))
        self.mdp_var.trace_add("write", self._sur_modif_reseau)
        self.ssid_var.trace_add("write", self._sur_modif_reseau)
        self.entree_mdp.bind("<Return>", lambda _e: self._on_demarrer())
        self._sur_modif_reseau()

        # -- carte appareils
        carte_a = Carte(c, t, titre="APPAREILS CONNECTÉS")
        carte_a.pack(fill="x", padx=px(16), pady=px((0, 12)))
        self.carte_appareils = carte_a
        self.cadre_appareils = carte_a.corps
        self.label_aucun = tk.Label(self.cadre_appareils, text="Aucun appareil connecté.",
                                    font=(POLICE, 12), bg=t["carte"], fg=t["texte_att"], anchor="w")
        self.label_aucun.pack(fill="x")
        self.lignes_appareils = {}

        # -- demandes de connexion (liste blanche), affichee seulement si besoin
        self.carte_demandes = Carte(c, t, titre="DEMANDES DE CONNEXION")
        self.lignes_demandes = {}

        # -- carte bloques (affichee seulement si besoin)
        self.carte_bloques = Carte(c, t, titre="APPAREILS BLOQUÉS")
        self.lignes_bloques = {}

        self.label_message = tk.Label(c, text="", font=(POLICE, 11), bg=t["fond"],
                                      fg=t["texte_att"], wraplength=px(490), justify="left", anchor="w")
        self.label_message.pack(fill="x", padx=px(22), pady=px((0, 16)))

    def _defiler(self, delta, evenement=None):
        try:
            fen = evenement.widget.winfo_toplevel() if evenement is not None else self
        except (AttributeError, KeyError, tk.TclError):
            fen = self
        cible = getattr(fen, "_defilement", None)
        if cible:
            toile, contenu = cible
            if toile.winfo_exists() and contenu.winfo_reqheight() > toile.winfo_height():
                toile.yview_scroll(delta, "units")
            return
        if fen is not self:
            return
        if self.toile.winfo_exists() and self.contenu.winfo_reqheight() > self.toile.winfo_height():
            self.toile.yview_scroll(delta, "units")

    def _centrer_fenetre(self):
        self.update_idletasks()
        hauteur = min(self.HAUTEUR, self.winfo_screenheight() - 80)
        x = (self.winfo_screenwidth() - self.LARGEUR) // 2
        y = max(20, (self.winfo_screenheight() - hauteur) // 3)
        self.geometry(f"{self.LARGEUR}x{hauteur}+{x}+{y}")

    def _champ(self, parent, libelle, valeur, secret=False):
        t = self.t
        tk.Label(parent, text=libelle, font=(POLICE, 11), bg=t["carte"], fg=t["texte_att"],
                 anchor="w").pack(fill="x", pady=px((6, 3)))
        cadre = tk.Frame(parent, bg=t["champ"], highlightthickness=1,
                         highlightbackground=t["bordure"], highlightcolor=t["bleu"])
        cadre.pack(fill="x")
        variable = tk.StringVar(value=valeur)
        entree = tk.Entry(cadre, textvariable=variable, font=(POLICE, 12), bg=t["champ"],
                          fg=t["texte"], insertbackground=t["texte"], relief="flat",
                          highlightthickness=0, show="•" if secret else "")
        entree.pack(fill="x", padx=px(10), ipady=px(6))
        entree.bind("<FocusIn>", lambda _e: cadre.config(highlightbackground=t["bleu"]))
        entree.bind("<FocusOut>", lambda _e: cadre.config(highlightbackground=t["bordure"]))
        if secret:
            return variable, entree
        return variable

    # -- reseau -----------------------------------------------------------------
    def _basculer_mdp(self):
        visible = self.entree_mdp.cget("show") == ""
        self.entree_mdp.config(show="•" if visible else "")
        self.bouton_voir.configurer(texte="Afficher" if visible else "Masquer")

    def _generer_mdp(self):
        alphabet = string.ascii_letters + string.digits
        self.mdp_var.set("".join(secrets.choice(alphabet) for _ in range(12)))
        self.entree_mdp.config(show="")
        self.bouton_voir.configurer(texte="Masquer")

    def _copier_mdp(self):
        self.clipboard_clear()
        self.clipboard_append(self.mdp_var.get())
        self.bouton_copier.configurer(texte="Copié ✓")
        self.after(1300, lambda: self.bouton_copier.configurer(texte="Copier"))

    def _sur_modif_reseau(self, *_a):
        n = len(self.mdp_var.get())
        t = self.t
        if n == 0:
            self.label_aide_mdp.config(text="8 caractères minimum", fg=t["texte_att"])
        elif n < 8:
            self.label_aide_mdp.config(text=f"Encore {8 - n} caractère(s)", fg=t["rouge"])
        else:
            self.label_aide_mdp.config(text="✓ Mot de passe valide", fg=t["accent"])
        if self._qr_visible:
            self._afficher_qr()
        self._maj_bouton_appliquer()

    def _maj_bouton_appliquer(self):
        en_service = lire_ssid_en_service() if self.etat.get("running") else None
        change = bool(self.etat.get("running")) and (
            self.ssid_var.get().strip() != (en_service or self.config_actuelle["ssid"])
            or self.mdp_var.get() != self.config_actuelle["password"])
        if change and not self.bouton_appliquer.winfo_ismapped():
            self.bouton_appliquer.pack(fill="x", pady=px((12, 0)))
        elif not change and self.bouton_appliquer.winfo_ismapped():
            self.bouton_appliquer.pack_forget()

    def _basculer_qr(self):
        if self._qr_visible:
            self._qr_visible = False
            for e in self.cadre_qr.winfo_children():
                e.destroy()
            self.cadre_qr.pack_forget()
            self.bouton_qr.configurer(texte="QR code")
            return
        if not QR_DISPONIBLE:
            self._message("Le module qrcode n'est pas installé (paquet python3-qrcode).", "rouge")
            return
        self._qr_visible = True
        self.bouton_qr.configurer(texte="Masquer le QR")
        self._afficher_qr()

    def _afficher_qr(self):
        for e in self.cadre_qr.winfo_children():
            e.destroy()
        t = self.t
        ssid, mdp = self.ssid_var.get().strip(), self.mdp_var.get()
        if not ssid or len(mdp) < 8:
            tk.Label(self.cadre_qr, text="Renseigne un nom et un mot de passe valides.",
                     font=(POLICE, 11), bg=t["carte"], fg=t["texte_att"]).pack(pady=px(8))
        else:
            CACHE_DIR.mkdir(parents=True, exist_ok=True)
            chemin = CACHE_DIR / "qr.png"
            contenu = f"WIFI:T:WPA;S:{echapper_wifi(ssid)};P:{echapper_wifi(mdp)};;"
            qrcode.make(contenu, box_size=6, border=2).convert("RGB").save(chemin)
            image = tk.PhotoImage(file=str(chemin))
            lab = tk.Label(self.cadre_qr, image=image, bg=t["carte"])
            lab.image = image
            lab.pack(pady=px((12, 4)))
            tk.Label(self.cadre_qr, text="Scanne ce code avec l'appareil à connecter.",
                     font=(POLICE, 11), bg=t["carte"], fg=t["texte_att"]).pack()
        if not self.cadre_qr.winfo_ismapped():
            self.cadre_qr.pack(fill="x")

    # -- appareils ----------------------------------------------------------------
    def _nom_appareil(self, app):
        mac = app.get("mac", "")
        hote = app.get("hostname")
        if hote in (None, "", "*", "Appareil inconnu"):
            hote = None
        return (self.config_actuelle["surnoms"].get(mac) or hote
                or self._nom_historique(mac) or "Appareil inconnu")

    def _afficher_appareils(self, appareils):
        t = self.t
        if self.config_actuelle.get("liste_blanche"):
            appareils = [a for a in appareils if a.get("mac") not in self._demandes]
        macs = [a["mac"] for a in appareils if a.get("mac")]
        for mac in list(self.lignes_appareils):
            if mac not in macs:
                self.lignes_appareils.pop(mac)["cadre"].destroy()
        if appareils:
            self.label_aucun.pack_forget()
        elif not self.label_aucun.winfo_ismapped():
            self.label_aucun.pack(fill="x")
        for app in appareils:
            mac = app.get("mac")
            if not mac:
                continue
            ligne = self.lignes_appareils.get(mac)
            if ligne is None:
                ligne = self._creer_ligne_appareil(mac)
                self.lignes_appareils[mac] = ligne
            coupe = bool(app.get("coupe"))
            st = self.stations.get(mac, {})
            ligne["nom"].config(text=self._nom_appareil(app))
            details = [app.get("ip", "")]
            limite = self.config_actuelle["limites_debit"].get(mac)
            if limite:
                details.append(f"max {format_kbit(limite)}")
            if st.get("connected_time"):
                details.append(f"connecté depuis {format_duree(int(st['connected_time']))}")
            ligne["details"].config(text="  ·  ".join(d for d in details if d))
            if st.get("tx_bytes"):
                conso = (f"↓ {format_octets(int(st['tx_bytes']))}   "
                         f"↑ {format_octets(int(st.get('rx_bytes', 0)))}")
            else:
                conso = mac
            du_jour = self.compteur.appareil_jour(mac)
            limite = int(self.config_actuelle.get("quota_appareil_mo") or 0) * MO
            if limite:
                conso += f"   ·   aujourd'hui {format_octets(du_jour)} / {format_octets(limite)}"
            elif du_jour:
                conso += f"   ·   aujourd'hui {format_octets(du_jour)}"
            ligne["conso"].config(text=conso,
                                  fg=t["orange"] if limite and du_jour >= 0.8 * limite else t["texte_att"])
            invite = self._keyids.get(mac) == "invite"
            ligne["etat"].config(text="Internet coupé" if coupe else ("Invité" if invite else ""),
                                 fg=t["orange"] if coupe else t["bleu"])
            ligne["bouton_couper"].configurer(texte="Rétablir Internet" if coupe else "Couper Internet",
                                              style="bleu" if coupe else "secondaire")
            ligne["coupe"] = coupe
            self._dessiner_signal(ligne["signal"], barres_signal(st.get("signal")))

    def _creer_ligne_appareil(self, mac):
        t = self.t
        cadre = tk.Frame(self.cadre_appareils, bg=t["carte"])
        cadre.pack(fill="x", pady=px((4, 8)))
        haut = tk.Frame(cadre, bg=t["carte"])
        haut.pack(fill="x")
        signal = tk.Canvas(haut, width=px(22), height=px(18), bg=t["carte"], highlightthickness=0)
        signal.pack(side="left", padx=px((0, 8)))
        nom = tk.Label(haut, text="", font=(POLICE, 12, "bold"), bg=t["carte"], fg=t["texte"],
                       anchor="w")
        nom.pack(side="left")
        nom.bind("<Button-1>", lambda _e: self._renommer(mac))
        etat = tk.Label(haut, text="", font=(POLICE, 11, "bold"), bg=t["carte"], fg=t["orange"])
        etat.pack(side="right")
        details = tk.Label(cadre, text="", font=(POLICE, 11), bg=t["carte"], fg=t["texte_att"], anchor="w")
        details.pack(fill="x", padx=px((30, 0)))
        conso = tk.Label(cadre, text="", font=(POLICE, 11), bg=t["carte"], fg=t["texte_att"], anchor="w")
        conso.pack(fill="x", padx=px((30, 0)))
        boutons = tk.Frame(cadre, bg=t["carte"])
        boutons.pack(fill="x", padx=px((30, 0)), pady=px((6, 0)))
        ligne = {"cadre": cadre, "nom": nom, "details": details, "conso": conso, "etat": etat,
                 "signal": signal, "coupe": False}
        ligne["bouton_couper"] = BoutonPilule(
            boutons, t, "Couper Internet",
            lambda: self._action_appareil("restore" if ligne["coupe"] else "cut", mac),
            taille=11, hauteur=px(26))
        ligne["bouton_couper"].pack(side="left")
        BoutonPilule(boutons, t, "Bloquer",
                     lambda: self._action_appareil("kick", mac), style="danger",
                     taille=11, hauteur=px(26)).pack(side="left", padx=px(6))
        BoutonPilule(boutons, t, "Renommer", lambda: self._renommer(mac),
                     taille=11, hauteur=px(26)).pack(side="left")
        BoutonPilule(boutons, t, "Débit", lambda: self._choisir_debit(mac),
                     taille=11, hauteur=px(26)).pack(side="left", padx=px((6, 0)))
        tk.Frame(cadre, bg=t["bordure"], height=max(1, px(1))).pack(fill="x", pady=px((10, 0)))
        return ligne

    def _dessiner_signal(self, canvas, barres):
        canvas.delete("all")
        t = self.t
        for i in range(4):
            h = px(5 + i * 4)
            x = px(2 + i * 5)
            actif = barres is not None and i < barres
            canvas.create_rectangle(x, px(18) - h, x + px(3), px(18),
                                    fill=t["accent"] if actif else t["eteint"], outline="")

    def _afficher_bloques(self, bloques):
        t = self.t
        for mac in list(self.lignes_bloques):
            if mac not in bloques:
                self.lignes_bloques.pop(mac).destroy()
        for mac in bloques:
            if mac in self.lignes_bloques:
                continue
            ligne = tk.Frame(self.carte_bloques.corps, bg=t["carte"])
            ligne.pack(fill="x", pady=px(3))
            nom = self.config_actuelle["surnoms"].get(mac) or self._nom_historique(mac) or mac
            tk.Label(ligne, text=nom, font=(POLICE, 12), bg=t["carte"], fg=t["texte"],
                     anchor="w").pack(side="left")
            BoutonPilule(ligne, t, "Débloquer", lambda m=mac: self._action_appareil("unblock", m),
                         style="bleu", taille=11, hauteur=px(26)).pack(side="right")
            self.lignes_bloques[mac] = ligne
        if bloques and not self.carte_bloques.winfo_ismapped():
            self.carte_bloques.pack(fill="x", padx=px(16), pady=px((0, 12)), before=self.label_message)
        elif not bloques and self.carte_bloques.winfo_ismapped():
            self.carte_bloques.pack_forget()

    def _nom_historique(self, mac):
        for entree in reversed(charger_historique()):
            if entree.get("mac") == mac and entree.get("hostname") not in (None, "", "Appareil inconnu"):
                return entree["hostname"]
        return None

    def _renommer(self, mac):
        t = self.t
        fen = tk.Toplevel(self, bg=t["fond"])
        fen.title("Renommer l'appareil")
        fen.resizable(False, False)
        fen.transient(self)
        actuel = self.config_actuelle["surnoms"].get(mac, "")
        app = next((a for a in self.etat.get("devices", []) if a.get("mac") == mac), {})
        tk.Label(fen, text="Nom de l'appareil", font=(POLICE, 13, "bold"), bg=t["fond"],
                 fg=t["texte"]).pack(padx=px(20), pady=px((18, 4)), anchor="w")
        tk.Label(fen, text=f"{app.get('hostname', '')}  ·  {mac}", font=(POLICE, 11),
                 bg=t["fond"], fg=t["texte_att"]).pack(padx=px(20), anchor="w")
        var = tk.StringVar(value=actuel or app.get("hostname", ""))
        entree = tk.Entry(fen, textvariable=var, font=(POLICE, 12), bg=t["carte"], fg=t["texte"],
                          insertbackground=t["texte"], relief="flat", highlightthickness=1,
                          highlightbackground=t["bordure"], width=32)
        entree.pack(padx=px(20), pady=px(12), ipady=px(6), fill="x")
        entree.focus_set()
        entree.select_range(0, "end")

        def valider(_e=None):
            nom = var.get().strip()
            if nom and nom != app.get("hostname"):
                self.config_actuelle["surnoms"][mac] = nom
            else:
                self.config_actuelle["surnoms"].pop(mac, None)
            sauvegarder_config(self.config_actuelle)
            fen.destroy()
            self._afficher_appareils(self.etat.get("devices", []))

        entree.bind("<Return>", valider)
        boutons = tk.Frame(fen, bg=t["fond"])
        boutons.pack(fill="x", padx=px(20), pady=px((0, 18)))
        BoutonPilule(boutons, t, "Enregistrer", valider, style="principal", hauteur=px(32),
                     fond_parent=t["fond"]).pack(side="right")
        BoutonPilule(boutons, t, "Annuler", fen.destroy, hauteur=px(32),
                     fond_parent=t["fond"]).pack(side="right", padx=px(8))
        fen.after(100, lambda: _premier_plan(fen))

    def _demander(self, titre, detail="", oui="Oui", non="Annuler", style_oui="principal", parent=None):
        """Confirmation aux couleurs d'Optima (remplace la petite boite Tk en anglais).
        Renvoie True si l'utilisateur choisit le bouton `oui`."""
        t = self.t
        parent = parent or self
        d = tk.Toplevel(parent, bg=t["fond"])
        d.title("Optima")
        d.transient(parent)
        d.resizable(False, False)
        reponse = {"oui": False}

        def fermer(valeur):
            reponse["oui"] = valeur
            d.destroy()

        tk.Label(d, text=titre, font=(POLICE, 15, "bold"), bg=t["fond"], fg=t["texte"],
                 wraplength=px(400), justify="left", anchor="w").pack(fill="x", padx=px(26), pady=px((24, 8)))
        if detail:
            tk.Label(d, text=detail, font=(POLICE, 12), bg=t["fond"], fg=t["texte_att"],
                     wraplength=px(400), justify="left", anchor="w").pack(fill="x", padx=px(26))
        bas = tk.Frame(d, bg=t["fond"])
        bas.pack(fill="x", padx=px(26), pady=px((22, 22)))
        BoutonPilule(bas, t, oui, lambda: fermer(True), style=style_oui, taille=12, hauteur=px(40),
                     fond_parent=t["fond"]).pack(side="right")
        BoutonPilule(bas, t, non, lambda: fermer(False), taille=12, hauteur=px(40),
                     fond_parent=t["fond"]).pack(side="right", padx=px(10))
        d.bind("<Return>", lambda _e: fermer(True))
        d.bind("<Escape>", lambda _e: fermer(False))
        d.protocol("WM_DELETE_WINDOW", lambda: fermer(False))
        d.update_idletasks()
        largeur, hauteur = px(460), d.winfo_reqheight()
        try:
            x = parent.winfo_rootx() + (parent.winfo_width() - largeur) // 2
            y = parent.winfo_rooty() + max(px(40), (parent.winfo_height() - hauteur) // 3)
            taille = f"{largeur}x{hauteur}+{max(0, x)}+{max(0, y)}"
        except tk.TclError:
            taille = f"{largeur}x{hauteur}"
        d.geometry(taille)

        def afficher():
            _premier_plan(d)
            d.geometry(taille)
            try:
                d.grab_set()
            except tk.TclError:
                pass

        d.after(100, afficher)
        d.wait_window()
        return reponse["oui"]

    def _action_appareil(self, action, mac):
        app = next((a for a in self.etat.get("devices", []) if a.get("mac") == mac), {"mac": mac})
        nom = self._nom_appareil(app)
        if action == "kick" and not self._demander(
                f"Déconnecter « {nom} » ?",
                "Cet appareil sera déconnecté et ne pourra plus se reconnecter à ton partage. "
                "Tu pourras le débloquer plus tard depuis la liste des appareils bloqués.",
                oui="Déconnecter et bloquer", style_oui="danger"):
            return
        textes = {
            "cut": (f"Coupure d'Internet pour {nom}…", f"Internet coupé pour {nom}."),
            "restore": (f"Rétablissement d'Internet pour {nom}…", f"Internet rétabli pour {nom}."),
            "kick": (f"Déconnexion de {nom}…", f"{nom} est déconnecté et bloqué."),
            "unblock": (f"Déblocage…", f"{nom} peut à nouveau se connecter."),
        }
        attente, succes = textes[action]
        self._executer(lambda: executer_privilegie(action, mac), attente, succes)

    # -- actions principales -----------------------------------------------------------
    def _basculer(self):
        if self.etat.get("running"):
            n = len(self.etat.get("devices") or [])
            if n and self.config_actuelle.get("confirmer_arret", True):
                _premier_plan(self)
                if not self._demander(
                        "Arrêter le partage ?",
                        f"{n} appareil{'s sont encore connectés' if n > 1 else ' est encore connecté'} : "
                        f"{'ils perdront' if n > 1 else 'il perdra'} Internet tout de suite.",
                        oui="Arrêter le partage", style_oui="danger"):
                    return
            self._on_arreter()
        else:
            depassee = self._limite_depassee()
            if depassee and not self._demander(
                    f"La limite {depassee} est atteinte",
                    "Tu as demandé à Optima de couper le partage une fois la limite atteinte. "
                    "Partager quand même ?", oui="Partager quand même"):
                return
            self._on_demarrer()

    def _on_demarrer(self):
        ssid = self.ssid_var.get().strip()
        mdp = self.mdp_var.get()
        if not ssid:
            self._message("Le nom du réseau ne peut pas être vide.", "rouge")
            return
        if len(mdp) < 8:
            self._message("Le mot de passe doit faire au moins 8 caractères.", "rouge")
            return
        self.config_actuelle["ssid"] = ssid
        self.config_actuelle["password"] = mdp
        sauvegarder_config(self.config_actuelle)
        args = self._args_demarrage(ssid, mdp)
        self._executer(lambda: executer_privilegie(*args),
                       "Démarrage du point d'accès…", "Point d'accès démarré.", notifier=True)

    def _args_demarrage(self, ssid, mdp):
        c = self.config_actuelle
        return ("start", ssid, mdp, str(c.get("max_clients", 20)), str(c.get("bandwidth_mbps", 0)),
                "1" if c.get("reseau_masque") else "0", "1" if c.get("isoler_appareils") else "0",
                c.get("dns", "auto") if c.get("dns") in ("auto", "cloudflare", "google", "quad9") else "auto",
                ",".join(f"{m}={k}" for m, k in c.get("limites_debit", {}).items() if int(k) > 0))

    def _on_arreter(self, auto=False, raison=None):
        self.arret_programme = None
        self.duree_arret = 0
        self.bouton_arret_prog.configurer(texte="Jamais")
        self._executer(lambda: executer_privilegie("stop"), "Arrêt du point d'accès…",
                       raison or ("Partage arrêté automatiquement." if auto else "Partage arrêté."),
                       notifier=True)

    def _demarrage_auto(self):
        ssid = self.config_actuelle.get("ssid", "").strip()
        mdp = self.config_actuelle.get("password", "")
        if not ssid or len(mdp) < 8:
            return
        if self._limite_depassee():
            self._message("Démarrage automatique annulé : la limite de données est atteinte.", "orange")
            return
        args = self._args_demarrage(ssid, mdp)

        def action():
            ok, sortie = False, ""
            for _ in range(24):
                ok, sortie = executer_privilegie(*args)
                if ok or "aucune connexion wi-fi active" not in sortie.lower():
                    break
                time.sleep(5)
            return ok, sortie

        self._executer(action, "Démarrage automatique…", "Point d'accès démarré automatiquement.",
                       notifier=True)

    def _executer(self, action, attente, succes, notifier=False):
        if self._occupe:
            return
        self._occupe = True
        self.bouton_marche.definir("attente")
        self._message(attente, "texte_att")

        def tache():
            ok, sortie = action()
            self._en_main(lambda: self._sur_resultat(ok, sortie, succes, notifier))

        threading.Thread(target=tache, daemon=True).start()

    def _sur_resultat(self, ok, sortie, succes, notifier):
        self._occupe = False
        if ok:
            self._message(f"✓ {succes}", "accent")
            if notifier:
                envoyer_notification("Optima", succes)
        else:
            texte = message_convivial(sortie)
            self._message(f"⚠ {texte}", "rouge")
            if notifier:
                envoyer_notification("Optima — erreur", texte)
        self._recuperer_statut()

    def _message(self, texte, couleur="texte_att"):
        self.label_message.config(text=texte, fg=self.t.get(couleur, couleur))

    # -- arret programme ----------------------------------------------------------
    def _cycle_arret(self):
        i = ARRETS_PROGRAMMES.index(self.duree_arret) if self.duree_arret in ARRETS_PROGRAMMES else 0
        self.duree_arret = ARRETS_PROGRAMMES[(i + 1) % len(ARRETS_PROGRAMMES)]
        if self.duree_arret == 0:
            self.arret_programme = None
            self.bouton_arret_prog.configurer(texte="Jamais")
            self._message("Arrêt automatique désactivé.")
        else:
            self.arret_programme = time.time() + self.duree_arret * 60
            self._maj_texte_arret()
            self._message("Le partage s'arrêtera tout seul à la fin du délai "
                          "(ton mot de passe sera demandé à ce moment-là).")

    def _maj_texte_arret(self):
        if self.arret_programme:
            reste = self.arret_programme - time.time()
            self.bouton_arret_prog.configurer(texte=f"dans {format_duree(reste)}")

    # -- boucles de mise a jour ---------------------------------------------------------
    def _boucle_rapide(self):
        """Chaque seconde : debit, graphique, duree, arret programme (lectures sans root)."""
        try:
            maintenant = time.time()
            compteurs = lire_compteurs_ap() if self.etat.get("running") else None
            if compteurs and self._compteurs_prec:
                (bas, haut), (t0, b0, h0) = compteurs, self._compteurs_prec
                dt = max(0.2, maintenant - t0)
                vb, vh = max(0, bas - b0) / dt, max(0, haut - h0) / dt
                self.graphe.ajouter(vb, vh)
                self.stat_vals["vitesse"].config(text=f"↓ {format_debit(vb)}\n↑ {format_debit(vh)}")
            elif not compteurs:
                self.graphe.ajouter(0, 0)
                self.stat_vals["vitesse"].config(text="—")
            if compteurs:
                self.compteur.ajouter_total(compteurs, debut_du_partage())
            self._maj_conso()
            self._verifier_quotas()
            self._verifier_planning()
            self.compteur.sauvegarder()
            if compteurs:
                self._compteurs_prec = (maintenant, *compteurs)
                self.stat_vals["session"].config(
                    text=f"↓ {format_octets(compteurs[0])}\n↑ {format_octets(compteurs[1])}")
            else:
                self._compteurs_prec = None
                self.stat_vals["session"].config(text="—")
            if self.etat.get("running") and self._session_depart:
                self.label_sous_etat.config(
                    text=f"« {lire_ssid_en_service() or self.config_actuelle['ssid']} » · "
                         f"depuis {format_duree(maintenant - self._session_depart)}")
            delai = int(self.config_actuelle.get("arret_inactif_min", 0) or 0)
            if (delai > 0 and self._sans_appareil_depuis and self.etat.get("running")
                    and not self._occupe and maintenant - self._sans_appareil_depuis >= delai * 60):
                self._sans_appareil_depuis = None
                self._on_arreter(auto=True, raison=f"Aucun appareil depuis {delai} min : partage arrêté.")
            if self.arret_programme:
                if maintenant >= self.arret_programme and self.etat.get("running") and not self._occupe:
                    self._on_arreter(auto=True)
                else:
                    self._maj_texte_arret()
        except tk.TclError:
            return
        self.after(1000, self._boucle_rapide)

    def _boucle_statut(self):
        self._recuperer_statut()
        self.after(3000, self._boucle_statut)

    def _recuperer_statut(self):
        def tache():
            etat = lire_statut()
            stations = lire_stations() if etat.get("running") else {}
            for mac, st in stations.items():
                if st.get("keyid"):
                    self._keyids[mac] = st["keyid"]
            source = nom_reseau_source(etat.get("uplink")) if etat.get("uplink") not in (None, "null") else None
            # Theme "Auto" : suit en direct le mode clair/sombre de GNOME
            mode = theme_systeme() if self.config_actuelle.get("theme", "auto") not in THEMES else None
            self._en_main(lambda: self._appliquer_statut(etat, stations, source, mode))

        threading.Thread(target=tache, daemon=True).start()

    def _appliquer_statut(self, etat, stations, source, mode=None):
        if mode in THEMES and THEMES[mode] is not self.t and not self._occupe:
            self._reconstruire()   # relit le statut juste apres, avec les nouvelles couleurs
            return
        t = self.t
        self.etat, self.stations = etat, stations
        actif = bool(etat.get("running"))
        if actif and stations:
            self.compteur.ajouter_stations(stations, etat.get("devices") or [])
        if not self._occupe:
            self.bouton_marche.definir("marche" if actif else "arret")
        appareils = etat.get("devices") or []
        if actif and not appareils:
            if self._sans_appareil_depuis is None:
                self._sans_appareil_depuis = time.time()
        else:
            self._sans_appareil_depuis = None
        if actif:
            self._session_depart = debut_du_partage()
            self.label_etat.config(text="Partage actif", fg=t["accent"])
            if source:
                self.label_source.config(text=f"Connexion partagée : {source}")
            n = len(appareils)
            self.stat_vals["appareils"].config(text=f"{n} connecté{'s' if n > 1 else ''}")
        else:
            self._session_depart = None
            self.label_etat.config(text="Partage arrêté", fg=t["texte"])
            self.label_sous_etat.config(text="Appuie sur le bouton pour partager ta connexion.")
            self.label_source.config(text=f"Réseau disponible : {source}" if source else
                                     "Aucune connexion Wi-Fi à partager pour l'instant.")
            self.stat_vals["appareils"].config(text="—")
        self._traiter_diff_appareils(appareils)
        self._afficher_appareils(appareils)
        if actif:
            self._verifier_quotas_appareils(appareils)
            self._suivre_invite(stations)
            self._suivre_limites(appareils)
        self._maj_label_invite()
        self._afficher_bloques([m.lower() for m in etat.get("blocked") or []])
        self._maj_bouton_appliquer()
        if TRAY_DISPONIBLE and self.icone_tray and time.time() - getattr(self, "_tray_maj", 0) > 30:
            self._tray_maj = time.time()
            try:
                self.icone_tray.update_menu()   # rafraichit « Aujourd'hui : … »
            except Exception:
                pass
        if TRAY_DISPONIBLE and self.icone_tray and getattr(self, "_tray_actif", None) != actif:
            self._tray_actif = actif
            self.icone_tray.icon = image_icone_tray(actif)
            self.icone_tray.title = f"Optima — {'actif' if actif else 'arrêté'}"

    def _traiter_diff_appareils(self, appareils):
        actuels = {a["mac"]: a for a in appareils if a.get("mac")}
        if self.appareils_precedents is None:
            self.appareils_precedents = actuels
            return
        for mac in set(actuels) - set(self.appareils_precedents):
            self._journaliser_evenement("connecte", actuels[mac])
        for mac in set(self.appareils_precedents) - set(actuels):
            self._journaliser_evenement("deconnecte", self.appareils_precedents[mac])
        self.appareils_precedents = actuels

    def _journaliser_evenement(self, evenement, appareil):
        entree = {
            "heure": datetime.now().strftime("%d/%m %H:%M:%S"),
            "evenement": evenement,
            "hostname": appareil.get("hostname", "Appareil inconnu"),
            "ip": appareil.get("ip", ""),
            "mac": appareil.get("mac", ""),
        }
        ajouter_historique(entree)
        if self.config_actuelle.get("notifier_appareils", True):
            verbe = "s'est connecté" if evenement == "connecte" else "s'est déconnecté"
            envoyer_notification("Optima", f"{self._nom_appareil(appareil)} {verbe}")

    # -- donnees et quotas (26/11/2026) -----------------------------------------------
    def _maj_conso(self):
        c = self.config_actuelle
        for cle, total, limite in (
                ("jour", self.compteur.total_jour(), int(c.get("quota_jour_mo") or 0) * MO),
                ("mois", self.compteur.total_mois(), int(c.get("quota_mois_go") or 0) * GO)):
            ligne = self.lignes_conso[cle]
            texte = format_octets(total)
            if limite:
                texte += f" / {format_octets(limite)}"
                if not ligne["jauge"].winfo_ismapped():
                    ligne["jauge"].pack(fill="x", pady=px((4, 0)))
                ligne["jauge"].definir(total / limite)
            elif ligne["jauge"].winfo_ismapped():
                ligne["jauge"].pack_forget()
            couleur = (self.t["rouge"] if limite and total >= limite else
                       self.t["orange"] if limite and total >= 0.8 * limite else self.t["texte"])
            ligne["valeur"].config(text=texte, fg=couleur)

    def _limite_depassee(self):
        """Nom de la limite atteinte (« du jour », « du mois ») si le mode bloquer est actif."""
        c = self.config_actuelle
        if c.get("quota_action") != "bloquer":
            return None
        jour, mois = int(c.get("quota_jour_mo") or 0) * MO, int(c.get("quota_mois_go") or 0) * GO
        if jour and self.compteur.total_jour() >= jour:
            return "du jour"
        if mois and self.compteur.total_mois() >= mois:
            return "du mois"
        return None

    def _verifier_quotas(self):
        c, cp = self.config_actuelle, self.compteur
        bloquer = c.get("quota_action") == "bloquer"
        aujourdhui = date.today().isoformat()
        for portee, periode, limite, total, libelle in (
                ("jour", aujourdhui, int(c.get("quota_jour_mo") or 0) * MO, cp.total_jour(), "du jour"),
                ("mois", aujourdhui[:7], int(c.get("quota_mois_go") or 0) * GO, cp.total_mois(), "du mois")):
            if limite <= 0:
                continue
            cle = f"{portee}|{periode}"
            if total >= limite and not cp.alerte_deja(cle + "|100"):
                if bloquer and self.etat.get("running"):
                    if self._occupe:
                        continue   # on reessaiera a la seconde suivante
                    cp.noter_alerte(cle + "|80")
                    cp.noter_alerte(cle + "|100")
                    self._on_arreter(auto=True, raison=f"Limite {libelle} atteinte "
                                                       f"({format_octets(limite)}) : partage arrêté.")
                    return
                cp.noter_alerte(cle + "|80")
                cp.noter_alerte(cle + "|100")
                envoyer_notification("Optima — limite atteinte",
                                     f"Limite {libelle} atteinte : {format_octets(total)} "
                                     f"sur {format_octets(limite)}.")
            elif total >= 0.8 * limite and not cp.alerte_deja(cle + "|80"):
                cp.noter_alerte(cle + "|80")
                envoyer_notification("Optima", f"80 % de la limite {libelle} utilisés "
                                               f"({format_octets(total)} sur {format_octets(limite)}).")

    def _verifier_quotas_appareils(self, appareils):
        c, cp = self.config_actuelle, self.compteur
        limite = int(c.get("quota_appareil_mo") or 0) * MO
        if limite <= 0:
            return
        aujourdhui = date.today().isoformat()
        for app in appareils:
            mac = app.get("mac")
            if not mac or cp.appareil_jour(mac) < limite:
                continue
            cle = f"app|{aujourdhui}|{mac}"
            if cp.alerte_deja(cle):
                continue
            nom = self._nom_appareil(app)
            if c.get("quota_action") == "bloquer" and not app.get("coupe"):
                if self._occupe:
                    continue
                cp.noter_alerte(cle)
                self._executer(lambda m=mac: executer_privilegie("cut", m),
                               f"{nom} a atteint sa limite : coupure d'Internet…",
                               f"{nom} a atteint sa limite du jour ({format_octets(limite)}) : "
                               f"Internet coupé pour cet appareil.", notifier=True)
                return
            cp.noter_alerte(cle)
            envoyer_notification("Optima — limite d'un appareil",
                                 f"{nom} a utilisé {format_octets(cp.appareil_jour(mac))} aujourd'hui "
                                 f"(limite {format_octets(limite)}).")

    # -- plages horaires (26/11/2026) ---------------------------------------------------
    def _verifier_planning(self):
        c = self.config_actuelle
        if not c.get("planning_actif"):
            self._plage_prec = None
            if self.label_planning.winfo_ismapped():
                self.label_planning.pack_forget()
            return
        dedans = dans_plage(c)
        cle_minute = (datetime.now().strftime("%Y%m%d%H%M"), c.get("planning_debut"),
                      c.get("planning_fin"), tuple(c.get("planning_jours") or ()))
        if self._cache_planning[0] != cle_minute:
            self._cache_planning = (cle_minute, prochain_changement(c))
            prochain = self._cache_planning[1]
            if prochain:
                t_, demarre = prochain
                texte = (f"Plage horaire : démarrage {format_moment(t_)}" if demarre
                         else f"Plage horaire : arrêt {format_moment(t_)}")
            else:
                texte = "Plage horaire : heures ou jours à vérifier dans les réglages."
            self.label_planning.config(text=texte)
            if not self.label_planning.winfo_ismapped():
                self.label_planning.pack(fill="x", pady=px((6, 0)))
        if dedans is None:
            return
        if self._plage_prec is None:
            self._plage_prec = dedans   # pas d'action a l'ouverture : seulement aux changements
            return
        if dedans == self._plage_prec or self._occupe:
            return
        self._plage_prec = dedans
        if dedans and not self.etat.get("running"):
            self._demarrage_auto()
        elif not dedans and self.etat.get("running"):
            self._on_arreter(auto=True, raison="Fin de la plage horaire : partage arrêté.")

    # -- qualite de la connexion source (26/11/2026) ---------------------------------------
    def _boucle_qualite(self):
        try:
            uplink = self.etat.get("uplink")
            if not self.config_actuelle.get("surveiller_connexion", True):
                self._pings.clear()
                self._niveau_qualite = "off"
                self._afficher_qualite()
            elif uplink in (None, "", "null"):
                self._pings.clear()
                self._niveau_qualite = "aucune"
                self._afficher_qualite()
            elif not self._qualite_en_cours:
                self._qualite_en_cours = True
                maintenant = time.time()
                delai = 15 if self._connectivite in ("portal", "none", "limited") else 60
                verifier = maintenant - self._derniere_verif > delai

                def tache():
                    ms = mesurer_ping(uplink)
                    if ms is None:
                        ms = mesurer_ping(uplink, "8.8.8.8")
                    conn = None
                    if verifier or (ms is None and time.time() - self._derniere_verif > 15):
                        self._derniere_verif = time.time()
                        conn = verifier_connectivite()
                    self._en_main(lambda: self._appliquer_qualite(ms, conn))

                threading.Thread(target=tache, daemon=True).start()
            self.after(5000, self._boucle_qualite)
        except tk.TclError:
            pass

    def _appliquer_qualite(self, ms, conn):
        self._qualite_en_cours = False
        self._pings.append(ms)
        if conn in ("full", "portal", "limited", "none"):
            self._connectivite = conn
        elif ms is not None and self._connectivite in ("none", "limited"):
            self._connectivite = "full"
        mesures = list(self._pings)
        reussies = [x for x in mesures if x is not None]
        ancien = self._niveau_qualite
        if self._connectivite == "portal":
            niveau, texte = "portail", "Le Wi-Fi source redemande la connexion (portail captif) : les appareils n'ont plus Internet."
        elif len(mesures) >= 3 and all(x is None for x in mesures[-3:]):
            niveau, texte = "coupe", "Internet ne répond plus sur le réseau source."
        elif not reussies:
            niveau, texte = "mesure", "Mesure de la connexion…"
        else:
            perte = round(100 * (len(mesures) - len(reussies)) / len(mesures))
            moyenne = sum(reussies[-6:]) / len(reussies[-6:])
            if moyenne < 80 and perte < 5:
                niveau, mot = "bonne", "excellente"
            elif moyenne < 200 and perte < 20:
                niveau, mot = "moyenne", "correcte"
            else:
                niveau, mot = "faible", "faible"
            texte = f"Connexion source {mot} · {moyenne:.0f} ms · {perte} % de perte"
        self._niveau_qualite, self._texte_qualite = niveau, texte
        self._afficher_qualite()
        if self.etat.get("running") and ancien not in (None, "mesure", "off", "aucune") and niveau != ancien:
            if niveau == "portail":
                envoyer_notification("Optima — reconnexion nécessaire",
                                     "Le Wi-Fi source demande de se reconnecter (portail captif) : "
                                     "les appareils n'ont plus Internet.")
            elif niveau == "coupe":
                envoyer_notification("Optima — Internet coupé",
                                     "Internet ne répond plus sur le réseau source.")
            elif ancien in ("portail", "coupe") and niveau in ("bonne", "moyenne", "faible"):
                envoyer_notification("Optima", "Internet est revenu sur le réseau source.")

    def _afficher_qualite(self):
        t = self.t
        niveau = self._niveau_qualite
        couleurs = {"bonne": t["accent"], "moyenne": t["orange"], "faible": t["rouge"],
                    "coupe": t["rouge"], "portail": t["orange"]}
        if niveau in ("off", "aucune"):
            self.point_qualite.master.pack_forget()
        else:
            if not self.point_qualite.master.winfo_ismapped():
                self.point_qualite.master.pack(fill="x", pady=px((2, 0)))
            self.point_qualite.config(fg=couleurs.get(niveau, t["eteint"]))
            self.label_qualite.config(text=self._texte_qualite or "Mesure de la connexion…",
                                      fg=t["texte"] if niveau in ("coupe", "portail") else t["texte_att"])
        if niveau == "portail" and not self.bouton_portail.winfo_ismapped():
            self.bouton_portail.pack(anchor="w", pady=px((6, 0)))
        elif niveau != "portail" and self.bouton_portail.winfo_ismapped():
            self.bouton_portail.pack_forget()

    def _ouvrir_portail(self):
        # page en http simple : le portail captif l'intercepte et affiche sa page de connexion
        try:
            subprocess.Popen(["xdg-open", "http://neverssl.com"], stdout=subprocess.DEVNULL,
                             stderr=subprocess.DEVNULL)
            self._derniere_verif = 0.0   # reverifier vite apres la reconnexion
        except FileNotFoundError:
            self._message("Ouvre un navigateur sur http://neverssl.com pour te reconnecter.", "orange")

    # -- liste blanche : demandes de connexion (26/11/2026) ---------------------------------
    def _boucle_garde(self):
        """Toutes les 1,5 s si la liste blanche est active : un appareil inconnu qui
        s'associe est aussitot refuse (deny_acl + deauth, sans root) et apparait dans
        « Demandes de connexion »."""
        try:
            if (self.config_actuelle.get("liste_blanche") and self.etat.get("running")
                    and not self._garde_en_cours):
                self._garde_en_cours = True
                autorises = {m.lower() for m in self.config_actuelle.get("appareils_autorises", [])}

                def tache():
                    refuses = []
                    for mac in lister_stations_rapide() or []:
                        if mac in autorises:
                            continue
                        if keyid_station(mac) == "invite":
                            self._keyids[mac] = "invite"
                            continue
                        hostapd_cmd("deny_acl", "ADD_MAC", mac)
                        hostapd_cmd("deauthenticate", mac)
                        refuses.append(mac)
                    self._en_main(lambda: self._apres_garde(refuses))

                threading.Thread(target=tache, daemon=True).start()
            self.after(1500, self._boucle_garde)
        except tk.TclError:
            pass

    def _apres_garde(self, refuses):
        self._garde_en_cours = False
        for mac in refuses:
            nouveau = mac not in self._demandes
            self._demandes[mac] = time.time()
            if nouveau and mac not in self._refuses:
                self._refuses.add(mac)
                nom = self._nom_appareil({"mac": mac})
                ajouter_historique({"heure": datetime.now().strftime("%d/%m %H:%M:%S"),
                                    "evenement": "refuse", "hostname": nom, "ip": "", "mac": mac})
                envoyer_notification("Optima — appareil refusé",
                                     f"{nom} a essayé de se connecter. Ouvre Optima pour l'autoriser.")
        if refuses:
            self._afficher_demandes()

    def _afficher_demandes(self):
        t = self.t
        for mac in list(self.lignes_demandes):
            if mac not in self._demandes:
                self.lignes_demandes.pop(mac).destroy()
        for mac, quand in sorted(self._demandes.items(), key=lambda e: -e[1]):
            if mac in self.lignes_demandes:
                continue
            ligne = tk.Frame(self.carte_demandes.corps, bg=t["carte"])
            ligne.pack(fill="x", pady=px(4))
            bloc = tk.Frame(ligne, bg=t["carte"])
            bloc.pack(side="left", fill="x", expand=True)
            nom = self._nom_appareil({"mac": mac})
            tk.Label(bloc, text=nom, font=(POLICE, 12, "bold"), bg=t["carte"], fg=t["texte"],
                     anchor="w").pack(fill="x")
            tk.Label(bloc, text=f"{mac}  ·  refusé à {datetime.fromtimestamp(quand):%H:%M}",
                     font=(POLICE, 10), bg=t["carte"], fg=t["texte_att"], anchor="w").pack(fill="x")
            BoutonPilule(ligne, t, "Ignorer", lambda m=mac: self._ignorer_demande(m), taille=11,
                         hauteur=px(28)).pack(side="right")
            BoutonPilule(ligne, t, "Autoriser", lambda m=mac: self._autoriser(m), style="principal",
                         taille=11, hauteur=px(28)).pack(side="right", padx=px((0, 6)))
            self.lignes_demandes[mac] = ligne
        if self._demandes and not self.carte_demandes.winfo_ismapped():
            self.carte_demandes.pack(fill="x", padx=px(16), pady=px((0, 12)), before=self.carte_appareils)
        elif not self._demandes and self.carte_demandes.winfo_ismapped():
            self.carte_demandes.pack_forget()

    def _autoriser(self, mac):
        liste = self.config_actuelle.setdefault("appareils_autorises", [])
        if mac not in liste:
            liste.append(mac)
        sauvegarder_config(self.config_actuelle)
        self._demandes.pop(mac, None)
        self._refuses.discard(mac)
        threading.Thread(target=lambda: hostapd_cmd("deny_acl", "DEL_MAC", mac), daemon=True).start()
        self._afficher_demandes()
        self._message(f"✓ {self._nom_appareil({'mac': mac})} est autorisé : il peut se reconnecter "
                      "(reconnecte-le à la main s'il ne le fait pas tout seul).", "accent")

    def _ignorer_demande(self, mac):
        # reste refuse (deny_acl) mais disparait de la liste
        self._demandes.pop(mac, None)
        self._afficher_demandes()

    def _activer_liste_blanche(self):
        """A l'activation : les appareils deja connectes et ceux qui ont un surnom sont autorises."""
        c = self.config_actuelle
        liste = c.setdefault("appareils_autorises", [])
        for mac in [a.get("mac") for a in self.etat.get("devices") or []] + list(c["surnoms"]):
            if mac and mac not in liste:
                liste.append(mac)

    def _gerer_autorises(self, parent):
        t = self.t
        fen = tk.Toplevel(parent, bg=t["fond"])
        fen.title("Appareils autorisés")
        fen.transient(parent)
        tk.Label(fen, text="Appareils autorisés", font=(POLICE, 15, "bold"), bg=t["fond"],
                 fg=t["texte"]).pack(anchor="w", padx=px(20), pady=px((18, 4)))
        tk.Label(fen, text="Quand la liste est active, les autres appareils sont refusés et "
                           "apparaissent dans « Demandes de connexion ».",
                 font=(POLICE, 11), bg=t["fond"], fg=t["texte_att"], wraplength=px(400),
                 justify="left").pack(anchor="w", padx=px(20))
        carte = Carte(fen, t)
        carte.pack(fill="both", padx=px(16), pady=px((10, 16)))

        def remplir():
            for e in carte.corps.winfo_children():
                e.destroy()
            liste = self.config_actuelle.get("appareils_autorises", [])
            if not liste:
                tk.Label(carte.corps, text="Aucun appareil pour l'instant.", font=(POLICE, 12),
                         bg=t["carte"], fg=t["texte_att"]).pack(anchor="w")
            for mac in liste:
                l = tk.Frame(carte.corps, bg=t["carte"])
                l.pack(fill="x", pady=px(3))
                tk.Label(l, text=self._nom_appareil({"mac": mac}), font=(POLICE, 12), bg=t["carte"],
                         fg=t["texte"], anchor="w").pack(side="left")
                BoutonPilule(l, t, "Retirer", lambda m=mac: retirer(m), style="danger", taille=11,
                             hauteur=px(26)).pack(side="right")

        def retirer(mac):
            self.config_actuelle["appareils_autorises"].remove(mac)
            sauvegarder_config(self.config_actuelle)
            remplir()

        remplir()
        fen.update_idletasks()
        taille = f"{px(460)}x{min(px(600), fen.winfo_reqheight() + px(20))}"
        fen.geometry(taille)
        fen.after(100, lambda: (_premier_plan(fen), fen.geometry(taille)))

    # -- acces invite (26/11/2026) -------------------------------------------------------
    def _invite_active(self):
        inv = self.config_actuelle.get("invite")
        return inv if inv and inv.get("expire", 0) > time.time() else None

    def _maj_label_invite(self):
        inv = self._invite_active()
        if inv:
            n = len(inv.get("macs", []))
            texte = f"Actif · expire dans {format_duree(inv['expire'] - time.time())}"
            if n:
                texte += f" · {n} invité{'s' if n > 1 else ''}"
            self.label_invite.config(text=texte, fg=self.t["bleu"])
        else:
            self.label_invite.config(text="Mot de passe temporaire pour tes visiteurs.",
                                     fg=self.t["texte_att"])

    def _suivre_invite(self, stations):
        inv = self.config_actuelle.get("invite")
        if not inv:
            return
        if inv.get("expire", 0) <= time.time():
            self._revoquer_invite(raison="expire")
            return
        for mac, st in stations.items():
            if st.get("keyid") == "invite" and mac not in inv.setdefault("macs", []):
                inv["macs"].append(mac)
                sauvegarder_config(self.config_actuelle)
        if invites_disponibles():
            threading.Thread(target=lambda: ecrire_invites(inv["mdp"]), daemon=True).start()

    def _creer_invite(self, heures):
        if not invites_disponibles():
            return False
        inv = self._invite_active()
        mdp = inv["mdp"] if inv else generer_mdp_invite()
        if not ecrire_invites(mdp):
            return False
        maintenant = time.time()
        self.config_actuelle["invite"] = {
            "mdp": mdp, "debut": inv["debut"] if inv else maintenant,
            "expire": (inv["expire"] if inv else maintenant) + heures * 3600,
            "macs": inv.get("macs", []) if inv else []}
        sauvegarder_config(self.config_actuelle)
        self._maj_label_invite()
        return True

    def _revoquer_invite(self, raison="manuel"):
        inv = self.config_actuelle.get("invite") or {}
        macs = set(inv.get("macs", [])) | {m for m, k in self._keyids.items() if k == "invite"}
        self.config_actuelle["invite"] = None
        sauvegarder_config(self.config_actuelle)

        def tache():
            ecrire_invites(None)
            for mac in macs:
                hostapd_cmd("deauthenticate", mac)

        threading.Thread(target=tache, daemon=True).start()
        self._maj_label_invite()
        texte = "Accès invité expiré" if raison == "expire" else "Accès invité révoqué"
        if macs:
            texte += f" : {len(macs)} invité{'s' if len(macs) > 1 else ''} déconnecté{'s' if len(macs) > 1 else ''}"
        self._message(f"✓ {texte}.", "accent")
        if raison == "expire":
            envoyer_notification("Optima", texte + ".")

    def _ouvrir_invite(self):
        t = self.t
        ancienne = getattr(self, "_fen_invite", None)
        if ancienne is not None and ancienne.winfo_exists():
            _premier_plan(ancienne)
            return
        fen = tk.Toplevel(self, bg=t["fond"])
        self._fen_invite = fen
        fen.title("Accès invité")
        fen.transient(self)
        corps = tk.Frame(fen, bg=t["fond"])
        corps.pack(fill="both", expand=True)

        def construire():
            for e in corps.winfo_children():
                e.destroy()
            tk.Label(corps, text="Accès invité", font=(POLICE, 16, "bold"), bg=t["fond"],
                     fg=t["texte"]).pack(anchor="w", padx=px(20), pady=px((18, 4)))
            tk.Label(corps, text="Un second mot de passe pour le même réseau, qui s'arrête tout seul. "
                                 "À la fin, les invités sont déconnectés ; ton mot de passe ne change pas.",
                     font=(POLICE, 11), bg=t["fond"], fg=t["texte_att"], wraplength=px(420),
                     justify="left").pack(anchor="w", padx=px(20))
            carte = Carte(corps, t)
            carte.pack(fill="x", padx=px(16), pady=px((12, 16)))
            c = carte.corps
            inv = self._invite_active()
            if not self.etat.get("running"):
                tk.Label(c, text="Démarre d'abord le partage.", font=(POLICE, 12), bg=t["carte"],
                         fg=t["texte_att"]).pack(anchor="w")
                return
            if not invites_disponibles():
                tk.Label(c, text="Disponible au prochain démarrage du partage", font=(POLICE, 12, "bold"),
                         bg=t["carte"], fg=t["texte"], anchor="w").pack(fill="x")
                tk.Label(c, text="Le partage en cours a été lancé avant cette nouveauté. Arrête-le puis "
                                 "relance-le quand ça t'arrange : l'accès invité sera prêt.",
                         font=(POLICE, 11), bg=t["carte"], fg=t["texte_att"], wraplength=px(400),
                         justify="left", anchor="w").pack(fill="x", pady=px((4, 0)))
                return
            if not inv:
                tk.Label(c, text="Durée", font=(POLICE, 12), bg=t["carte"], fg=t["texte"]).pack(anchor="w")
                rangee = tk.Frame(c, bg=t["carte"])
                rangee.pack(fill="x", pady=px((8, 0)))
                for h, lib in ((1, "1 heure"), (3, "3 heures"), (24, "24 heures")):
                    BoutonPilule(rangee, t, lib, lambda h=h: creer(h), style="bleu", taille=11,
                                 hauteur=px(32)).pack(side="left", padx=px((0, 6)))
                return
            tk.Label(c, text="Réseau", font=(POLICE, 10), bg=t["carte"], fg=t["texte_att"]).pack(anchor="w")
            tk.Label(c, text=lire_ssid_en_service() or self.config_actuelle["ssid"], font=(POLICE, 13),
                     bg=t["carte"], fg=t["texte"]).pack(anchor="w")
            tk.Label(c, text="Mot de passe invité", font=(POLICE, 10), bg=t["carte"],
                     fg=t["texte_att"]).pack(anchor="w", pady=px((8, 0)))
            tk.Label(c, text=inv["mdp"], font=("Monospace", 16, "bold"), bg=t["carte"],
                     fg=t["bleu"]).pack(anchor="w")
            label_exp = tk.Label(c, text="", font=(POLICE, 11), bg=t["carte"], fg=t["texte_att"])
            label_exp.pack(anchor="w", pady=px((4, 0)))

            def tic():
                if not label_exp.winfo_exists():
                    return
                i = self._invite_active()
                if not i:
                    construire()
                    return
                fin = datetime.fromtimestamp(i["expire"])
                n = len(i.get("macs", []))
                label_exp.config(text=f"Expire à {fin:%H:%M} (dans {format_duree(i['expire'] - time.time())})"
                                      f"  ·  {n} invité{'s' if n > 1 else ''} connecté{'s' if n > 1 else ''}")
                label_exp.after(1000, tic)

            tic()
            if QR_DISPONIBLE:
                CACHE_DIR.mkdir(parents=True, exist_ok=True)
                chemin = CACHE_DIR / "qr-invite.png"
                ssid = lire_ssid_en_service() or self.config_actuelle["ssid"]
                qrcode.make(f"WIFI:T:WPA;S:{echapper_wifi(ssid)};P:{echapper_wifi(inv['mdp'])};;",
                            box_size=5, border=2).convert("RGB").save(chemin)
                image = tk.PhotoImage(file=str(chemin))
                lab = tk.Label(c, image=image, bg=t["carte"])
                lab.image = image
                lab.pack(pady=px((10, 0)))
            bas = tk.Frame(c, bg=t["carte"])
            bas.pack(fill="x", pady=px((12, 0)))
            BoutonPilule(bas, t, "Copier", lambda: (self.clipboard_clear(), self.clipboard_append(inv["mdp"])),
                         taille=11, hauteur=px(30)).pack(side="left")
            BoutonPilule(bas, t, "+1 heure", lambda: creer(1), taille=11,
                         hauteur=px(30)).pack(side="left", padx=px(6))
            BoutonPilule(bas, t, "Révoquer maintenant", revoquer, style="danger", taille=11,
                         hauteur=px(30)).pack(side="right")

        def creer(h):
            if not self._creer_invite(h):
                self._message("⚠ Impossible de créer l'accès invité (relance le partage).", "rouge")
            construire()
            dimensionner()

        def revoquer():
            if self._demander("Révoquer l'accès invité ?", "Les invités connectés seront déconnectés "
                              "tout de suite.", oui="Révoquer", style_oui="danger", parent=fen):
                self._revoquer_invite()
                construire()
                dimensionner()

        def dimensionner():
            fen.update_idletasks()
            taille = f"{px(480)}x{corps.winfo_reqheight()}"
            fen.geometry(taille)
            return taille

        construire()
        taille = dimensionner()
        fen.after(100, lambda: (_premier_plan(fen), fen.geometry(taille)))

    # -- debit par appareil (26/11/2026) -------------------------------------------------
    def _choisir_debit(self, mac):
        t = self.t
        app = next((a for a in self.etat.get("devices", []) if a.get("mac") == mac), {"mac": mac})
        nom = self._nom_appareil(app)
        actuel = int(self.config_actuelle["limites_debit"].get(mac, 0))
        fen = tk.Toplevel(self, bg=t["fond"])
        fen.title("Débit maximum")
        fen.transient(self)
        fen.resizable(False, False)
        tk.Label(fen, text=f"Débit maximum pour « {nom} »", font=(POLICE, 14, "bold"), bg=t["fond"],
                 fg=t["texte"], wraplength=px(400), justify="left").pack(anchor="w", padx=px(22), pady=px((20, 4)))
        tk.Label(fen, text="S'applique tout de suite, dans les deux sens, sans déconnecter l'appareil. "
                           "Ton mot de passe est demandé.", font=(POLICE, 11), bg=t["fond"],
                 fg=t["texte_att"], wraplength=px(400), justify="left").pack(anchor="w", padx=px(22))
        rangee = tk.Frame(fen, bg=t["fond"])
        rangee.pack(fill="x", padx=px(22), pady=px((14, 22)))

        def choisir(kbit):
            fen.destroy()
            if kbit == actuel:
                return
            self._appliquer_debit(mac, kbit, nom)

        for kbit in DEBITS_APPAREIL:
            lib = "Illimité" if kbit == 0 else f"{kbit // 1000} Mbit/s"
            BoutonPilule(rangee, t, lib, lambda k=kbit: choisir(k), taille=11, hauteur=px(32),
                         style="bleu" if kbit == actuel else "secondaire",
                         fond_parent=t["fond"]).pack(side="left", padx=px((0, 6)))
        fen.update_idletasks()
        taille = f"{max(px(460), fen.winfo_reqwidth())}x{fen.winfo_reqheight()}"
        fen.geometry(taille)
        fen.after(100, lambda: (_premier_plan(fen), fen.geometry(taille)))

    def _appliquer_debit(self, mac, kbit, nom, silencieux=False):
        app = next((a for a in self.etat.get("devices", []) if a.get("mac") == mac), {})

        def action():
            ok, sortie = executer_privilegie("limit", mac, str(kbit))
            if ok:
                limites = self.config_actuelle["limites_debit"]
                if kbit:
                    limites[mac] = kbit
                else:
                    limites.pop(mac, None)
                sauvegarder_config(self.config_actuelle)
            return ok, sortie

        self._ip_limitees[mac] = app.get("ip")
        succes = (f"Débit de {nom} limité à {format_kbit(kbit)}." if kbit
                  else f"Débit de {nom} : illimité.")
        if silencieux:
            succes = f"Limite de débit de {nom} réappliquée (nouvelle adresse IP)."
        self._executer(action, f"Réglage du débit de {nom}…", succes)

    def _suivre_limites(self, appareils):
        """Une limite suit l'adresse IP : si l'appareil en change, on la remet."""
        limites = self.config_actuelle.get("limites_debit", {})
        for app in appareils:
            mac, ip = app.get("mac"), app.get("ip")
            if mac not in limites or not ip:
                continue
            if mac not in self._ip_limitees:
                self._ip_limitees[mac] = ip   # deja en place au lancement d'Optima
            elif self._ip_limitees[mac] != ip and not self._occupe:
                self._appliquer_debit(mac, int(limites[mac]), self._nom_appareil(app), silencieux=True)
                return

    # -- test de vitesse (26/11/2026) -----------------------------------------------------
    def _ouvrir_vitesse(self):
        t = self.t
        ancienne = getattr(self, "_fen_vitesse", None)
        if ancienne is not None and ancienne.winfo_exists():
            _premier_plan(ancienne)
            return
        fen = tk.Toplevel(self, bg=t["fond"])
        self._fen_vitesse = fen
        fen.title("Test de vitesse")
        fen.transient(self)
        tk.Label(fen, text="Test de vitesse", font=(POLICE, 16, "bold"), bg=t["fond"],
                 fg=t["texte"]).pack(anchor="w", padx=px(20), pady=px((18, 4)))
        tk.Label(fen, text="Mesure la connexion source (celle que tu partages). Utilise environ "
                           "30 Mo de données.", font=(POLICE, 11), bg=t["fond"], fg=t["texte_att"],
                 wraplength=px(420), justify="left").pack(anchor="w", padx=px(20))
        carte = Carte(fen, t)
        carte.pack(fill="x", padx=px(16), pady=px((12, 12)))
        grille = tk.Frame(carte.corps, bg=t["carte"])
        grille.pack(fill="x")
        valeurs = {}
        for i, (cle, titre) in enumerate((("ping", "Ping"), ("bas", "Réception"), ("haut", "Envoi"))):
            grille.columnconfigure(i, weight=1, uniform="v")
            bloc = tk.Frame(grille, bg=t["carte"])
            bloc.grid(row=0, column=i, sticky="w")
            tk.Label(bloc, text=titre, font=(POLICE, 10), bg=t["carte"], fg=t["texte_att"]).pack(anchor="w")
            v = tk.Label(bloc, text="—", font=(POLICE, 17, "bold"), bg=t["carte"], fg=t["texte"])
            v.pack(anchor="w")
            valeurs[cle] = v
        etat = tk.Label(carte.corps, text="", font=(POLICE, 11), bg=t["carte"], fg=t["texte_att"],
                        anchor="w", wraplength=px(400), justify="left")
        etat.pack(fill="x", pady=px((10, 0)))
        bouton = BoutonPilule(carte.corps, t, "Lancer le test", lambda: lancer(), style="principal",
                              taille=12, hauteur=px(36))
        bouton.pack(fill="x", pady=px((12, 0)))
        carte_h = Carte(fen, t, titre="DERNIERS TESTS")
        carte_h.pack(fill="x", padx=px(16), pady=px((0, 16)))

        def historique():
            for e in carte_h.corps.winfo_children()[1:]:
                e.destroy()
            tests = self.config_actuelle.get("tests_vitesse", [])
            if not tests:
                tk.Label(carte_h.corps, text="Aucun test pour l'instant.", font=(POLICE, 11),
                         bg=t["carte"], fg=t["texte_att"]).pack(anchor="w")
            for r in reversed(tests[-5:]):
                tk.Label(carte_h.corps, font=(POLICE, 11), bg=t["carte"], fg=t["texte"], anchor="w",
                         text=f"{r['date']}   ↓ {format_bits(r['bas'])}   ↑ {format_bits(r['haut'])}"
                              f"    {r['ping']:.0f} ms").pack(fill="x")

        def vivant():
            try:
                return fen.winfo_exists()
            except tk.TclError:
                return False

        def maj(cle, texte, msg=None):
            if vivant():
                valeurs[cle].config(text=texte)
                if msg is not None:
                    etat.config(text=msg, fg=t["texte_att"])

        def lancer():
            if getattr(self, "_test_en_cours", False):
                return
            self._test_en_cours = True
            bouton.configurer(texte="Test en cours…", actif=False)
            for v in valeurs.values():
                v.config(text="—")
            uplink = self.etat.get("uplink")
            reseau = nom_reseau_source(uplink) if uplink not in (None, "null") else None

            def tache():
                try:
                    self._en_main(lambda: maj("ping", "…", "Mesure du ping…"))
                    pings = [x for x in (mesurer_ping(uplink if uplink not in (None, "null") else None)
                                         for _ in range(4)) if x is not None]
                    if not pings:
                        raise RuntimeError("le serveur ne répond pas")
                    ping = sorted(pings)[len(pings) // 2]
                    self._en_main(lambda: maj("ping", f"{ping:.0f} ms", "Test de réception…"))
                    derniere = [0.0]

                    def rappel(bits):
                        if time.time() - derniere[0] > 0.25:
                            derniere[0] = time.time()
                            self._en_main(lambda b=bits: maj("bas", format_bits(b)))

                    bas = test_descente(25 * MO, rappel)
                    self._en_main(lambda: maj("bas", format_bits(bas), "Test d'envoi…"))
                    haut = test_montee(5 * MO)
                    self._en_main(lambda: maj("haut", format_bits(haut)))
                    resultat = {"date": datetime.now().strftime("%d/%m %H:%M"), "ping": ping,
                                "bas": bas, "haut": haut, "reseau": reseau}

                    def fin():
                        tests = self.config_actuelle.setdefault("tests_vitesse", [])
                        tests.append(resultat)
                        del tests[:-20]
                        sauvegarder_config(self.config_actuelle)
                        if vivant():
                            etat.config(text="✓ Test terminé.", fg=t["accent"])
                            historique()
                    self._en_main(fin)
                except Exception as erreur:
                    texte = str(getattr(erreur, "reason", erreur))
                    self._en_main(lambda: vivant() and etat.config(
                        text=f"⚠ Test impossible : {texte}. Vérifie la connexion (portail captif ?).",
                        fg=t["rouge"]))
                finally:
                    def liberer():
                        self._test_en_cours = False
                        if vivant():
                            bouton.configurer(texte="Relancer le test", actif=True)
                    self._en_main(liberer)

            threading.Thread(target=tache, daemon=True).start()

        historique()
        fen.update_idletasks()
        taille = f"{px(480)}x{fen.winfo_reqheight()}"
        fen.geometry(taille)
        fen.after(100, lambda: (_premier_plan(fen), fen.geometry(taille)))

    # -- fenetres secondaires ---------------------------------------------------------
    def _ouvrir_parametres(self):
        t = self.t
        ancienne = getattr(self, "_fen_reglages", None)
        if ancienne is not None and ancienne.winfo_exists():
            _premier_plan(ancienne)
            return
        fen = tk.Toplevel(self, bg=t["fond"])
        self._fen_reglages = fen
        fen.title("Réglages")
        fen.transient(self)
        fen.resizable(True, True)
        fen.minsize(px(380), px(320))
        textes_aide = []   # leur largeur suit celle de la fenetre
        cfg = self.config_actuelle
        var_auto = tk.BooleanVar(value=cfg.get("auto_start", False))
        var_ouverture = tk.BooleanVar(value=cfg.get("partage_ouverture", False))
        var_fermeture = tk.StringVar(value=cfg.get("fermeture", "reduire"))
        var_notif = tk.BooleanVar(value=cfg.get("notifier_appareils", True))
        var_max = tk.IntVar(value=cfg.get("max_clients", 20))
        var_debit = tk.IntVar(value=cfg.get("bandwidth_mbps", 0))
        var_masque = tk.BooleanVar(value=cfg.get("reseau_masque", False))
        var_isoler = tk.BooleanVar(value=cfg.get("isoler_appareils", False))
        var_dns = tk.StringVar(value=cfg.get("dns", "auto"))
        var_inactif = tk.StringVar(value=str(cfg.get("arret_inactif_min", 0)))
        var_confirmer = tk.BooleanVar(value=cfg.get("confirmer_arret", True))
        var_theme = tk.StringVar(value=cfg.get("theme", "auto"))
        var_q_jour = tk.IntVar(value=int(cfg.get("quota_jour_mo") or 0))
        var_q_mois = tk.IntVar(value=int(cfg.get("quota_mois_go") or 0))
        var_q_app = tk.IntVar(value=int(cfg.get("quota_appareil_mo") or 0))
        var_q_action = tk.StringVar(value=cfg.get("quota_action", "alerter"))
        var_plan = tk.BooleanVar(value=cfg.get("planning_actif", False))
        var_debut = tk.StringVar(value=cfg.get("planning_debut", "08:00"))
        var_fin = tk.StringVar(value=cfg.get("planning_fin", "23:00"))
        jours_choisis = set(cfg.get("planning_jours") or [])
        var_surveiller = tk.BooleanVar(value=cfg.get("surveiller_connexion", True))
        var_blanche = tk.BooleanVar(value=cfg.get("liste_blanche", False))

        # boutons du bas fixes, le reste defile
        bas = tk.Frame(fen, bg=t["fond"])
        bas.pack(side="bottom", fill="x", padx=px(20), pady=px((10, 18)))
        toile = tk.Canvas(fen, bg=t["fond"], highlightthickness=0, bd=0)
        toile.pack(side="top", fill="both", expand=True)
        contenu = tk.Frame(toile, bg=t["fond"])
        id_contenu = toile.create_window(0, 0, window=contenu, anchor="nw")
        contenu.bind("<Configure>", lambda _e: toile.configure(scrollregion=toile.bbox("all")))
        def sur_redim(e):
            toile.itemconfig(id_contenu, width=e.width)
            for lab in textes_aide:
                lab.config(wraplength=max(px(200), e.width - px(150)))
        toile.bind("<Configure>", sur_redim)
        fen._defilement = (toile, contenu)

        tk.Label(contenu, text="Réglages", font=(POLICE, 16, "bold"), bg=t["fond"],
                 fg=t["texte"]).pack(anchor="w", padx=px(20), pady=px((18, 4)))

        def section(titre):
            carte = Carte(contenu, t, titre=titre)
            carte.pack(fill="x", padx=px(16), pady=px((10, 0)))
            return carte.corps

        def bloc_texte(corps, l, texte, aide):
            bloc = tk.Frame(l, bg=t["carte"])
            bloc.pack(side="left", fill="x", expand=True)
            tk.Label(bloc, text=texte, font=(POLICE, 12), bg=t["carte"], fg=t["texte"],
                     anchor="w").pack(fill="x")
            if aide:
                lab = tk.Label(bloc, text=aide, font=(POLICE, 10), bg=t["carte"], fg=t["texte_att"],
                               anchor="w", wraplength=px(270), justify="left")
                lab.pack(fill="x")
                textes_aide.append(lab)

        def ligne_interrupteur(corps, texte, var, aide=None):
            l = tk.Frame(corps, bg=t["carte"])
            l.pack(fill="x", pady=px(6))
            Interrupteur(l, t, var).pack(side="right", anchor="n", pady=px((2, 0)))
            bloc_texte(corps, l, texte, aide)

        def ligne_nombre(corps, texte, var, mini, maxi, pas=1, aide=None):
            l = tk.Frame(corps, bg=t["carte"])
            l.pack(fill="x", pady=px(6))
            tk.Spinbox(l, from_=mini, to=maxi, increment=pas, textvariable=var, width=5,
                       font=(POLICE, 12), bg=t["champ"], fg=t["texte"], insertbackground=t["texte"],
                       buttonbackground=t["bouton"], relief="flat", highlightthickness=1,
                       highlightbackground=t["bordure"], justify="center").pack(side="right", ipady=px(3))
            bloc_texte(corps, l, texte, aide)

        def ligne_choix(corps, texte, var, options, aide=None):
            """Libelle puis une rangee de pilules ; la pilule choisie est en bleu."""
            l = tk.Frame(corps, bg=t["carte"])
            l.pack(fill="x", pady=px(6))
            haut = tk.Frame(l, bg=t["carte"])
            haut.pack(fill="x")
            bloc_texte(corps, haut, texte, aide)
            rangee = tk.Frame(l, bg=t["carte"])
            rangee.pack(fill="x", pady=px((6, 0)))
            boutons = {}

            def choisir(v):
                var.set(v)
                for k, b in boutons.items():
                    b.configurer(style="bleu" if k == v else "secondaire")

            for v, lib in options:
                b = BoutonPilule(rangee, t, lib, lambda v=v: choisir(v), taille=11, hauteur=px(28))
                b.pack(side="left", padx=px((0, 6)))
                boutons[v] = b
            choisir(var.get() if var.get() in boutons else options[0][0])

        # -- Demarrage
        c = section("DÉMARRAGE")
        ligne_interrupteur(c, "Démarrer à l'ouverture de session", var_auto,
                           "Optima se lance discrètement et partage ta connexion.")
        ligne_interrupteur(c, "Partager dès l'ouverture d'Optima", var_ouverture,
                           "Le partage démarre tout seul quand tu ouvres l'appli.")
        ligne_choix(c, "Quand je ferme la fenêtre", var_fermeture,
                    (("reduire", "Réduire dans la barre"), ("quitter", "Quitter Optima")),
                    "Le partage continue dans les deux cas.")

        # -- Reseau
        c = section("RÉSEAU")
        ligne_interrupteur(c, "Réseau masqué", var_masque,
                           "Le nom du réseau n'apparaît plus dans les listes Wi-Fi : "
                           "il faut le taper à la main (ou scanner le QR code).")
        ligne_interrupteur(c, "Isoler les appareils", var_isoler,
                           "Les appareils connectés ont Internet mais ne se voient pas "
                           "entre eux (plus sûr avec des invités).")
        ligne_choix(c, "DNS des appareils", var_dns,
                    (("auto", "Auto"), ("cloudflare", "Cloudflare"), ("google", "Google"), ("quad9", "Quad9")),
                    "Auto = celui du réseau partagé. Les autres peuvent accélérer "
                    "l'ouverture des sites ; Quad9 bloque aussi des sites malveillants.")
        ligne_interrupteur(c, "Seulement les appareils autorisés", var_blanche,
                           "Un appareil inconnu est refusé aussitôt et apparaît dans « Demandes de "
                           "connexion » pour que tu l'autorises. Les appareils déjà connectés et ceux "
                           "que tu as renommés sont autorisés d'office. Optima doit rester ouvert "
                           "(ou réduit dans la barre).")
        BoutonPilule(c, t, "Gérer les appareils autorisés", lambda: self._gerer_autorises(fen),
                     taille=11, hauteur=px(28)).pack(anchor="w", pady=px((0, 6)))
        ligne_nombre(c, "Appareils maximum", var_max, 1, 50)
        ligne_nombre(c, "Limite de débit (Mbit/s)", var_debit, 0, 1000, 5, "0 = illimité")

        def ligne_heures(corps):
            l = tk.Frame(corps, bg=t["carte"])
            l.pack(fill="x", pady=px(6))
            champs = {}
            for texte, var in (("De", var_debut), ("à", var_fin)):
                tk.Label(l, text=texte, font=(POLICE, 12), bg=t["carte"], fg=t["texte"]).pack(
                    side="left", padx=px((0 if texte == "De" else 10, 6)))
                e = tk.Entry(l, textvariable=var, width=6, font=(POLICE, 12), justify="center",
                             bg=t["champ"], fg=t["texte"], insertbackground=t["texte"], relief="flat",
                             highlightthickness=1, highlightbackground=t["bordure"],
                             highlightcolor=t["bleu"])
                e.pack(side="left", ipady=px(4))
                champs[texte] = e
            return champs

        def ligne_jours(corps):
            l = tk.Frame(corps, bg=t["carte"])
            l.pack(fill="x", pady=px((4, 6)))
            boutons = {}

            def basculer(i):
                jours_choisis.symmetric_difference_update({i})
                boutons[i].configurer(style="bleu" if i in jours_choisis else "secondaire")

            for i, lettre in enumerate(JOURS_COURTS):
                b = BoutonPilule(l, t, lettre, lambda i=i: basculer(i), taille=11, hauteur=px(30),
                                 largeur=px(36), style="bleu" if i in jours_choisis else "secondaire")
                b.pack(side="left", padx=px((0, 5)))
                boutons[i] = b

        # -- Donnees et limites
        c = section("DONNÉES ET LIMITES")
        ligne_nombre(c, "Limite par jour (Mo)", var_q_jour, 0, 100000, 100,
                     "Tous appareils confondus. 0 = aucune limite.")
        ligne_nombre(c, "Limite par mois (Go)", var_q_mois, 0, 2000, 1,
                     "Mois civil, remis à zéro le 1er. 0 = aucune limite.")
        ligne_nombre(c, "Limite par appareil et par jour (Mo)", var_q_app, 0, 100000, 100,
                     "0 = aucune limite.")
        ligne_choix(c, "Quand une limite est atteinte", var_q_action,
                    (("alerter", "Me prévenir"), ("bloquer", "Couper")),
                    "Une notification arrive dès 80 %. « Couper » arrête le partage (limite du jour "
                    "ou du mois) ou coupe Internet pour l'appareil concerné ; tu le rétablis "
                    "avec son bouton. Ton mot de passe est demandé à ce moment-là.")

        # -- Plage horaire
        c = section("PLAGE HORAIRE")
        ligne_interrupteur(c, "Partager seulement à certaines heures", var_plan,
                           "Le partage démarre au début de la plage et s'arrête à la fin. "
                           "Tu peux toujours le lancer ou l'arrêter à la main entre-temps.")
        champs_heures = ligne_heures(c)
        ligne_jours(c)
        label_erreur_plan = tk.Label(c, text="", font=(POLICE, 10), bg=t["carte"], fg=t["rouge"],
                                     anchor="w")
        label_erreur_plan.pack(fill="x")

        # -- Arret
        c = section("ARRÊT ET ÉCONOMIE")
        ligne_choix(c, "Arrêter si plus aucun appareil depuis", var_inactif,
                    (("0", "Jamais"), ("5", "5 min"), ("10", "10 min"), ("30", "30 min")),
                    "Économise la batterie. Ton mot de passe sera demandé au moment de l'arrêt.")
        ligne_interrupteur(c, "Confirmer avant d'arrêter", var_confirmer,
                           "Demande confirmation si des appareils sont encore connectés.")

        # -- Notifications et apparence
        c = section("NOTIFICATIONS ET APPARENCE")
        ligne_interrupteur(c, "Notifier les connexions", var_notif,
                           "Une notification quand un appareil se connecte ou part.")
        ligne_interrupteur(c, "Surveiller la connexion source", var_surveiller,
                           "Mesure le ping toutes les 5 s et te prévient si le Wi-Fi source "
                           "coupe Internet ou redemande la page de connexion (portail captif).")
        ligne_choix(c, "Apparence", var_theme, (("auto", "Auto"), ("clair", "Clair"), ("sombre", "Sombre")))

        texte_bas = "Les réglages Réseau s'appliquent au prochain démarrage du partage."
        if self.etat.get("running"):
            texte_bas = ("Le partage est actif : les réglages Réseau s'appliqueront quand "
                         "tu le relanceras.")
        tk.Label(contenu, text=texte_bas, font=(POLICE, 10), bg=t["fond"], fg=t["texte_att"],
                 wraplength=px(440), justify="left").pack(anchor="w", padx=px(20), pady=px((8, 4)))

        if est_admin():
            self._construire_panneau_admin(contenu)

        def enregistrer():
            debut, fin = lire_heure(var_debut.get()), lire_heure(var_fin.get())
            if var_plan.get() and (debut is None or fin is None or not jours_choisis):
                label_erreur_plan.config(
                    text="Heures au format 08:00 et au moins un jour, s'il te plaît."
                    if jours_choisis else "Choisis au moins un jour.")
                champ = champs_heures["De" if debut is None else "à"]
                champ.focus_set()
                toile.yview_moveto(0.35)
                return
            ancien_theme = cfg.get("theme", "auto")
            if debut is not None:
                cfg["planning_debut"] = f"{debut // 60:02d}:{debut % 60:02d}"
            if fin is not None:
                cfg["planning_fin"] = f"{fin // 60:02d}:{fin % 60:02d}"
            cfg["planning_jours"] = sorted(jours_choisis)
            if var_plan.get() and not cfg.get("planning_actif"):
                self._plage_prec = None
            cfg["planning_actif"] = var_plan.get()
            cfg["quota_action"] = var_q_action.get()
            cfg["surveiller_connexion"] = var_surveiller.get()
            if var_blanche.get() and not cfg.get("liste_blanche"):
                self._activer_liste_blanche()
            cfg["liste_blanche"] = var_blanche.get()
            if not cfg["liste_blanche"] and self._demandes:
                macs = list(self._demandes)
                self._demandes.clear()
                self._afficher_demandes()
                threading.Thread(target=lambda: [hostapd_cmd("deny_acl", "DEL_MAC", m) for m in macs],
                                 daemon=True).start()
            for cle, var, maxi in (("quota_jour_mo", var_q_jour, 100000), ("quota_mois_go", var_q_mois, 2000),
                                   ("quota_appareil_mo", var_q_app, 100000)):
                try:
                    cfg[cle] = max(0, min(maxi, int(var.get())))
                except (tk.TclError, ValueError):
                    pass
            self._cache_planning = (None, None)
            cfg["auto_start"] = var_auto.get()
            cfg["partage_ouverture"] = var_ouverture.get()
            cfg["fermeture"] = var_fermeture.get()
            cfg["notifier_appareils"] = var_notif.get()
            cfg["reseau_masque"] = var_masque.get()
            cfg["isoler_appareils"] = var_isoler.get()
            cfg["dns"] = var_dns.get()
            cfg["confirmer_arret"] = var_confirmer.get()
            try:
                cfg["arret_inactif_min"] = max(0, int(var_inactif.get()))
            except ValueError:
                cfg["arret_inactif_min"] = 0
            try:
                cfg["max_clients"] = max(1, min(50, int(var_max.get())))
                cfg["bandwidth_mbps"] = max(0, min(1000, int(var_debit.get())))
            except (tk.TclError, ValueError):
                pass
            cfg["theme"] = var_theme.get()
            sauvegarder_config(cfg)
            (activer_autostart if var_auto.get() else desactiver_autostart)()
            fen.destroy()
            self._message("✓ Réglages enregistrés.", "accent")
            if cfg["theme"] != ancien_theme:
                self._reconstruire()

        def par_defaut():
            if not self._demander("Remettre les réglages par défaut ?",
                                  "Le nom du réseau, le mot de passe et les surnoms des appareils sont gardés.",
                                  oui="Remettre par défaut", parent=fen):
                return
            ancien_theme = cfg.get("theme", "auto")
            for cle, valeur in CONFIG_PAR_DEFAUT.items():
                if cle not in ("ssid", "password", "surnoms", "auto_start"):
                    cfg[cle] = json.loads(json.dumps(valeur))
            sauvegarder_config(cfg)
            fen.destroy()
            if cfg["theme"] != ancien_theme:
                self._reconstruire()
            self._ouvrir_parametres()

        BoutonPilule(bas, t, "Enregistrer", enregistrer, style="principal", hauteur=px(34),
                     fond_parent=t["fond"]).pack(side="right")
        BoutonPilule(bas, t, "Annuler", fen.destroy, hauteur=px(34),
                     fond_parent=t["fond"]).pack(side="right", padx=px(8))
        BoutonPilule(bas, t, "Par défaut", par_defaut, hauteur=px(34), taille=11,
                     fond_parent=t["fond"]).pack(side="left")
        fen.update_idletasks()
        hauteur = min(contenu.winfo_reqheight() + bas.winfo_reqheight() + px(30),
                      px(640), fen.winfo_screenheight() - px(220))
        taille = f"{px(500)}x{hauteur}"
        fen.geometry(taille)
        # GNOME ignore parfois la taille demandee avant l'affichage : on la redonne apres.
        fen.after(100, lambda: (_premier_plan(fen), fen.geometry(taille)))

    def _reconstruire(self):
        valeurs = (self.ssid_var.get(), self.mdp_var.get())
        for enfant in self.winfo_children():
            if not isinstance(enfant, tk.Toplevel):
                enfant.destroy()
        self.lignes_appareils, self.lignes_bloques, self.lignes_demandes = {}, {}, {}
        self._qr_visible = False
        self._construire()
        self.ssid_var.set(valeurs[0])
        self.mdp_var.set(valeurs[1])
        if self.arret_programme:
            self._maj_texte_arret()
        self._afficher_qualite()
        self._afficher_demandes()
        self._recuperer_statut()

    def _construire_panneau_admin(self, fen):
        t = self.t
        carte = Carte(fen, t, titre="PANNEAU ADMIN (cette machine seulement)")
        carte.pack(fill="x", padx=px(16), pady=px((12, 0)))
        var_cle = tk.StringVar(value="")
        tk.Entry(carte.corps, textvariable=var_cle, font=(POLICE, 13, "bold"), justify="center",
                 bg=t["champ"], fg=t["accent"], relief="flat", state="readonly",
                 readonlybackground=t["champ"]).pack(fill="x", ipady=px(8))
        l = tk.Frame(carte.corps, bg=t["carte"])
        l.pack(fill="x", pady=px((8, 0)))
        BoutonPilule(l, t, "Générer une clé", lambda: var_cle.set(generer_cle_licence()),
                     taille=11, hauteur=px(28)).pack(side="left")

        def copier():
            self.clipboard_clear()
            self.clipboard_append(var_cle.get())

        BoutonPilule(l, t, "Copier", copier, taille=11, hauteur=px(28)).pack(side="left", padx=px(6))

    def _ouvrir_stats(self):
        t = self.t
        ancienne = getattr(self, "_fen_stats", None)
        if ancienne is not None and ancienne.winfo_exists():
            _premier_plan(ancienne)
            return
        self.compteur.sauvegarder(forcer=True)
        fen = tk.Toplevel(self, bg=t["fond"])
        self._fen_stats = fen
        fen.title("Statistiques")
        fen.transient(self)
        fen.minsize(px(400), px(400))
        toile = tk.Canvas(fen, bg=t["fond"], highlightthickness=0, bd=0)
        toile.pack(fill="both", expand=True)
        contenu = tk.Frame(toile, bg=t["fond"])
        id_contenu = toile.create_window(0, 0, window=contenu, anchor="nw")
        contenu.bind("<Configure>", lambda _e: toile.configure(scrollregion=toile.bbox("all")))
        toile.bind("<Configure>", lambda e: toile.itemconfig(id_contenu, width=e.width))
        fen._defilement = (toile, contenu)
        periode = {"n": 30}

        tete = tk.Frame(contenu, bg=t["fond"])
        tete.pack(fill="x", padx=px(20), pady=px((18, 10)))
        tk.Label(tete, text="Statistiques", font=(POLICE, 16, "bold"), bg=t["fond"],
                 fg=t["texte"]).pack(side="left")
        BoutonPilule(tete, t, "Exporter (CSV)", lambda: exporter(), taille=11, hauteur=px(28),
                     fond_parent=t["fond"]).pack(side="right")

        # resume
        carte = Carte(contenu, t)
        carte.pack(fill="x", padx=px(16), pady=px((0, 12)))
        resume = tk.Frame(carte.corps, bg=t["carte"])
        resume.pack(fill="x")
        cp = self.compteur
        for i, (titre, valeur) in enumerate((("Aujourd'hui", cp.total_jour()),
                                              ("7 derniers jours", cp.total_periode(7)),
                                              ("Ce mois-ci", cp.total_mois()))):
            resume.columnconfigure(i, weight=1, uniform="r")
            bloc = tk.Frame(resume, bg=t["carte"])
            bloc.grid(row=0, column=i, sticky="w")
            tk.Label(bloc, text=titre, font=(POLICE, 10), bg=t["carte"], fg=t["texte_att"]).pack(anchor="w")
            tk.Label(bloc, text=format_octets(valeur), font=(POLICE, 15, "bold"), bg=t["carte"],
                     fg=t["texte"]).pack(anchor="w")

        # graphique par jour
        carte_g = Carte(contenu, t, titre="DONNÉES PAR JOUR")
        carte_g.pack(fill="x", padx=px(16), pady=px((0, 12)))
        choix = tk.Frame(carte_g.corps, bg=t["carte"])
        choix.pack(fill="x", pady=px((0, 8)))
        boutons_p = {}
        info = tk.Label(carte_g.corps, text="", font=(POLICE, 11), bg=t["carte"], fg=t["texte_att"],
                        anchor="w")
        info.pack(fill="x")
        graphe = tk.Canvas(carte_g.corps, height=px(170), bg=t["carte"], highlightthickness=0, bd=0)
        graphe.pack(fill="x", pady=px((6, 0)))
        legende = tk.Frame(carte_g.corps, bg=t["carte"])
        legende.pack(fill="x", pady=px((6, 0)))
        for couleur, texte in ((t["graphe_bas"], "Reçu par les appareils"), (t["graphe_haut"], "Envoyé")):
            pt = tk.Canvas(legende, width=px(10), height=px(10), bg=t["carte"], highlightthickness=0)
            pt.create_oval(px(1), px(1), px(9), px(9), fill=couleur, outline="")
            pt.pack(side="left", padx=px((0, 4)))
            tk.Label(legende, text=texte, font=(POLICE, 10), bg=t["carte"],
                     fg=t["texte_att"]).pack(side="left", padx=px((0, 12)))
        zones = []

        def texte_info_defaut():
            total = cp.total_periode(periode["n"])
            jours_actifs = sum(1 for _d, b, h in cp.serie(periode["n"]) if b + h)
            moyenne = total / jours_actifs if jours_actifs else 0
            return (f"Total : {format_octets(total)}   ·   moyenne par jour de partage : "
                    f"{format_octets(moyenne)}")

        def dessiner_graphe(_e=None):
            graphe.delete("all")
            zones.clear()
            w, h = graphe.winfo_width(), graphe.winfo_height()
            if w < 20:
                return
            serie = cp.serie(periode["n"])
            bas_axe, haut_axe = h - px(22), px(14)
            maxi = max([b + hh for _d, b, hh in serie] + [1])
            n = len(serie)
            pas = w / n
            larg = max(px(3), min(px(28), pas * 0.62))
            graphe.create_line(0, bas_axe, w, bas_axe, fill=t["bordure"])
            if maxi > 1:
                graphe.create_text(0, 0, text=f"max {format_octets(maxi)}", anchor="nw",
                                   font=(POLICE, 9), fill=t["texte_att"])
            for i, (d, b, hh) in enumerate(serie):
                cx = pas * i + pas / 2
                x1, x2 = cx - larg / 2, cx + larg / 2
                hb = (b / maxi) * (bas_axe - haut_axe)
                hh_ = (hh / maxi) * (bas_axe - haut_axe)
                if b:
                    graphe.create_rectangle(x1, bas_axe - hb, x2, bas_axe, fill=t["graphe_bas"], outline="")
                if hh:
                    graphe.create_rectangle(x1, bas_axe - hb - hh_, x2, bas_axe - hb,
                                            fill=t["graphe_haut"], outline="")
                if d == date.today():
                    graphe.create_rectangle(x1, bas_axe + px(3), x2, bas_axe + px(5),
                                            fill=t["bleu"], outline="")
                if periode["n"] <= 7:
                    etiquette = JOURS_SEMAINE[d.weekday()]
                elif i % 5 == (n - 1) % 5:
                    etiquette = d.strftime("%d/%m")
                else:
                    etiquette = None
                if etiquette:
                    demi = px(18)   # garder l'etiquette dans le cadre aux deux bords
                    graphe.create_text(min(max(cx, demi), w - demi), h - px(2), text=etiquette,
                                       anchor="s", font=(POLICE, 9), fill=t["texte_att"])
                zones.append((pas * i, pas * (i + 1), d, b, hh))

        def survol(e):
            for x1, x2, d, b, hh in zones:
                if x1 <= e.x < x2:
                    info.config(text=f"{JOURS_SEMAINE[d.weekday()]} {d:%d/%m} : {format_octets(b + hh)}"
                                     f"   (↓ {format_octets(b)}  ↑ {format_octets(hh)})", fg=t["texte"])
                    return

        graphe.bind("<Configure>", dessiner_graphe)
        graphe.bind("<Motion>", survol)
        graphe.bind("<Leave>", lambda _e: info.config(text=texte_info_defaut(), fg=t["texte_att"]))

        # classement des appareils
        carte_a = Carte(contenu, t, titre="APPAREILS LES PLUS GOURMANDS")
        carte_a.pack(fill="x", padx=px(16), pady=px((0, 16)))
        cadre_cl = tk.Frame(carte_a.corps, bg=t["carte"])
        cadre_cl.pack(fill="x")

        def nom_mac(mac):
            return (self.config_actuelle["surnoms"].get(mac) or cp.donnees["noms"].get(mac)
                    or self._nom_historique(mac) or mac)

        def remplir_classement():
            for e in cadre_cl.winfo_children():
                e.destroy()
            classement = cp.classement(periode["n"])[:8]
            if not classement:
                tk.Label(cadre_cl, text="Pas encore de données par appareil sur cette période.",
                         font=(POLICE, 12), bg=t["carte"], fg=t["texte_att"], anchor="w").pack(fill="x")
                return
            maxi = max(b + h for _m, (b, h) in classement) or 1
            for mac, (b, h) in classement:
                l = tk.Frame(cadre_cl, bg=t["carte"])
                l.pack(fill="x", pady=px((4, 4)))
                haut = tk.Frame(l, bg=t["carte"])
                haut.pack(fill="x")
                tk.Label(haut, text=nom_mac(mac), font=(POLICE, 12), bg=t["carte"], fg=t["texte"],
                         anchor="w").pack(side="left")
                tk.Label(haut, text=format_octets(b + h), font=(POLICE, 12, "bold"), bg=t["carte"],
                         fg=t["texte"]).pack(side="right")
                jauge = Jauge(l, t)
                jauge.pack(fill="x", pady=px((3, 0)))
                jauge.ratio = (b + h) / maxi * 0.79   # toujours en vert : ce n'est pas un quota
                jauge.definir(jauge.ratio)

        def choisir(n):
            periode["n"] = n
            for k, b in boutons_p.items():
                b.configurer(style="bleu" if k == n else "secondaire")
            info.config(text=texte_info_defaut(), fg=t["texte_att"])
            dessiner_graphe()
            remplir_classement()

        for n, lib in ((7, "7 jours"), (30, "30 jours"), (90, "90 jours")):
            b = BoutonPilule(choix, t, lib, lambda n=n: choisir(n), taille=11, hauteur=px(28))
            b.pack(side="left", padx=px((0, 6)))
            boutons_p[n] = b

        def exporter():
            chemin = dossier_documents() / f"optima-donnees-{date.today().isoformat()}.csv"
            lignes = ["date;appareil;mac;recu_octets;envoye_octets"]
            for jour in sorted(cp.donnees["jours"]):
                j = cp.donnees["jours"][jour]
                lignes.append(f"{jour};TOTAL;;{j.get('bas', 0)};{j.get('haut', 0)}")
                for mac, (b, h) in sorted(j.get("appareils", {}).items()):
                    nom = nom_mac(mac).replace(";", ",")
                    lignes.append(f"{jour};{nom};{mac};{b};{h}")
            try:
                chemin.write_text("\n".join(lignes) + "\n")
                info.config(text=f"✓ Exporté dans {chemin}", fg=t["accent"])
            except OSError as erreur:
                info.config(text=f"⚠ Export impossible : {erreur}", fg=t["rouge"])

        choisir(30)
        fen.update_idletasks()
        hauteur = min(contenu.winfo_reqheight() + px(10), px(720), fen.winfo_screenheight() - px(160))
        taille = f"{px(520)}x{hauteur}"
        fen.geometry(taille)
        fen.after(100, lambda: (_premier_plan(fen), fen.geometry(taille)))

    def _ouvrir_historique(self):
        t = self.t
        fen = tk.Toplevel(self, bg=t["fond"])
        fen.title("Historique des connexions")
        fen.geometry(f"{px(500)}x{px(560)}")
        fen.transient(self)
        tete = tk.Frame(fen, bg=t["fond"])
        tete.pack(fill="x", padx=px(20), pady=px((18, 10)))
        tk.Label(tete, text="Historique", font=(POLICE, 16, "bold"), bg=t["fond"],
                 fg=t["texte"]).pack(side="left")

        cadre = tk.Frame(fen, bg=t["carte"], highlightthickness=1, highlightbackground=t["bordure"])
        cadre.pack(fill="both", expand=True, padx=px(16), pady=px((0, 16)))
        zone = tk.Text(cadre, bg=t["carte"], fg=t["texte"], relief="flat", font=(POLICE, 12),
                       wrap="word", padx=px(12), pady=px(10), highlightthickness=0)
        defil = tk.Scrollbar(cadre, command=zone.yview, relief="flat", bd=0, width=px(10),
                             troughcolor=t["carte"], bg=t["bouton"], activebackground=t["bouton_survol"])
        zone.config(yscrollcommand=defil.set)
        defil.pack(side="right", fill="y")
        zone.pack(side="left", fill="both", expand=True)
        zone.tag_configure("jour", font=(POLICE, 11, "bold"), foreground=t["texte_att"], spacing1=px(8), spacing3=px(4))
        zone.tag_configure("on", foreground=t["accent"])
        zone.tag_configure("off", foreground=t["texte_att"])
        zone.tag_configure("heure", foreground=t["texte_att"])
        zone.tag_configure("refus", foreground=t["rouge"])

        def remplir():
            zone.config(state="normal")
            zone.delete("1.0", "end")
            historique = list(reversed(charger_historique()))
            if not historique:
                zone.insert("end", "Aucun événement pour l'instant.", "off")
            jour_prec = None
            for e in historique:
                jour, _, heure = e.get("heure", "").partition(" ")
                if jour != jour_prec:
                    zone.insert("end", f"{jour}\n", "jour")
                    jour_prec = jour
                if e.get("evenement") == "refuse":
                    nom = self.config_actuelle["surnoms"].get(e.get("mac", "")) or e.get("hostname", "?")
                    zone.insert("end", f"{heure[:5]}  ", "heure")
                    zone.insert("end", "✕ ", "refus")
                    zone.insert("end", f"{nom} refusé (pas dans la liste)  ")
                    zone.insert("end", f"{e.get('mac', '')}\n", "heure")
                    continue
                on = e.get("evenement") == "connecte"
                nom = self.config_actuelle["surnoms"].get(e.get("mac", "")) or e.get("hostname", "?")
                zone.insert("end", f"{heure[:5]}  ", "heure")
                zone.insert("end", "● " if on else "○ ", "on" if on else "off")
                zone.insert("end", f"{nom} {'connecté' if on else 'déconnecté'}  ")
                zone.insert("end", f"{e.get('ip', '')}\n", "heure")
            zone.config(state="disabled")

        def effacer():
            if self._demander("Effacer tout l'historique ?",
                              "La liste des connexions passées sera vidée. Cette action est définitive.",
                              oui="Effacer", style_oui="danger", parent=fen):
                HISTORY_PATH.write_text("[]")
                remplir()

        BoutonPilule(tete, t, "Effacer", effacer, style="danger", taille=11, hauteur=px(28),
                     fond_parent=t["fond"]).pack(side="right")
        remplir()
        fen.after(100, lambda: _premier_plan(fen))

    # -- zone de notification ---------------------------------------------------------
    def _configurer_tray(self):
        if not TRAY_DISPONIBLE:
            self.protocol("WM_DELETE_WINDOW", self._quitter)
            return
        try:
            self.icone_tray = pystray.Icon(
                "optima", image_icone_tray(False), "Optima",
                menu=pystray.Menu(
                    pystray.MenuItem("Ouvrir Optima", self._tray_ouvrir, default=True),
                    pystray.MenuItem(lambda _i: "Arrêter le partage" if self.etat.get("running")
                                     else "Démarrer le partage", self._tray_basculer),
                    pystray.MenuItem(lambda _i: f"Aujourd'hui : {format_octets(self.compteur.total_jour())}",
                                     None, enabled=False),
                    pystray.MenuItem("Statistiques", lambda *_a: self._en_main(self._ouvrir_stats)),
                    pystray.MenuItem("Test de vitesse", lambda *_a: self._en_main(self._ouvrir_vitesse)),
                    pystray.Menu.SEPARATOR,
                    pystray.MenuItem("Quitter", self._tray_quitter),
                ),
            )
            threading.Thread(target=self.icone_tray.run, daemon=True).start()
        except Exception:
            self.icone_tray = None
        self.protocol("WM_DELETE_WINDOW", self._on_fermeture)

    def _tray_ouvrir(self, _icon=None, _item=None):
        self._en_main(lambda: _premier_plan(self))

    def _tray_basculer(self, _icon=None, _item=None):
        self._en_main(self._basculer)

    def _tray_quitter(self, _icon=None, _item=None):
        self._en_main(self._quitter)

    def _quitter(self):
        self.compteur.sauvegarder(forcer=True)
        if self.icone_tray:
            try:
                self.icone_tray.stop()
            except Exception:
                pass
        self.destroy()

    def _on_fermeture(self):
        if self.config_actuelle.get("fermeture") == "quitter":
            self._quitter()
            return
        if self.icone_tray:
            if not self._averti_reduction:
                envoyer_notification("Optima", "Optima continue dans la barre du haut. "
                                               "Le partage reste actif.")
                self._averti_reduction = True
            self.withdraw()
        else:
            self._quitter()


class FenetreActivation(tk.Tk):
    def __init__(self, sur_succes):
        super().__init__(className="Optima")
        _curseur_systeme(self)
        self.after(250, lambda: _premier_plan(self))
        t = THEMES[theme_systeme()]
        self.sur_succes = sur_succes
        self.title("Optima — Activation")
        self.geometry(f"{px(420)}x{px(320)}")
        self.configure(bg=t["fond"])
        tk.Label(self, text="Optima", font=(POLICE, 20, "bold"), bg=t["fond"], fg=t["texte"]).pack(pady=px((28, 6)))
        tk.Label(self, text="Ce logiciel nécessite une clé d'activation.\n"
                            "Demande-la à la personne qui te l'a partagé.",
                 font=(POLICE, 12), bg=t["fond"], fg=t["texte_att"]).pack(pady=px((0, 20)))
        self.var_cle = tk.StringVar()
        entree = tk.Entry(self, textvariable=self.var_cle, font=(POLICE, 14), justify="center",
                          bg=t["carte"], fg=t["texte"], insertbackground=t["texte"], relief="flat",
                          highlightthickness=1, highlightbackground=t["bordure"])
        entree.pack(fill="x", padx=px(32), ipady=px(8))
        entree.bind("<Return>", lambda _e: self._verifier())
        entree.focus_set()
        self.label_erreur = tk.Label(self, text="", font=(POLICE, 11), bg=t["fond"], fg=t["rouge"])
        self.label_erreur.pack(pady=px((12, 0)))
        BoutonPilule(self, t, "Activer", self._verifier, style="principal", hauteur=px(38),
                     fond_parent=t["fond"]).pack(fill="x", padx=px(32), pady=px((16, 0)))

    def _verifier(self):
        cle = self.var_cle.get()
        if valider_cle_licence(cle):
            activer_licence(cle)
            self.destroy()
            self.sur_succes()
        else:
            self.label_erreur.config(text="Clé invalide. Vérifie et réessaie.")


def _lancer():
    OptimaApp(mode_auto="--auto" in sys.argv).mainloop()


if __name__ == "__main__":
    if est_active():
        _lancer()
    else:
        FenetreActivation(sur_succes=_lancer).mainloop()
