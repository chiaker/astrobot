FROM python:3.12-slim

ARG GIT_SHA=dev
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PROJECT_ENVIRONMENT=/opt/venv \
    ASTROBOT_GIT_SHA=$GIT_SHA

RUN apt-get update && apt-get install -y --no-install-recommends \
        build-essential \
        curl \
        ca-certificates \
        pkg-config \
        libcairo2-dev \
        libfreetype6 \
        libfreetype6-dev \
    && rm -rf /var/lib/apt/lists/*

# --- Russian Trusted Root CA (Минцифры) --------------------------------------
# MAX's API (platform-api2.max.ru) serves a TLS cert issued by Минцифры. Without
# the root + intermediate in the trust store, the bot's OUTGOING calls to MAX
# fail TLS verification. aiohttp (maxapi's client) reads the system store, so
# update-ca-certificates is sufficient. Harmless for the Telegram deploy (just
# extra trusted CAs) — keeps one image for both platforms.
RUN curl -fsSL https://gu-st.ru/content/lending/russian_trusted_root_ca_pem.crt \
        -o /usr/local/share/ca-certificates/russian_trusted_root_ca.crt \
 && curl -fsSL https://gu-st.ru/content/lending/russian_trusted_sub_ca_pem.crt \
        -o /usr/local/share/ca-certificates/russian_trusted_sub_ca.crt \
 && update-ca-certificates

RUN pip install --no-cache-dir uv

WORKDIR /app

COPY pyproject.toml ./
COPY uv.lock* ./
# --extra max installs maxapi so ONE image serves both Telegram and MAX
# (PLATFORM selects the runtime; the TG path never imports maxapi).
RUN uv sync --no-install-project --extra max

COPY src/ ./src/
COPY alembic.ini ./
RUN uv sync --extra max

ENV PATH="/opt/venv/bin:$PATH"

CMD ["python", "-m", "astrobot.main"]
