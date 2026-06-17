"""Phase 2G-B — real-data CSV validation runner (strict, offline / read-only).

A thin, **strict wrapper** around the Phase 2G-A shadow validation harness
(``research/run_phase2g_shadow_validation.py``) intended for a real, read-only
historical price CSV export. This phase does **not** export from the production
database and does **not** run against GCP — it prepares the safe local runner,
the validation gate, and the runbook so a later phase (2G-C) can drop in a real
read-only CSV and execute it without changing any live behavior.

What this wrapper adds on top of the 2G-A harness:

  * **Two explicit gates before any real-data run.** It refuses to run unless
    an explicit ``--input-csv`` path is given, and refuses to treat the data as
    real unless ``--confirm-real-data`` is also given. A dedicated
    ``--test-fixture-mode`` flag (used only by the tests) bypasses the
    real-data confirmation but forces the output to be marked as a non-real,
    non-edge, NO_GO fixture run.
  * **Input pre-flight validation.** Before invoking the harness it checks the
    CSV has the required columns (``ticker, date, adj_close``), that the
    benchmark ticker (default ``SPY``) is present unless ``--benchmark`` is
    overridden, and that the export meets minimum breadth/length thresholds
    (``--min-tickers`` default 20, ``--min-dates`` default 120 for real data).
  * **A go / no-go verdict.** It surfaces the harness ``safe_for_canary`` result
    and renders an explicit ``go_no_go`` of ``GO_CANDIDATE`` or ``NO_GO`` with a
    human-readable reason. A GO_CANDIDATE is *still only a candidate*; enabling
    ``PREDICTOR_USE_MODEL_V2`` remains a separate, later, human-approved phase.

Safety contract (enforced by ``tests/test_phase2g_real_data_validation.py``):
  * **No DB connection**, no migration, no deployment, no ``gcloud``, no SSH, no
    service restart, no env-var write. Offline and file-based by construction.
  * Does **not** import or modify ``api_server.py``; does **not** import Paper
    Trader; performs no trade execution, no order placement, and no scheduling.
  * Never enables ``PREDICTOR_USE_MODEL_V2`` (the summary records
    ``model_v2_enabled: false``).
  * **Never claims a production edge.** ``production_edge_claimed`` is always
    false; fixture / test output is marked ``test_fixture_mode: true``,
    ``safe_for_canary: false``, ``go_no_go: NO_GO``.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import sys
from typing import Any, Dict, List, Optional, Sequence, Tuple

import pandas as pd

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from research import run_phase2g_shadow_validation as shadow  # noqa: E402

PHASE = "2G-B"
FEATURE_FLAG_ENV = shadow.FEATURE_FLAG_ENV
REQUIRED_COLUMNS = shadow.REQUIRED_COLUMNS  # ("ticker", "date", "adj_close")
DEFAULT_BENCHMARK = shadow.DEFAULT_BENCHMARK

# Minimum breadth / length we expect of a *real* export before we even attempt a
# shadow validation. Deliberately conservative; overridable on the CLI.
DEFAULT_MIN_TICKERS = 20
DEFAULT_MIN_DATES = 120

DEFAULT_OUTPUT_JSON = os.path.join(
    "research", "output", "phase2g_real_data_validation.json")
DEFAULT_SCORED_CSV = os.path.join(
    "research", "output", "phase2g_real_data_scored.csv")

GO_CANDIDATE = "GO_CANDIDATE"
NO_GO = "NO_GO"


# --------------------------------------------------------------------------- #
# Errors
# --------------------------------------------------------------------------- #
class ValidationRefusal(Exception):
    """Raised when a pre-flight gate refuses to run. Carries an exit code."""

    def __init__(self, message: str, *, exit_code: int = 2) -> None:
        super().__init__(message)
        self.exit_code = exit_code


# --------------------------------------------------------------------------- #
# Pre-flight input validation (read-only)
# --------------------------------------------------------------------------- #
def validate_input_csv(path: str, *, benchmark: str = DEFAULT_BENCHMARK,
                       require_benchmark: bool = True,
                       min_tickers: int = DEFAULT_MIN_TICKERS,
                       min_dates: int = DEFAULT_MIN_DATES
                       ) -> Tuple[bool, List[str], Dict[str, Any]]:
    """Read-only pre-flight check of a price CSV. Never writes, never mutates.

    Returns ``(ok, problems, stats)``. ``ok`` is True only when the file exists,
    has the required columns, contains the benchmark (unless ``require_benchmark``
    is False), and meets the minimum ticker / date thresholds.
    """
    problems: List[str] = []
    stats: Dict[str, Any] = {
        "input_csv": path, "row_count": 0, "ticker_count": 0,
        "universe_ticker_count": 0, "date_count": 0, "benchmark": benchmark,
        "benchmark_present": False, "min_tickers": min_tickers,
        "min_dates": min_dates,
    }

    if not path:
        problems.append("no --input-csv provided")
        return False, problems, stats
    if not os.path.exists(path):
        problems.append(f"input CSV does not exist: {path}")
        return False, problems, stats

    df = pd.read_csv(path)
    missing = [c for c in REQUIRED_COLUMNS if c not in df.columns]
    if missing:
        problems.append(f"input CSV missing required columns: {missing}")
        return False, problems, stats

    tickers = set(str(t) for t in df["ticker"].dropna().unique())
    universe = sorted(t for t in tickers if t != benchmark)
    dates = pd.to_datetime(df["date"], errors="coerce").dropna()

    stats.update({
        "row_count": int(len(df)),
        "ticker_count": int(len(tickers)),
        "universe_ticker_count": int(len(universe)),
        "date_count": int(dates.dt.date.nunique()),
        "benchmark_present": benchmark in tickers,
    })

    if require_benchmark and not stats["benchmark_present"]:
        problems.append(
            f"benchmark ticker {benchmark!r} not present in CSV "
            "(override with --benchmark or disable the check)")
    if stats["universe_ticker_count"] < min_tickers:
        problems.append(
            f"too few universe tickers: {stats['universe_ticker_count']} "
            f"< required {min_tickers}")
    if stats["date_count"] < min_dates:
        problems.append(
            f"too few distinct dates: {stats['date_count']} "
            f"< required {min_dates}")

    return (not problems), problems, stats


# --------------------------------------------------------------------------- #
# Verdict
# --------------------------------------------------------------------------- #
def _go_no_go(*, confirm_real_data: bool, test_fixture_mode: bool,
              safe_for_canary: bool) -> Tuple[str, str]:
    """Render the go/no-go verdict + reason. Fail-safe toward NO_GO."""
    if test_fixture_mode:
        return NO_GO, ("test-fixture-mode run: synthetic data only, not real "
                       "and not production edge")
    if not confirm_real_data:
        return NO_GO, "real-data run not confirmed (--confirm-real-data absent)"
    if safe_for_canary:
        return GO_CANDIDATE, ("real-data shadow validation cleared the harness "
                              "canary gate (safe_for_canary=true); a canary is a "
                              "candidate only and still requires separate human "
                              "approval to enable PREDICTOR_USE_MODEL_V2")
    return NO_GO, ("real-data shadow validation did not clear the harness canary "
                   "gate (safe_for_canary=false)")


# --------------------------------------------------------------------------- #
# Summary assembly
# --------------------------------------------------------------------------- #
def build_runner_summary(*, phase2g_a_summary: Optional[Dict[str, Any]],
                         input_csv: str, confirm_real_data: bool,
                         test_fixture_mode: bool,
                         real_data_validation_attempted: bool,
                         benchmark: str, input_stats: Dict[str, Any]
                         ) -> Dict[str, Any]:
    """Assemble the 2G-B runner summary document (pure, JSON-safe)."""
    safe_for_canary = bool(
        phase2g_a_summary is not None
        and phase2g_a_summary.get("safe_for_canary") is True
        and not test_fixture_mode)
    go_no_go, reason = _go_no_go(
        confirm_real_data=confirm_real_data,
        test_fixture_mode=test_fixture_mode,
        safe_for_canary=safe_for_canary)

    return {
        "phase": PHASE,
        "generated_at": dt.datetime.now().isoformat(timespec="seconds"),
        "input_mode": "csv",
        "input_csv": input_csv,
        "benchmark": benchmark,
        "feature_flag": FEATURE_FLAG_ENV,
        # Gate state.
        "confirm_real_data": bool(confirm_real_data),
        "test_fixture_mode": bool(test_fixture_mode),
        "real_data_validation_attempted": bool(real_data_validation_attempted),
        # Safety flags (machine-readable; asserted by the tests).
        "database_touched": False,
        "migration_executed": False,
        "deployment_executed": False,
        "model_v2_enabled": False,
        "production_edge_claimed": False,
        "no_trading": True,
        "no_orders": True,
        "no_automation": True,
        # Verdict.
        "safe_for_canary": safe_for_canary,
        "go_no_go": go_no_go,
        "go_no_go_reason": reason,
        # Pre-flight stats + nested harness result.
        "input_stats": input_stats,
        "phase2g_a_summary": phase2g_a_summary,
    }


# --------------------------------------------------------------------------- #
# Orchestration
# --------------------------------------------------------------------------- #
def run_real_data_validation(*, input_csv: str, output_json: str,
                             scored_csv: Optional[str] = None,
                             confirm_real_data: bool = False,
                             test_fixture_mode: bool = False,
                             benchmark: str = DEFAULT_BENCHMARK,
                             horizon: int = shadow.DEFAULT_HORIZON,
                             min_history: int = shadow.DEFAULT_MIN_HISTORY,
                             min_tickers: int = DEFAULT_MIN_TICKERS,
                             min_dates: int = DEFAULT_MIN_DATES,
                             legacy_csv: Optional[str] = None,
                             require_benchmark: bool = True) -> Dict[str, Any]:
    """Validate the CSV, then (if it passes) run the 2G-A harness and verdict.

    Raises :class:`ValidationRefusal` when a gate refuses the run. In
    ``test_fixture_mode`` the breadth/length thresholds are relaxed (a fixture is
    intentionally small) but every honesty flag is forced safe.
    """
    if not input_csv:
        raise ValidationRefusal("refusing to run: --input-csv is required")
    if not confirm_real_data and not test_fixture_mode:
        raise ValidationRefusal(
            "refusing to run: real-data validation requires --confirm-real-data "
            "(or --test-fixture-mode for tests)")

    # In fixture mode the export is intentionally small; don't enforce real-data
    # breadth/length minimums, but still validate columns + benchmark presence.
    eff_min_tickers = 1 if test_fixture_mode else min_tickers
    eff_min_dates = 1 if test_fixture_mode else min_dates

    ok, problems, stats = validate_input_csv(
        input_csv, benchmark=benchmark, require_benchmark=require_benchmark,
        min_tickers=eff_min_tickers, min_dates=eff_min_dates)
    if not ok:
        raise ValidationRefusal(
            "refusing to run: input CSV failed pre-flight validation: "
            + "; ".join(problems))

    # fixture_only is true unless this is a confirmed real-data run (and never in
    # test-fixture-mode). This is the only switch that lets the harness mark the
    # data as real, and it is gated behind --confirm-real-data.
    fixture_only = test_fixture_mode or (not confirm_real_data)

    phase2g_a_summary = shadow.run_validation(
        input_csv, output_json=None, fixture_only=fixture_only,
        horizon=horizon, min_history=min_history, benchmark=benchmark,
        legacy_csv=legacy_csv, scored_csv=scored_csv)

    real_data_validation_attempted = bool(confirm_real_data and not test_fixture_mode)

    summary = build_runner_summary(
        phase2g_a_summary=phase2g_a_summary, input_csv=input_csv,
        confirm_real_data=confirm_real_data, test_fixture_mode=test_fixture_mode,
        real_data_validation_attempted=real_data_validation_attempted,
        benchmark=benchmark, input_stats=stats)

    if scored_csv:
        summary["scored_csv"] = scored_csv
    if output_json:
        shadow.write_json(output_json, summary)
        summary["output_json"] = output_json
    return summary


def build_arg_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="Phase 2G-B real-data CSV validation runner (offline / "
                    "read-only). Strict wrapper around the 2G-A shadow harness: "
                    "validates a real price CSV export and emits a go/no-go "
                    "verdict. No DB, no deploy, no gcloud, no SSH, no flag "
                    "enable, no trading.")
    p.add_argument("--input-csv", type=str, default=None,
                   help="Real, read-only price CSV (ticker,date,adj_close"
                        "[,volume]). REQUIRED.")
    p.add_argument("--confirm-real-data", action="store_true",
                   help="Explicitly confirm the input is a real, read-only "
                        "export. REQUIRED for a real run (sets fixture_only="
                        "false in the harness).")
    p.add_argument("--test-fixture-mode", action="store_true",
                   help="Tests-only: bypass --confirm-real-data and force the "
                        "output to a non-real, non-edge, NO_GO fixture run.")
    p.add_argument("--output-json", type=str, default=DEFAULT_OUTPUT_JSON,
                   help=f"Summary JSON path (default {DEFAULT_OUTPUT_JSON}).")
    p.add_argument("--scored-csv", type=str, default=DEFAULT_SCORED_CSV,
                   help=f"Scored per-row CSV path (default {DEFAULT_SCORED_CSV}).")
    p.add_argument("--legacy-csv", type=str, default=None,
                   help="Optional legacy-prediction CSV enabling a comparison "
                        "(required for the harness canary gate to pass).")
    p.add_argument("--benchmark", type=str, default=DEFAULT_BENCHMARK,
                   help="Benchmark ticker for excess-return labels.")
    p.add_argument("--no-require-benchmark", action="store_true",
                   help="Disable the benchmark-presence pre-flight check.")
    p.add_argument("--horizon-days", type=int, default=shadow.DEFAULT_HORIZON)
    p.add_argument("--min-history", type=int, default=shadow.DEFAULT_MIN_HISTORY)
    p.add_argument("--min-tickers", type=int, default=DEFAULT_MIN_TICKERS,
                   help=f"Minimum universe tickers (default {DEFAULT_MIN_TICKERS}).")
    p.add_argument("--min-dates", type=int, default=DEFAULT_MIN_DATES,
                   help=f"Minimum distinct dates (default {DEFAULT_MIN_DATES}).")
    return p


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = build_arg_parser().parse_args(argv)
    try:
        summary = run_real_data_validation(
            input_csv=args.input_csv, output_json=args.output_json,
            scored_csv=args.scored_csv,
            confirm_real_data=args.confirm_real_data,
            test_fixture_mode=args.test_fixture_mode,
            benchmark=args.benchmark, horizon=args.horizon_days,
            min_history=args.min_history, min_tickers=args.min_tickers,
            min_dates=args.min_dates, legacy_csv=args.legacy_csv,
            require_benchmark=not args.no_require_benchmark)
    except ValidationRefusal as e:
        print(f"[phase2g-b] REFUSED: {e}")
        return e.exit_code

    print(f"[phase2g-b] input csv          : {summary['input_csv']}")
    print(f"[phase2g-b] confirm_real_data  : {summary['confirm_real_data']}")
    print(f"[phase2g-b] test_fixture_mode  : {summary['test_fixture_mode']}")
    print(f"[phase2g-b] universe / dates   : "
          f"{summary['input_stats']['universe_ticker_count']} / "
          f"{summary['input_stats']['date_count']}")
    inner = summary.get("phase2g_a_summary") or {}
    print(f"[phase2g-b] rank IC            : {inner.get('rank_ic')}")
    print(f"[phase2g-b] safe_for_canary    : {summary['safe_for_canary']}")
    print(f"[phase2g-b] production_edge     : {summary['production_edge_claimed']}")
    print(f"[phase2g-b] model_v2_enabled   : {summary['model_v2_enabled']}")
    print(f"[phase2g-b] database_touched   : {summary['database_touched']}")
    print(f"[phase2g-b] GO/NO-GO           : {summary['go_no_go']}")
    print(f"[phase2g-b] reason             : {summary['go_no_go_reason']}")
    print(f"[phase2g-b] summary JSON       : {summary.get('output_json')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
