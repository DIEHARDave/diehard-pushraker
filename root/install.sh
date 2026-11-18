#!/bin/sh
# diehard-pushraker: root's part, Kiln OS plugin API 1.6. Kiln runs it as
# root from /data/ad5x/plugins-root/diehard-pushraker (root's copy of root/) at
#   ENABLE_PLUGIN NAME=diehard-pushraker ROOT=1
# and, as update.sh, after each update that changes root/.
#
# It serves pushraker's settings page (and /pushraker-updates/, the update
# notice's confirm page), with the page from the plugin's
# python/pushraker/web:
#   - on port 81, inside mainsail-kiln's Fluidd/Mainsail server: a snippet
#     in /data/ad5x/nginx/port81.d, which that server includes (it already
#     has the Moonraker API and webcam locations the page uses). The
#     snippet is written even when mainsail-kiln is older or missing, so a
#     later mainsail-kiln picks it up.
#   - else on port 82: an extra nginx server of its own
#     (/data/ad5x/nginx/diehard-pushraker.conf, which Kiln's nginx.conf
#     includes). Kiln's port 80 server is in the read-only image.
# nginx keeps both files across reboots: no boot script is needed.
set -e
PORT=82
CONF_DIR=/data/ad5x/nginx
CONF=$CONF_DIR/diehard-pushraker.conf
# mainsail-kiln's port 81 server and the snippets it includes
X81=$CONF_DIR/port81.d
SNIPPET=$X81/diehard-pushraker.conf
MS81=$CONF_DIR/mainsail.conf
WEB=${PLUGIN_DIR:-/data/ad5x/plugins/diehard-pushraker}/python/pushraker/web
# The confirm page behind the Discord update notice's link buttons
UPDATES=${PLUGIN_DIR:-/data/ad5x/plugins/diehard-pushraker}/web

grep -qs 'include /data/ad5x/nginx/\*\.conf;' /etc/nginx/nginx.conf || {
	echo "install.sh: not the Kiln OS this was written for"
	echo "  (nginx.conf must include /data/ad5x/nginx/*.conf)"
	exit 1
}

mkdir -p $CONF_DIR $X81
chmod 755 $X81
for f in $CONF $SNIPPET; do
	rm -f $f.old
	[ -f $f ] && cp $f $f.old
done
# Kiln's nginx is built without PCRE and without the rewrite module: no
# regex locations, no "return"
cat > $SNIPPET <<EOF
# diehard-pushraker (root/install.sh): its pages inside the port 81 server.
# The plugin rewrites this file: do not edit.
        location /notifications/ {
            alias $WEB/;
            index index.html;
            add_header Cache-Control "no-store";
        }
        location /pushraker-updates/ {
            alias $UPDATES/;
            index index.html;
            add_header Cache-Control "no-store";
        }
EOF
if grep -qs "include $X81/\*\.conf;" $MS81; then
	where=81
	rm -f $CONF
else
	where=$PORT
	cat > $CONF <<EOF
# diehard-pushraker (root/install.sh): pushraker's settings page on port $PORT.
# The plugin rewrites this file: do not edit.
    server {
        listen $PORT;
        server_name _;

        include $SNIPPET;
        location /webcam/ { proxy_pass http://127.0.0.1:8090/; include webcam_proxy.conf; }
        location /webcam2/ { proxy_pass http://127.0.0.1:8091/; include webcam_proxy.conf; }
        location /printer/ { include moonraker_proxy.conf; }
        location /access/ { include moonraker_proxy.conf; }
        location /machine/ { include moonraker_proxy.conf; }
        location /server/ { include moonraker_proxy.conf; }
    }
EOF
fi

if ! nginx -t 2>/tmp/diehard-pushraker-nginx.err; then
	cat /tmp/diehard-pushraker-nginx.err
	for f in $CONF $SNIPPET; do
		if [ -f $f.old ]; then mv $f.old $f; else rm -f $f; fi
	done
	echo "install.sh: nginx refused the config; left as it was"
	exit 1
fi
rm -f $CONF.old $SNIPPET.old /tmp/diehard-pushraker-nginx.err
nginx -s reload
echo "pushraker settings page: http://<printer>:$where/notifications/"
