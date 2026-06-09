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

RUN pip install --no-cache-dir uv

WORKDIR /app

COPY pyproject.toml ./
COPY uv.lock* ./
RUN uv sync --no-install-project

COPY src/ ./src/
RUN uv sync

ENV PATH="/opt/venv/bin:$PATH"

CMD ["python", "-m", "astrobot.main"]
