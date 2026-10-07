FROM node:22-alpine AS web
WORKDIR /web
RUN npm install -g pnpm@11.19.0
COPY frontend/package.json frontend/pnpm-lock.yaml frontend/pnpm-workspace.yaml ./
RUN pnpm install --frozen-lockfile
COPY frontend/ ./
RUN pnpm build

FROM python:3.12-slim
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
