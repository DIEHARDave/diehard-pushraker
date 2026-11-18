#!/bin/bash
# Removes diehard-pushraker from the FLSun T1 Pro. Your notification
# settings (pushraker.conf and pushraker.secrets) are kept, so reinstalling
# picks them up again; delete them yourself if you don't need them.

set -e

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
CONFIG_DIR="${HOME}/printer_data/config"
MOONRAKER_CONF="${CONFIG_DIR}/moonraker.conf"
VENV="${HOME}/moonraker-env"
SERVICE="diehard-pushraker"
SNIPPET="/etc/nginx/snippets/flsun-pushraker.conf"
COMPONENTS_DIR="${HOME}/moonraker/moonraker/components"

msg() { printf '\033[1;32m==>\033[0m %s\n' "$*"; }

[ "$(id -u)" -ne 0 ] || { echo "run as the pi user, not root" >&2; exit 1; }

msg "Removing the service"
sudo systemctl disable --now "${SERVICE}.service" > /dev/null 2>&1 || true
sudo rm -f "/etc/systemd/system/${SERVICE}.service"
sudo systemctl daemon-reload
ASVC="${HOME}/printer_data/moonraker.asvc"
[ -f "$ASVC" ] && sed -i "/^${SERVICE}\$/d" "$ASVC"

msg "Removing /notifications/ from the web server"
for link in /etc/nginx/sites-enabled/*; do
    site=$(readlink -f "$link")
    [ -f "$site" ] || continue
    sudo sed -i "\|include ${SNIPPET};|d" "$site"
done
sudo rm -f "$SNIPPET"
sudo nginx -t > /dev/null 2>&1 && sudo systemctl reload nginx

msg "Removing pushraker (component shim, [pushraker], Klipper macros)"
if [ -x "${VENV}/bin/python" ]; then
    (cd "$HOME" && PYTHONPATH="${REPO_DIR}/python" "${VENV}/bin/python" -m pushraker uninstall) || true
fi

msg "Removing the Discord card and update notice components"
for comp in pushraker_cards pushraker_updates; do
    [ -L "${COMPONENTS_DIR}/${comp}.py" ] && rm -f "${COMPONENTS_DIR}/${comp}.py"
done
rm -f "${HOME}/printer_data/.diehard_pushraker_code" \
    "${HOME}/printer_data/.pushraker_updates.json"

msg "Removing the Moonraker settings"
sed -i '/^\[include diehard-pushraker\.conf\]/d' "$MOONRAKER_CONF"
rm -f "${CONFIG_DIR}/diehard-pushraker.conf"

msg "Restarting Moonraker and Klipper"
sudo systemctl restart moonraker
state=$(curl -s -m 5 "http://127.0.0.1:7125/printer/objects/query?print_stats=state" \
    | sed -n 's/.*"state": *"\([a-z]*\)".*/\1/p')
case "$state" in
    printing|paused) echo "A print is $state; Klipper drops the pushraker macros at its next restart." ;;
    *) sudo systemctl restart klipper ;;
esac

msg "Done. Delete ~/diehard-pushraker to finish."
