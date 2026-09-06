#!/usr/bin/env python3
"""Optima - point d'acces Wi-Fi partage, avec normalisation TTL.
Interface de bureau (Tkinter)."""
import hashlib
import hmac
import json
import os
import re
import secrets
import string
import subprocess
import sys
import threading
import time
import tkinter as tk
from datetime import datetime
from pathlib import Path
from tkinter import messagebox, ttk

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

ADMIN_MARKER = Path.home() / ".config" / "optima" / ".admin"
ACTIVATION_PATH = Path.home() / ".config" / "optima" / "activation.json"

# Systeme de licence optionnel, desactive par defaut dans cette version
# publique (aucun secret n'est publie dans ce depot). Pour l'activer sur ta
# propre distribution (ex: donner un binaire compile a quelqu'un et exiger
# une cle que toi seul peux generer) :
#   1. Genere un secret :  python3 -c "import secrets; print(secrets.token_hex(32))"
#   2. Exporte-le avant de lancer/compiler :  export OPTIMA_LICENSE_SECRET=<le_secret>
#   3. Cree ~/.config/optima/.admin sur ta machine pour debloquer le panneau
#      admin (generation de cles) dans les Parametres.
# Sans cette variable d'environnement, l'application est entierement libre
# d'acces : pas d'ecran d'activation, pas de panneau admin.
_SECRET_HEX = os.environ.get("OPTIMA_LICENSE_SECRET")
LICENCE_ACTIVE = bool(_SECRET_HEX)
SECRET_LICENCE = bytes.fromhex(_SECRET_HEX) if _SECRET_HEX else b""

COULEUR_FOND = "#0f1115"
COULEUR_CARTE = "#171a21"
COULEUR_CHAMP = "#0f1115"
COULEUR_BORDURE = "#252932"
COULEUR_ACCENT = "#22c55e"
COULEUR_ACCENT_SURVOL = "#16a34a"
COULEUR_TEXTE = "#e5e7eb"
COULEUR_TEXTE_ATT = "#9ca3af"
COULEUR_ERREUR = "#f87171"
COULEUR_BOUTON_SECONDAIRE = "#2a2f3a"
COULEUR_BOUTON_SECONDAIRE_SURVOL = "#3a4150"

CONFIG_PAR_DEFAUT = {
    "ssid": "Optima",
    "password": "",
    "auto_start": False,
    "max_clients": 20,
    "bandwidth_mbps": 0,
}

ERREURS_CONNUES = {
    "aucune connexion wi-fi active": (
        "Aucune connexion Wi-Fi active detectee. Connecte-toi d'abord a un "
        "reseau, puis relance le partage."
    ),
    "hostapd a echoue deux fois": (
        "Le point d'acces n'a pas reussi a demarrer (bug hostapd connu sur "
        "cette machine). Reessaie une fois ; si ca persiste, redemarre le PC."
    ),
    "mot de passe doit faire au moins 8": (
        "Le mot de passe doit faire au moins 8 caracteres."
    ),
    "delai depasse": (
        "L'operation a pris trop de temps et a ete interrompue. Reessaie."
    ),
}


def message_convivial(sortie):
    bas = (sortie or "").lower()
    for cle, message in ERREURS_CONNUES.items():
        if cle in bas:
            return message
    return sortie or "Une erreur est survenue."


def charger_config():
    cfg = dict(CONFIG_PAR_DEFAUT)
    if CONFIG_PATH.exists():
        try:
            cfg.update(json.loads(CONFIG_PATH.read_text()))
        except (ValueError, json.JSONDecodeError):
            pass
    return cfg


def sauvegarder_config(cfg):
    CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    CONFIG_PATH.write_text(json.dumps(cfg, indent=2))


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
    historique = historique[-200:]
    HISTORY_PATH.parent.mkdir(parents=True, exist_ok=True)
    HISTORY_PATH.write_text(json.dumps(historique, indent=2))


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
            ["notify-send", "-i", "network-wireless-hotspot", titre, message],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
    except FileNotFoundError:
        pass


def _empreinte_licence(partie):
    return hmac.new(SECRET_LICENCE, partie.encode(), hashlib.sha256).hexdigest().upper()[:8]


def generer_cle_licence():
    alphabet = string.ascii_uppercase + string.digits
    partie = "".join(secrets.choice(alphabet) for _ in range(10))
    empreinte = _empreinte_licence(partie)
    return f"{partie[:5]}-{partie[5:]}-{empreinte}"


def valider_cle_licence(cle):
    cle = (cle or "").strip().upper().replace(" ", "")
    morceaux = cle.split("-")
    if len(morceaux) != 3 or len(morceaux[0]) != 5 or len(morceaux[1]) != 5:
        return False
    partie = morceaux[0] + morceaux[1]
    return hmac.compare_digest(_empreinte_licence(partie), morceaux[2])


def est_admin():
    return LICENCE_ACTIVE and ADMIN_MARKER.exists()


def est_active():
    if not LICENCE_ACTIVE:
        return True
    if est_admin():
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
    ACTIVATION_PATH.write_text(json.dumps({
        "cle": cle.strip().upper(),
        "date": datetime.now().isoformat(),
    }, indent=2))


def echapper_wifi(valeur):
    return re.sub(r'([\\;,":])', r'\\\1', valeur)


def image_icone_tray(actif):
    taille = 64
    image = Image.new("RGBA", (taille, taille), (0, 0, 0, 0))
    dessin = ImageDraw.Draw(image)
    couleur = (34, 197, 94, 255) if actif else (107, 114, 128, 255)
    dessin.ellipse((4, 4, taille - 4, taille - 4), fill=couleur)
    return image


def executer_privilegie(*args):
    """Lance le script hotspot en root via pkexec, renvoie (ok, sortie)."""
    env = os.environ.copy()
    env.setdefault("DISPLAY", ":0")
    env.setdefault("WAYLAND_DISPLAY", "wayland-0")
    env.setdefault("XDG_RUNTIME_DIR", f"/run/user/{os.getuid()}")
    try:
        resultat = subprocess.run(
            ["pkexec", "bash", str(HOTSPOT_SCRIPT), *args],
            capture_output=True, text=True, timeout=40, env=env,
        )
        sortie = (resultat.stdout + resultat.stderr).strip()
        return resultat.returncode == 0, sortie
    except subprocess.TimeoutExpired:
        return False, "Delai depasse."


class OptimaApp(tk.Tk):
    def __init__(self, mode_auto=False):
        super().__init__(className="Optima")
        self.title("Optima")
        self.geometry("460x780")
        self.minsize(420, 640)
        self.configure(bg=COULEUR_FOND)
        self.resizable(True, True)
        try:
            self._image_icone = tk.PhotoImage(file=str(ICONE_APP))
            self.iconphoto(True, self._image_icone)
        except tk.TclError:
            pass

        self.config_actuelle = charger_config()
        self.afficher_mdp = tk.BooleanVar(value=False)
        self.appareils_precedents = None
        self.dernier_etat_actif = False
        self.icone_tray = None
        self._averti_reduction = False

        self._construire_interface()
        self._centrer_fenetre()
        self._configurer_tray()
        self._rafraichir_statut()

        if mode_auto:
            if TRAY_DISPONIBLE:
                self.withdraw()
            self.after(1000, self._demarrage_auto)

    def _centrer_fenetre(self):
        self.update_idletasks()
        largeur, hauteur = 460, 780
        x = (self.winfo_screenwidth() - largeur) // 2
        y = (self.winfo_screenheight() - hauteur) // 3
        self.geometry(f"{largeur}x{hauteur}+{x}+{y}")

    # -- construction de l'interface --------------------------------
    def _construire_interface(self):
        barre_haut = tk.Frame(self, bg=COULEUR_FOND)
        barre_haut.pack(fill="x", padx=24, pady=(24, 0))
        barre_haut.columnconfigure(0, weight=1)

        tk.Label(
            barre_haut, text="📶 Optima", font=("Sans", 22, "bold"),
            bg=COULEUR_FOND, fg=COULEUR_TEXTE,
        ).grid(row=0, column=0, sticky="w")

        bouton_historique = tk.Label(
            barre_haut, text="🕘", font=("Sans", 16), bg=COULEUR_FOND,
            fg=COULEUR_TEXTE_ATT, cursor="hand2",
        )
        bouton_historique.grid(row=0, column=1, padx=(0, 10))
        bouton_historique.bind("<Button-1>", lambda _e: self._ouvrir_historique())

        bouton_parametres = tk.Label(
            barre_haut, text="⚙", font=("Sans", 16), bg=COULEUR_FOND,
            fg=COULEUR_TEXTE_ATT, cursor="hand2",
        )
        bouton_parametres.grid(row=0, column=2)
        bouton_parametres.bind("<Button-1>", lambda _e: self._ouvrir_parametres())

        tk.Label(
            self, text="Partage ta connexion Wi-Fi en un clic",
            font=("Sans", 10), bg=COULEUR_FOND, fg=COULEUR_TEXTE_ATT,
        ).pack(pady=(2, 20))

        carte = tk.Frame(
            self, bg=COULEUR_CARTE, highlightthickness=1,
            highlightbackground=COULEUR_BORDURE,
        )
        carte.pack(fill="x", padx=24)

        self.ssid_var = self._champ(
            carte, "Nom du reseau (SSID)", self.config_actuelle["ssid"],
            aide="Visible par les appareils a proximite.",
            bouton_copier=True,
        )
        self.mdp_var, self.entree_mdp = self._champ(
            carte, "Mot de passe", self.config_actuelle["password"],
            secret=True, bouton_copier=True, bouton_oeil=True,
            bouton_dado=True, retourne_entree=True,
        )
        self.label_aide_mdp = tk.Label(
            carte, text="8 caracteres minimum", font=("Sans", 8),
            bg=COULEUR_CARTE, fg=COULEUR_TEXTE_ATT, anchor="w",
        )
        self.label_aide_mdp.pack(fill="x", padx=16, pady=(0, 16))
        self.mdp_var.trace_add("write", self._verifier_mdp)
        self.entree_mdp.bind("<Return>", lambda _e: self._on_demarrer())

        self.bouton_action = tk.Button(
            self, text="Enregistrer et demarrer", command=self._on_demarrer,
            bg=COULEUR_ACCENT, fg="#06210f", font=("Sans", 11, "bold"),
            relief="flat", padx=12, pady=10, activebackground=COULEUR_ACCENT_SURVOL,
            cursor="hand2",
        )
        self.bouton_action.pack(fill="x", padx=24, pady=(20, 8))

        self.bouton_stop = tk.Button(
            self, text="Arreter le partage", command=self._on_arreter,
            bg=COULEUR_BOUTON_SECONDAIRE, fg=COULEUR_TEXTE, font=("Sans", 10),
            relief="flat", padx=12, pady=8,
            activebackground=COULEUR_BOUTON_SECONDAIRE_SURVOL, cursor="hand2",
        )
        self.bouton_stop.pack(fill="x", padx=24)

        self.bouton_qr = tk.Button(
            self, text="📱 QR code de connexion", command=self._ouvrir_qr,
            bg=COULEUR_BOUTON_SECONDAIRE, fg=COULEUR_TEXTE, font=("Sans", 10),
            relief="flat", padx=12, pady=8,
            activebackground=COULEUR_BOUTON_SECONDAIRE_SURVOL, cursor="hand2",
        )
        self.bouton_qr.pack(fill="x", padx=24, pady=(8, 0))

        self.barre_progression = ttk.Progressbar(self, mode="indeterminate")

        self.cadre_statut = tk.Frame(self, bg=COULEUR_FOND)
        self.cadre_statut.pack(fill="x", padx=24, pady=(24, 0))

        self.pastille = tk.Canvas(self.cadre_statut, width=10, height=10, bg=COULEUR_FOND, highlightthickness=0)
        self.pastille.pack(side="left", padx=(0, 8))
        self.pastille_id = self.pastille.create_oval(0, 0, 10, 10, fill="#6b7280", outline="")

        self.label_statut = tk.Label(
            self.cadre_statut, text="Verification...", font=("Sans", 10),
            bg=COULEUR_FOND, fg=COULEUR_TEXTE_ATT, anchor="w",
        )
        self.label_statut.pack(side="left", fill="x")

        self.cadre_appareils = tk.Frame(self, bg=COULEUR_FOND)
        self.cadre_appareils.pack(fill="x", padx=24, pady=(8, 0))

        self.label_message = tk.Label(
            self, text="", font=("Sans", 9), bg=COULEUR_FOND,
            fg=COULEUR_TEXTE_ATT, wraplength=400, justify="left",
        )
        self.label_message.pack(fill="x", padx=24, pady=(12, 0))

    def _champ(self, parent, libelle, valeur, secret=False, aide=None,
               bouton_copier=False, bouton_oeil=False, bouton_dado=False,
               retourne_entree=False):
        tk.Label(
            parent, text=libelle, font=("Sans", 9), bg=COULEUR_CARTE,
            fg=COULEUR_TEXTE_ATT, anchor="w",
        ).pack(fill="x", padx=16, pady=(16, 4))

        ligne = tk.Frame(parent, bg=COULEUR_CARTE)
        ligne.pack(fill="x", padx=16)

        variable = tk.StringVar(value=valeur)
        entree = tk.Entry(
            ligne, textvariable=variable, font=("Sans", 11),
            bg=COULEUR_CHAMP, fg=COULEUR_TEXTE, insertbackground=COULEUR_TEXTE,
            relief="flat", show="*" if secret else "",
        )
        entree.pack(side="left", fill="x", expand=True, ipady=6)

        if bouton_oeil:
            bouton = tk.Label(
                ligne, text="👁", font=("Sans", 11), bg=COULEUR_CARTE,
                fg=COULEUR_TEXTE_ATT, cursor="hand2", padx=8,
            )
            bouton.pack(side="left")
            bouton.bind("<Button-1>", lambda _e: self._basculer_visibilite_mdp(entree, bouton))
            self.bouton_oeil_mdp = bouton
        if bouton_dado:
            bouton = tk.Label(
                ligne, text="🎲", font=("Sans", 11), bg=COULEUR_CARTE,
                fg=COULEUR_TEXTE_ATT, cursor="hand2", padx=8,
            )
            bouton.pack(side="left")
            bouton.bind("<Button-1>", lambda _e: self._generer_mdp())
        if bouton_copier:
            bouton = tk.Label(
                ligne, text="📋", font=("Sans", 11), bg=COULEUR_CARTE,
                fg=COULEUR_TEXTE_ATT, cursor="hand2", padx=8,
            )
            bouton.pack(side="left")
            bouton.bind("<Button-1>", lambda _e: self._copier(variable.get(), bouton))

        if aide:
            tk.Label(
                parent, text=aide, font=("Sans", 8), bg=COULEUR_CARTE,
                fg=COULEUR_TEXTE_ATT, anchor="w",
            ).pack(fill="x", padx=16, pady=(4, 0))

        if retourne_entree:
            return variable, entree
        return variable

    def _basculer_visibilite_mdp(self, entree, bouton):
        self.afficher_mdp.set(not self.afficher_mdp.get())
        visible = self.afficher_mdp.get()
        entree.config(show="" if visible else "*")
        bouton.config(text="🙈" if visible else "👁")

    def _generer_mdp(self):
        alphabet = string.ascii_letters + string.digits
        mdp = "".join(secrets.choice(alphabet) for _ in range(14))
        self.mdp_var.set(mdp)
        self.afficher_mdp.set(True)
        self.entree_mdp.config(show="")
        self.bouton_oeil_mdp.config(text="🙈")

    def _copier(self, valeur, bouton):
        self.clipboard_clear()
        self.clipboard_append(valeur)
        texte_origine = bouton.cget("text")
        bouton.config(text="✓")
        self.after(1200, lambda: bouton.config(text=texte_origine))

    def _verifier_mdp(self, *_args):
        longueur = len(self.mdp_var.get())
        if longueur == 0:
            self.label_aide_mdp.config(text="8 caracteres minimum", fg=COULEUR_TEXTE_ATT)
        elif longueur < 8:
            manquants = 8 - longueur
            self.label_aide_mdp.config(
                text=f"Encore {manquants} caractere(s)", fg=COULEUR_ERREUR,
            )
        else:
            self.label_aide_mdp.config(text="✓ Longueur suffisante", fg=COULEUR_ACCENT)

    # -- QR code ---------------------------------------------------------
    def _ouvrir_qr(self):
        if not QR_DISPONIBLE:
            messagebox.showinfo("Optima", "Le module qrcode n'est pas installe.")
            return
        ssid = self.ssid_var.get().strip()
        mdp = self.mdp_var.get()
        if not ssid or len(mdp) < 8:
            messagebox.showerror(
                "Optima",
                "Renseigne un SSID et un mot de passe valides (8 caracteres "
                "minimum) avant de generer le QR code.",
            )
            return

        chemin = self._generer_image_qr(ssid, mdp)

        fenetre = tk.Toplevel(self, bg=COULEUR_FOND)
        fenetre.title("QR code de connexion")
        fenetre.configure(bg=COULEUR_FOND)
        fenetre.resizable(False, False)

        image = tk.PhotoImage(file=str(chemin))
        fenetre.image = image
        tk.Label(fenetre, image=image, bg=COULEUR_FOND).pack(padx=24, pady=(24, 12))
        tk.Label(
            fenetre, text=ssid, font=("Sans", 11, "bold"),
            bg=COULEUR_FOND, fg=COULEUR_TEXTE,
        ).pack()
        tk.Label(
            fenetre, text="Scanne ce code avec l'appareil a connecter.",
            font=("Sans", 9), bg=COULEUR_FOND, fg=COULEUR_TEXTE_ATT,
        ).pack(padx=24, pady=(4, 20))

    def _generer_image_qr(self, ssid, mdp):
        CACHE_DIR.mkdir(parents=True, exist_ok=True)
        chemin = CACHE_DIR / "qr.png"
        contenu = f"WIFI:T:WPA;S:{echapper_wifi(ssid)};P:{echapper_wifi(mdp)};;"
        image = qrcode.make(contenu, box_size=8, border=3)
        image.save(chemin)
        return chemin

    # -- parametres --------------------------------------------------------
    def _ouvrir_parametres(self):
        hauteur = "400x640" if est_admin() else "400x420"
        fenetre = tk.Toplevel(self, bg=COULEUR_FOND)
        fenetre.title("Parametres")
        fenetre.geometry(hauteur)
        fenetre.minsize(400, 420)
        fenetre.configure(bg=COULEUR_FOND)

        var_auto = tk.BooleanVar(value=self.config_actuelle.get("auto_start", False))
        var_max = tk.IntVar(value=self.config_actuelle.get("max_clients", 20))
        var_debit = tk.IntVar(value=self.config_actuelle.get("bandwidth_mbps", 0))

        tk.Label(
            fenetre, text="Parametres", font=("Sans", 14, "bold"),
            bg=COULEUR_FOND, fg=COULEUR_TEXTE,
        ).pack(pady=(20, 14))

        carte = tk.Frame(
            fenetre, bg=COULEUR_CARTE, highlightthickness=1,
            highlightbackground=COULEUR_BORDURE,
        )
        carte.pack(fill="x", padx=20)

        tk.Checkbutton(
            carte, text="Demarrer automatiquement a l'ouverture de session",
            variable=var_auto, bg=COULEUR_CARTE, fg=COULEUR_TEXTE,
            activebackground=COULEUR_CARTE, activeforeground=COULEUR_TEXTE,
            selectcolor=COULEUR_CHAMP, highlightthickness=0,
            wraplength=320, justify="left", anchor="w",
        ).pack(anchor="w", fill="x", padx=16, pady=(16, 14))

        cadre_max = tk.Frame(carte, bg=COULEUR_CARTE)
        cadre_max.pack(fill="x", padx=16, pady=(0, 14))
        tk.Label(
            cadre_max, text="Nombre max d'appareils", bg=COULEUR_CARTE, fg=COULEUR_TEXTE_ATT,
        ).pack(side="left")
        tk.Spinbox(
            cadre_max, from_=1, to=50, textvariable=var_max, width=5,
            bg=COULEUR_CHAMP, fg=COULEUR_TEXTE, insertbackground=COULEUR_TEXTE,
            buttonbackground=COULEUR_BOUTON_SECONDAIRE, relief="flat",
            highlightthickness=1, highlightbackground=COULEUR_BORDURE, justify="center",
        ).pack(side="right", ipady=3)

        cadre_debit = tk.Frame(carte, bg=COULEUR_CARTE)
        cadre_debit.pack(fill="x", padx=16, pady=(0, 16))
        tk.Label(
            cadre_debit, text="Limite de debit (Mbit/s, 0 = illimite)",
            bg=COULEUR_CARTE, fg=COULEUR_TEXTE_ATT, wraplength=200, justify="left", anchor="w",
        ).pack(side="left")
        tk.Spinbox(
            cadre_debit, from_=0, to=1000, increment=5, textvariable=var_debit, width=5,
            bg=COULEUR_CHAMP, fg=COULEUR_TEXTE, insertbackground=COULEUR_TEXTE,
            buttonbackground=COULEUR_BOUTON_SECONDAIRE, relief="flat",
            highlightthickness=1, highlightbackground=COULEUR_BORDURE, justify="center",
        ).pack(side="right", ipady=3)

        if not TRAY_DISPONIBLE:
            tk.Label(
                fenetre,
                text="(Sans icone de zone de notification, l'appli s'ouvrira\nvisible lors du demarrage automatique.)",
                font=("Sans", 8), bg=COULEUR_FOND, fg=COULEUR_TEXTE_ATT, justify="left",
            ).pack(padx=20, pady=(10, 0))

        if est_admin():
            self._construire_panneau_admin(fenetre)

        def enregistrer():
            self.config_actuelle["auto_start"] = var_auto.get()
            self.config_actuelle["max_clients"] = var_max.get()
            self.config_actuelle["bandwidth_mbps"] = var_debit.get()
            sauvegarder_config(self.config_actuelle)
            if var_auto.get():
                activer_autostart()
            else:
                desactiver_autostart()
            fenetre.destroy()

        tk.Button(
            fenetre, text="Enregistrer", command=enregistrer, bg=COULEUR_ACCENT,
            fg="#06210f", font=("Sans", 10, "bold"), relief="flat", pady=8,
            activebackground=COULEUR_ACCENT_SURVOL, cursor="hand2",
        ).pack(fill="x", padx=20, pady=(20, 8))
        tk.Button(
            fenetre, text="Annuler", command=fenetre.destroy,
            bg=COULEUR_BOUTON_SECONDAIRE, fg=COULEUR_TEXTE, relief="flat", pady=6,
            activebackground=COULEUR_BOUTON_SECONDAIRE_SURVOL, cursor="hand2",
        ).pack(fill="x", padx=20)

    def _construire_panneau_admin(self, fenetre):
        tk.Label(
            fenetre, text="🔑 Panneau admin (visible uniquement sur cette machine)",
            font=("Sans", 9, "bold"), bg=COULEUR_FOND, fg=COULEUR_ACCENT,
            wraplength=360, justify="left",
        ).pack(anchor="w", padx=20, pady=(24, 8))

        carte_admin = tk.Frame(
            fenetre, bg=COULEUR_CARTE, highlightthickness=1,
            highlightbackground=COULEUR_BORDURE,
        )
        carte_admin.pack(fill="x", padx=20)

        tk.Label(
            carte_admin, text="Genere une cle a donner a un ami pour debloquer son Optima.",
            font=("Sans", 8), bg=COULEUR_CARTE, fg=COULEUR_TEXTE_ATT,
            wraplength=340, justify="left",
        ).pack(anchor="w", padx=16, pady=(14, 10))

        ligne_cle = tk.Frame(carte_admin, bg=COULEUR_CARTE)
        ligne_cle.pack(fill="x", padx=16, pady=(0, 14))

        var_cle_generee = tk.StringVar(value="")
        entree_cle = tk.Entry(
            ligne_cle, textvariable=var_cle_generee, font=("Sans", 12, "bold"),
            justify="center", bg=COULEUR_CHAMP, fg=COULEUR_ACCENT,
            insertbackground=COULEUR_TEXTE, relief="flat", state="readonly",
            readonlybackground=COULEUR_CHAMP,
        )
        entree_cle.pack(side="left", fill="x", expand=True, ipady=8)

        bouton_copier_cle = tk.Label(
            ligne_cle, text="📋", font=("Sans", 11), bg=COULEUR_CARTE,
            fg=COULEUR_TEXTE_ATT, cursor="hand2", padx=8,
        )
        bouton_copier_cle.pack(side="left")
        bouton_copier_cle.bind(
            "<Button-1>", lambda _e: self._copier(var_cle_generee.get(), bouton_copier_cle),
        )

        def generer():
            var_cle_generee.set(generer_cle_licence())

        tk.Button(
            carte_admin, text="🎲 Generer une nouvelle cle", command=generer,
            bg=COULEUR_BOUTON_SECONDAIRE, fg=COULEUR_TEXTE, font=("Sans", 10),
            relief="flat", pady=8, activebackground=COULEUR_BOUTON_SECONDAIRE_SURVOL,
            cursor="hand2",
        ).pack(fill="x", padx=16, pady=(0, 16))

    # -- historique --------------------------------------------------------
    def _ouvrir_historique(self):
        fenetre = tk.Toplevel(self, bg=COULEUR_FOND)
        fenetre.title("Historique des connexions")
        fenetre.geometry("380x420")
        fenetre.configure(bg=COULEUR_FOND)

        tk.Label(
            fenetre, text="Historique des connexions", font=("Sans", 13, "bold"),
            bg=COULEUR_FOND, fg=COULEUR_TEXTE,
        ).pack(pady=(16, 8))

        cadre_liste = tk.Frame(fenetre, bg=COULEUR_FOND)
        cadre_liste.pack(fill="both", expand=True, padx=16, pady=(0, 16))

        defilement = ttk.Scrollbar(cadre_liste)
        defilement.pack(side="right", fill="y")
        zone = tk.Text(
            cadre_liste, bg=COULEUR_CARTE, fg=COULEUR_TEXTE, relief="flat",
            font=("Sans", 9), wrap="word", yscrollcommand=defilement.set,
        )
        zone.pack(side="left", fill="both", expand=True)
        defilement.config(command=zone.yview)

        historique = list(reversed(charger_historique()))
        if not historique:
            zone.insert("end", "Aucun evenement pour l'instant.")
        else:
            for entree in historique:
                verbe = "s'est connecte" if entree["evenement"] == "connecte" else "s'est deconnecte"
                zone.insert("end", f"{entree['heure']}  —  {entree['hostname']} ({entree['ip']}) {verbe}\n")
        zone.config(state="disabled")

    # -- actions -------------------------------------------------------
    def _on_demarrer(self):
        ssid = self.ssid_var.get().strip()
        mdp = self.mdp_var.get()

        if not ssid:
            messagebox.showerror("Optima", "Le nom du reseau ne peut pas etre vide.")
            return
        if len(mdp) < 8:
            messagebox.showerror("Optima", "Le mot de passe doit faire au moins 8 caracteres.")
            return

        self.config_actuelle["ssid"] = ssid
        self.config_actuelle["password"] = mdp
        sauvegarder_config(self.config_actuelle)

        max_clients = self.config_actuelle.get("max_clients", 20)
        bandwidth = self.config_actuelle.get("bandwidth_mbps", 0)
        self._executer_en_arriere_plan(
            lambda: executer_privilegie("start", ssid, mdp, str(max_clients), str(bandwidth)),
            "Demarrage du point d'acces...",
            "Point d'acces demarre.",
        )

    def _on_arreter(self):
        self._executer_en_arriere_plan(
            lambda: executer_privilegie("stop"),
            "Arret du point d'acces...",
            "Partage arrete.",
        )

    def _demarrage_auto(self):
        ssid = self.config_actuelle.get("ssid", "").strip()
        mdp = self.config_actuelle.get("password", "")
        if not ssid or len(mdp) < 8:
            return
        max_clients = self.config_actuelle.get("max_clients", 20)
        bandwidth = self.config_actuelle.get("bandwidth_mbps", 0)

        def tache():
            ok, sortie = False, ""
            for _ in range(24):
                ok, sortie = executer_privilegie("start", ssid, mdp, str(max_clients), str(bandwidth))
                if ok or "aucune connexion wi-fi active" not in sortie.lower():
                    break
                time.sleep(5)
            self.after(0, lambda: self._sur_resultat(ok, sortie, "Point d'acces demarre automatiquement."))

        threading.Thread(target=tache, daemon=True).start()

    def _executer_en_arriere_plan(self, action, message_attente, message_succes):
        self.bouton_action.config(state="disabled")
        self.bouton_stop.config(state="disabled")
        self.label_message.config(text=message_attente, fg=COULEUR_TEXTE_ATT)
        self.barre_progression.pack(fill="x", padx=24, pady=(4, 0))
        self.barre_progression.start(12)

        def tache():
            ok, sortie = action()
            self.after(0, lambda: self._sur_resultat(ok, sortie, message_succes))

        threading.Thread(target=tache, daemon=True).start()

    def _sur_resultat(self, ok, sortie, message_succes):
        self.barre_progression.stop()
        self.barre_progression.pack_forget()
        self.bouton_action.config(state="normal")
        self.bouton_stop.config(state="normal")
        if ok:
            self.label_message.config(text=f"✓ {message_succes}", fg=COULEUR_ACCENT)
            envoyer_notification("Optima", message_succes)
        else:
            texte = message_convivial(sortie)
            self.label_message.config(text=f"⚠ {texte}", fg=COULEUR_ERREUR)
            envoyer_notification("Optima — erreur", texte)
        self._recuperer_statut()

    def _rafraichir_statut(self):
        self._recuperer_statut()
        self.after(5000, self._rafraichir_statut)

    def _recuperer_statut(self):
        def tache():
            try:
                resultat = subprocess.run(
                    ["bash", str(HOTSPOT_SCRIPT), "status"],
                    capture_output=True, text=True, timeout=10,
                )
                etat = json.loads(resultat.stdout)
            except Exception:
                etat = {"running": False, "uplink": None, "clients": 0, "devices": []}
            self.after(0, lambda: self._appliquer_statut(etat))

        threading.Thread(target=tache, daemon=True).start()

    def _appliquer_statut(self, etat):
        actif = bool(etat.get("running"))
        self.dernier_etat_actif = actif
        if actif:
            self.pastille.itemconfig(self.pastille_id, fill=COULEUR_ACCENT)
            nb = etat.get("clients", 0)
            texte = f"Actif — {nb} appareil(s) connecte(s)"
            if etat.get("uplink"):
                texte += f" (via {etat['uplink']})"
            self.label_statut.config(text=texte)
        else:
            self.pastille.itemconfig(self.pastille_id, fill="#6b7280")
            self.label_statut.config(text="Arrete")

        appareils = etat.get("devices") or []
        self._traiter_diff_appareils(appareils)
        self._afficher_appareils(appareils)

        if TRAY_DISPONIBLE and self.icone_tray:
            self.icone_tray.icon = image_icone_tray(actif)
            self.icone_tray.title = f"Optima — {'actif' if actif else 'arrete'}"

    def _afficher_appareils(self, appareils):
        for enfant in self.cadre_appareils.winfo_children():
            enfant.destroy()
        if not appareils:
            return
        for appareil in appareils:
            ligne = tk.Frame(self.cadre_appareils, bg=COULEUR_FOND)
            ligne.pack(fill="x", pady=1)
            tk.Label(
                ligne, text=f"  • {appareil.get('hostname', 'Appareil inconnu')}",
                font=("Sans", 9), bg=COULEUR_FOND, fg=COULEUR_TEXTE_ATT, anchor="w",
            ).pack(side="left")
            tk.Label(
                ligne, text=appareil.get("ip", ""), font=("Sans", 9),
                bg=COULEUR_FOND, fg=COULEUR_TEXTE_ATT, anchor="e",
            ).pack(side="right")

    def _traiter_diff_appareils(self, appareils):
        actuels = {a["mac"]: a for a in appareils if a.get("mac")}
        if self.appareils_precedents is None:
            self.appareils_precedents = actuels
            return
        macs_avant = set(self.appareils_precedents)
        macs_maintenant = set(actuels)
        for mac in macs_maintenant - macs_avant:
            self._journaliser_evenement("connecte", actuels[mac])
        for mac in macs_avant - macs_maintenant:
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
        verbe = "s'est connecte" if evenement == "connecte" else "s'est deconnecte"
        envoyer_notification("Optima", f"{entree['hostname']} {verbe}")

    # -- zone de notification (tray) ----------------------------------
    def _configurer_tray(self):
        if not TRAY_DISPONIBLE:
            return
        self.icone_tray = pystray.Icon(
            "optima",
            image_icone_tray(False),
            "Optima — arrete",
            menu=pystray.Menu(
                pystray.MenuItem("Ouvrir Optima", self._tray_ouvrir, default=True),
                pystray.MenuItem(self._texte_bouton_tray, self._tray_basculer),
                pystray.Menu.SEPARATOR,
                pystray.MenuItem("Quitter", self._tray_quitter),
            ),
        )
        threading.Thread(target=self.icone_tray.run, daemon=True).start()
        self.protocol("WM_DELETE_WINDOW", self._on_fermeture)

    def _texte_bouton_tray(self, _item):
        return "Arreter le partage" if self.dernier_etat_actif else "Demarrer le partage"

    def _tray_ouvrir(self, _icon=None, _item=None):
        self.after(0, self._afficher_fenetre)

    def _afficher_fenetre(self):
        self.deiconify()
        self.lift()
        self.focus_force()

    def _tray_basculer(self, _icon=None, _item=None):
        self.after(0, self._on_arreter if self.dernier_etat_actif else self._on_demarrer)

    def _tray_quitter(self, _icon=None, _item=None):
        self.after(0, self._quitter)

    def _quitter(self):
        if TRAY_DISPONIBLE and self.icone_tray:
            self.icone_tray.stop()
        self.destroy()

    def _on_fermeture(self):
        if TRAY_DISPONIBLE:
            if not self._averti_reduction:
                envoyer_notification("Optima", "L'appli continue de tourner dans la zone de notification.")
                self._averti_reduction = True
            self.withdraw()
        else:
            self._quitter()


class FenetreActivation(tk.Tk):
    def __init__(self, sur_succes):
        super().__init__(className="Optima")
        self.sur_succes = sur_succes
        self.title("Optima — Activation")
        self.geometry("420x320")
        self.minsize(420, 320)
        self.configure(bg=COULEUR_FOND)
        try:
            self._image_icone = tk.PhotoImage(file=str(ICONE_APP))
            self.iconphoto(True, self._image_icone)
        except tk.TclError:
            pass

        tk.Label(
            self, text="📶 Optima", font=("Sans", 20, "bold"),
            bg=COULEUR_FOND, fg=COULEUR_TEXTE,
        ).pack(pady=(28, 6))
        tk.Label(
            self, text="Ce logiciel necessite une cle d'activation.\n"
                        "Demande-la a la personne qui te l'a partage.",
            font=("Sans", 10), bg=COULEUR_FOND, fg=COULEUR_TEXTE_ATT,
            justify="center",
        ).pack(pady=(0, 20))

        carte = tk.Frame(
            self, bg=COULEUR_CARTE, highlightthickness=1,
            highlightbackground=COULEUR_BORDURE,
        )
        carte.pack(fill="x", padx=32)

        self.var_cle = tk.StringVar()
        entree = tk.Entry(
            carte, textvariable=self.var_cle, font=("Sans", 13), justify="center",
            bg=COULEUR_CHAMP, fg=COULEUR_TEXTE, insertbackground=COULEUR_TEXTE,
            relief="flat",
        )
        entree.pack(fill="x", padx=16, pady=16, ipady=8)
        entree.bind("<Return>", lambda _e: self._verifier())
        entree.focus_set()

        self.label_erreur = tk.Label(
            self, text="", font=("Sans", 9), bg=COULEUR_FOND, fg=COULEUR_ERREUR,
        )
        self.label_erreur.pack(pady=(12, 0))

        tk.Button(
            self, text="Activer", command=self._verifier, bg=COULEUR_ACCENT,
            fg="#06210f", font=("Sans", 11, "bold"), relief="flat", pady=10,
            activebackground=COULEUR_ACCENT_SURVOL, cursor="hand2",
        ).pack(fill="x", padx=32, pady=(20, 0))

    def _verifier(self):
        cle = self.var_cle.get()
        if valider_cle_licence(cle):
            activer_licence(cle)
            self.destroy()
            self.sur_succes()
        else:
            self.label_erreur.config(text="Cle invalide. Verifie et reessaie.")


def _lancer():
    mode_auto = "--auto" in sys.argv
    OptimaApp(mode_auto=mode_auto).mainloop()


if __name__ == "__main__":
    if est_active():
        _lancer()
    else:
        FenetreActivation(sur_succes=_lancer).mainloop()
