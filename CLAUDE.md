# Cerno — working agreements

## Scope

Cerno is a **ranked anomaly triage** tool, not a chat app. The Anomaly Queue is the hero surface; chat is a supplement. When in doubt about a feature, check the design doc at `~/.gstack/projects/Cerno/`.

## Layout

- Python backend lives in `src/cerno/` (src-layout). Import as `cerno.foo`, never `backend.cerno.foo`.
- Frontend lives in `frontend/` with its own `package.json` and `node_modules/`.
- Tests live in `tests/`. Fixtures go in `tests/fixtures/` — **do not** fabricate anomaly fixtures; use files from the eval set once Dad provides them.
- Packaging (PyInstaller, updater, signing) is deferred to Week 3. Do not scaffold `desktop/` or `build/` until discovery is done.

## Stack discipline

- Python **3.12** (pinned in `.python-version`). No 3.13/3.14 without a PyInstaller smoke test.
- `uv` for Python deps. `bun` for frontend deps. Never mix in npm/pip.
- **No `run_python` tool in v1.** Only `run_sql` over DuckDB views. See design doc.
- **No PDFs in v1.** Excel + CSV only.
- **No ECharts in v1.** Static tables + a spark column for anomalies.

## Tests and deploys

- After every service change, run `uv run pytest` and `cd frontend && bun run build`.
- Anomaly thresholds (MAD 3.5, rare-value 1%, key-overlap 60%) are first-cut numbers. Tune against Dad's eval set before shipping. Do not silently change thresholds without updating `docs/eval-set.md`.
- No `git push` or release actions without explicit user approval. No auto-update testing against Dad's machine without explicit user approval.

## LLM calls

- All numeric answers go through `run_sql`. If the model tries to answer a numeric question without emitting a tool call, reject + re-prompt.
- Schema questions (column names, dtypes, row counts) route through a non-tool path that reads cached schema JSON directly.

## Style

- TypeScript over JS. Named exports. Functional components. Early returns.
- Write no comments unless the WHY is non-obvious. Don't narrate the code.
- Don't add features beyond the task. No hypothetical-future abstractions.
