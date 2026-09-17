# Optima

Desktop application (Linux/GNOME, Python + Tkinter) that turns your Wi-Fi card into an access point and shares your internet connection with other devices — useful when your network is behind a captive portal and you don't have a Linux equivalent to tools like Connectify.

## Features

- Wi-Fi access point (hostapd + dnsmasq + iptables NAT) controlled from a simple graphical interface
- TTL normalization on shared traffic
- Connection QR code, password generation, quick copy/paste
- Tray icon, system notifications, connected devices history
- Device count and shared bandwidth limits
- Automatic startup on login
- Optional licensing system (see below), disabled by default

## Requirements

- Ubuntu/Debian with GNOME (tested on recent Ubuntu, Wayland)
- A Wi-Fi card whose driver supports creating a virtual interface in AP mode (`iw list` → check for AP mode in the supported combinations)
- python3 + python3-tk
- hostapd, dnsmasq, policykit-1 (for pkexec), iw, iptables, network-manager

Optional (features degrade gracefully if missing):

- pillow (icons, QR code) — often already present on Ubuntu
- qrcode (`pip install --user qrcode`) for the connection QR code
- pystray (`pip install --user pystray`) for the tray icon (requires `gir1.2-ayatanaappindicator3-0.1` on Ubuntu/GNOME)

## Installation

```bash
git clone <url-of-this-repo>
cd optima
./install.sh
```

The script installs any missing system dependencies (apt, will prompt for your password), copies the application to `~/.local/share/optima`, and creates a launcher (`optima`) and an entry in the applications menu.

## How it works

Optima creates a virtual `ap0` interface alongside your main Wi-Fi card (most card drivers don't support simultaneous client + access point mode on a single interface). hostapd manages the access point on `ap0`, dnsmasq hands out IP addresses, and iptables rules perform NAT to your main connection.

Known limitation: on some cards/drivers, hostapd can crash reproducibly after many quick start/stop cycles (degraded driver state). Rebooting the machine resolves this if it happens.

## Licensing system (optional)

By default, there are no restrictions: the application is fully usable right after installation. If you want to distribute a compiled binary to third parties while requiring an activation key that only you can generate:

```bash
# 1. Generate a secret that you keep private (never commit it)
python3 -c "import secrets; print(secrets.token_hex(32))"

# 2. Export it before running/building the application
export OPTIMA_LICENSE_SECRET=<the_secret>

# 3. Create this file on your own machine to unlock the admin panel
#    (key generation) in Settings:
mkdir -p ~/.config/optima && touch ~/.config/optima/.admin
```

Without `OPTIMA_LICENSE_SECRET`, this mechanism is entirely disabled (no activation screen, no admin panel). This is intentional: this public repository does not contain any embedded secret.

Note: this is plain Python, not a robust anti-piracy system — it filters out occasional copying, not someone who reads the source code.

## License

This project is licensed under the MIT License — see the [LICENSE](LICENSE) file for details.
