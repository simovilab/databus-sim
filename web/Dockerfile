FROM nginx:1.27-alpine

COPY nginx.conf /etc/nginx/conf.d/default.conf
COPY index.html style.css app.js /usr/share/nginx/html/
COPY lib/    /usr/share/nginx/html/lib/
COPY tabs/   /usr/share/nginx/html/tabs/
COPY modals/ /usr/share/nginx/html/modals/
COPY --from=shapes shapes.json /usr/share/nginx/html/shapes.json
