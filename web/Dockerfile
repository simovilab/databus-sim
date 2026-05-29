FROM nginx:1.27-alpine

# nginx.conf.template → rendered to /etc/nginx/conf.d/default.conf by the
# image's 20-envsubst-on-templates.sh entrypoint (substitutes ${DATABUS_HOST},
# ${DATABUS_HTTP_PORT}, ${SIM_HTTP_PORT}).
COPY nginx.conf.template /etc/nginx/templates/default.conf.template

# config.js.template is rendered to the html root by our own entrypoint script.
# It lives OUTSIDE /etc/nginx/templates so the nginx envsubst step ignores it.
COPY config.js.template /etc/nginx/config.js.template
COPY docker-entrypoint.d/40-config-js.sh /docker-entrypoint.d/40-config-js.sh
RUN chmod +x /docker-entrypoint.d/40-config-js.sh

COPY index.html style.css app.js /usr/share/nginx/html/
COPY lib/    /usr/share/nginx/html/lib/
COPY tabs/   /usr/share/nginx/html/tabs/
COPY modals/ /usr/share/nginx/html/modals/
COPY --from=shapes shapes.json /usr/share/nginx/html/shapes.json
