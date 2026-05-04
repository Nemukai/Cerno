# Cerno — working agreements

## Scope

Cerno is a **link-aware data analysis tool**: landing page → workspace dashboard → upload files → agent discovers links and schema → user approves the data map → dashboard generated → chat spawns per-question dashboard views → export. Link discovery + dashboards are the hero; anomalies are one engine feeding the dashboard. The schema surface also contains the generated data guide.

## Layout

- Python backend lives in `src/cerno/` (src-layout). Import as `cerno.foo`, never `backend.cerno.foo`.
- Frontend lives in `frontend/` with its own `package.json` and `node_modules/`.
- Packaging (PyInstaller, updater, signing) is the last phase. Signing cert procurement runs in parallel from day 1.

## Stack discipline

- Python **3.12** (pinned in `.python-version`). No 3.13/3.14 without a PyInstaller smoke test.
- `uv` for Python deps. `bun` for frontend deps. Never mix in npm/pip.
- **Analysis model:** model-runs / user-reviews. The agent can use Python-backed tools to inspect approved session data and render dashboard widgets. The user-facing product is dashboard views plus schema guidance.
- Tables in `run_python` are exposed as pandas DataFrames, one per ingested file, named by a slugified filename.
- Excel + CSV only for ingest in v1. PDF export allowed (reportlab for 1-pager brief).
- ECharts is in scope for dashboard widgets. Tree-shake imports.

## Verification and deploys

- After every service change, run `cd frontend && bun run build`.
- Anomaly thresholds (MAD 3.5, rare-value 1%, key-overlap 60%) are first-cut numbers. Tune against Dad's eval set before shipping. Do not silently change thresholds without updating the canonical product notes.
- All thresholds live in `~/.cerno/config.toml`, hot-reloaded, user-editable in Settings.
- No `git push` or release actions without explicit user approval. No auto-update testing against Dad's machine without explicit user approval.

## LLM calls

- LLM connection is a trusted private pipe. No PII denylists, no column redaction gates — data flows freely to the model.
- Numeric answers in chat go through `run_sql` or `run_python` (slot-filled into the response). Schema questions route through a non-tool path reading cached schema JSON directly.
- The agent decides text-in-chat vs. spawn-a-dashboard-page based on whether its final answer emits widgets.
- Before changing or recommending an LLM model name, web-search for the latest releases from OpenAI/Anthropic/Google so the choice reflects current state. Don't rely on memory — model lineups shift faster than the training cutoff.

## Style

- TypeScript over JS. Named exports. Functional components. Early returns.
- Write no comments unless the WHY is non-obvious. Don't narrate the code.
- Don't add features beyond the task. No hypothetical-future abstractions.
