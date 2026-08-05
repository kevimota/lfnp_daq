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

### USB access to devices (digitizers & power supplies)

Direct USB connections (desktop digitizers like DT5742/DT5743, USB power supplies) are handled **in user space** via `libusb` by the CAEN libraries. No kernel driver is required.

Inside the container this works through the device mapping declared in `docker-compose.yml`:

```yaml
daq-dev:
  devices:
    - /dev/bus/usb:/dev/bus/usb
  privileged: true
```

- `/dev/bus/usb` lets the container enumerate the host USB bus (libusb).
- `privileged: true` grants the container access to those device nodes.

### CAENUSBdrvB (VME bridges only — host OS)

`CAENUSBdrvB` is a **Linux kernel driver** (DKMS module). It is **only** needed for USB VME bridges (A2818, A3818, V1718) that open kernel-created device nodes (`/dev/a2818_0`, `/dev/a3818_0`, `/dev/usb/v1718_0`).

It **cannot** be built or loaded inside the Docker container: containers share the host kernel, and DKMS requires the exact host kernel headers plus `modprobe` into the host. It is therefore intentionally excluded from `install_CAEN.sh`.

To use a VME bridge, install the driver **on the host OS** once:

```bash
# On the host machine the VME bridge USB is plugged into
sudo apt install dkms build-essential linux-headers-$(uname -r)
cd caen-libs/CAENUSBdrvB-v1.6.2
sudo ./install.sh          # builds, installs, and loads the module
ls /dev/a2818_0 /dev/a3818_0 /dev/usb/v1718_0   # device nodes now present
```

Then expose the resulting device nodes to the container in `docker-compose.yml`:

```yaml
daq-dev:
  devices:
    - /dev/bus/usb:/dev/bus/usb
    - /dev/a2818_0:/dev/a2818_0
    - /dev/a3818_0:/dev/a3818_0
    - /dev/usb:/dev/usb
  privileged: true
```

A VME digitizer is then configured with connection type `USB` or `OPTICAL_LINK` plus its `vme_base_address` in the hardware page.

> Note: PCIe VME bridges (A5818) require PCI passthrough and are not covered by the USB setup above.

## Data Storage

All data written to `/data/daq/raw/run_{run_id}/`:

- `point_{n}_{timestamp}.csv` — one CSV per voltage point
- `configuration.json` — run parameters
- `daq.log` — timestamped FSM events (append-mode, survives restarts)
