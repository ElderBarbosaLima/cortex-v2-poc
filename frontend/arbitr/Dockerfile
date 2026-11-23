# The Cortex PoC frontend. Build context: the repository root (the app imports
# the generated contract types from libraries/cortex-contract/ts).
FROM docker.io/library/node:22.12.0-slim@sha256:35531c52ce27b6575d69755c73e65d4468dba93a25644eed56dc12879cae9213 AS deps
ENV NUXT_TELEMETRY_DISABLED=1 NPM_CONFIG_UPDATE_NOTIFIER=false NPM_CONFIG_FUND=false
# npm 10.9.0 (bundled with this node) crashes resolving vitest's peer set
# ("Cannot read properties of null (reading 'edgesOut')"); 11.6.2 does not.
RUN npm install -g npm@11.6.2
WORKDIR /workspace/frontend/arbitr
COPY frontend/arbitr/package.json frontend/arbitr/package-lock.json ./
RUN npm ci --ignore-scripts

FROM deps AS app
COPY libraries/cortex-contract/ts /workspace/libraries/cortex-contract/ts
COPY frontend/arbitr/ ./
RUN npx nuxt prepare && chown -R node:node /workspace
USER node
EXPOSE 5001
CMD ["npx", "nuxt", "dev"]
