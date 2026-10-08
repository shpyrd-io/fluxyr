FROM node:22-alpine AS web
WORKDIR /web
RUN npm install -g pnpm@11.19.0
COPY frontend/package.json frontend/pnpm-lock.yaml frontend/pnpm-workspace.yaml ./
RUN pnpm install --frozen-lockfile
COPY frontend/ ./
COPY assets/ /assets/
RUN pnpm build

FROM python:3.12-slim AS runtime
ENV PYTHONUNBUFFERED=1 FLUXYR_ROOT=/var/lib/fluxyr FLUXYR_HOST=0.0.0.0 PORT=5050
WORKDIR /app
COPY pyproject.toml setup.py requirements.lock NOTICE LICENSE README.md ./
COPY fluxyr/ ./fluxyr/
COPY --from=web /fluxyr/static/ ./fluxyr/static/
RUN pip install --no-cache-dir -r requirements.lock && pip install --no-cache-dir --no-deps . \
    && useradd --uid 10001 --create-home agent \
    && mkdir -p /var/lib/fluxyr && chown agent:agent /var/lib/fluxyr
USER agent
EXPOSE 5050
CMD ["python", "-m", "fluxyr"]

# Optional Chrome image: docker build --target browser -t fluxyr-browser .
FROM node:22-bookworm-slim AS browser-node
WORKDIR /browser
RUN npm install -g pnpm@11.19.0
COPY fluxyr/browser_runtime/ ./
RUN pnpm install --prod --frozen-lockfile --ignore-scripts

FROM runtime AS browser
USER root
COPY --from=browser-node /usr/local/bin/node /usr/local/bin/node
COPY --from=browser-node /browser /opt/fluxyr-browser
COPY --chmod=755 fluxyr/browser_runtime/chromium-container.sh /usr/local/bin/fluxyr-chromium
RUN apt-get update && apt-get install -y --no-install-recommends chromium ca-certificates \
    && rm -rf /var/lib/apt/lists/*
ENV FLUXYR_BROWSER_ENABLED=true FLUXYR_BROWSER_RUNTIME=/opt/fluxyr-browser CHROME_PATH=/usr/local/bin/fluxyr-chromium
USER agent

# Keep the ordinary image small; Chrome is an explicit build target.
FROM runtime AS default
