#!/bin/sh
# diehard-pushraker on Zmod - run by Zmod's DISABLE_PLUGIN
# name=diehard-pushraker through the plugin's uninstall.sh.
# Removes install.sh's links and include and stops the settings page. The
# notification settings (printer_data/config/pushraker.conf and
# printer_data/pushraker.secrets) stay, for a later reinstall.

[ -f /opt/config/mod/.shell/0.sh ] && . /opt/config/mod/.shell/0.sh
[ -f /usr/data/zmod/zmod/.shell/0.sh ] && . /usr/data/zmod/zmod/.shell/0.sh

set -u

NAME=diehard-pushraker
CHROOT_PLUGIN_DIR="/opt/config/mod_data/plugins/$NAME"
# This script is platform/zmod/<name>.sh in the plugin
PLUGIN_DIR=$(CDPATH= cd -- "$(dirname -- "$0")/../.." && pwd -P)
CONFIG_DIR=$(CDPATH= cd -- "$PLUGIN_DIR/../../.." && pwd -P)

# ---- host: Klipper ---------------------------------------------------------
if [ ! -f /ZMOD ] && [ -z "${ZMOD_PUSHRAKER_CHROOT:-}" ]; then
    for EXTRAS_DIR in \
        /usr/prog/klipper/klippy/extras \
        /usr/data/zmod/klipper/klippy/extras \
        ${KLIPPER_DIR:+"$KLIPPER_DIR/klippy/extras"}
    do
        TARGET="$EXTRAS_DIR/pushraker_hooks.py"
        [ -L "$TARGET" ] || continue
        case "$(readlink "$TARGET")" in
            "$PLUGIN_DIR"/*|*/plugins/$NAME/*) rm -f "$TARGET" ;;
        esac
    done
    if [ -n "${MOD:-}" ] && [ -d "$MOD" ]; then
        unset LD_LIBRARY_PATH LD_PRELOAD
        exec chroot "$MOD" /bin/sh -c \
            "ZMOD_PUSHRAKER_CHROOT=1 $CHROOT_PLUGIN_DIR/platform/zmod/uninstall.sh"
    fi
fi

# ---- chroot: Moonraker -----------------------------------------------------
PUSHRAKER_WEB_CHROOT=1 /bin/sh "$CHROOT_PLUGIN_DIR/platform/zmod/web.sh" stop
# Mainsail's sidebar link (config/.theme/navi.json)
PY=/root/moonraker-env/bin/python3
[ -x "$PY" ] || PY=python3
"$PY" -I -c 'import importlib.util as u, sys; s = u.spec_from_file_location("p", sys.argv[1]); m = u.module_from_spec(s); s.loader.exec_module(m); print(m.sidebar_entry(sys.argv[2], False))' "$CHROOT_PLUGIN_DIR/moonraker/components/pushraker_updates.py" "$CONFIG_DIR" 2>/dev/null || true
# The pages on zmod_dualweb's port 81
if [ -f /opt/config/mod_data/dualweb.d/diehard-pushraker.conf ]; then
    rm -f /opt/config/mod_data/dualweb.d/diehard-pushraker.conf
    [ -f /opt/config/mod_data/plugins/zmod_dualweb/dualweb.sh ] &&
        DUALWEB_CHROOT=1 /bin/sh /opt/config/mod_data/plugins/zmod_dualweb/dualweb.sh restart >/dev/null 2>&1
fi

FILE="$CONFIG_DIR/mod_data/plugins.moonraker.conf"
[ -f "$FILE" ] && sed -i "\|plugins/$NAME/$NAME\.moonraker\.conf|d" "$FILE"

MOON_DIR=
for dir in ${DATA:+"$DATA/zmod/moonraker"} /usr/data/zmod/moonraker \
    /data/zmod/moonraker /root/printer_data/config/base/moonraker \
    "$CONFIG_DIR/base/moonraker"; do
    if [ -d "$dir/components" ]; then
        MOON_DIR=$dir
        break
    fi
done
if [ -n "$MOON_DIR" ]; then
    for COMP in pushraker pushraker_cards pushraker_updates; do
        TARGET="$MOON_DIR/components/$COMP.py"
        if [ -L "$TARGET" ]; then
            case "$(readlink "$TARGET")" in
                "$CHROOT_PLUGIN_DIR"/*) rm "$TARGET" ;;
            esac
        fi
        EXCLUDE="$MOON_DIR/.git/info/exclude"
        [ -f "$EXCLUDE" ] && sed -i "\|^/components/$COMP\.py\$|d" "$EXCLUDE"
    done
fi

echo "$NAME uninstalled"
echo "REBOOT" >/tmp/printer
