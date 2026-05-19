# LFNP DAQ

Data Acquisition System for LFNP particle detectors with CAEN power supply control, scan management, and a Svelte 5 frontend / FastAPI backend.

## Architecture

```
frontend/    Svelte 5 + SvelteKit + DaisyUI v5 + Tailwind CSS v4
backend/     FastAPI + SQLModel + PostgreSQL (authentication, orchestration)
daq/         FastAPI (standalone) + caen_libs (CAEN hardware control, FSM, data writing)
```

- **Frontend** communicates with the **backend** via REST (authenticated with JWT).
- **Backend** proxies scan control commands to the **DAQ** service and manages the database.
- **DAQ** runs scans directly, controls CAEN power supplies via `caen_libs`, and serves a per-run WebSocket for live monitoring.
- Data is stored on a shared volume at `/data/daq/raw/run_{run_id}/`.

## Quick Start

You need to download the latest version of the libraries from the [CAEN website](https://www.caen.it/subfamilies/software-libraries/) and put it in the daq folder (`daq/CAENHVWrapper-version`).

Create a new env file from the reference one `.env_ref`, update the `SECRET_KEY`, `db_username` and `db_password`

Run the command bellow to start the development environment
```bash
# Start dev environment
docker compose --profile dev up -d

# Frontend at http://localhost:5173
# Backend API at http://localhost:8000
# DAQ service at http://localhost:8001
# PostgreSQL at localhost:5432
```

## Services

### Frontend (`frontend/`)
- Svelte 5 with runes (`$state`, `$props`, `$derived`)
- DaisyUI v5 + Tailwind CSS v4
- OAuth2 login with JWT token handling
- Pages: Overview, Scans (list + detail per run), Hardware (CAEN PS management)

### Backend (`backend/`)
- FastAPI with SQLModel + PostgreSQL
- User authentication (JWT)
- DAQ run management (CRUD, proxy to DAQ)
- CAEN power supply CRUD
- Channel conflict detection

### DAQ (`daq/`)
- Standalone FastAPI service
- Finite State Machine (FSM) for scan lifecycle:
  `INITIALIZING → HALTED → CONFIGURING → WAITING → RECORDING ↔ PAUSED → FINISHED / FAILED`
- CAEN hardware control via `caen_libs`
- Per-point CSV data writing
- Per-run WebSocket for live data broadcast
- Scan configuration and log file management

## Environment Variables

See `.env` for required values:

| Variable | Description |
|---|---|
| `SECRET_KEY` | JWT signing key |
| `DATABASE_URL` | PostgreSQL connection string |
| `DAQ_URL` | DAQ service URL (internal) |
| `PUBLIC_API_URL` | Backend URL (browser-facing) |
| `PUBLIC_DAQ_URL` | DAQ URL (browser-facing, for WebSocket) |
| `ACCESS_TOKEN_EXPIRE_MINUTES` | JWT expiry |

## Data Flow

1. User creates a scan run via the frontend → backend stores config + run in DB
2. User starts the run → backend proxies to DAQ, which instantiates an FSM + scanner
3. Scanner iterates through voltage points: set voltage → ramp → wait → record → next
4. During recording, samples are written to CSV and broadcast via WebSocket
5. On completion/failure, the DAQ updates the run status in the DB
