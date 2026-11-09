# benefit-be

Backend for the Benefit Case Platform: a FastAPI API, a Temporal worker and MongoDB. **All data is synthetic.** Nothing here is deployed. Everything runs natively on a Mac.

Specs, plans and rules live in the `ai-immersion-metarepo` repo (`specs/000-platform-foundations/`, `project-rules/`). The frontend is the sibling repo `benefit-fe`.

## Run it (from a fresh clone, under 30 minutes)

Prerequisites: an Apple-silicon Mac with [Homebrew](https://brew.sh), and `benefit-fe` cloned next to this repo (`../benefit-fe`).

```bash
make doctor      # lists any missing tool
make setup       # brew bundle, uv sync, pnpm install, .env with generated secrets, MongoDB
make reset       # migrate and load the synthetic data set
make dev         # start every component (leave it running)
make health      # in another terminal: every component should report ready
```

Open http://localhost:5173. The synthetic-data banner is on every screen.

| Command | What it does |
|---|---|
| `make dev` | Every process in development mode (reload on, tracing on) |
| `make prod` | Builds the frontend, then runs the same processes with production settings |
| `make reset` | Clears all data and reloads the synthetic data set. Safe while the system runs |
| `make seed` | Runs the fixed-seed generator only |
| `make health` | Polls every component and prints a table. Exit 0 means all usable |
| `make mongo-up` / `mongo-down` | Start or stop the local MongoDB (single-node replica set with authentication) |
| `make check` | Lint, types, tests, contract and secrets checks (what CI runs) |
| `make test` | Every test level that needs no running stack |
| `make e2e` | The Playwright suite in `benefit-fe` against the running stack |

### Components and ports

| Component | Port | Health |
|---|---|---|
| API (FastAPI) | 8000 | `/api/v1/health`, `/api/v1/ready` |
| Worker (Temporal) | 8001 | `/health`, `/ready` |
| Temporal dev server | 7233 (UI 8233) | `temporal operator cluster health` |
| MongoDB | 27017 | checked by readiness |
| Phoenix (AI tracing) | 6006 | `/healthz` |
| Web (Vite) | 5173 | HTTP 200 on `/` |

`/health` says the process is alive. `/ready` says its dependencies are reachable. A tracing outage reports `degraded`, and AI runs carry on.

### Settings

All settings come from environment variables. `.env.example` lists every one and is committed. `.env` is git-ignored: `make setup` creates it and generates the secrets. A missing or invalid setting stops the process with exit code 2 and names the setting, never its value. The full list is in `specs/000-platform-foundations/contracts/settings.md`.

## Layout

```
apps/api/        FastAPI: routers -> services -> repositories
apps/worker/     Temporal worker: workflows (deterministic) and activities
packages/        domain models, platform helpers, agents, rag (see project-rules/backend-architecture.md)
db/              MongoDB provisioning, migrations and the seed generator
analytics/       dbt models (DuckDB reporting store)
scripts/         run, health and CI helper scripts
tests/           mirrors the source tree
```

## Testing

Tests are named after the requirement they prove, for example `test_FR_000_04_missing_setting_refuses_start_names_setting`. Each test gets one level marker from its folder (`unit`, `workflow`, `contract`, `integration`, `api`, `stack`). Integration tests use their own databases (`benefit_test`), so they never touch development data. They need `make mongo-up`.

No test calls a live AI model. The one exception is `make smoke-ai`, which runs the `live_llm` smoke test against the provider set by `LLM_PROVIDER` (`anthropic` or `bedrock`) and is refused when `CI=true`. The `anthropic` run needs `ANTHROPIC_API_KEY`. The `bedrock` run needs `AWS_REGION`, `BEDROCK_MODEL_ID` and AWS credentials (an `AWS_PROFILE` or the usual AWS environment variables). Switching provider changes only these settings, never code.

## Troubleshooting

- `make mongo-up` fails: look at `.data/mongo/mongod.log`. Delete `.data/mongo` to start MongoDB from scratch (this removes local data).
- `make health` shows `temporal down`: `make dev` starts it. Check `.data/temporal.db` is writable.
- `make reset` refuses: it only runs against a local, synthetic-marked or empty database.
