# DAQ

Data Acquisition system for LFNP detectors.

## Development

```bash
uv sync
uv run fastapi dev --host 0.0.0.0 --port 8002
```

## Production

```bash
uv sync
uv run fastapi run --host 0.0.0.0 --port 8002
```

## Configuration

The DAQ connects to the same PostgreSQL database as the backend. Connection string is read from the `DATABASE_URL` environment variable.

## Ports

- 8002: REST API
- 8001: WebSocket (live data streaming)

## Data Storage

Raw data is stored in `/data/daq/raw/` (mounted as a shared volume).