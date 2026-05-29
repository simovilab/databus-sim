#!/bin/sh
# Render config.js from the environment so the browser knows the published
# MQTT-WS port. Runs after the nginx image's own 20-envsubst-on-templates.sh.
set -eu

envsubst '${MQTT_WS_PORT}' \
    < /etc/nginx/config.js.template \
    > /usr/share/nginx/html/config.js

echo "40-config-js.sh: rendered config.js (MQTT_WS_PORT=${MQTT_WS_PORT:-})"
