# Phase 5-G1B — Raw Cache Gitignore Safety Patch (v1)

**Track A (quant brain) repo-hygiene patch. Read-only audit. Preview-only.** No live
collection, no Alpha Vantage call, no API key, no network, no new paid data, no provider
shopping, no FMP / SimFin work, no model training, no deployment, no Paper Trader / GCP,
no orders / broker / automation, no binary artifacts, no package installs. No existing raw
file is deleted, modified, or untracked. No commit, no push.

## Why this phase exists

Phase 5-G1 found the existing Phase 3-M earnings collector safely reusable to expand
PIT-safe coverage from 50→75(→128), and recommended `READY_FOR_CONTROLLED_EARNINGS_COLLECTION`.
The one open repo-hygiene caveat: there was **no repo-root `.gitignore`**, so the 50 raw
Alpha Vantage payloads were tracked and — more importantly — any **future** raw payload
written by a `--live` run would be stageable by accident. This phase closes that gap
**before** any live collection.

## What changed

### 1 — New repo-root `.gitignore`

There was no root `.gitignore` (only directory-local ones under `research/data/fmp/` and
`research/data/simfin/`, which use the same `raw/` + belt-and-braces convention). A new
root `.gitignore` was created with:

```gitignore
# Phase 3-M earnings collector raw Alpha Vantage payloads (explicit target).
research/output/phase3m_earnings_estimates_signal_gate/raw/

# Belt-and-braces: any per-provider raw payload directory under research/output.
research/output/*/raw/
```

The file's header comments explain, per the task: (a) existing historical raw files may
already be tracked and are intentionally left in place; (b) the rule prevents newly
collected raw payloads from being accidentally staged; (c) committed-safe summary
artifacts under `research/output/<phase>/` remain allowed (only the per-provider `raw/`
payload dirs are ignored). The `*` in `research/output/*/raw/` does not cross `/`, so it
matches only `research/output/<phase>/raw/` dirs (currently phase3f, phase3g, phase3l,
phase3m) and never a committed-safe summary artifact one level up.

### 2 — Audit generator + committed-safe artifacts

`research/run_phase5g1b_raw_cache_gitignore_safety.py` is a read-only audit (no network,
no key). It writes two committed-safe artifacts to
`research/output/phase5g1b_raw_cache_gitignore_safety/`:

- `phase5g1b_raw_cache_gitignore_safety.json` — the full report (all required fields).
- `raw_cache_gitignore_audit.csv` — flat check / value / detail audit rows.

### 3 — Tests

`tests/test_phase5g1b_raw_cache_gitignore_safety.py` (16 tests) verifies the patch and the
forbidden-surface guarantees. Every run is redirected to a temp output dir, so the suite
can never overwrite the committed artifacts.

## Key git semantic (verified)

`git check-ignore` **never reports an already-tracked path as ignored.** The 50 historical
Alpha Vantage payloads under the Phase 3-M raw dir remain tracked and are intentionally
left in place, so `git check-ignore` of the raw **directory** still returns *not ignored*
(exit 1). That is correct and expected — the existing files are genuinely not ignored.

The meaningful safety property is proven against a **new, untracked** payload:

```text
$ git check-ignore -v research/output/phase3m_earnings_estimates_signal_gate/raw/alpha_vantage_ZZZZ_HYPOTHETICAL_NEW_PAYLOAD.json
.gitignore:34:research/output/*/raw/   ...raw/alpha_vantage_ZZZZ_HYPOTHETICAL_NEW_PAYLOAD.json   (exit 0 = ignored)
```

So a future `--live` run that writes, e.g., `alpha_vantage_GS.json` produces a file that
Git ignores by default — it cannot be accidentally staged.

## Audit findings

| Check | Value |
|---|---|
| `root_gitignore_exists` | True |
| `raw_cache_ignore_rule_present` | True (both rules) |
| `hypothetical_new_raw_payload_ignored` | True |
| `phase5g1_committed_safe_artifacts_not_ignored` | True (0 / 6 ignored) |
| `existing_raw_files_present_count` | 50 (unchanged) |
| `existing_raw_files_tracked_count` | 50 (unchanged) |
| `existing_raw_files_deleted` | False |
| `existing_raw_files_untracked` | False |
| `phase3m_outputs_modified` | False |
| `network_used` / `paid_apis_used` / `live_collection_run` | False |

## Recommendation

**`READY_FOR_CONTROLLED_LIVE_EARNINGS_COLLECTION`.** Future raw earnings payloads are now
gitignored by default; existing tracked data is untouched; committed-safe artifacts remain
tracked. Controlled live collection is now safe from a git-hygiene standpoint.

### Recommended next phase

**5-G1-LIVE** — run the controlled live Phase 3-M earnings collection via the Phase 5-G1
wrapper to expand coverage 50→≥75, then proceed to **5-G2** (event-alpha rerun). The exact
live command (NOT executed here):

```powershell
Set-Location C:\Users\binis\Stock_Prediction_app_push
$env:ALPHAVANTAGE_API_KEY = "<your_key>"
python research\run_phase5g1_earnings_coverage_expansion.py --live --max-new-tickers 20
```

## Run commands (audit + tests)

```powershell
Set-Location C:\Users\binis\Stock_Prediction_app_push
python research\run_phase5g1b_raw_cache_gitignore_safety.py
python -m pytest tests\test_phase5g1b_raw_cache_gitignore_safety.py -q
```

## Safety contract

Read-only audit · no live network / API call · no API key · no live collection · no
provider shopping · existing raw payloads neither deleted, modified, nor untracked ·
committed-safe text artifacts only · no Paper Trader / GCP / deploy · no orders / broker /
automation · no binary artifacts · no package installs · no commit · no push.
