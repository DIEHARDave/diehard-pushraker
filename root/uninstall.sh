#!/bin/sh
# diehard-pushraker: undo install.sh. Kiln runs it (root's copy) at
# DISABLE_PLUGIN / REMOVE_PLUGIN. The notification settings
# (printer_data/config/pushraker.conf, printer_data/pushraker.secrets)
# stay, for when the plugin comes back.
rm -f /data/ad5x/nginx/diehard-pushraker.conf /data/ad5x/nginx/diehard-pushraker.conf.old \
	/data/ad5x/nginx/port81.d/diehard-pushraker.conf /data/ad5x/nginx/port81.d/diehard-pushraker.conf.old
nginx -t >/dev/null 2>&1 && nginx -s reload
# Mainsail's sidebar link (config/.theme/navi.json)
COMP=${PLUGIN_DIR:-/data/ad5x/plugins/diehard-pushraker}/moonraker/components/pushraker_updates.py
[ -f "$COMP" ] && "python3" -I -c 'import importlib.util as u, sys; s = u.spec_from_file_location("p", sys.argv[1]); m = u.module_from_spec(s); s.loader.exec_module(m); print(m.sidebar_entry(sys.argv[2], False))' "$COMP" "/home/klipper/printer_data/config" 2>/dev/null || true
echo "diehard-pushraker: settings page removed (port 81 and 82)"
exit 0
