# DAQ Engine

Data acquisition engine for detectors. Controls CAEN high-voltage power supplies, runs automated current-vs-voltage scans, and streams live data via WebSocket.

## Architecture

The DAQ is a standalone FastAPI application with per-run finite state machine (FSM) instances:

```
ScanManager dict[int, ScanContext]
  ├── DAQFSM          — state machine & log file
  ├── PowerSystemInterface — CAEN hardware bindings
  ├── CurrentScanner  — iterates voltage points, ramps/monitors/records
  ├── DataWriter      — CSV per voltage point + configuration.json
  ├── DataBroadcaster — WebSocket push to connected browsers
  └── asyncio.Task    — background scan loop
```

### FSM States

```
INITIALIZING
     │
     ▼
   HALTED ◄──────────────────────────────┐
     │                                   │
     ▼ (start)                           │
  CONFIGURING ────┐                      │
     │            │ (pause)              │
     ▼ (to_waiting)                    [stop]
   WAITING ────────┤                     │
     │            │                      │
     ▼ (to_recording)                    │
  RECORDING ──────┤                      │
     │            │                      │
     │  ┌── PAUSED                       │
     │  │    │ (resume)                  │
     │  │    └──► CONFIGURING            │
     │  │                                │
     ├──┼── (configure_next — more) ─────┘
     │  │
     ▼  └── (fail) ──► FAILED
     ▼
  FINISHED
```

## Endpoints

| Method | Path | Description |
|--------|------|-------------|
| POST | `/daq/runs/{run_id}/start` | Start a new scan |
| POST | `/daq/runs/{run_id}/stop` | Stop an active scan |
| POST | `/daq/runs/{run_id}/pause` | Pause (recording/waiting/configuring) |
| POST | `/daq/runs/{run_id}/resume` | Resume from paused — redoes current point |
| GET | `/daq/runs/{run_id}/status` | Current FSM state + HV point progress |
| GET | `/daq/runs/{run_id}/info` | Detailed FSM info |
| GET | `/daq/runs/{run_id}/log` | daq.log contents (active or filesystem) |
| GET | `/daq/runs/{run_id}/files` | List files in run directory |
| GET | `/daq/runs/{run_id}/files/{filename}` | Download individual file |
| GET | `/daq/runs/{run_id}/download` | Download whole run as ZIP |
| GET | `/daq/storage` | Disk usage of `/data/daq` |
| WS | `/ws/{run_id}` | Live data stream |
| GET | `/health` | Health check + active runs |

## Development

```bash
uv sync
uv run fastapi dev --host 0.0.0.0 --port 8001
```

## Production

```bash
uv sync
uv run fastapi run --host 0.0.0.0 --port 8001
```

## Docker

Multi-stage build with dev/prod profiles:

- **Dev** (`--profile dev`): hot-reload via `--reload`
- **Prod**: optimized, no reload

```bash
docker compose --profile dev up daq-dev
docker compose --profile prod up daq-prod
```

## Configuration

Environment variables (`.env`):

| Variable | Default | Description |
|----------|---------|-------------|
| `DATABASE_URL` | — | PostgreSQL connection string |

The DAQ connects directly to PostgreSQL for read access (run configurations) and status updates.

## CAEN Hardware

Requires `libcaenhvwrapper.so` installed to `/usr/lib64` and `libssl1.1` (Debian bullseye). The `install_CAEN.sh` script handles installation from `CAENHVWrapper-6.6/`.

`LD_LIBRARY_PATH=/usr/lib64` is set in the Docker image so the linker finds the CAEN C library.

## Data Storage

All data written to `/data/daq/raw/run_{run_id}/`:

- `point_{n}_{timestamp}.csv` — one CSV per voltage point
- `configuration.json` — run parameters
- `daq.log` — timestamped FSM events (append-mode, survives restarts)
