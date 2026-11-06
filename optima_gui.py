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
import tkinter.font as tkfont
from collections import deque
from datetime import datetime
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
            ["notify-send", "-a", "Optima", "-i", str(ICONE_APP), titre, message],
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


def executer_privilegie(*args):
    """Lance le script hotspot en root via pkexec, renvoie (ok, sortie)."""
    env = os.environ.copy()
    env.setdefault("DISPLAY", ":0")
    env.setdefault("WAYLAND_DISPLAY", "wayland-0")
    env.setdefault("XDG_RUNTIME_DIR", f"/run/user/{os.getuid()}")
    try:
        resultat = subprocess.run(
            ["pkexec", "bash", str(HOTSPOT_SCRIPT), *args],
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


def image_icone_tray(actif):
    taille = 64
    image = Image.new("RGBA", (taille, taille), (0, 0, 0, 0))
    dessin = ImageDraw.Draw(image)
    couleur = (52, 199, 89, 255) if actif else (142, 142, 147, 255)
    for i, r in enumerate((28, 19, 10)):
        dessin.arc((32 - r, 40 - r, 32 + r, 40 + r), 220, 320, fill=couleur, width=6)
    dessin.ellipse((27, 35, 37, 45), fill=couleur)
    return image


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
            tk.Label(self.corps, text=titre, font=(POLICE, 10, "bold"), bg=t["carte"],
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
    def __init__(self, parent, t, texte, commande, style="secondaire", taille=10,
                 hauteur=px(34), largeur=None, fond_parent=None):
        self.t = t
        self.texte, self.commande, self.style = texte, commande, style
        self.police = (POLICE, taille, "bold" if style == "principal" else "normal")
        self.actif = True
        bg = fond_parent or t["carte"]
        self.largeur_fixe = largeur
        l = largeur or self._largeur_texte(parent, texte)
        super().__init__(parent, width=l, height=hauteur, bg=bg, highlightthickness=0,
                         bd=0, cursor="hand2")
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
            self.config(cursor="hand2" if actif else "arrow")
        if style is not None:
            self.style = style
        self._dessiner()


class Interrupteur(tk.Canvas):
    """Interrupteur facon macOS."""

    def __init__(self, parent, t, variable, fond_parent=None):
        super().__init__(parent, width=px(44), height=px(26), bg=fond_parent or t["carte"],
                         highlightthickness=0, bd=0, cursor="hand2")
        self.t, self.variable = t, variable
        self.bind("<ButtonRelease-1>", lambda _e: (variable.set(not variable.get()), self._dessiner()))
        self._dessiner()

    def _dessiner(self):
        self.delete("all")
        on = bool(self.variable.get())
        rect_arrondi(self, 0, 0, px(44), px(26), px(13), fill=self.t["accent"] if on else self.t["eteint"], outline="")
        x = px(31) if on else px(13)
        self.create_oval(x - px(11), px(2), x + px(11), px(24), fill="#ffffff", outline="")


class BoutonMarche(tk.Canvas):
    """Gros bouton rond marche/arret."""

    TAILLE = px(92)

    def __init__(self, parent, t, commande):
        super().__init__(parent, width=self.TAILLE, height=self.TAILLE, bg=t["carte"],
                         highlightthickness=0, bd=0, cursor="hand2")
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
    LARGEUR, HAUTEUR = px(480), px(860)

    def __init__(self, mode_auto=False):
        super().__init__(className="Optima")
        self.after(250, lambda: _premier_plan(self))
        self.title("Optima")
        self.minsize(px(440), px(600))
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
        self.duree_arret = 0
        self._nom_source = None
        self._qr_visible = False
        # Les fils d'arriere-plan (statut, pkexec, icone de la barre) ne touchent
        # jamais Tk directement : ils deposent une fonction ici, executee par Tk.
        self._file = queue.Queue()
        self._vider_file()

        self._construire()
        self._centrer_fenetre()
        self._configurer_tray()
        self._boucle_rapide()
        self._boucle_statut()

        if mode_auto:
            if TRAY_DISPONIBLE:
                self.withdraw()
            self.after(1000, self._demarrage_auto)

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
            self.bind_all(seq, lambda _e, d=delta: self._defiler(d))
        self.bind_all("<MouseWheel>", lambda e: self._defiler(-1 if e.delta > 0 else 1))

        c = self.contenu
        # en-tete
        tete = tk.Frame(c, bg=t["fond"])
        tete.pack(fill="x", padx=px(20), pady=px((18, 10)))
        tk.Label(tete, text="Optima", font=(POLICE, 22, "bold"), bg=t["fond"],
                 fg=t["texte"]).pack(side="left")
        BoutonPilule(tete, t, "Réglages", self._ouvrir_parametres, taille=9, hauteur=px(28),
                     fond_parent=t["fond"]).pack(side="right")
        BoutonPilule(tete, t, "Historique", self._ouvrir_historique, taille=9, hauteur=px(28),
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
        self.label_sous_etat = tk.Label(infos, text="", font=(POLICE, 10), bg=t["carte"],
                                        fg=t["texte_att"], anchor="w", justify="left",
                                        wraplength=px(300))
        self.label_sous_etat.pack(fill="x")
        self.label_source = tk.Label(infos, text="", font=(POLICE, 9), bg=t["carte"],
                                     fg=t["texte_att"], anchor="w", justify="left", wraplength=px(300))
        self.label_source.pack(fill="x", pady=px((2, 0)))

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
            tk.Label(bloc, text=titre, font=(POLICE, 8), bg=t["carte"], fg=t["texte_att"],
                     anchor="w").pack(fill="x")
            v = tk.Label(bloc, text="—", font=(POLICE, 10, "bold"), bg=t["carte"],
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
            tk.Label(legende, text=texte, font=(POLICE, 8), bg=t["carte"],
                     fg=t["texte_att"]).pack(side="left", padx=px((0, 12)))

        # arret programme
        ligne_arret = tk.Frame(carte.corps, bg=t["carte"])
        ligne_arret.pack(fill="x", pady=px((12, 0)))
        tk.Label(ligne_arret, text="Arrêt automatique", font=(POLICE, 10), bg=t["carte"],
                 fg=t["texte"]).pack(side="left")
        self.bouton_arret_prog = BoutonPilule(ligne_arret, t, "Jamais", self._cycle_arret,
                                              taille=9, hauteur=px(28), largeur=px(130))
        self.bouton_arret_prog.pack(side="right")

        # -- carte reseau
        carte_r = Carte(c, t, titre="RÉSEAU PARTAGÉ")
        carte_r.pack(fill="x", padx=px(16), pady=px((0, 12)))
        corps = carte_r.corps
        self.ssid_var = self._champ(corps, "Nom du réseau", self.config_actuelle["ssid"])
        self.mdp_var, self.entree_mdp = self._champ(corps, "Mot de passe",
                                                    self.config_actuelle["password"], secret=True)
        self.label_aide_mdp = tk.Label(corps, text="", font=(POLICE, 8), bg=t["carte"],
                                       fg=t["texte_att"], anchor="w")
        self.label_aide_mdp.pack(fill="x", pady=px((2, 0)))
        outils = tk.Frame(corps, bg=t["carte"])
        outils.pack(fill="x", pady=px((10, 0)))
        self.bouton_voir = BoutonPilule(outils, t, "Afficher", self._basculer_mdp, taille=9, hauteur=px(28))
        self.bouton_voir.pack(side="left")
        BoutonPilule(outils, t, "Générer", self._generer_mdp, taille=9, hauteur=px(28)).pack(side="left", padx=px(6))
        self.bouton_copier = BoutonPilule(outils, t, "Copier", self._copier_mdp, taille=9, hauteur=px(28))
        self.bouton_copier.pack(side="left")
        self.bouton_qr = BoutonPilule(outils, t, "QR code", self._basculer_qr, taille=9, hauteur=px(28),
                                      style="bleu")
        self.bouton_qr.pack(side="right")
        self.cadre_qr = tk.Frame(corps, bg=t["carte"])
        self.bouton_appliquer = BoutonPilule(corps, t, "Appliquer les changements (redémarre le partage)",
                                             self._on_demarrer, style="bleu", taille=9, hauteur=px(30))
        self.mdp_var.trace_add("write", self._sur_modif_reseau)
        self.ssid_var.trace_add("write", self._sur_modif_reseau)
        self.entree_mdp.bind("<Return>", lambda _e: self._on_demarrer())
        self._sur_modif_reseau()

        # -- carte appareils
        carte_a = Carte(c, t, titre="APPAREILS CONNECTÉS")
        carte_a.pack(fill="x", padx=px(16), pady=px((0, 12)))
        self.cadre_appareils = carte_a.corps
        self.label_aucun = tk.Label(self.cadre_appareils, text="Aucun appareil connecté.",
                                    font=(POLICE, 10), bg=t["carte"], fg=t["texte_att"], anchor="w")
        self.label_aucun.pack(fill="x")
        self.lignes_appareils = {}

        # -- carte bloques (affichee seulement si besoin)
        self.carte_bloques = Carte(c, t, titre="APPAREILS BLOQUÉS")
        self.lignes_bloques = {}

        self.label_message = tk.Label(c, text="", font=(POLICE, 9), bg=t["fond"],
                                      fg=t["texte_att"], wraplength=px(430), justify="left", anchor="w")
        self.label_message.pack(fill="x", padx=px(22), pady=px((0, 16)))

    def _defiler(self, delta):
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
        tk.Label(parent, text=libelle, font=(POLICE, 9), bg=t["carte"], fg=t["texte_att"],
                 anchor="w").pack(fill="x", pady=px((6, 3)))
        cadre = tk.Frame(parent, bg=t["champ"], highlightthickness=1,
                         highlightbackground=t["bordure"], highlightcolor=t["bleu"])
        cadre.pack(fill="x")
        variable = tk.StringVar(value=valeur)
        entree = tk.Entry(cadre, textvariable=variable, font=(POLICE, 11), bg=t["champ"],
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
                     font=(POLICE, 9), bg=t["carte"], fg=t["texte_att"]).pack(pady=px(8))
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
                     font=(POLICE, 9), bg=t["carte"], fg=t["texte_att"]).pack()
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
            if st.get("connected_time"):
                details.append(f"connecté depuis {format_duree(int(st['connected_time']))}")
            ligne["details"].config(text="  ·  ".join(d for d in details if d))
            if st.get("tx_bytes"):
                conso = (f"↓ {format_octets(int(st['tx_bytes']))}   "
                         f"↑ {format_octets(int(st.get('rx_bytes', 0)))}")
            else:
                conso = mac
            ligne["conso"].config(text=conso)
            ligne["etat"].config(text="Internet coupé" if coupe else "",
                                 fg=t["orange"])
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
        nom = tk.Label(haut, text="", font=(POLICE, 11, "bold"), bg=t["carte"], fg=t["texte"],
                       anchor="w", cursor="hand2")
        nom.pack(side="left")
        nom.bind("<Button-1>", lambda _e: self._renommer(mac))
        etat = tk.Label(haut, text="", font=(POLICE, 9, "bold"), bg=t["carte"], fg=t["orange"])
        etat.pack(side="right")
        details = tk.Label(cadre, text="", font=(POLICE, 9), bg=t["carte"], fg=t["texte_att"], anchor="w")
        details.pack(fill="x", padx=px((30, 0)))
        conso = tk.Label(cadre, text="", font=(POLICE, 9), bg=t["carte"], fg=t["texte_att"], anchor="w")
        conso.pack(fill="x", padx=px((30, 0)))
        boutons = tk.Frame(cadre, bg=t["carte"])
        boutons.pack(fill="x", padx=px((30, 0)), pady=px((6, 0)))
        ligne = {"cadre": cadre, "nom": nom, "details": details, "conso": conso, "etat": etat,
                 "signal": signal, "coupe": False}
        ligne["bouton_couper"] = BoutonPilule(
            boutons, t, "Couper Internet",
            lambda: self._action_appareil("restore" if ligne["coupe"] else "cut", mac),
            taille=9, hauteur=px(26))
        ligne["bouton_couper"].pack(side="left")
        BoutonPilule(boutons, t, "Déconnecter et bloquer",
                     lambda: self._action_appareil("kick", mac), style="danger",
                     taille=9, hauteur=px(26)).pack(side="left", padx=px(6))
        BoutonPilule(boutons, t, "Renommer", lambda: self._renommer(mac),
                     taille=9, hauteur=px(26)).pack(side="left")
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
            tk.Label(ligne, text=nom, font=(POLICE, 10), bg=t["carte"], fg=t["texte"],
                     anchor="w").pack(side="left")
            BoutonPilule(ligne, t, "Débloquer", lambda m=mac: self._action_appareil("unblock", m),
                         style="bleu", taille=9, hauteur=px(26)).pack(side="right")
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
        tk.Label(fen, text="Nom de l'appareil", font=(POLICE, 12, "bold"), bg=t["fond"],
                 fg=t["texte"]).pack(padx=px(20), pady=px((18, 4)), anchor="w")
        tk.Label(fen, text=f"{app.get('hostname', '')}  ·  {mac}", font=(POLICE, 9),
                 bg=t["fond"], fg=t["texte_att"]).pack(padx=px(20), anchor="w")
        var = tk.StringVar(value=actuel or app.get("hostname", ""))
        entree = tk.Entry(fen, textvariable=var, font=(POLICE, 11), bg=t["carte"], fg=t["texte"],
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

    def _action_appareil(self, action, mac):
        app = next((a for a in self.etat.get("devices", []) if a.get("mac") == mac), {"mac": mac})
        nom = self._nom_appareil(app)
        if action == "kick" and not messagebox.askyesno(
                "Optima", f"Déconnecter « {nom} » et l'empêcher de se reconnecter ?\n\n"
                          "Tu pourras le débloquer plus tard.", parent=self):
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
            self._on_arreter()
        else:
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
        mx = str(self.config_actuelle.get("max_clients", 20))
        bw = str(self.config_actuelle.get("bandwidth_mbps", 0))
        self._executer(lambda: executer_privilegie("start", ssid, mdp, mx, bw),
                       "Démarrage du point d'accès…", "Point d'accès démarré.", notifier=True)

    def _on_arreter(self, auto=False):
        self.arret_programme = None
        self.duree_arret = 0
        self.bouton_arret_prog.configurer(texte="Jamais")
        self._executer(lambda: executer_privilegie("stop"), "Arrêt du point d'accès…",
                       "Partage arrêté automatiquement." if auto else "Partage arrêté.", notifier=True)

    def _demarrage_auto(self):
        ssid = self.config_actuelle.get("ssid", "").strip()
        mdp = self.config_actuelle.get("password", "")
        if not ssid or len(mdp) < 8:
            return
        mx = str(self.config_actuelle.get("max_clients", 20))
        bw = str(self.config_actuelle.get("bandwidth_mbps", 0))

        def action():
            ok, sortie = False, ""
            for _ in range(24):
                ok, sortie = executer_privilegie("start", ssid, mdp, mx, bw)
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
            source = nom_reseau_source(etat.get("uplink")) if etat.get("uplink") not in (None, "null") else None
            self._en_main(lambda: self._appliquer_statut(etat, stations, source))

        threading.Thread(target=tache, daemon=True).start()

    def _appliquer_statut(self, etat, stations, source):
        t = self.t
        self.etat, self.stations = etat, stations
        actif = bool(etat.get("running"))
        if not self._occupe:
            self.bouton_marche.definir("marche" if actif else "arret")
        appareils = etat.get("devices") or []
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
        self._afficher_bloques([m.lower() for m in etat.get("blocked") or []])
        self._maj_bouton_appliquer()
        if TRAY_DISPONIBLE and self.icone_tray:
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

    # -- fenetres secondaires ---------------------------------------------------------
    def _ouvrir_parametres(self):
        t = self.t
        fen = tk.Toplevel(self, bg=t["fond"])
        fen.title("Réglages")
        fen.transient(self)
        fen.resizable(False, False)
        cfg = self.config_actuelle
        var_auto = tk.BooleanVar(value=cfg.get("auto_start", False))
        var_notif = tk.BooleanVar(value=cfg.get("notifier_appareils", True))
        var_max = tk.IntVar(value=cfg.get("max_clients", 20))
        var_debit = tk.IntVar(value=cfg.get("bandwidth_mbps", 0))
        var_theme = tk.StringVar(value=cfg.get("theme", "auto"))

        tk.Label(fen, text="Réglages", font=(POLICE, 16, "bold"), bg=t["fond"],
                 fg=t["texte"]).pack(anchor="w", padx=px(20), pady=px((18, 10)))
        carte = Carte(fen, t)
        carte.pack(fill="x", padx=px(16))
        corps = carte.corps

        def ligne_interrupteur(texte, var, aide=None):
            l = tk.Frame(corps, bg=t["carte"])
            l.pack(fill="x", pady=px(6))
            bloc = tk.Frame(l, bg=t["carte"])
            bloc.pack(side="left", fill="x", expand=True)
            tk.Label(bloc, text=texte, font=(POLICE, 10), bg=t["carte"], fg=t["texte"],
                     anchor="w").pack(fill="x")
            if aide:
                tk.Label(bloc, text=aide, font=(POLICE, 8), bg=t["carte"], fg=t["texte_att"],
                         anchor="w", wraplength=px(280), justify="left").pack(fill="x")
            Interrupteur(l, t, var).pack(side="right")

        def ligne_nombre(texte, var, mini, maxi, pas=1, aide=None):
            l = tk.Frame(corps, bg=t["carte"])
            l.pack(fill="x", pady=px(6))
            bloc = tk.Frame(l, bg=t["carte"])
            bloc.pack(side="left", fill="x", expand=True)
            tk.Label(bloc, text=texte, font=(POLICE, 10), bg=t["carte"], fg=t["texte"],
                     anchor="w").pack(fill="x")
            if aide:
                tk.Label(bloc, text=aide, font=(POLICE, 8), bg=t["carte"], fg=t["texte_att"],
                         anchor="w").pack(fill="x")
            tk.Spinbox(l, from_=mini, to=maxi, increment=pas, textvariable=var, width=5,
                       font=(POLICE, 10), bg=t["champ"], fg=t["texte"], insertbackground=t["texte"],
                       buttonbackground=t["bouton"], relief="flat", highlightthickness=1,
                       highlightbackground=t["bordure"], justify="center").pack(side="right", ipady=px(3))

        ligne_interrupteur("Démarrer à l'ouverture de session", var_auto)
        ligne_interrupteur("Notifier les connexions", var_notif,
                           "Une notification quand un appareil se connecte ou part.")
        ligne_nombre("Appareils maximum", var_max, 1, 50)
        ligne_nombre("Limite de débit (Mbit/s)", var_debit, 0, 1000, 5, "0 = illimité")

        l = tk.Frame(corps, bg=t["carte"])
        l.pack(fill="x", pady=px((10, 4)))
        tk.Label(l, text="Apparence", font=(POLICE, 10), bg=t["carte"], fg=t["texte"]).pack(side="left")
        boutons_theme = {}

        def choisir_theme(v):
            var_theme.set(v)
            for k, b in boutons_theme.items():
                b.configurer(style="bleu" if k == v else "secondaire")

        for v, lib in (("sombre", "Sombre"), ("clair", "Clair"), ("auto", "Auto")):
            b = BoutonPilule(l, t, lib, lambda v=v: choisir_theme(v), taille=9, hauteur=px(26))
            b.pack(side="right", padx=px((4, 0)))
            boutons_theme[v] = b
        choisir_theme(var_theme.get())

        tk.Label(fen, text="La limite de débit et le nombre maximum s'appliquent au prochain démarrage du partage.",
                 font=(POLICE, 8), bg=t["fond"], fg=t["texte_att"], wraplength=px(380),
                 justify="left").pack(anchor="w", padx=px(20), pady=px((8, 0)))

        if est_admin():
            self._construire_panneau_admin(fen)

        def enregistrer():
            ancien_theme = cfg.get("theme", "auto")
            cfg["auto_start"] = var_auto.get()
            cfg["notifier_appareils"] = var_notif.get()
            try:
                cfg["max_clients"] = max(1, min(50, int(var_max.get())))
                cfg["bandwidth_mbps"] = max(0, min(1000, int(var_debit.get())))
            except (tk.TclError, ValueError):
                pass
            cfg["theme"] = var_theme.get()
            sauvegarder_config(cfg)
            (activer_autostart if var_auto.get() else desactiver_autostart)()
            fen.destroy()
            if cfg["theme"] != ancien_theme:
                self._reconstruire()

        bas = tk.Frame(fen, bg=t["fond"])
        bas.pack(fill="x", padx=px(20), pady=px((16, 18)))
        BoutonPilule(bas, t, "Enregistrer", enregistrer, style="principal", hauteur=px(34),
                     fond_parent=t["fond"]).pack(side="right")
        BoutonPilule(bas, t, "Annuler", fen.destroy, hauteur=px(34),
                     fond_parent=t["fond"]).pack(side="right", padx=px(8))
        fen.update_idletasks()
        fen.geometry(f"{px(420)}x{max(fen.winfo_reqheight(), px(300))}")
        fen.after(100, lambda: _premier_plan(fen))

    def _reconstruire(self):
        valeurs = (self.ssid_var.get(), self.mdp_var.get())
        for enfant in self.winfo_children():
            if not isinstance(enfant, tk.Toplevel):
                enfant.destroy()
        self.lignes_appareils, self.lignes_bloques = {}, {}
        self._qr_visible = False
        self._construire()
        self.ssid_var.set(valeurs[0])
        self.mdp_var.set(valeurs[1])
        if self.arret_programme:
            self._maj_texte_arret()
        self._recuperer_statut()

    def _construire_panneau_admin(self, fen):
        t = self.t
        carte = Carte(fen, t, titre="PANNEAU ADMIN (cette machine seulement)")
        carte.pack(fill="x", padx=px(16), pady=px((12, 0)))
        var_cle = tk.StringVar(value="")
        tk.Entry(carte.corps, textvariable=var_cle, font=(POLICE, 12, "bold"), justify="center",
                 bg=t["champ"], fg=t["accent"], relief="flat", state="readonly",
                 readonlybackground=t["champ"]).pack(fill="x", ipady=px(8))
        l = tk.Frame(carte.corps, bg=t["carte"])
        l.pack(fill="x", pady=px((8, 0)))
        BoutonPilule(l, t, "Générer une clé", lambda: var_cle.set(generer_cle_licence()),
                     taille=9, hauteur=px(28)).pack(side="left")

        def copier():
            self.clipboard_clear()
            self.clipboard_append(var_cle.get())

        BoutonPilule(l, t, "Copier", copier, taille=9, hauteur=px(28)).pack(side="left", padx=px(6))

    def _ouvrir_historique(self):
        t = self.t
        fen = tk.Toplevel(self, bg=t["fond"])
        fen.title("Historique des connexions")
        fen.geometry(f"{px(440)}x{px(520)}")
        fen.transient(self)
        tete = tk.Frame(fen, bg=t["fond"])
        tete.pack(fill="x", padx=px(20), pady=px((18, 10)))
        tk.Label(tete, text="Historique", font=(POLICE, 16, "bold"), bg=t["fond"],
                 fg=t["texte"]).pack(side="left")

        cadre = tk.Frame(fen, bg=t["carte"], highlightthickness=1, highlightbackground=t["bordure"])
        cadre.pack(fill="both", expand=True, padx=px(16), pady=px((0, 16)))
        zone = tk.Text(cadre, bg=t["carte"], fg=t["texte"], relief="flat", font=(POLICE, 10),
                       wrap="word", padx=px(12), pady=px(10), highlightthickness=0, cursor="arrow")
        defil = tk.Scrollbar(cadre, command=zone.yview, relief="flat", bd=0, width=px(10),
                             troughcolor=t["carte"], bg=t["bouton"], activebackground=t["bouton_survol"])
        zone.config(yscrollcommand=defil.set)
        defil.pack(side="right", fill="y")
        zone.pack(side="left", fill="both", expand=True)
        zone.tag_configure("jour", font=(POLICE, 9, "bold"), foreground=t["texte_att"], spacing1=px(8), spacing3=px(4))
        zone.tag_configure("on", foreground=t["accent"])
        zone.tag_configure("off", foreground=t["texte_att"])
        zone.tag_configure("heure", foreground=t["texte_att"])

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
                on = e.get("evenement") == "connecte"
                nom = self.config_actuelle["surnoms"].get(e.get("mac", "")) or e.get("hostname", "?")
                zone.insert("end", f"{heure[:5]}  ", "heure")
                zone.insert("end", "● " if on else "○ ", "on" if on else "off")
                zone.insert("end", f"{nom} {'connecté' if on else 'déconnecté'}  ")
                zone.insert("end", f"{e.get('ip', '')}\n", "heure")
            zone.config(state="disabled")

        def effacer():
            if messagebox.askyesno("Optima", "Effacer tout l'historique ?", parent=fen):
                HISTORY_PATH.write_text("[]")
                remplir()

        BoutonPilule(tete, t, "Effacer", effacer, style="danger", taille=9, hauteur=px(28),
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
        if self.icone_tray:
            try:
                self.icone_tray.stop()
            except Exception:
                pass
        self.destroy()

    def _on_fermeture(self):
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
        self.after(250, lambda: _premier_plan(self))
        t = THEMES[theme_systeme()]
        self.sur_succes = sur_succes
        self.title("Optima — Activation")
        self.geometry(f"{px(420)}x{px(320)}")
        self.configure(bg=t["fond"])
        tk.Label(self, text="Optima", font=(POLICE, 20, "bold"), bg=t["fond"], fg=t["texte"]).pack(pady=px((28, 6)))
        tk.Label(self, text="Ce logiciel nécessite une clé d'activation.\n"
                            "Demande-la à la personne qui te l'a partagé.",
                 font=(POLICE, 10), bg=t["fond"], fg=t["texte_att"]).pack(pady=px((0, 20)))
        self.var_cle = tk.StringVar()
        entree = tk.Entry(self, textvariable=self.var_cle, font=(POLICE, 13), justify="center",
                          bg=t["carte"], fg=t["texte"], insertbackground=t["texte"], relief="flat",
                          highlightthickness=1, highlightbackground=t["bordure"])
        entree.pack(fill="x", padx=px(32), ipady=px(8))
        entree.bind("<Return>", lambda _e: self._verifier())
        entree.focus_set()
        self.label_erreur = tk.Label(self, text="", font=(POLICE, 9), bg=t["fond"], fg=t["rouge"])
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
