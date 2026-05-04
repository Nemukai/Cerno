# Cerno

Cerno turns messy Excel and CSV uploads into an approved data map, a working
dashboard, and chat-driven analysis pages. It is a Nemukai project.

The product flow is:

1. Start or resume a session.
2. Upload spreadsheets or CSV files.
3. Process the files so Cerno can infer headers, friendly names, column meaning,
   and relationships between files.
4. Review and approve the data map.
5. Use the generated dashboard and chat to inspect the data.

Schema review is the source of truth. The schema surface includes the internal
data guide: file descriptions, grains, relationships, caveats, glossary terms,
and starter questions. Chat and dashboard generation use that approved
understanding instead of guessing from raw columns alone.

## Status

Prototype in active development. Current implementation includes:

- Google sign-in with private beta access codes.
- Session creation, resume, and delete.
- Excel/CSV ingest with raw parquet preservation and content-hash deduplication.
- LLM-backed discovery using full-file Python profiling plus structured schema output.
- Review/edit/approve flow for files, columns, and relationships.
- Generated schema guidance merged into the schema review surface.
- Dashboard generation with editable widget layout.
- Chat that can answer from the approved data map and spawn dashboard views.

Still pending before a serious MVP:

- A polished landing page and Workspaces surface.
- Stronger demo-data validation against real office-style workbooks.
- Export flows.
- Desktop shell, packaging, signing, and updater work.

## Stack

- **Backend:** Python 3.12, FastAPI, SQLite, Polars, pandas, DuckDB,
  python-calamine, pyarrow, Authlib, OpenAI Responses API.
- **Frontend:** Vite, React, TypeScript, Tailwind, ECharts.
- **Package managers:** `uv` for Python, `bun` for the frontend.
- **Storage:** `~/.cerno` by default for SQLite metadata and session parquet files.

## Development

```bash
# One-time
uv sync --extra dev
cd frontend && bun install

# Run backend from repo root
uv run cerno-server

# Run frontend dev server in another shell
cd frontend && bun run dev
```

Copy `.env.example` to `.env.local` and fill in the Google OAuth and OpenAI
settings for your environment.

## Useful Commands

```bash
cd frontend && bun run typecheck
cd frontend && bun run build
uv run ruff check src
```

## Layout

- `src/cerno/` — FastAPI backend, repositories, LLM client, ingest, discovery,
  dashboard, and chat services.
- `frontend/` — Vite/React app.
- `~/.gstack/projects/Cerno/` — planning notes and prior product reviews.

## License

Proprietary. Do not redistribute.
