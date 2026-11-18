#!/bin/bash
# diehard-pushraker installer for the FLSun T1 Pro. FLSUN_Updater runs it
# (flsun_install: platform/flsun/install.sh), or by hand as the pi user:
#   ~/diehard-pushraker/install.sh
# Safe to re-run, e.g. after a FLSun firmware update.

set -e

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
CONFIG_DIR="${HOME}/printer_data/config"
MOONRAKER_CONF="${CONFIG_DIR}/moonraker.conf"
SERVICE="diehard-pushraker"

msg() { printf '\033[1;32m==>\033[0m %s\n' "$*"; }
die() { printf '\033[1;31mERROR:\033[0m %s\n' "$*" >&2; exit 1; }

[ "$(id -u)" -ne 0 ] || die "run as the pi user, not root (sudo is used where needed)"
# The service points at this path.
[ "$REPO_DIR" = "${HOME}/diehard-pushraker" ] \
    || die "clone the repo to ${HOME}/diehard-pushraker (it is at $REPO_DIR)"
[ -f "$MOONRAKER_CONF" ] || die "$MOONRAKER_CONF not found"
[ -d /etc/nginx/sites-enabled ] || die "nginx not found"
"${HOME}/moonraker-env/bin/python" -c 'import sys; sys.exit(sys.version_info < (3, 9))' 2> /dev/null \
    || die "pushraker needs Python 3.9 or newer; FLSun's Moonraker has 3.7. Install FLSUN_moonraker first (https://github.com/DIEHARDave/FLSUN_moonraker)."
[ ! -d "${HOME}/FLSUN_pushraker" ] || ! systemctl is-enabled --quiet flsun_pushraker 2> /dev/null \
    || die "FLSUN_pushraker is still installed: turn it off first (flsun_pushraker: no in flsun_plugins.cfg, or ~/FLSUN_pushraker/uninstall.sh)"

chmod +x "${REPO_DIR}/platform/flsun/sync.sh"

msg "Adding the Moonraker settings and update manager entry"
if [ ! -f "${CONFIG_DIR}/diehard-pushraker.conf" ]; then
    cp "${REPO_DIR}/platform/flsun/diehard-pushraker.conf" "${CONFIG_DIR}/diehard-pushraker.conf"
fi
if ! grep -q '^\[include diehard-pushraker\.conf\]' "$MOONRAKER_CONF"; then
    cp "$MOONRAKER_CONF" "${MOONRAKER_CONF}.diehard_pushraker.bak"
    printf '\n[include diehard-pushraker.conf]\n' >> "$MOONRAKER_CONF"
fi
# Moonraker may only restart services listed here.
ASVC="${HOME}/printer_data/moonraker.asvc"
if [ -f "$ASVC" ] && ! grep -qx "$SERVICE" "$ASVC"; then
    [ -z "$(tail -c1 "$ASVC")" ] || echo >> "$ASVC"
    echo "$SERVICE" >> "$ASVC"
fi

msg "Installing the service"
sudo install -m 644 "${REPO_DIR}/platform/flsun/${SERVICE}.service" "/etc/systemd/system/${SERVICE}.service"
sudo systemctl daemon-reload
sudo systemctl enable "${SERVICE}.service" > /dev/null 2>&1

# The first sync runs here, so its output shows. Moonraker restarts either
# way (the update manager entry is new to it); Klipper restarts if its
# macros changed and no print is running.
sudo "${REPO_DIR}/platform/flsun/sync.sh" --restart-moonraker

msg "Done. Open http://<printer-ip>/notifications/ to set up notifications."
