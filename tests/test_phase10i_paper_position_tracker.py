"""Targeted tests for Phase 10-I paper-only position tracker.

Verifies the user's explicit asks and the standing safety contract:
  - the selected book is read and validated (paper-only, NO_ORDER, equal weights, 25/25 or as declared)
  - mark-to-market uses ONLY owned local prices; pending when no post-inception local price
  - no provider acquisition / no live API / no Paper Trader writes / no signals / no trade decisions /
    no orders / no automation
  - order_action stays NO_ORDER; equal weights preserved
  - exposure summary + safety badges + status are written
  - final decision is in the allowed set
"""
import csv
import json
import sys
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from research import run_phase10i_paper_position_tracker as i10  # noqa: E402

_PF_HEADER = ("side,rank_in_side,ticker,sector,comp_sn,comp_sn_z,liquidity_proxy,cohort,"
              "target_weight,target_weight_pct,side_gross_pct,weighting,review_status,order_action")

# (side, ticker, sector, liq)
_BOOK = [
    ("LONG", "LA", "Information Technology", 9.0e8),
    ("LONG", "LB", "Financials", 4.0e8),
    ("LONG", "LC", "Energy", 2.0e8),
    ("SHORT", "SA", "Information Technology", 8.0e8),
    ("SHORT", "SB", "Financials", 3.0e8),
    ("SHORT", "SC", "Energy", 1.5e8),
]


def _write_portfolio(tmp_path, book=_BOOK, weight=0.04, order_action="NO_ORDER",
                     review_status="PAPER_REVIEW_ONLY", weights=None):
    lines = [_PF_HEADER]
    for i, (side, tk, sec, liq) in enumerate(book):
        w = weights[i] if weights else weight
        lines.append(f"{side},{i+1},{tk},{sec},1.0,1.0,{liq},old,{w},{w*100},100.0,EQUAL,"
                     f"{review_status},{order_action}")
    pf = tmp_path / "selected_paper_portfolio.csv"
    pf.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return pf


def _write_h10(tmp_path, n_long=3, n_short=3):
    j = tmp_path / "phase10h.json"
    j.write_text(json.dumps({
        "as_of": "2026-06-26", "expected_rebalance_date": "2026-09-30",
        "n_long": n_long, "n_short": n_short, "long_weight_each": 0.04, "short_weight_each": 0.04,
        "rebalance_cadence": "QUARTERLY",
    }), encoding="utf-8")
    return j


def _write_d10(tmp_path):
    j = tmp_path / "phase10d.json"
    # composite_raw scores higher, but the book ranks sector-neutral -> composite_sn is the benchmark
    j.write_text(json.dumps({"signal_results": [
        {"signal": "composite_raw", "ic_t_63d": 3.0, "quarterly_net_25bps": 0.0065,
         "quarterly_net_50bps": 0.0035, "quarterly_turnover": 0.6},
        {"signal": "composite_sn", "ic_t_63d": 2.5, "quarterly_net_25bps": 0.004,
         "quarterly_net_50bps": 0.001, "quarterly_turnover": 0.61},
        {"signal": "operating_accruals", "ic_t_63d": 3.1, "quarterly_net_25bps": 0.0053},
    ]}), encoding="utf-8")
    return j


def _write_eod(eod_dir, ticker, series):
    eod_dir.mkdir(parents=True, exist_ok=True)
    (eod_dir / f"{ticker}.json").write_text(
        json.dumps([{"date": d, "adjusted_close": px, "close": px} for d, px in series]),
        encoding="utf-8")


def _read(path):
    with open(path, newline="", encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


@pytest.fixture()
def env(tmp_path):
    pf = _write_portfolio(tmp_path)
    h10 = _write_h10(tmp_path)
    d10 = _write_d10(tmp_path)
    eod = tmp_path / "eod"
    # LA/SA have a post-inception price -> MARKED; LB/SB end at inception -> PENDING; LC/SC missing.
    _write_eod(eod, "LA", [("2026-06-26", 100.0), ("2026-07-30", 110.0)])   # long +10%
    _write_eod(eod, "SA", [("2026-06-26", 50.0), ("2026-07-30", 45.0)])     # short of -10% -> +10%
    _write_eod(eod, "LB", [("2026-05-01", 20.0), ("2026-06-26", 22.0)])     # pending
    _write_eod(eod, "SB", [("2026-05-01", 30.0), ("2026-06-26", 28.0)])     # pending
    out = tmp_path / "out"
    rep = i10.run(out_dir=out, portfolio_csv=pf, h10_json=h10, d10_json=d10, eod_dir=eod,
                  verbose=False)
    return {
        "rep": rep, "out": out,
        "ledger": _read(out / i10._ARTIFACTS["ledger"]),
        "plan": _read(out / i10._ARTIFACTS["mtm_plan"]),
        "snap": _read(out / i10._ARTIFACTS["mtm_snapshot"]),
        "exp_real": _read(out / i10._ARTIFACTS["exp_real"]),
        "exposure": _read(out / i10._ARTIFACTS["exposure"]),
        "sector": _read(out / i10._ARTIFACTS["sector"]),
        "badges": _read(out / i10._ARTIFACTS["badges"]),
        "status": _read(out / i10._ARTIFACTS["status"]),
    }


def test_selected_book_read_and_decision_ready_when_marked(env):
    rep = env["rep"]
    assert rep["decision"] == "PAPER_POSITION_TRACKER_READY"
    assert rep["decision"] in i10.ALLOWED_DECISIONS
    assert rep["n_holdings"] == 6 and rep["n_long"] == 3 and rep["n_short"] == 3
    for name in i10._ARTIFACTS.values():
        assert (env["out"] / name).exists(), f"missing artifact {name}"


def test_mark_to_market_uses_local_prices_only(env):
    rep = env["rep"]
    assert rep["mtm_status"] == "COMPUTED"
    assert rep["no_live_market_api"] is True
    assert rep["price_coverage"] == {"n_marked": 2, "n_pending": 2, "n_no_price": 2}
    snap = {r["ticker"]: r for r in env["snap"]}
    assert snap["LA"]["price_status"] == "MARKED"
    assert float(snap["LA"]["paper_return_pct"]) == pytest.approx(10.0, abs=1e-6)
    assert float(snap["LA"]["side_signed_return_pct"]) == pytest.approx(10.0, abs=1e-6)
    # short profits when price falls: raw -10% but side-signed +10%
    assert float(snap["SA"]["paper_return_pct"]) == pytest.approx(-10.0, abs=1e-6)
    assert float(snap["SA"]["side_signed_return_pct"]) == pytest.approx(10.0, abs=1e-6)
    assert snap["LB"]["price_status"] == "PENDING_PRICE_REFRESH"
    assert snap["LC"]["price_status"] == "NO_LOCAL_PRICE"
    assert rep["realized_paper_return_to_date"] == pytest.approx(0.008, abs=1e-6)
    assert rep["holding_period_days_elapsed"] == 34


def test_pending_when_no_post_inception_price(tmp_path):
    pf = _write_portfolio(tmp_path)
    h10 = _write_h10(tmp_path)
    d10 = _write_d10(tmp_path)
    eod = tmp_path / "eod_empty"          # no files -> nothing can be marked
    eod.mkdir()
    rep = i10.run(out_dir=tmp_path / "out", portfolio_csv=pf, h10_json=h10, d10_json=d10,
                  eod_dir=eod, verbose=False)
    assert rep["decision"] == "PAPER_POSITION_TRACKER_READY_PENDING_PRICE_REFRESH"
    assert rep["mtm_status"] == "PENDING_PRICE_REFRESH"
    assert rep["realized_paper_return_to_date"] is None


def test_order_action_no_order_and_equal_weights_preserved(env):
    assert all(r["order_action"] == "NO_ORDER" for r in env["ledger"])
    long_w = {r["target_weight"] for r in env["ledger"] if r["side"] == "LONG"}
    short_w = {r["target_weight"] for r in env["ledger"] if r["side"] == "SHORT"}
    assert len(long_w) == 1 and len(short_w) == 1          # equal within each side
    assert env["rep"]["order_action_all"] == "NO_ORDER"


def test_exposure_summary_and_net_zero(env):
    exp = {r["metric"]: r["value"] for r in env["exposure"]}
    assert exp["n_long"] == "3" and exp["n_short"] == "3"
    assert float(exp["long_gross_pct"]) == float(exp["short_gross_pct"])
    assert float(env["rep"]["net_pct"]) == 0.0
    assert float(env["rep"]["gross_pct"]) == pytest.approx(
        float(exp["long_gross_pct"]) + float(exp["short_gross_pct"]), abs=1e-6)
    # sector exposure written for both sides
    assert {r["side"] for r in env["sector"]} == {"LONG", "SHORT"}


def test_expected_benchmark_read_from_10d(env):
    rep = env["rep"]
    # the book ranks sector-neutral -> benchmark is composite_sn, NOT the higher raw/leg signals
    assert rep["expected_benchmark_signal"] == "composite_sn"
    assert rep["expected_quarterly_net_25bps"] == pytest.approx(0.004)
    assert rep["expected_quarterly_net_50bps"] == pytest.approx(0.001)
    er = {r["metric"]: r for r in env["exp_real"]}
    assert er["quarterly_net_25bps_spread"]["expected"] == "0.004"


def test_safety_badges_and_status_written(env):
    badges = {r["badge"] for r in env["badges"]}
    for b in ("PAPER TRACKING ONLY", "NO ORDERS", "NO AUTOMATION", "NO BROKER",
              "HUMAN REVIEW REQUIRED"):
        assert b in badges
    status = {r["field"]: r["value"] for r in env["status"]}
    assert status["NO ORDERS"] == "CONFIRMED" and status["NO AUTOMATION"] == "CONFIRMED"
    assert status["NO BROKER"] == "CONFIRMED" and status["PAPER TRACKING ONLY"] == "YES"


def test_no_paper_trader_orders_or_automation(env):
    rep = env["rep"]
    for k in ("wrote_to_paper_trader", "creates_orders", "creates_automation",
              "creates_paper_trader_signals", "creates_trade_decisions", "live_trading",
              "broker_connected", "deploy", "performs_provider_acquisition", "performs_network"):
        assert rep[k] is False, f"{k} must be False"
    parts = [p.lower() for p in i10._DEFAULT_OUT.parts]
    assert "research" in parts and "output" in parts
    assert not any(p in parts for p in ("api", "app", "paper_trader_app"))


def test_secret_audit_written_and_clean(env):
    assert (env["out"] / i10._ARTIFACTS["secret_audit"]).exists()
    assert env["rep"]["secret_safety_leak_scan_clean"] is True
    assert env["rep"]["api_key_printed"] is False


def test_source_has_no_network_order_automation_or_pt_write_tokens():
    src = (_REPO_ROOT / "research" / "run_phase10i_paper_position_tracker.py").read_text(
        encoding="utf-8")
    banned = [
        "requests.get", "requests.post", "urllib.request", "http://", "https://", "socket.",
        "create_order", "submit_order", "place_order", "OrderModel", "broker.",
        "session.add", "db.add", ".commit(", "schedule.every", "crontab", "APScheduler",
        "subprocess", "os.system",
    ]
    hits = [t for t in banned if t in src]
    assert not hits, f"source contains banned token(s): {hits}"


def test_decision_sets_allowed_and_disjoint():
    assert set(i10.ALLOWED_DECISIONS).isdisjoint(set(i10.FORBIDDEN_DECISIONS))


def test_blocked_missing_selected_book(tmp_path):
    rep = i10.run(out_dir=tmp_path / "out", portfolio_csv=tmp_path / "nope.csv", verbose=False)
    assert rep["decision"] == "PAPER_POSITION_TRACKER_BLOCKED_MISSING_SELECTED_BOOK"
    assert rep["decision"] in i10.ALLOWED_DECISIONS
    assert rep["wrote_to_paper_trader"] is False and rep["creates_orders"] is False


def test_blocked_invalid_portfolio_order_action(tmp_path):
    pf = _write_portfolio(tmp_path, order_action="BUY")     # not NO_ORDER -> invalid
    h10 = _write_h10(tmp_path)
    rep = i10.run(out_dir=tmp_path / "out", portfolio_csv=pf, h10_json=h10,
                  eod_dir=tmp_path / "eod", verbose=False)
    assert rep["decision"] == "PAPER_POSITION_TRACKER_BLOCKED_INVALID_PORTFOLIO"


def test_blocked_invalid_portfolio_unequal_weights(tmp_path):
    pf = _write_portfolio(tmp_path, weights=[0.04, 0.05, 0.04, 0.04, 0.04, 0.04])  # long unequal
    h10 = _write_h10(tmp_path)
    rep = i10.run(out_dir=tmp_path / "out", portfolio_csv=pf, h10_json=h10,
                  eod_dir=tmp_path / "eod", verbose=False)
    assert rep["decision"] == "PAPER_POSITION_TRACKER_BLOCKED_INVALID_PORTFOLIO"


def test_real_inputs_if_present():
    if not i10._DEF_PORTFOLIO.exists():
        pytest.skip("10-H selected portfolio not present in this checkout")
    rep = i10.run(verbose=False)
    assert rep["decision"] in i10.ALLOWED_DECISIONS
    assert rep["creates_orders"] is False and rep["creates_automation"] is False
    assert rep["wrote_to_paper_trader"] is False
    assert rep["net_pct"] == 0.0
