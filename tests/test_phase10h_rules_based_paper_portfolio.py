"""Targeted tests for Phase 10-H rules-based paper portfolio constructor.

Verifies the user's explicit asks and the standing safety contract:
  - portfolio is built from RULES, not per-ticker approval
  - selected book size is capped (<= target per side)
  - bottom-quartile liquidity filter is applied
  - 25%-of-side sector cap is applied
  - equal weighting / no optimised weights
  - extreme-score names are flagged and HELD OUT (not auto-included)
  - exceptions report + excluded report are written and explain every excluded name
  - no provider acquisition / no live API / no Paper Trader writes / no signals / no trade decisions /
    no orders / no automation
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

from research import run_phase10h_rules_based_paper_portfolio as h10  # noqa: E402

_BOOK_HEADER = ("side,rank_sn,ticker,comp_sn,comp_raw,sector,sector_is_unknown,"
                "sector_repaired,before_review_label,cohort,liquidity_proxy,review_status")
_CAND_HEADER = ("ticker,comp_sn_z,fcf_to_assets,operating_accruals,"
                "avail_fcf_to_assets,avail_operating_accruals")

_HIGH = 1_000_000_000.0   # high liquidity -> survives the p25 filter
_LOW = 1_000_000.0        # bottom-quartile liquidity -> excluded

# (side, ticker, comp_sn, sector, liq, z, fcf, accr, is_unknown)
_SPECS = [
    # ---- LONGS (ranked by comp_sn desc) ----
    ("LONG", "LXEXT", 9.0, "Energy", _HIGH, 5.0, 0.10, -0.05, False),     # extreme -> held out
    ("LONG", "LIT1", 8.5, "Information Technology", _HIGH, 1.8, 0.09, -0.04, False),  # select (IT slot)
    ("LONG", "LIT2", 8.0, "Information Technology", _HIGH, 1.7, 0.08, -0.03, False),  # sector_cap
    ("LONG", "LFIN1", 7.5, "Financials", _HIGH, 1.5, 0.07, -0.02, False),  # select
    ("LONG", "LHE1", 7.0, "Health Care", _HIGH, 1.2, 0.06, -0.02, False),  # select
    ("LONG", "LIND1", 6.5, "Industrials", _HIGH, 1.0, 0.05, -0.01, False),  # select (book full)
    ("LONG", "LCD1", 6.0, "Consumer Discretionary", _HIGH, 0.9, 0.04, -0.01, False),  # below cutoff
    ("LONG", "LLOW1", 5.5, "Materials", _LOW, 0.8, 0.03, -0.01, False),    # low liquidity
    ("LONG", "LLOW2", 5.0, "Utilities", _LOW, 0.7, 0.02, -0.01, False),    # low liquidity
    ("LONG", "LSEC", 4.5, "Unknown", _HIGH, 0.6, 0.02, -0.01, True),       # missing sector
    ("LONG", "LINP", 4.0, "Energy", _HIGH, 0.5, "", -0.01, False),         # missing composite input
    # ---- SHORTS (ranked by comp_sn asc) ----
    ("SHORT", "SXEXT", -9.0, "Energy", _HIGH, -5.0, -0.10, 0.05, False),   # extreme -> held out
    ("SHORT", "SIT1", -8.5, "Information Technology", _HIGH, -1.8, -0.09, 0.04, False),  # select
    ("SHORT", "SIT2", -8.0, "Information Technology", _HIGH, -1.7, -0.08, 0.03, False),  # sector_cap
    ("SHORT", "SFIN1", -7.5, "Financials", _HIGH, -1.5, -0.07, 0.02, False),  # select
    ("SHORT", "SHE1", -7.0, "Health Care", _HIGH, -1.2, -0.06, 0.02, False),  # select
    ("SHORT", "SIND1", -6.5, "Industrials", _HIGH, -1.0, -0.05, 0.01, False),  # select (book full)
    ("SHORT", "SLOW1", -6.0, "Materials", _LOW, -0.8, -0.03, 0.01, False),  # low liquidity
    ("SHORT", "SCD1", -5.5, "Consumer Discretionary", _HIGH, -0.7, -0.02, 0.01, False),  # below cutoff
]


def _write_inputs(tmp_path, specs=_SPECS):
    book_lines, cand_lines = [_BOOK_HEADER], [_CAND_HEADER]
    for i, (side, tk, sn, sec, liq, z, fcf, accr, unk) in enumerate(specs):
        book_lines.append(f"{side},{i+1},{tk},{sn},{sn},{sec},{unk},False,{side},old,{liq},"
                          f"PAPER_REVIEW_ONLY")
        cand_lines.append(f"{tk},{z},{fcf},{accr},2026-05-01,2026-05-01")
    book = tmp_path / "reranked_paper_review_long_short_book.csv"
    cand = tmp_path / "reranked_paper_review_candidate_list.csv"
    book.write_text("\n".join(book_lines) + "\n", encoding="utf-8")
    cand.write_text("\n".join(cand_lines) + "\n", encoding="utf-8")
    f10 = tmp_path / "phase10f_owned_sector_mapping_repair.json"
    f10.write_text(json.dumps({"as_of": "2026-06-26", "latest_quarter": "2026Q2"}), encoding="utf-8")
    return book, cand, f10


def _read(path):
    with open(path, newline="", encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


@pytest.fixture()
def env(tmp_path):
    book, cand, f10 = _write_inputs(tmp_path)
    out = tmp_path / "out"
    rep = h10.run(out_dir=out, book_csv=book, cand_csv=cand,
                  risk_csv=tmp_path / "no_risk.csv", f10_json=f10,
                  target_per_side=4, verbose=False)
    return {
        "rep": rep, "out": out,
        "portfolio": _read(out / h10._ARTIFACTS["portfolio"]),
        "long": _read(out / h10._ARTIFACTS["long"]),
        "short": _read(out / h10._ARTIFACTS["short"]),
        "excluded": _read(out / h10._ARTIFACTS["excluded"]),
        "exceptions": _read(out / h10._ARTIFACTS["exceptions"]),
        "sector": _read(out / h10._ARTIFACTS["sector"]),
        "rules": _read(out / h10._ARTIFACTS["rules"]),
        "checklist": _read(out / h10._ARTIFACTS["checklist"]),
    }


def test_end_to_end_decision_with_exceptions_and_artifacts(env):
    rep = env["rep"]
    assert rep["decision"] == "RULES_BASED_PAPER_PORTFOLIO_READY_WITH_EXCEPTIONS"
    assert rep["decision"] in h10.ALLOWED_DECISIONS
    for name in h10._ARTIFACTS.values():
        assert (env["out"] / name).exists(), f"missing artifact {name}"


def test_book_size_is_capped(env):
    rep = env["rep"]
    assert rep["n_long"] == 4 and rep["n_short"] == 4
    assert rep["n_long"] <= rep["target_per_side"] and rep["n_short"] <= rep["target_per_side"]
    assert len(env["long"]) == 4 and len(env["short"]) == 4


def test_liquidity_filter_applied(env):
    rep = env["rep"]
    assert rep["liquidity_filter"]["applied"] is True
    assert rep["liquidity_filter"]["threshold"] is not None
    assert rep["liquidity_filter"]["n_excluded_total"] == 3        # LLOW1, LLOW2, SLOW1
    excl = {r["ticker"]: r for r in env["excluded"]}
    for tk in ("LLOW1", "LLOW2", "SLOW1"):
        assert excl[tk]["primary_reason"] == "bottom_quartile_liquidity"
    selected = {r["ticker"] for r in env["portfolio"]}
    assert not ({"LLOW1", "LLOW2", "SLOW1"} & selected)


def test_sector_cap_applied(env):
    excl = {r["ticker"]: r for r in env["excluded"]}
    assert excl["LIT2"]["primary_reason"] == "sector_cap"
    assert excl["SIT2"]["primary_reason"] == "sector_cap"
    # no selected sector exceeds the 25%-of-side cap
    assert all(r["within_cap"] == "True" for r in env["sector"])
    assert env["rep"]["largest_sector_share"] <= 25.0 + 1e-9
    assert env["rep"]["sector_cap_respected"] is True


def test_extreme_score_flagged_and_held_out(env):
    selected = {r["ticker"] for r in env["portfolio"]}
    assert "LXEXT" not in selected and "SXEXT" not in selected
    exc = {r["ticker"]: r for r in env["exceptions"]}
    assert exc["LXEXT"]["exception_type"] == "extreme_score_flagged"
    assert exc["SXEXT"]["exception_type"] == "extreme_score_flagged"
    assert exc["LXEXT"]["action"] == "HELD_OUT_NEEDS_RULE_EXCEPTION"
    assert env["rep"]["extreme_score_threshold_abs_z"] == 3.0


def test_equal_weighting_and_no_optimization(env):
    long_w = {r["target_weight"] for r in env["long"]}
    short_w = {r["target_weight"] for r in env["short"]}
    assert long_w == {"0.25"} and short_w == {"0.25"}          # 1/4 each, equal
    assert env["rep"]["weighting"] == "EQUAL"
    assert env["rep"]["optimised_weights"] is False
    rule11 = next(r for r in env["rules"] if r["rule_id"] == "11")
    assert rule11["rule"] == "weights" and rule11["value"] == "False"


def test_rules_based_not_per_ticker_approval(env):
    rep = env["rep"]
    assert rep["per_ticker_approval_required"] is False
    # the human approves RULES (checklist items), none of which is a per-ticker sign-off
    items = {r["item"] for r in env["checklist"]}
    assert {"approve_book_size", "approve_liquidity_filter", "approve_sector_cap",
            "approve_equal_weighting", "approve_quarterly_cadence", "confirm_paper_only",
            "confirm_no_orders_automation"} <= items
    assert all(r["status"] == "NEEDS_APPROVAL" for r in env["checklist"])


def test_exceptions_report_written_and_typed(env):
    types = {r["exception_type"] for r in env["exceptions"]}
    assert {"extreme_score_flagged", "bottom_quartile_liquidity", "sector_cap"} <= types
    assert env["rep"]["exceptions_total"] == len(env["exceptions"])
    assert env["rep"]["exceptions_total"] > 0


def test_excluded_report_explains_every_excluded_name(env):
    # every non-selected long/short universe name appears with a non-empty reason
    assert len(env["excluded"]) == 19 - 8                      # 19 universe - 8 selected
    assert all(r["primary_reason"] for r in env["excluded"])
    reasons = {r["primary_reason"] for r in env["excluded"]}
    assert {"extreme_score_flagged", "bottom_quartile_liquidity", "sector_cap",
            "missing_sector", "missing_composite_input", "below_selection_cutoff"} <= reasons


def test_long_short_balance_and_rebalance(env):
    rep = env["rep"]
    assert rep["sides_balanced"] is True
    assert rep["long_gross_pct"] == 100.0 and rep["short_gross_pct"] == 100.0
    assert rep["net_pct"] == 0.0 and rep["gross_pct"] == 200.0
    assert rep["rebalance_cadence"] == "QUARTERLY"
    assert rep["expected_rebalance_date"] == "2026-09-30"      # quarter after 2026Q2


def test_no_paper_trader_orders_or_automation(env):
    rep = env["rep"]
    for k in ("wrote_to_paper_trader", "creates_orders", "creates_automation",
              "creates_paper_trader_signals", "creates_trade_decisions", "live_trading",
              "broker_connected", "deploy", "performs_provider_acquisition", "performs_network"):
        assert rep[k] is False, f"{k} must be False"
    assert all(r["order_action"] == "NO_ORDER" for r in env["portfolio"])
    # output is metadata under research/output, never inside a Paper Trader app dir
    parts = [p.lower() for p in h10._DEFAULT_OUT.parts]
    assert "research" in parts and "output" in parts
    assert not any(p in parts for p in ("api", "app", "paper_trader_app"))


def test_secret_audit_written_and_clean(env):
    assert (env["out"] / h10._ARTIFACTS["secret_audit"]).exists()
    assert env["rep"]["secret_safety_leak_scan_clean"] is True
    assert env["rep"]["api_key_printed"] is False


def test_source_has_no_network_order_automation_or_pt_write_tokens():
    src = (_REPO_ROOT / "research" / "run_phase10h_rules_based_paper_portfolio.py").read_text(
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
    assert set(h10.ALLOWED_DECISIONS).isdisjoint(set(h10.FORBIDDEN_DECISIONS))


def test_blocked_when_inputs_missing(tmp_path):
    rep = h10.run(out_dir=tmp_path / "out", book_csv=tmp_path / "nope.csv",
                  cand_csv=tmp_path / "nope2.csv", verbose=False)
    assert rep["decision"] == "HARD_BLOCKER_REQUIRES_USER_ACTION"
    assert rep["decision"] in h10.ALLOWED_DECISIONS
    assert rep["wrote_to_paper_trader"] is False and rep["creates_orders"] is False


def test_blocked_too_few_candidates(tmp_path):
    # a side with only 2 eligible names (rest missing inputs) -> below the viability floor
    specs = [
        ("LONG", "LA", 9.0, "Energy", _HIGH, 1.0, 0.1, -0.01, False),
        ("LONG", "LB", 8.0, "Financials", _HIGH, 1.0, 0.1, -0.01, False),
        ("LONG", "LC", 7.0, "Health Care", _HIGH, 1.0, "", -0.01, False),   # missing input
        ("SHORT", "SA", -9.0, "Energy", _HIGH, -1.0, -0.1, 0.01, False),
        ("SHORT", "SB", -8.0, "Financials", _HIGH, -1.0, -0.1, 0.01, False),
        ("SHORT", "SC", -7.0, "Health Care", _HIGH, -1.0, -0.1, 0.01, False),
    ]
    book, cand, f10 = _write_inputs(tmp_path, specs)
    rep = h10.run(out_dir=tmp_path / "out", book_csv=book, cand_csv=cand,
                  risk_csv=tmp_path / "no.csv", f10_json=f10, target_per_side=4, verbose=False)
    assert rep["decision"] == "RULES_BASED_PAPER_PORTFOLIO_BLOCKED_TOO_FEW_CANDIDATES"
    assert rep["n_long"] == 2


def test_real_inputs_yield_capped_book_if_present():
    if not (h10._DEF_BOOK.exists() and h10._DEF_CAND.exists()):
        pytest.skip("10-F-A inputs not present in this checkout")
    rep = h10.run(verbose=False)
    assert rep["decision"] in h10.ALLOWED_DECISIONS
    assert rep["n_long"] <= 25 and rep["n_short"] <= 25
    assert rep["creates_orders"] is False and rep["creates_automation"] is False
    assert rep["wrote_to_paper_trader"] is False
