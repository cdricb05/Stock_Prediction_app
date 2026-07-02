"""Fully-offline targeted tests for Phase 10-F-A - Owned Metadata Sector Mapping Repair and Rerank.

No key, no network, no writes outside a tmp dir, NO provider acquisition, NO Paper Trader / orders /
automation. A small synthetic Norgate-style event panel (all sectors initially 'Unknown', mirroring the
10-E reality) plus a FAKE owned EODHD fundamentals dir (per-ticker JSON with General.GicSector /
General.Sector) are injected. Phase 10-E is run first to produce the 'before' book; Phase 10-F-A then
repairs sectors from the owned metadata, rebuilds the sector-neutral composite, and reranks. The tests
verify scope, safety, owned-only sourcing, per-repair provenance + confidence, retention (no fabrication)
of unrepairable names, the sector-neutral rebuild, the before/after + rank-movement reports, and the
missing-input blocker.
"""
from __future__ import annotations

import csv
import importlib
import json
from pathlib import Path

import pytest

MOD = importlib.import_module("research.run_phase10f_owned_sector_mapping_repair")
E10 = importlib.import_module("research.run_phase10e_quarterly_quality_paper_review_harness")
D10 = importlib.import_module("research.run_phase10d_quarterly_quality_composite_validation")
FWD = D10.FWD_WINDOWS
N_TICK = 20
TICKERS = ["T%02d" % i for i in range(N_TICK)]
N_MONTHS = 18
# GicSector labels assigned to T00..T17 (HIGH-confidence repairs), cycled across diverse GICS sectors.
GIC_SECTORS = ["Information Technology", "Health Care", "Financials", "Industrials",
               "Consumer Discretionary", "Energy"]


def _make_panel():
    """All 20 tickers start Unknown (the 10-E heavy-Unknown reality)."""
    import numpy as np
    import pandas as pd
    rng = np.random.default_rng(20260630)
    qf = {tk: float(rng.standard_normal()) for tk in TICKERS}
    qa = {tk: float(rng.standard_normal()) for tk in TICKERS}
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
            rows.append({"ticker": tk, "entry_date": entry, "sector": "Unknown",
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


def _fake_fund_dir(tmp_path):
    """Owned-style EODHD fundamentals: T00..T17 carry General.GicSector (HIGH); T18 only General.Sector
    'Technology' (MEDIUM crosswalk -> Information Technology); T19 has NO file (stays Unknown)."""
    d = tmp_path / "eodhd_fund"
    d.mkdir(parents=True, exist_ok=True)
    for i in range(18):
        gs = GIC_SECTORS[i % len(GIC_SECTORS)]
        (d / ("%s.json" % TICKERS[i])).write_text(json.dumps(
            {"General": {"GicSector": gs, "GicIndustry": gs + " Industry",
                         "Sector": "Misc", "Industry": "Misc", "Type": "Common Stock"}}),
            encoding="utf-8")
    (d / ("%s.json" % TICKERS[18])).write_text(json.dumps(
        {"General": {"GicSector": "", "GicIndustry": "", "Sector": "Technology",
                     "Industry": "Semiconductors", "Type": "Common Stock"}}), encoding="utf-8")
    # T19: deliberately no file -> unrepairable from owned data.
    return d


@pytest.fixture()
def env(tmp_path):
    ev, fcf_norm, acc_norm = _make_panel()
    ncsv = _norm_csvs(tmp_path, fcf_norm, acc_norm)
    fund = _fake_fund_dir(tmp_path)
    curated = tmp_path / "curated.csv"   # tiny owned map (Unknown names absent, as in reality)
    curated.write_text("ticker,sector,industry\nZZZ,Energy,Oil\n", encoding="utf-8")
    # Produce the Phase 10-E 'before' book over the same panel.
    e10_dir = tmp_path / "e10"
    E10.run(out_dir=e10_dir, ev=ev.copy(), norm_csvs=ncsv, verbose=False)
    return {"ev": ev, "norm_csvs": ncsv, "fund": fund, "curated": curated, "e10_dir": e10_dir}


def _run(env, tmp_path, sub="f10"):
    return MOD.run(out_dir=tmp_path / sub, ev=env["ev"].copy(), norm_csvs=env["norm_csvs"],
                   fund_dir=env["fund"], curated_csv=env["curated"], phase10e_dir=env["e10_dir"],
                   verbose=False)


# --------------------------------------------------------------------------- #
# Scope / composite reuse / no provider acquisition / no network.
# --------------------------------------------------------------------------- #
def test_composite_imported_and_not_redefined():
    assert MOD.LEGS is D10.LEGS
    assert MOD.COMP_SN == "comp_sn" == D10.COMP_SN
    assert MOD.DEFAULT_REVIEW_SCORE == "comp_sn"
    # composite + book logic reused, not re-implemented here.
    assert not hasattr(MOD, "build_composite")
    assert not hasattr(MOD, "build_book")
    assert not hasattr(MOD, "latest_quarter_cross_section")


def test_no_network_no_provider_no_orders_no_automation_in_source():
    assert MOD.PERFORMS_NETWORK is False
    src = Path(MOD.__file__).read_text(encoding="utf-8")
    for banned in ("urllib.request", "urlopen", "import requests", "http.client", "socket.",
                   "financialmodelingprep", "polygon.io", "finnhub", "alphavantage"):
        assert banned not in src
    # owned data only - never construct a path into the on-disk FMP / other-provider caches.
    for banned in ("data/fmp", "data\\fmp", "fmp/raw", "fmp\\raw", "data/polygon", "data/finnhub",
                   "data/simfin"):
        assert banned not in src
    for banned in ("scipy", "sklearn", "minimize(", "LinearRegression"):
        assert banned not in src
    for n in ("acquire_eodhd", "create_order", "execute_order", "place_order", "submit_order",
              "create_signal", "create_trade_decision", "schedule", "deploy"):
        assert not hasattr(MOD, n)


def test_no_paper_trader_or_gcp_or_keyprint_in_source():
    src = Path(MOD.__file__).read_text(encoding="utf-8")
    for banned in ("from api", "import api.app", "paper_trader.app", "sessionmaker", "gcloud",
                   "google.cloud", "print(os.environ", "getenv('EODHD"):
        assert banned not in src


def test_uses_owned_local_eodhd_fundamentals():
    # the default source is the owned, gitignored EODHD raw fundamentals dir
    assert MOD.EODHD_FUND_DIR.parts[-3:] == ("eodhd", "raw", "fundamentals")


# --------------------------------------------------------------------------- #
# End-to-end repair + rerank.
# --------------------------------------------------------------------------- #
def test_end_to_end_decision_allowed_and_artifacts(env, tmp_path):
    report = _run(env, tmp_path)
    assert report["decision"] in MOD.ALLOWED_DECISIONS
    assert report["decision"] not in MOD.FORBIDDEN_DECISIONS
    assert report["performs_network"] is False and report["offline"] is True
    assert report["uses_owned_data_only"] is True
    assert report["performs_provider_acquisition"] is False
    assert report["fabricated_sectors"] is False
    assert report["creates_paper_trader_signals"] is False
    assert report["creates_trade_decisions"] is False
    assert report["creates_orders"] is False
    assert report["creates_automation"] is False
    assert report["wrote_to_paper_trader"] is False
    assert report["live_trading"] is False and report["broker_connected"] is False
    assert report["deploy"] is False
    out = tmp_path / "f10"
    for name in report["required_artifacts"]:
        assert (out / name).is_file(), "missing artifact %s" % name


def test_repair_reduces_unknown_and_book_has_both_sides(env, tmp_path):
    report = _run(env, tmp_path)
    assert report["decision"] in (MOD.DEC_REPAIRED, MOD.DEC_PARTIAL)
    # before was Unknown-heavy; after must be materially lower
    assert report["unknown_sector_book_share_after"] < report["unknown_sector_book_share_before"]
    assert report["repair"]["n_repaired"] >= 1
    assert report["book"]["n_long"] >= 1 and report["book"]["n_short"] >= 1


def test_every_repair_has_source_and_confidence(env, tmp_path):
    _run(env, tmp_path)
    rows = list(csv.DictReader(open(tmp_path / "f10" / "repaired_sector_mapping.csv", encoding="utf-8")))
    real = [r for r in rows if r.get("ticker")]
    assert real, "expected at least one repaired ticker"
    for r in real:
        assert r["repaired_sector"] and r["repaired_sector"] != "Unknown"
        assert r["repaired_sector"] in MOD.CANONICAL_GICS
        assert r["source_file_or_source_family"]
        assert r["source_field"]
        assert r["confidence"] in (MOD.CONF_HIGH, MOD.CONF_MEDIUM, MOD.CONF_LOW)
    confs = {r["ticker"]: r["confidence"] for r in real}
    # GicSector names are HIGH; the General.Sector crosswalk name (T18) is MEDIUM -> Information Technology
    assert confs.get("T00") == MOD.CONF_HIGH
    t18 = next((r for r in real if r["ticker"] == "T18"), None)
    assert t18 is not None and t18["confidence"] == MOD.CONF_MEDIUM
    assert t18["repaired_sector"] == "Information Technology"


def test_unrepairable_names_retained_not_fabricated(env, tmp_path):
    _run(env, tmp_path)
    unrep = list(csv.DictReader(open(tmp_path / "f10" / "unrepaired_unknown_sector_names.csv",
                                     encoding="utf-8")))
    names = {r["ticker"] for r in unrep if r.get("ticker")}
    assert "T19" in names                       # no owned fundamentals file -> stays Unknown
    for r in unrep:
        if r.get("ticker"):
            assert r["sector"] == "Unknown"     # never invented
    # and T19 must NOT appear as a repaired mapping
    rep = list(csv.DictReader(open(tmp_path / "f10" / "repaired_sector_mapping.csv", encoding="utf-8")))
    assert "T19" not in {r["ticker"] for r in rep if r.get("ticker")}


def test_repair_attempts_logged_in_priority_order(env, tmp_path):
    _run(env, tmp_path)
    rows = list(csv.DictReader(open(tmp_path / "f10" / "unknown_sector_repair_attempts.csv",
                                    encoding="utf-8")))
    fams = {r["source_family"] for r in rows}
    # Norgate attempted-but-unavailable is recorded honestly; EODHD GicSector is the primary source.
    assert MOD.SRC_NORGATE in fams
    assert any("UNAVAILABLE" in r["outcome"] for r in rows if r["source_family"] == MOD.SRC_NORGATE)
    assert MOD.SRC_EODHD_GIC in fams


def test_sector_neutral_score_rebuilt(env, tmp_path):
    report = _run(env, tmp_path)
    assert report["sector_neutral_composite_rebuilt"] is True
    audit = list(csv.DictReader(open(tmp_path / "f10" / "sector_neutral_score_rebuild_audit.csv",
                                     encoding="utf-8")))
    items = {r["item"]: r for r in audit}
    assert items["sector_neutral_composite_rebuilt"]["after"] == "True"
    # repair maps far more names into their TRUE sector instead of one giant Unknown bucket
    assert int(items["mapped_tickers_in_panel"]["after"]) > \
        int(items["mapped_tickers_in_panel"]["before"])


def test_before_after_and_rank_movement_reports_written(env, tmp_path):
    _run(env, tmp_path)
    out = tmp_path / "f10"
    ba_sec = list(csv.DictReader(open(out / "before_after_sector_exposure.csv", encoding="utf-8")))
    assert ba_sec and {"before_n_book", "after_n_book"} <= set(ba_sec[0].keys())
    ba_unk = list(csv.DictReader(open(out / "before_after_unknown_sector_exposure.csv",
                                      encoding="utf-8")))
    metrics = {r["metric"] for r in ba_unk}
    assert "unknown_book_share" in metrics
    rm = list(csv.DictReader(open(out / "rank_movement_report.csv", encoding="utf-8")))
    assert rm and {"before_rank_sn", "after_rank_sn", "rank_delta(up=+)"} <= set(rm[0].keys())
    bc = list(csv.DictReader(open(out / "long_short_book_change_report.csv", encoding="utf-8")))
    assert bc and "change" in bc[0]


def test_reranked_book_and_candidates_have_repair_provenance(env, tmp_path):
    _run(env, tmp_path)
    out = tmp_path / "f10"
    cand = list(csv.DictReader(open(out / "reranked_paper_review_candidate_list.csv", encoding="utf-8")))
    assert cand
    cols = set(cand[0].keys())
    assert {"sector_repaired", "before_rank_sn", "rank_delta", "comp_sn", "comp_raw"} <= cols
    assert {"fcf_to_assets", "operating_accruals"} <= cols
    book = list(csv.DictReader(open(out / "reranked_paper_review_long_short_book.csv", encoding="utf-8")))
    sides = {r["side"] for r in book}
    assert "LONG" in sides and "SHORT" in sides
    assert all(r["review_status"] == "PAPER_REVIEW_ONLY" for r in book)


def test_source_audit_marks_norgate_unavailable_and_eodhd_available(env, tmp_path):
    _run(env, tmp_path)
    rows = list(csv.DictReader(open(tmp_path / "f10" / "sector_mapping_source_audit.csv",
                                    encoding="utf-8")))
    by_fam = {r["source_family"]: r for r in rows}
    assert by_fam[MOD.SRC_NORGATE]["available"] == "False"
    assert by_fam[MOD.SRC_EODHD_GIC]["available"] == "True"
    assert int(by_fam[MOD.SRC_EODHD_GIC]["n_hit"]) >= 1


def test_no_secret_leak(env, tmp_path):
    report = _run(env, tmp_path)
    assert report["api_key_printed"] is False and report["api_key_written_to_disk"] is False
    assert report["secret_safety_leak_scan_clean"] is True
    audit = list(csv.DictReader(open(tmp_path / "f10" / "secret_safety_audit.csv", encoding="utf-8")))
    assert audit and all(r["clean"] == "True" for r in audit)


# --------------------------------------------------------------------------- #
# Missing-input blocker + decision hygiene.
# --------------------------------------------------------------------------- #
def test_blocked_when_phase10e_missing(env, tmp_path):
    report = MOD.run(out_dir=tmp_path / "blk", ev=env["ev"].copy(), norm_csvs=env["norm_csvs"],
                     fund_dir=env["fund"], curated_csv=env["curated"],
                     phase10e_dir=tmp_path / "does_not_exist", verbose=False)
    assert report["decision"] == MOD.DEC_HARD_BLOCKER
    assert report["decision"] not in MOD.FORBIDDEN_DECISIONS


def test_all_decisions_allowed_and_forbidden_disjoint():
    assert set(MOD.ALLOWED_DECISIONS).isdisjoint(set(MOD.FORBIDDEN_DECISIONS))
    for d in ("LIVE_TRADING_READY", "ORDER_READY", "AUTOMATION_READY",
              "STRONG_ALPHA_FOUND_READY_FOR_REVIEW", "NEEDS_PROVIDER", "NO_DATA"):
        assert d in MOD.FORBIDDEN_DECISIONS
