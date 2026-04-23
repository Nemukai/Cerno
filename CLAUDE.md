# Cerno — working agreements

## Scope

Cerno is a **link-aware data analysis tool**: upload files → agent discovers links → dashboard generated → chat spawns per-question dashboard pages backed by a model-run notebook → export. Link discovery + dashboards are the hero; anomalies are one engine feeding the dashboard. See `~/.gstack/projects/Cerno/priyanshsingh-main-plan-reframe-20260424-000000.md` for the canonical plan.

## Layout

- Python backend lives in `src/cerno/` (src-layout). Import as `cerno.foo`, never `backend.cerno.foo`.
- Frontend lives in `frontend/` with its own `package.json` and `node_modules/`.
- Tests live in `tests/`. Fixtures go in `tests/fixtures/`. Synthetic 3-file fixtures are fine for link discovery + dashboard tests.
- Packaging (PyInstaller, updater, signing) is the last phase. Signing cert procurement runs in parallel from day 1.

## Stack discipline

- Python **3.12** (pinned in `.python-version`). No 3.13/3.14 without a PyInstaller smoke test.
- `uv` for Python deps. `bun` for frontend deps. Never mix in npm/pip.
- **Notebook model:** model-writes / user-reads. The agent writes Python + SQL cells; the user sees output on the dashboard or in chat but does not edit cells. Tools: `run_python`, `run_sql`, `list_tables`, `describe_table`, `read_cells`, `render_widget`.
- Tables in `run_python` are exposed as pandas DataFrames, one per ingested file, named by a slugified filename.
- Excel + CSV only for ingest in v1. PDF export allowed (reportlab for 1-pager brief).
- ECharts is in scope for dashboard widgets. Tree-shake imports.

## Tests and deploys

- After every service change, run `uv run pytest` and `cd frontend && bun run build`.
- Anomaly thresholds (MAD 3.5, rare-value 1%, key-overlap 60%) are first-cut numbers. Tune against Dad's eval set before shipping. Do not silently change thresholds without updating `docs/eval-set.md`.
- All thresholds live in `~/.cerno/config.toml`, hot-reloaded, user-editable in Settings.
- No `git push` or release actions without explicit user approval. No auto-update testing against Dad's machine without explicit user approval.

## LLM calls

- LLM connection is a trusted private pipe. No PII denylists, no column redaction gates — data flows freely to the model.
- Numeric answers in chat go through `run_sql` or `run_python` (slot-filled into the response). Schema questions route through a non-tool path reading cached schema JSON directly.
- The agent decides text-in-chat vs. spawn-a-dashboard-page based on whether its final answer emits widgets.

## Style

- TypeScript over JS. Named exports. Functional components. Early returns.
- Write no comments unless the WHY is non-obvious. Don't narrate the code.
- Don't add features beyond the task. No hypothetical-future abstractions.
