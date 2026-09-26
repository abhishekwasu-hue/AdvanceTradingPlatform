FROM node:24-slim AS build
WORKDIR /app
COPY package.json package-lock.json ./
RUN npm ci
COPY . .
RUN npm run build

# Mainline rather than the 1.28 stable line: the official image installs nginx.org's own apk
# with a pinned version, so Alpine's backported nginx security revisions (the ones the image
# scanner gates on) never apply to it; mainline carries those fixes at source. Everything else
# in the image is Alpine's and is brought to the branch's current fixes by apk upgrade.
FROM nginx:alpine
RUN apk upgrade --no-cache
COPY --from=build /app/dist /usr/share/nginx/html
COPY nginx.conf /etc/nginx/conf.d/default.conf
EXPOSE 80

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD wget -q -O- http://localhost/ >/dev/null || exit 1
