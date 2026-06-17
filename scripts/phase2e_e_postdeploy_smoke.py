"""Phase 2E-E — read-only post-deploy smoke test for the flag-OFF GCP service.

Probes a *running* prediction service over HTTP and verifies the flag-off
contract: the legacy routes answer, the legacy response keys are present, and the
model-v2 path is inactive (``model_v2`` absent or false). It is deliberately
inert:

  * Python **standard library only** (urllib/json/argparse) — no requests, no
    gcloud, no SSH, no DB driver.
  * **Read-only** HTTP: GET for ``/ping`` ``/healthz`` ``/config``
    ``/predict/{ticker}`` and a single POST to ``/predict_all_models/`` whose
    body only *names a ticker to read* a prediction for. No write/admin route is
    touched.
  * It **never** sets or mutates ``PREDICTOR_USE_MODEL_V2`` (or any env var),
    never opens a DB connection, never runs a migration, never deploys.

A ``--base-url`` is **required**; the script refuses to run without one. Point it
at the local tunnel (``http://127.0.0.1:9000``) or a staging URL — never a
production URL unless you have explicitly chosen to.

Run:
    python scripts/phase2e_e_postdeploy_smoke.py --base-url http://127.0.0.1:9000
    python scripts/phase2e_e_postdeploy_smoke.py --base-url http://127.0.0.1:9000 \
        --ticker AAPL --summary-output research/output/phase2e_e_smoke_run.json

Exit code is 0 when ``safe_flagoff_smoke`` is true, else 1.
"""
from __future__ import annotations

import argparse
import json
import sys
import urllib.error
import urllib.request

# Legacy response keys existing consumers read on a /predict response. With the
# flag off these must all be present.
LEGACY_KEYS = ("recommendation", "confidence", "agreement",
               "ensemble_day5", "predictions", "zscore")

FLAG = "PREDICTOR_USE_MODEL_V2"  # named only for documentation; never set here.

DEFAULT_TICKER = "AAPL"
_TIMEOUT = 30  # seconds; read-only requests, generous for cold model runs.


def _http_get(url: str) -> tuple[int, object]:
    """Issue a read-only GET and return (status, parsed-json-or-text)."""
    req = urllib.request.Request(url, method="GET")
    with urllib.request.urlopen(req, timeout=_TIMEOUT) as resp:  # noqa: S310
        body = resp.read().decode("utf-8", "replace")
        return resp.status, _maybe_json(body)


def _http_post_json(url: str, payload: dict) -> tuple[int, object]:
    """Issue a read-only POST (reads a prediction); body only names a ticker."""
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        url, data=data, method="POST",
        headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=_TIMEOUT) as resp:  # noqa: S310
        body = resp.read().decode("utf-8", "replace")
        return resp.status, _maybe_json(body)


def _maybe_json(text: str):
    try:
        return json.loads(text)
    except Exception:
        return text


def _ok_status(status: int) -> bool:
    return 200 <= status < 300


def _is_mapping(obj) -> bool:
    return isinstance(obj, dict)


def _legacy_keys_present(obj) -> bool:
    return _is_mapping(obj) and all(k in obj for k in LEGACY_KEYS)


def _model_v2_inactive(obj) -> bool:
    """True when the model-v2 path is OFF: ``model_v2`` absent or falsey."""
    if not _is_mapping(obj):
        return True
    if "model_v2" not in obj:
        return True
    return not bool(obj.get("model_v2"))


def _check_ping(base: str) -> bool:
    try:
        status, _ = _http_get(base + "/ping")
        return _ok_status(status)
    except Exception as e:  # noqa: BLE001
        print(f"  /ping error: {e}")
        return False


def _check_health(base: str) -> bool:
    try:
        status, _ = _http_get(base + "/healthz")
        return _ok_status(status)
    except Exception as e:  # noqa: BLE001
        print(f"  /healthz error: {e}")
        return False


def _check_config(base: str) -> bool:
    try:
        status, _ = _http_get(base + "/config")
        return _ok_status(status)
    except Exception as e:  # noqa: BLE001
        print(f"  /config error: {e}")
        return False


def _check_predict(base: str, ticker: str) -> tuple[bool, object]:
    try:
        status, body = _http_get(base + f"/predict/{ticker}")
        return _ok_status(status) and _is_mapping(body), body
    except Exception as e:  # noqa: BLE001
        print(f"  /predict/{ticker} error: {e}")
        return False, None


def _check_predict_all(base: str, ticker: str) -> tuple[bool, object]:
    try:
        status, body = _http_post_json(
            base + "/predict_all_models/", {"ticker": ticker})
        return _ok_status(status) and _is_mapping(body), body
    except Exception as e:  # noqa: BLE001
        print(f"  /predict_all_models/ error: {e}")
        return False, None


def run_smoke(base_url: str, ticker: str) -> dict:
    base = base_url.rstrip("/")
    print(f"smoke target: {base}  (ticker={ticker})")

    ping_ok = _check_ping(base)
    health_ok = _check_health(base)
    config_ok = _check_config(base)
    predict_ok, predict_body = _check_predict(base, ticker)
    predict_all_ok, predict_all_body = _check_predict_all(base, ticker)

    # Legacy-key / flag-off assertions evaluated against whichever predict
    # responses we actually received (both must hold where a body came back).
    bodies = [b for b in (predict_body, predict_all_body) if _is_mapping(b)]
    legacy_keys_present = bool(bodies) and all(_legacy_keys_present(b) for b in bodies)
    model_v2_inactive = all(_model_v2_inactive(b) for b in bodies)

    # This script performs no DB/migration/deploy actions, by construction.
    database_touched = False
    migration_executed = False
    deployment_executed = False

    safe_flagoff_smoke = all([
        ping_ok, health_ok, config_ok, predict_ok, predict_all_ok,
        legacy_keys_present, model_v2_inactive,
        not database_touched, not migration_executed, not deployment_executed,
    ])

    return {
        "phase": "2E-E",
        "base_url": base,
        "ticker": ticker,
        "feature_flag": FLAG,
        "ping_ok": ping_ok,
        "health_ok": health_ok,
        "config_ok": config_ok,
        "predict_ok": predict_ok,
        "predict_all_ok": predict_all_ok,
        "legacy_keys_present": legacy_keys_present,
        "model_v2_inactive": model_v2_inactive,
        "database_touched": database_touched,
        "migration_executed": migration_executed,
        "deployment_executed": deployment_executed,
        "safe_flagoff_smoke": safe_flagoff_smoke,
        "notes": (
            "Read-only HTTP smoke only. No env var was set, no gcloud/SSH, no "
            "DB connection, no migration, no deployment. PREDICTOR_USE_MODEL_V2 "
            "was never enabled by this script."
        ),
    }


def _parse_args(argv) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Read-only flag-off post-deploy smoke test (stdlib only).")
    # Required: refuse to run without an explicit target.
    p.add_argument("--base-url", required=True,
                   help="Base URL of the running service, e.g. "
                        "http://127.0.0.1:9000 (local tunnel) or a staging URL.")
    p.add_argument("--ticker", default=DEFAULT_TICKER,
                   help=f"Ticker to read a prediction for (default {DEFAULT_TICKER}).")
    p.add_argument("--summary-output", default=None,
                   help="Optional path to write the summary JSON. If omitted, "
                        "nothing is written to disk.")
    return p.parse_args(argv)


def main(argv=None) -> int:
    args = _parse_args(sys.argv[1:] if argv is None else argv)
    summary = run_smoke(args.base_url, args.ticker)

    print(json.dumps(summary, indent=2))

    if args.summary_output:
        import os
        out = args.summary_output
        out_dir = os.path.dirname(os.path.abspath(out))
        os.makedirs(out_dir, exist_ok=True)
        with open(out, "w", encoding="utf-8") as f:
            json.dump(summary, f, indent=2)
            f.write("\n")
        print(f"\nwrote {out}")

    return 0 if summary["safe_flagoff_smoke"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
