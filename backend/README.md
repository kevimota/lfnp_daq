# Backend

FastAPI REST API for the LFNP DAQ system. Handles authentication, user management, power supply CRUD, run configuration, and proxies control commands to the DAQ engine.

## Endpoints

### Authentication & Users

| Method | Path | Description |
|--------|------|-------------|
| POST | `/login/token` | OAuth2 login (returns JWT) |
| GET | `/users/me` | Current user profile |
| PATCH | `/users/me` | Update own profile |
| PATCH | `/users/me/password` | Change own password |
| GET | `/users` | List all users (admin) |
| PATCH | `/users/{id}` | Update any user (admin) |
| DELETE | `/users/{id}` | Delete user (admin) |

### DAQ Runs

| Method | Path | Description |
|--------|------|-------------|
| GET | `/daq/runs` | Paginated list (`page`, `per_page`, `search`) |
| POST | `/daq/runs` | Create run + configuration |
| DELETE | `/daq/runs/{id}` | Delete run |
| POST | `/daq/runs/{id}/start` | Start scan (proxied to DAQ) |
| POST | `/daq/runs/{id}/stop` | Stop scan (proxied to DAQ) |
| POST | `/daq/runs/{id}/pause` | Pause scan (proxied to DAQ) |
| POST | `/daq/runs/{id}/resume` | Resume scan (proxied to DAQ) |
| GET | `/daq/runs/{id}/status` | FSM status (proxied to DAQ) |
| GET | `/daq/runs/{id}/info` | FSM info (proxied to DAQ) |
| GET | `/daq/runs/{id}/log` | daq.log (proxied to DAQ) |
| GET | `/daq/runs/{id}/files` | Run file list (proxied to DAQ) |
| GET | `/daq/runs/{id}/files/{filename}` | Download file (proxied to DAQ) |
| GET | `/daq/runs/{id}/download` | Download ZIP (proxied to DAQ) |

### Configurations

| Method | Path | Description |
|--------|------|-------------|
| GET | `/daq/configs` | List all configurations |
| GET | `/daq/configs/{id}` | Get configuration |
| PUT | `/daq/configs/{id}` | Update configuration |
| DELETE | `/daq/configs/{id}` | Delete configuration |

### Hardware (CAEN Power Supplies)

| Method | Path | Description |
|--------|------|-------------|
| GET | `/hardware/caen-ps` | List all power supplies |
| POST | `/hardware/caen-ps` | Create power supply |
| PUT | `/hardware/caen-ps/{id}` | Update power supply |
| DELETE | `/hardware/caen-ps/{id}` | Delete power supply |

### System

| Method | Path | Description |
|--------|------|-------------|
| GET | `/system/health` | Health check (proxies DAQ) |
| GET | `/system/storage` | Disk usage (proxies DAQ) |

## Development

```bash
uv sync
uv run fastapi dev --host 0.0.0.0
```

## Production

```bash
uv sync
uv run fastapi run --host 0.0.0.0 --port 8000
```

## Docker

Multi-stage build with dev/prod profiles:

```bash
docker compose --profile dev up backend-dev
docker compose --profile prod up backend-prod
```

## Configuration

Environment variables (`.env`):

| Variable | Default | Description |
|----------|---------|-------------|
| `DATABASE_URL` | — | PostgreSQL connection string |
| `SECRET_KEY` | — | JWT signing key |
| `ACCESS_TOKEN_EXPIRE_MINUTES` | — | JWT lifetime |
| `DAQ_URL` | `http://daq-dev:8001` | Internal DAQ container URL |
| `FIRST_SUPERUSER` | `admin` | Default admin username |
| `FIRST_SUPERUSER_PASSWORD` | `admin@1234` | Default admin password |
| `FIRST_SUPERUSER_EMAIL` | `admin@example.com` | Default admin email |

## Database

PostgreSQL with SQLModel ORM. Schema managed by the backend (the DAQ only reads/writes status). Tables:

- `users` — authentication and authorization
- `caenps` — registered CAEN power supplies (`system_type`, `link_type`, `arg`, `username`, `password`)
- `daqconfiguration` — scan configurations (`voltage_points`, `wait_time`, `sample_interval`, `number_of_samples`, `end_voltage`, `power_supply` FK)
- `daqruns` — run records (`configuration_id` FK, `status`, `label`, `comments`, `started_at`, `stopped_at`, `data_path`)
