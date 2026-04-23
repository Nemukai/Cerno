# Eval set — Week 1 discovery deliverables

The Anomaly Queue's scoring thresholds (MAD 3.5, rare-value 1%, key-overlap 60%) are first-cut numbers from the design doc. They must be tuned against real files from Dad's office before v1 ships.

## Needed from Dad

- [ ] **3 anonymized Excel files** from a known-good past investigation — ones where the analysts already know which rows are real anomalies vs. red herrings.
- [ ] A ground-truth list: for each file, which row IDs are real issues and which looked suspicious but turned out to be nothing.
- [ ] Confirmation that `openrouter.ai` and `api.openai.com` are reachable from the office network.
- [ ] Sign-off on OpenRouter/OpenAI ZDR data-handling policy (legal, not vendor).
- [ ] 30 min shadow session with one junior analyst on a real investigation.

## Status

- Files received: **0 / 3**
- Network confirmed: **no**
- ZDR sign-off: **no**
- Shadow session: **not scheduled**

## How these feed the code

Once files arrive, put them in `tests/fixtures/eval/` (git-ignored — gov data stays local). Golden tests in `tests/test_anomaly.py` will assert that the top-20 queue surfaces the known real anomalies with the red herrings ranked below. Threshold tuning happens by running the sweep at different MAD/rare-value cutoffs and picking the one with the best precision/recall on the eval set.

Tracked in the design doc as Assignment steps 1–3.
