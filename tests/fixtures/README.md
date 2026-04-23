# Test fixtures

- `eval/` — real (anonymized) Excel files from Dad's office. **Git-ignored** (gov data stays local). See `docs/eval-set.md`.
- Small synthetic fixtures for unit tests are committed alongside the relevant `test_*.py` file.

Do not fabricate anomaly fixtures and tune the scoring against them — the thresholds must be calibrated on the real eval set.
