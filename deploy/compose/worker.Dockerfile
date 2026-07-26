FROM python:3.11-slim

ARG SUPPORT_AGENT_RELEASE_ID=dev
LABEL org.opencontainers.image.revision=${SUPPORT_AGENT_RELEASE_ID}

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends curl ca-certificates && rm -rf /var/lib/apt/lists/*
RUN curl -LsSf https://astral.sh/uv/install.sh | sh
ENV PATH="/root/.local/bin:${PATH}"

COPY pyproject.toml README.md alembic.ini ./
COPY app ./app
COPY migrations ./migrations
RUN uv sync --no-dev

COPY docs ./docs
