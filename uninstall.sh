#!/bin/sh
# diehard-pushraker: uninstall on the printer this runs on.
#   Zmod (AD5X, Creator 5): Zmod's DISABLE_PLUGIN name=diehard-pushraker runs this
#                           -> platform/zmod/uninstall.sh
#   FLSUN T1 Pro:           ~/diehard-pushraker/uninstall.sh as pi (FLSUN_Updater
#                           runs platform/flsun/uninstall.sh itself)
#   Kiln OS:                not used: DISABLE_PLUGIN NAME=diehard-pushraker
#                           (plugin.json, plugin.cfg, root/)
DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd -P)
if [ -d /opt/config/mod_data ]; then
    exec /bin/sh "$DIR/platform/zmod/uninstall.sh" "$@"
fi
if [ -d /data/ad5x/plugins ]; then
    echo "diehard-pushraker: on Kiln OS, use DISABLE_PLUGIN NAME=diehard-pushraker in the console" >&2
    exit 1
fi
if [ -d /home/pi/printer_data ] && command -v systemctl >/dev/null 2>&1; then
    exec /bin/bash "$DIR/platform/flsun/uninstall.sh" "$@"
fi
echo "diehard-pushraker: unknown printer (Zmod, Kiln OS and the FLSUN T1 are supported)" >&2
exit 1
