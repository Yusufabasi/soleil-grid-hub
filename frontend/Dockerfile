# hub-web: the Next.js standalone server behind the balancer (`make up WEB=real`, which builds
# this file through infra/docker/compose.web.yaml). Multi-stage, so the image carries the built
# output and nothing else: no source tree, no dev dependencies, no lockfile.
#
# The compose service runs this read-only, as a non-root user, with every capability dropped and
# a tmpfs on /tmp. Nothing here writes to disk: the app is client-rendered, images are
# unoptimized and there is no incremental static regeneration, so `.next/cache` is never touched.

FROM node:22-alpine AS deps
WORKDIR /app
COPY package.json package-lock.json ./
# `npm ci` is the lockfile-exact install; the build fails rather than drifting from it.
RUN npm ci

FROM node:22-alpine AS build
WORKDIR /app
ENV NEXT_TELEMETRY_DISABLED=1
COPY --from=deps /app/node_modules ./node_modules
COPY . .
# `output: standalone` in next.config.ts is what makes the third stage possible.
RUN npm run build

FROM node:22-alpine AS runner
WORKDIR /app
ENV NODE_ENV=production \
    NEXT_TELEMETRY_DISABLED=1 \
    PORT=3000 \
    HOSTNAME=0.0.0.0
COPY --from=build --chown=node:node /app/.next/standalone ./
COPY --from=build --chown=node:node /app/.next/static ./.next/static
COPY --from=build --chown=node:node /app/public ./public
USER node
EXPOSE 3000
# `/web/health` is the route the balancer's `be_web` check asks for (infra/lb/haproxy.cfg).
CMD ["node", "server.js"]
