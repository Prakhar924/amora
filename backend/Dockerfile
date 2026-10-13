# syntax=docker/dockerfile:1

# ---- Base -----------------------------------------------------------------
# Multi-stage: dependencies are built in a stage with compilers available, and
# only the installed packages are copied into the slim runtime image.
FROM python:3.12-slim AS builder

ENV PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends build-essential libpq-dev \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt ./
RUN python -m venv /opt/venv \
    && /opt/venv/bin/pip install --no-cache-dir -r requirements.txt


# ---- Runtime --------------------------------------------------------------
FROM python:3.12-slim AS runtime

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PATH="/opt/venv/bin:$PATH"

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends libpq5 curl \
    && rm -rf /var/lib/apt/lists/* \
    # Run as a non-root user: a compromised web process should not be root.
    && useradd --create-home --uid 10001 amora

COPY --from=builder /opt/venv /opt/venv
COPY --chown=amora:amora . .

# The entrypoint must be executable inside the image. Git on Windows does not
# preserve the executable bit, so set it here rather than relying on the host.
RUN chmod +x ./entrypoint.sh

USER amora

EXPOSE 8000

HEALTHCHECK --interval=15s --timeout=5s --start-period=20s --retries=5 \
    CMD curl -fsS http://localhost:8000/health || exit 1

# `entrypoint.sh` applies migrations before serving, so a fresh volume comes
# up with a correct schema and no manual step.
ENTRYPOINT ["./entrypoint.sh"]
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
