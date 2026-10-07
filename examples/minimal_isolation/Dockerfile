FROM python:3.12-slim AS runtime

# Build/copy the Fluxyr wheel into vendor/ first; see README.md.
COPY vendor/*.whl /tmp/wheels/
RUN pip install --no-cache-dir /tmp/wheels/*.whl \
    && rm -rf /tmp/wheels \
    && useradd --uid 10001 --create-home agent \
    && mkdir -p /var/lib/fluxyr/data \
    && chown -R 10001:10001 /var/lib/fluxyr

WORKDIR /app
COPY app.py ./
ENV PYTHONUNBUFFERED=1 \
    FLUXYR_ROOT=/var/lib/fluxyr \
    FLUXYR_EXECUTION_MODE=landlock \
    FLUXYR_HOST=0.0.0.0 \
    PORT=8080

# Numeric UID also works with Shpyrd's runAsNonRoot policy.
USER 10001:10001
EXPOSE 8080
CMD ["python", "app.py"]
