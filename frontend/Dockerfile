FROM node:24-slim AS build
WORKDIR /app
COPY package.json package-lock.json ./
RUN npm ci
COPY . .
RUN npm run build

FROM nginx:1.28-alpine
# Pull the Alpine branch's current security fixes (openssl, musl, zlib, libxml2, libpng, ...):
# the nginx image is rebuilt only periodically, and the container scan gates on fixed CVEs.
RUN apk upgrade --no-cache
COPY --from=build /app/dist /usr/share/nginx/html
COPY nginx.conf /etc/nginx/conf.d/default.conf
EXPOSE 80

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD wget -q -O- http://localhost/ >/dev/null || exit 1
