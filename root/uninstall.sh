#!/bin/sh
# diehard-pushraker: undo install.sh. Kiln runs it (root's copy) at
# DISABLE_PLUGIN / REMOVE_PLUGIN. The notification settings
# (printer_data/config/pushraker.conf, printer_data/pushraker.secrets)
# stay, for when the plugin comes back.
rm -f /data/ad5x/nginx/diehard-pushraker.conf /data/ad5x/nginx/diehard-pushraker.conf.old \
	/data/ad5x/nginx/port81.d/diehard-pushraker.conf /data/ad5x/nginx/port81.d/diehard-pushraker.conf.old
nginx -t >/dev/null 2>&1 && nginx -s reload
echo "diehard-pushraker: settings page removed (port 81 and 82)"
exit 0
