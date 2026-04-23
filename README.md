# Cerno

Ranked anomaly triage for bulk tabular data. A Nemukai project.

Cerno ingests Excel and CSV files, runs a canned anomaly sweep (robust-z outliers, rare categoricals, duplicate keys across files, broken joins), and surfaces a top-20 queue of rows worth investigating. Chat is a supplement: an LLM tool-use loop with a single `run_sql` tool over DuckDB views.

## Status

v1 scaffold in progress. See the design doc at `~/.gstack/projects/Cerno/` for the full spec.

## Stack

- **Backend:** Python 3.12, FastAPI, Polars, DuckDB, SQLite (sessions), python-calamine (reads), openpyxl (exports), pywebview (desktop shell).
- **Frontend:** Vite, React, TypeScript, Tailwind.
- **Package managers:** `uv` for Python, `bun` for the frontend.
- **Distribution:** PyInstaller bundles per OS (macOS + Windows), hand-rolled updater.

## Development

```bash
# One-time
uv sync --extra dev
cd frontend && bun install

# Run backend (from repo root)
uv run cerno-server

# Run frontend dev server (separate shell)
cd frontend && bun run dev
```

Copy `.env.example` to `.env.local` and fill in your LLM API key.

## Tests

```bash
uv run pytest
cd frontend && bun run build   # smoke: build must succeed
```

## Layout

- `src/cerno/` — Python backend package.
- `frontend/` — Vite + React app.
- `tests/` — pytest suite.
- `docs/` — discovery notes, eval-set tracking.

## License

Proprietary. Do not redistribute.
