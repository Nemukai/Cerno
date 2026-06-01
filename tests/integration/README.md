# Integration Tests

These tests exercise Cerno against a real Postgres database with the pgvector
extension. They require Docker or another compatible container runtime.

Run them explicitly:

```bash
uv run pytest -m integration tests/integration
```

The default quick suite skips them via the `integration` pytest marker:

```bash
uv run pytest
```
