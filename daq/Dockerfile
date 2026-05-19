FROM python:3.12-slim AS base
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

RUN export UV_LINK_MODE=copy

WORKDIR /daq
COPY pyproject.toml uv.lock ./

# Development Profile
FROM base AS dev
RUN apt-get update && apt-get install -y --no-install-recommends curl \
    && curl -sL http://archive.debian.org/debian/pool/main/o/openssl/libssl1.1_1.1.1w-0+deb11u1_amd64.deb -o /tmp/libssl.deb \
    && dpkg -i /tmp/libssl.deb \
    && rm /tmp/libssl.deb \
    && rm -rf /var/lib/apt/lists/*
RUN uv sync --frozen --dev
COPY daq/ .
COPY CAENHVWrapper-6.6 .
COPY install_CAEN.sh .
RUN ./install_CAEN.sh
RUN echo "/usr/lib64" > /etc/ld.so.conf.d/caen.conf && ldconfig
CMD ["uv", "run", "uvicorn", "daq.main:app", "--host", "0.0.0.0", "--port", "8001", "--reload"]

# Production Profile
FROM base AS prod
RUN apt-get update && apt-get install -y --no-install-recommends curl \
    && curl -sL http://archive.debian.org/debian/pool/main/o/openssl/libssl1.1_1.1.1w-0+deb11u1_amd64.deb -o /tmp/libssl.deb \
    && dpkg -i /tmp/libssl.deb \
    && rm /tmp/libssl.deb \
    && rm -rf /var/lib/apt/lists/*
RUN uv sync --frozen --dev
COPY daq/ .
COPY CAENHVWrapper-6.6 .
COPY install_CAEN.sh .
RUN ./install_CAEN.sh
RUN echo "/usr/lib64" > /etc/ld.so.conf.d/caen.conf && ldconfig
CMD ["uv", "run", "uvicorn", "daq.main:app", "--host", "0.0.0.0", "--port", "8001"]