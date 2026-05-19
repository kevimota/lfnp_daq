FROM python:3.12-slim AS base
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

RUN export UV_LINK_MODE=copy

WORKDIR /backend
COPY ./app .
COPY pyproject.toml uv.lock ./

# Development Profile
FROM base AS dev
RUN uv sync --frozen --dev
COPY ./app .
CMD ["uv", "run", "fastapi", "dev", "--host", "0.0.0.0"]

# Production Profile
FROM base AS prod
RUN uv sync --frozen --no-dev
COPY ./app .
CMD ["uv", "run", "fastapi", "run", "--host", "0.0.0.0", "--port", "8000"]
