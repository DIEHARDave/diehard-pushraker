#!/bin/sh
# diehard-pushraker: install on the printer this runs on.
#   Zmod (AD5X, Creator 5): Zmod's ENABLE_PLUGIN name=diehard-pushraker runs this
#                           -> platform/zmod/install.sh
#   FLSUN T1 Pro:           ~/diehard-pushraker/install.sh as pi (FLSUN_Updater
#                           runs platform/flsun/install.sh itself)
#   Kiln OS:                not used: ENABLE_PLUGIN NAME=diehard-pushraker ROOT=1
#                           (plugin.json, plugin.cfg, root/)
DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd -P)
if [ -d /opt/config/mod_data ]; then
    exec /bin/sh "$DIR/platform/zmod/install.sh" "$@"
fi
if [ -d /data/ad5x/plugins ]; then
    echo "diehard-pushraker: on Kiln OS, use ENABLE_PLUGIN NAME=diehard-pushraker ROOT=1 in the console" >&2
    exit 1
fi
if [ -d /home/pi/printer_data ] && command -v systemctl >/dev/null 2>&1; then
    exec /bin/bash "$DIR/platform/flsun/install.sh" "$@"
fi
echo "diehard-pushraker: unknown printer (Zmod, Kiln OS and the FLSUN T1 are supported)" >&2
exit 1
