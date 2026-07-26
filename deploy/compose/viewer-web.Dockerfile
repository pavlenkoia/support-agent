FROM node:20-alpine AS build

WORKDIR /app

COPY viewer-web/package.json ./package.json
RUN npm install

COPY viewer-web/ ./
RUN npm run build

FROM nginx:1.27-alpine

ARG SUPPORT_AGENT_RELEASE_ID=dev
LABEL org.opencontainers.image.revision=${SUPPORT_AGENT_RELEASE_ID}

COPY deploy/compose/viewer-web.nginx.conf /etc/nginx/conf.d/default.conf
COPY --from=build /app/dist /usr/share/nginx/html
RUN chmod -R a+r /usr/share/nginx/html

EXPOSE 3000
