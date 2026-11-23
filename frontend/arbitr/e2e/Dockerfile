# Playwright for the Decision Desk e2e tests (`make test-e2e-ui`).
FROM mcr.microsoft.com/playwright:v1.59.1-noble@sha256:b0ab6f3cb99aa7803adbc14d9027ec1785fc6e433b97e134e0f8fe61683b6b53
ENV NPM_CONFIG_UPDATE_NOTIFIER=false NPM_CONFIG_FUND=false
WORKDIR /e2e
COPY frontend/arbitr/e2e/package.json frontend/arbitr/e2e/package-lock.json ./
RUN npm ci --ignore-scripts
COPY frontend/arbitr/e2e/ ./
RUN chown -R pwuser:pwuser /e2e
USER pwuser
CMD ["npx", "playwright", "test"]
