# Frontend

Svelte 5 web interface for the LFNP DAQ system. Built with DaisyUI 5 + Tailwind CSS v4.

## Pages

| Route | Description |
|-------|-------------|
| `/` | Overview dashboard — environmental charts (Chart.js), active scans, power supplies with active channels, recent runs, system health & storage |
| `/scans` | Paginated scans table with search (300ms debounce), new scan modal, delete per row |
| `/scans/[id]` | Scan detail — three tabs: Monitor (FSM status + controls + log + live WebSocket data), Edit (form/JSON toggle + visual voltage points editor), Download (file list + ZIP) |
| `/hardware` | CAEN power supply CRUD table with new/edit modals |
| `/profile` | Account info form + change password |
| `/admin` | User management table with admin/active toggles, delete |

## Development

```bash
pnpm install
pnpm run dev --host
```

Runs on port 5173 with hot-reload.

## Production

```bash
pnpm build
pnpm run preview
```

## Docker

Two Dockerfiles:

- `Dockerfile` — multi-stage production build (served via `@sveltejs/adapter-node` on port 3000)
- `Dockerfile.dev` — development with hot-reload (port 5173)

```bash
docker compose --profile dev up frontend-dev
docker compose --profile prod up frontend-prod
```

## Configuration

Dynamic environment variables at runtime (no rebuild needed):

| Variable | Default | Description |
|----------|---------|-------------|
| `PUBLIC_API_URL` | `http://localhost:8000` | Backend REST API URL |
| `PUBLIC_DAQ_URL` | `http://localhost:8001` | DAQ HTTP/WS URL |

Managed via SvelteKit's `$env/dynamic/public` — set them in `.env` or pass at container runtime.

## Tech Stack

| Library | Purpose |
|---------|---------|
| Svelte 5 (runes) | Reactive UI framework |
| DaisyUI 5 | UI component library |
| Tailwind CSS v4 | Utility-first styling |
| Axios | HTTP client with JWT interceptor |
| Chart.js v4.5 | Environmental data line charts |
| Lucide Svelte | Icon set |
| `@sveltejs/adapter-node` | Node.js production adapter |

## Project Structure

```
src/
├── app.css              — Global styles
├── lib/
│   ├── api.ts           — Axios client + JWT interceptor
│   ├── auth.ts          — Auth store (login/logout/user)
│   ├── toast.ts         — Toast notification store
│   ├── components/      — Shared components
│   │   ├── LoginButton.svelte  — Avatar dropdown (profile/admin/logout)
│   │   └── ToastContainer.svelte — Toast UI
│   └── assets/          — Static assets (favicon)
└── routes/
    ├── +layout.svelte   — Sidebar + navbar + footer
    ├── +page.svelte     — Overview dashboard
    ├── [...catchall]/   — 404 catch-all
    ├── scans/           — Scans list + detail
    ├── hardware/        — Power supply management
    ├── profile/         — User profile
    └── admin/           — User administration
```
