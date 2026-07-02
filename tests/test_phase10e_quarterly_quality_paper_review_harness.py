"""Fully-offline targeted tests for Phase 10-E - Paper-Only Review Harness for the quarterly quality
composite.

No key, no network, no writes outside a tmp dir, NO Paper Trader / orders / automation. A small synthetic
Norgate-style event panel (persistent per-ticker quality traits -> low quarterly turnover, like real
fundamentals) is injected into `run(ev=..., norm_csvs=...)`. The harness only RANKS the composite into a
paper-review package; it never trades. The tests verify scope, safety, the default sector-neutral view,
the reconstructed book, every required artifact, the Unknown-sector caveat path, and the missing-input
blocker.
"""
from __future__ import annotations

import csv
import importlib
from pathlib import Path

import pytest

MOD = importlib.import_module("research.run_phase10e_quarterly_quality_paper_review_harness")
D10 = importlib.import_module("research.run_phase10d_quarterly_quality_composite_validation")
FWD = MOD.FWD_WINDOWS
N_TICK = 20
TICKERS = ["T%02d" % i for i in range(N_TICK)]
MAPPED_SECTORS = ["Information Technology", "Health Care", "Financials", "Industrials"]
N_MONTHS = 18


def _make_panel(sectors):
    """`sectors` is a list assigning a sector to each ticker (may include 'Unknown')."""
    import numpy as np
    import pandas as pd
    rng = np.random.default_rng(20260629)
    qf = {tk: float(rng.standard_normal()) for tk in TICKERS}   # persistent fcf quality
    qa = {tk: float(rng.standard_normal()) for tk in TICKERS}   # persistent accrual level
    rows, fcf_norm, acc_norm = [], [], []
    start = pd.Timestamp("2019-04-01")
    for m in range(N_MONTHS):
        entry = start + pd.DateOffset(months=m) + pd.Timedelta(days=14)
        avail = entry - pd.Timedelta(days=5)
        for i, tk in enumerate(TICKERS):
            f = qf[tk] + 0.15 * float(rng.standard_normal())
            a = qa[tk] + 0.15 * float(rng.standard_normal())
            core = 0.05 * qf[tk] - 0.05 * qa[tk]
            ret = {h: core + 0.02 * float(rng.standard_normal()) for h in FWD}
            rows.append({"ticker": tk, "entry_date": entry, "sector": sectors[i],
                         "cohort": "old" if i < N_TICK // 2 else "new",
                         "liquidity_proxy": float((i + 1) * 1000),
                         "fwd_exc_1": ret[1], "fwd_exc_5": ret[5], "fwd_exc_21": ret[21],
                         "fwd_exc_63": ret[63]})
            fcf_norm.append({"ticker": tk, "available_date": avail.date().isoformat(),
                             "fcf_to_assets": f})
            acc_norm.append({"ticker": tk, "available_date": avail.date().isoformat(),
                             "operating_accruals": a})
    return pd.DataFrame(rows), fcf_norm, acc_norm


def _write_norm(path, rows, feature):
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["ticker", "available_date", feature])
        for r in rows:
            w.writerow([r["ticker"], r["available_date"], r[feature]])


def _norm_csvs(tmp_path, fcf_norm, acc_norm):
    fcf_csv = tmp_path / "norm" / "eodhd_fcf_to_assets" / "fcf_to_assets.csv"
    acc_csv = tmp_path / "norm" / "eodhd_operating_accruals" / "operating_accruals.csv"
    _write_norm(fcf_csv, fcf_norm, "fcf_to_assets")
    _write_norm(acc_csv, acc_norm, "operating_accruals")
    return {"eodhd_fcf_to_assets": fcf_csv, "eodhd_operating_accruals": acc_csv}


@pytest.fixture()
def mapped(tmp_path):
    sectors = [MAPPED_SECTORS[i % len(MAPPED_SECTORS)] for i in range(N_TICK)]
    ev, fcf_norm, acc_norm = _make_panel(sectors)
    return ev, _norm_csvs(tmp_path, fcf_norm, acc_norm)


@pytest.fixture()
def unknown_heavy(tmp_path):
    # 12 of 20 names unmapped -> Unknown-sector book share well above the caveat threshold.
    sectors = (["Unknown"] * 12) + [MAPPED_SECTORS[i % len(MAPPED_SECTORS)] for i in range(8)]
    ev, fcf_norm, acc_norm = _make_panel(sectors)
    return ev, _norm_csvs(tmp_path, fcf_norm, acc_norm)


def _run(fixture, tmp_path, sub="out"):
    ev, norm_csvs = fixture
    return MOD.run(out_dir=tmp_path / sub, ev=ev, norm_csvs=norm_csvs, verbose=False)


# --------------------------------------------------------------------------- #
# Scope / composite inputs.
# --------------------------------------------------------------------------- #
def test_only_phase10d_composite_inputs_used():
    # composite legs are imported verbatim from Phase 10-D (same object); only the two quality legs.
    assert MOD.LEGS is D10.LEGS
    assert {l["feature"] for l in MOD.LEGS} == {"fcf_to_assets", "operating_accruals"}
    assert MOD.ALLOWED_FAMILIES == frozenset({"eodhd_fcf_to_assets", "eodhd_operating_accruals"})
    # build_composite is reused, not re-implemented here.
    assert not hasattr(MOD, "build_composite")


def test_sector_neutral_score_is_default():
    assert MOD.DEFAULT_REVIEW_SCORE == "comp_sn" == D10.COMP_SN


def test_no_optimization_no_signflip_no_network_no_orders_no_automation():
    assert MOD.PERFORMS_NETWORK is False
    src = Path(MOD.__file__).read_text(encoding="utf-8")
    for banned in ("urllib.request", "urlopen", "import requests", "http.client", "socket.",
                   "alphavantage", "polygon.io", "finnhub", "financialmodelingprep"):
        assert banned not in src
    for banned in ("scipy", "sklearn", "minimize(", "LinearRegression", "argmax(weights"):
        assert banned not in src
    # no provider-acquisition / order / automation / deploy / Paper-Trader entry points
    for n in ("acquire_eodhd", "create_order", "execute_order", "place_order", "submit_order",
              "create_signal", "create_trade_decision", "schedule", "deploy"):
        assert not hasattr(MOD, n)


def test_no_paper_trader_or_gcp_or_keyprint_in_source():
    src = Path(MOD.__file__).read_text(encoding="utf-8")
    for banned in ("from api", "import api.app", "paper_trader.app", "sessionmaker", "gcloud",
                   "google.cloud", "deploy(", "print(os.environ", "EODHD_API_KEY']", "getenv('EODHD"):
        assert banned not in src


# --------------------------------------------------------------------------- #
# End-to-end (mapped sectors -> READY).
# --------------------------------------------------------------------------- #
def test_end_to_end_decision_allowed_and_artifacts(mapped, tmp_path):
    report = _run(mapped, tmp_path)
    assert report["decision"] in MOD.ALLOWED_DECISIONS
    assert report["decision"] not in MOD.FORBIDDEN_DECISIONS
    # nothing was traded / written to Paper Trader / automated
    assert report["creates_paper_trader_signals"] is False
    assert report["creates_trade_decisions"] is False
    assert report["creates_orders"] is False
    assert report["creates_automation"] is False
    assert report["wrote_to_paper_trader"] is False
    assert report["live_trading"] is False
    out = tmp_path / "out"
    for name in report["required_artifacts"]:
        assert (out / name).is_file(), "missing artifact %s" % name


def test_mapped_book_is_ready_with_long_and_short(mapped, tmp_path):
    report = _run(mapped, tmp_path)
    assert report["decision"] in (MOD.DEC_READY, MOD.DEC_READY_CAVEAT)
    assert report["book"]["n_long"] >= 1 and report["book"]["n_short"] >= 1


def test_candidate_list_includes_both_legs(mapped, tmp_path):
    _run(mapped, tmp_path)
    rows = list(csv.DictReader(open(tmp_path / "out" / "paper_review_candidate_list.csv",
                                    encoding="utf-8")))
    assert rows
    cols = set(rows[0].keys())
    assert {"fcf_to_assets", "operating_accruals"} <= cols
    assert "comp_sn" in cols and "comp_raw" in cols
    assert {"avail_fcf_to_assets", "avail_operating_accruals"} <= cols
    # the default ranked view uses the sector-neutral composite
    assert "rank_sn" in cols and "percentile_sn" in cols


def test_long_short_book_and_explainability(mapped, tmp_path):
    _run(mapped, tmp_path)
    out = tmp_path / "out"
    book = list(csv.DictReader(open(out / "paper_review_long_short_book.csv", encoding="utf-8")))
    sides = {r["side"] for r in book}
    assert "LONG" in sides and "SHORT" in sides
    assert all(r["review_status"] == "PAPER_REVIEW_ONLY" for r in book)
    expl = list(csv.DictReader(open(out / "paper_review_score_explainability.csv", encoding="utf-8")))
    assert expl
    assert {"fcf_to_assets_level", "operating_accruals_level"} <= set(expl[0].keys())


def test_safety_badges_present(mapped, tmp_path):
    _run(mapped, tmp_path)
    rows = list(csv.DictReader(open(tmp_path / "out" / "paper_review_safety_badges.csv",
                                    encoding="utf-8")))
    badges = {r["badge"] for r in rows}
    for required in ("PAPER REVIEW ONLY", "NO ORDERS", "NO AUTOMATION", "HUMAN APPROVAL REQUIRED"):
        assert required in badges


def test_calendar_unknown_audit_checklist_risk_turnover_written(mapped, tmp_path):
    _run(mapped, tmp_path)
    out = tmp_path / "out"
    cal = list(csv.DictReader(open(out / "quarterly_rebalance_calendar.csv", encoding="utf-8")))
    assert cal and "nominal_review_date" in cal[0] and any(r["status"] == "CURRENT_REVIEW" for r in cal)
    # unknown-sector audit file exists (mapped panel -> a single "no Unknown names" placeholder row)
    assert (out / "paper_review_unknown_sector_audit.csv").is_file()
    chk = list(csv.DictReader(open(out / "paper_review_human_checklist.csv", encoding="utf-8")))
    steps = " ".join(r["step"] + r["what_to_confirm"] + r["how_to_verify"] for r in chk)
    assert all(r["status"] == "PENDING_HUMAN" for r in chk)
    assert "leakage" in steps.lower() and "liquidity" in steps.lower()
    assert "freshness" in steps.lower() and "automation" in steps.lower()
    risk = list(csv.DictReader(open(out / "paper_review_risk_flags.csv", encoding="utf-8")))
    assert risk and {"unknown_sector", "low_liquidity", "missing_leg", "extreme_score",
                     "in_concentrated_sector"} <= set(risk[0].keys())
    turn = list(csv.DictReader(open(out / "paper_review_turnover_estimate.csv", encoding="utf-8")))
    assert turn and "book_turnover" in turn[0] and turn[0]["prior_quarter"]


def test_no_secret_leak(mapped, tmp_path):
    report = _run(mapped, tmp_path)
    assert report["offline"] is True and report["performs_network"] is False
    assert report["api_key_printed"] is False and report["api_key_written_to_disk"] is False
    assert report["secret_safety_leak_scan_clean"] is True
    audit = list(csv.DictReader(open(tmp_path / "out" / "secret_safety_audit.csv", encoding="utf-8")))
    assert audit and all(r["clean"] == "True" for r in audit)


# --------------------------------------------------------------------------- #
# Unknown-sector caveat path.
# --------------------------------------------------------------------------- #
def test_unknown_heavy_book_gets_sector_mapping_caveat(unknown_heavy, tmp_path):
    report = _run(unknown_heavy, tmp_path, sub="unk")
    assert report["decision"] == MOD.DEC_READY_CAVEAT
    assert report["sector_exposure"]["unknown_book_share"] >= MOD.UNKNOWN_CAVEAT_SHARE
    audit = list(csv.DictReader(open(tmp_path / "unk" / "paper_review_unknown_sector_audit.csv",
                                     encoding="utf-8")))
    # real Unknown names are listed with a remediation recommendation
    assert any(r.get("ticker") and r.get("recommended_remediation") for r in audit)


# --------------------------------------------------------------------------- #
# Missing-input blocker.
# --------------------------------------------------------------------------- #
def test_blocked_on_missing_composite_inputs(mapped, tmp_path):
    ev, _ = mapped
    bad = {"eodhd_fcf_to_assets": tmp_path / "x1.csv", "eodhd_operating_accruals": tmp_path / "x2.csv"}
    report = MOD.run(out_dir=tmp_path / "blk", ev=ev, norm_csvs=bad, verbose=False)
    assert report["decision"] == MOD.DEC_BLOCKED
    assert report["decision"] not in MOD.FORBIDDEN_DECISIONS


def test_all_decisions_allowed_and_forbidden_disjoint():
    assert set(MOD.ALLOWED_DECISIONS).isdisjoint(set(MOD.FORBIDDEN_DECISIONS))
    for d in ("LIVE_TRADING_READY", "ORDER_READY", "AUTOMATION_READY",
              "STRONG_ALPHA_FOUND_READY_FOR_REVIEW"):
        assert d in MOD.FORBIDDEN_DECISIONS
