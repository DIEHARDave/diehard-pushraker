#!/bin/bash
# diehard-pushraker on the FLSUN T1 Pro: keeps pushraker (the copy in the
# plugin's python/) wired into Moonraker and Klipper, with its settings page
# served at http://<printer-ip>/notifications/.
#
# Runs as root: from install.sh, at boot, and when Moonraker restarts the
# diehard-pushraker service after updating the plugin. Safe to run any
# time; it only restarts what changed (Klipper only when no print is
# running).
#
#   --restart-moonraker   restart Moonraker even if nothing here changed
#                         (install.sh: Moonraker has a new update entry)

set -e

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
USER_NAME="pi"
HOME_DIR="/home/${USER_NAME}"
VENV="${HOME_DIR}/moonraker-env"
PY="${VENV}/bin/python"
PKG="${REPO_DIR}/python"
# The name FLSUN_Updater's Fluidd site includes (flsun-pushraker*.conf)
SNIPPET="/etc/nginx/snippets/flsun-pushraker.conf"
MOONRAKER_URL="http://127.0.0.1:7125"
COMPONENTS_DIR="${HOME_DIR}/moonraker/moonraker/components"
NAME="FLSUN T1 Pro"

msg() { printf '\033[1;32m==>\033[0m %s\n' "$*"; }
warn() { printf '\033[1;33mWARNING:\033[0m %s\n' "$*"; }
die() { printf '\033[1;31mERROR:\033[0m %s\n' "$*" >&2; exit 1; }
as_user() { runuser -u "$USER_NAME" -- "$@"; }

[ "$(id -u)" -eq 0 ] || die "run as root (install.sh does this for you)"
[ -x "$PY" ] || die "Moonraker's Python not found at $PY"
"$PY" -c 'import sys; sys.exit(sys.version_info < (3, 9))' \
    || die "pushraker needs Python 3.9 or newer; FLSun's Moonraker has 3.7. Install FLSUN_moonraker first (https://github.com/DIEHARDave/FLSUN_moonraker)."

# Moonraker's JSON API, read with the system Python.
query() {
    curl -s -m 5 "${MOONRAKER_URL}$1" | python3 -c "$2" 2> /dev/null || true
}
print_state() {
    query "/printer/objects/query?print_stats=state" \
        'import sys, json; print(json.load(sys.stdin)["result"]["status"]["print_stats"]["state"])'
}
# After an update, Moonraker restarts this service and then finishes its
# own bookkeeping; restarting Moonraker before then would cut it off.
wait_update_manager() {
    for _ in $(seq 60); do
        busy=$(query "/machine/update/status" \
            'import sys, json; print(json.load(sys.stdin)["result"].get("busy", False))')
        [ "$busy" = "True" ] || return 0
        sleep 2
    done
}

restart_moonraker=0
restart_klipper=0
for arg in "$@"; do
    case "$arg" in
        --restart-moonraker) restart_moonraker=1 ;;
        *) die "unknown option: $arg" ;;
    esac
done

# A pip-installed pushraker (FLSUN_pushraker's way) would load instead of
# the plugin's copy
if as_user "$PY" -c 'import importlib.metadata as m; m.version("pushraker")' 2> /dev/null; then
    msg "Removing the pip-installed pushraker (the plugin brings its own)"
    as_user "${VENV}/bin/pip" uninstall --disable-pip-version-check -y -q pushraker
    restart_moonraker=1
fi

msg "Setting up pushraker"
# pushraker's own installer, from the plugin's copy: the component shim
# (which finds python/), [pushraker] in moonraker.conf, and the Klipper
# macros (pushraker.cfg, included from printer.cfg; FLSUN_klipper copies the
# include for upstream Klipper). Only writes what differs.
out=$(cd "$HOME_DIR" && as_user env PYTHONPATH="$PKG" "$PY" -m pushraker install \
    --name "$NAME" --site-packages "$PKG")
echo "$out"
if echo "$out" | grep -q '^Updated: .*\(components/\|moonraker\.conf\|\[pushraker\]\|push_notifications\)'; then
    restart_moonraker=1
fi
if echo "$out" | grep -q '^Updated: .*\.cfg$'; then
    restart_klipper=1
fi

msg "Linking the Discord card and update notice components"
COMPONENTS="pushraker_cards pushraker_updates"
for comp in $COMPONENTS; do
    COMPONENT="${COMPONENTS_DIR}/${comp}.py"
    if [ "$(readlink "$COMPONENT" 2> /dev/null)" != "${REPO_DIR}/moonraker/components/${comp}.py" ]; then
        as_user ln -sfn "${REPO_DIR}/moonraker/components/${comp}.py" "$COMPONENT"
        restart_moonraker=1
    fi
done
# FLSUN_pushraker's card component, if its uninstaller didn't run
if [ -L "${COMPONENTS_DIR}/flsun_pushraker.py" ]; then
    rm -f "${COMPONENTS_DIR}/flsun_pushraker.py"
    restart_moonraker=1
fi
# Keep Moonraker's update manager from reporting the links (and
# pushraker's own component) as untracked files in its repo
EXCLUDE="${HOME_DIR}/moonraker/.git/info/exclude"
if [ -d "${HOME_DIR}/moonraker/.git" ]; then
    as_user mkdir -p "$(dirname "$EXCLUDE")"
    as_user touch "$EXCLUDE"
    for comp in $COMPONENTS pushraker; do
        grep -qxF "/moonraker/components/${comp}.py" "$EXCLUDE" ||
            echo "/moonraker/components/${comp}.py" >> "$EXCLUDE"
    done
fi
CONF="${HOME_DIR}/printer_data/config/diehard-pushraker.conf"
TEMPLATE="${REPO_DIR}/platform/flsun/diehard-pushraker.conf"
# Sections added in later versions go to the end of an existing copy (the
# template lists them in that order)
for section in pushraker_cards pushraker_updates; do
    if [ -f "$CONF" ] && ! grep -q "^\[${section}\]" "$CONF"; then
        printf '\n%s\n' "$(sed -n "/^\[${section}\]/,\$p" "$TEMPLATE")" >> "$CONF"
        restart_moonraker=1
    fi
done
# An update changes the code, but Moonraker only restarts this service; it
# loads the new code when restarted.
STAMP="${HOME_DIR}/printer_data/.diehard_pushraker_code"
sum=$( (cd "${REPO_DIR}/moonraker/components" && cat pushraker_cards.py pushraker_updates.py; ls "$PKG") | sha256sum | cut -d' ' -f1)
if [ "$(cat "$STAMP" 2> /dev/null)" != "$sum" ]; then
    restart_moonraker=1
fi

WEB="${PKG}/pushraker/web"
msg "Serving the settings page at /notifications/"
mkdir -p /etc/nginx/snippets
snippet_text="# diehard-pushraker: pushraker's settings page. Written by platform/flsun/sync.sh.
location /notifications/ {
    alias ${WEB}/;
    index index.html;
}
# The confirm page behind the Discord update notice's link buttons
location /pushraker-updates/ {
    alias ${REPO_DIR}/web/;
    index index.html;
}"
reload_nginx=0
if [ "$(cat "$SNIPPET" 2> /dev/null)" != "$snippet_text" ]; then
    printf '%s\n' "$snippet_text" > "$SNIPPET"
    reload_nginx=1
fi
backups=()
for link in /etc/nginx/sites-enabled/*; do
    site=$(readlink -f "$link")
    [ -f "$site" ] || continue
    grep -q 'server_name' "$site" || continue
    # FLSUN_Updater's Fluidd site includes flsun-pushraker*.conf already.
    grep -qF 'flsun-pushraker' "$site" && continue
    cp "$site" "${site}.diehard_pushraker.bak"
    backups+=("$site")
    # One server block per site (Mainsail's, Fluidd's); add it inside.
    sed -i "0,/^\([[:space:]]*\)server_name .*;/s||&\n\1include ${SNIPPET};|" "$site"
    reload_nginx=1
done
if [ "$reload_nginx" = 1 ]; then
    if ! nginx -t > /dev/null 2>&1; then
        for site in "${backups[@]}"; do
            mv "${site}.diehard_pushraker.bak" "$site"
        done
        nginx -t || true
        die "nginx rejected the configuration; your sites were restored"
    fi
    for site in "${backups[@]}"; do
        rm -f "${site}.diehard_pushraker.bak"
    done
    systemctl reload nginx
fi

if [ "$restart_moonraker" = 1 ]; then
    wait_update_manager
    msg "Restarting Moonraker"
    systemctl restart moonraker
    # Klipper's state comes from Moonraker; give it time to connect.
    for _ in $(seq 30); do
        curl -sf -m 2 "${MOONRAKER_URL}/server/info" > /dev/null && break
        sleep 2
    done
    sleep 5
fi
# Moonraker runs this version of the code now
printf '%s\n' "$sum" > "$STAMP"
if [ "$restart_klipper" = 1 ]; then
    state=$(print_state)
    case "$state" in
        printing|paused)
            warn "a print is ${state}; the pushraker macros load at Klipper's next restart" ;;
        *)
            msg "Restarting Klipper"
            # A service restart, not RESTART: FLSUN_klipper copies printer.cfg's
            # includes for upstream Klipper when the service starts.
            systemctl restart klipper ;;
    esac
fi
msg "pushraker $(PYTHONPATH="$PKG" "$PY" -c 'import pushraker; print(pushraker.__version__)') with diehard-pushraker is ready: http://<printer-ip>/notifications/"
