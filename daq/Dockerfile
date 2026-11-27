FROM python:3.12-slim AS base
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

RUN export UV_LINK_MODE=copy

WORKDIR /daq
COPY pyproject.toml uv.lock ./

# System dependencies and CAEN libraries are identical in both profiles.
# Pin the libssl .deb by SHA256 so a tampered download cannot be installed.
RUN apt-get update && apt-get install -y --no-install-recommends curl libusb-1.0-0-dev \
    && curl -fsSL https://archive.debian.org/debian/pool/main/o/openssl/libssl1.1_1.1.1w-0+deb11u1_amd64.deb -o /tmp/libssl.deb \
    && echo "aadf8b4b197335645b230c2839b4517aa444fd2e8f434e5438c48a18857988f7  /tmp/libssl.deb" | sha256sum -c - \
    && dpkg -i /tmp/libssl.deb \
    && rm /tmp/libssl.deb \
    && rm -rf /var/lib/apt/lists/*
COPY caen-libs/ ./caen-libs/
COPY install_CAEN.sh .
RUN ./install_CAEN.sh
RUN echo "/usr/lib64" > /etc/ld.so.conf.d/caen.conf && ldconfig

# Development Profile
FROM base AS dev
RUN uv sync --frozen --dev
COPY daq/ .
CMD ["uv", "run", "uvicorn", "daq.main:app", "--host", "0.0.0.0", "--port", "8001", "--reload"]

# Production Profile
FROM base AS prod
RUN uv sync --frozen --no-dev
COPY daq/ .
CMD ["uv", "run", "uvicorn", "daq.main:app", "--host", "0.0.0.0", "--port", "8001"]
