"""Fully-offline targeted tests for Phase 16-A Part E - sector metadata integrity audit.

No key, no network, no writes outside a tmp dir, NO provider acquisition, NO Paper Trader / orders /
automation. A tiny synthetic frozen-panel CSV (all sectors initially 'Unknown', mirroring the 13-A
reality) plus a FAKE owned EODHD fundamentals dir (per-ticker JSON with General.GicSector /
General.Sector) are injected. The tests verify owned-only resolution, per-name provenance + confidence,
no fabrication of unresolvable names, the before/after coverage maths, the Part E artifacts, and the
read-only safety contract (never mutates the champion or the Phase 13-A package).
"""
from __future__ import annotations

import csv
import importlib
import json
from pathlib import Path

import pytest

MOD = importlib.import_module(
    "research.run_phase16a_sector_metadata_integrity_and_shadow_revalidation")

GICS = ["Information Technology", "Health Care", "Financials", "Industrials",
        "Consumer Discretionary", "Energy", "Materials", "Utilities"]


def _det(s: str) -> float:
    x = 0
    for ch in s:
        x = (x * 131 + ord(ch)) & 0xFFFFFFFF
    return (x % 100000) / 100000.0


def _build_panel(path: Path, tickers, orig_sectors, months, with_fwd=True):
    rows = []
    for m in months:
        for tk in tickers:
            rows.append({"rebalance_date": m + "-15", "ticker": tk,
                         "sector": orig_sectors.get(tk, "Unknown"),
                         "fcf_to_assets": repr(_det(tk + m + "f") * 2 - 1),
                         "operating_accruals": repr(_det(tk + m + "a") * 2 - 1),
                         "forward_63d_return": (repr(_det(tk + m + "r") * 0.1 - 0.05) if with_fwd else "")})
    month_index = {}
    for i, r in enumerate(rows):
        month_index.setdefault(r["rebalance_date"][:7], []).append(i)

    def orig_fn(tk):
        s = orig_sectors.get(tk.upper(), "Unknown")
        return s if s in MOD.CANONICAL_GICS else "Unknown"

    comp = MOD.recompute_composite(rows, month_index, orig_fn)
    for i, r in enumerate(rows):
        c = comp.get(i)
        r["composite_sn"] = "" if c is None else repr(c)
    cols = ["rebalance_date", "ticker", "sector", "fcf_to_assets", "operating_accruals",
            "forward_63d_return", "composite_sn"]
    with open(path, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=cols)
        w.writeheader()
        for r in rows:
            w.writerow(r)


def _fundamentals(dir_path: Path, mapping):
    dir_path.mkdir(parents=True, exist_ok=True)
    for tk, general in mapping.items():
        (dir_path / ("%s.json" % tk)).write_text(json.dumps({"General": general}), encoding="utf-8")


def _curated(path: Path, rows):
    with open(path, "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["ticker", "sector", "industry"])
        for tk, sec in rows:
            w.writerow([tk, sec, ""])


# --------------------------------------------------------------------------- #
# resolve_one - owned sources, priority, confidence, no fabrication.
# --------------------------------------------------------------------------- #
def test_resolve_one_gicsector_high_confidence():
    eodhd = {"AAA": {"gic_sector": "Information Technology", "gic_industry": "Software",
                     "sector": "Technology", "industry": "SW", "type": "Common Stock", "source_file": "x"}}
    res, attempts = MOD.resolve_one("AAA", eodhd, {})
    assert res is not None
    assert res["sector"] == "Information Technology"
    assert res["confidence"] == MOD.CONF_HIGH
    assert res["source_family"] == MOD.SRC_EODHD_GIC
    assert res["point_in_time"] is False
    # Norgate is always attempted first and recorded UNAVAILABLE (owned-only honesty).
    assert any(a["source_family"] == MOD.SRC_NORGATE for a in attempts)


def test_resolve_one_morningstar_crosswalk_medium():
    eodhd = {"BBB": {"gic_sector": "", "gic_industry": "", "sector": "Financial Services",
                     "industry": "Banks", "type": "Common Stock", "source_file": "y"}}
    res, _ = MOD.resolve_one("BBB", eodhd, {})
    assert res is not None
    assert res["sector"] == "Financials"
    assert res["confidence"] == MOD.CONF_MEDIUM
    assert res["source_family"] == MOD.SRC_EODHD_SECTOR


def test_resolve_one_unresolvable_is_not_fabricated():
    res, attempts = MOD.resolve_one("ZZZ", {}, {})
    assert res is None                                   # never invents a sector
    assert any(a["outcome"] == "SAME_AS_EODHD_GENERAL_BLOCK" for a in attempts)


def test_resolve_one_non_canonical_gicsector_rejected():
    eodhd = {"CCC": {"gic_sector": "Conglomerates", "gic_industry": "", "sector": "", "industry": "",
                     "type": "", "source_file": "z"}}
    res, attempts = MOD.resolve_one("CCC", eodhd, {})
    assert res is None                                   # non-canonical label is not accepted
    assert any(a["outcome"] == "PRESENT_BUT_NON_CANONICAL" for a in attempts)


# --------------------------------------------------------------------------- #
# End-to-end Part E: coverage before/after, artifacts, no fabrication, safety.
# --------------------------------------------------------------------------- #
def test_part_e_end_to_end(tmp_path):
    tickers = ["T%02d" % i for i in range(30)]
    orig = {tk: "Unknown" for tk in tickers}             # 13-A reality: everything Unknown
    months = ["2026-03", "2026-04", "2026-05"]
    panel = tmp_path / "panel.csv"
    _build_panel(panel, tickers, orig, months)
    # Owned fundamentals resolve 28/30 (two names left with NO file -> unresolved, kept Unknown).
    fund = tmp_path / "fundamentals"
    _fundamentals(fund, {tk: {"GicSector": GICS[i % len(GICS)]} for i, tk in enumerate(tickers[:28])})
    curated = tmp_path / "curated.csv"
    _curated(curated, [])
    out = tmp_path / "out"

    rep = MOD.run(out_dir=out, panel_csv=panel, fund_dir=fund, curated_csv=curated,
                  pkg_json=tmp_path / "missing_pkg.json", verbose=False)

    # Part E artifacts present.
    for name in ("sector_metadata_resolved.csv", "sector_metadata_unresolved.csv",
                 "sector_metadata_coverage.json", "top25_sector_exposure.csv",
                 "top50_sector_exposure.csv", "phase16a_sector_integrity_report.json"):
        assert (out / name).is_file(), name

    cov = json.loads((out / "sector_metadata_coverage.json").read_text(encoding="utf-8"))
    assert cov["all_234"]["resolved_before"] == 0          # all Unknown before
    assert cov["n_resolved"] == 28 and cov["n_unresolved"] == 2
    # Top25 book fully resolved from owned data (the 28 resolved cover the top ranks).
    assert cov["top25"]["resolved_before"] == 0
    assert cov["top25"]["resolved_pct_after"] >= 90.0

    # Unresolved names are KEPT, never fabricated.
    unres = list(csv.DictReader((out / "sector_metadata_unresolved.csv").open(encoding="utf-8")))
    assert len(unres) == 2
    for r in unres:
        assert r["disposition"].startswith("kept Unknown")

    # Safety: owned-data-only, offline, no mutation, no orders/automation/live.
    ir = json.loads((out / "phase16a_sector_integrity_report.json").read_text(encoding="utf-8"))
    assert ir["offline"] is True and ir["uses_owned_data_only"] is True
    assert ir["fabricated_sectors"] is False and ir["mutated_champion"] is False
    assert ir["modified_phase13a_package"] is False
    assert ir["creates_orders"] is False and ir["creates_automation"] is False
    assert ir["live_trading"] is False
    assert rep["decision"] in MOD.ALLOWED_DECISIONS
    assert rep["decision"] not in MOD.FORBIDDEN_DECISIONS


def test_run_never_touches_phase13a_package(tmp_path):
    """The runner must write ONLY to its own out dir; the Phase 13-A package path is read-only."""
    tickers = ["T%02d" % i for i in range(24)]
    _build_panel(tmp_path / "panel.csv", tickers, {tk: "Unknown" for tk in tickers}, ["2026-05"])
    _fundamentals(tmp_path / "fund", {tk: {"GicSector": GICS[i % len(GICS)]}
                                      for i, tk in enumerate(tickers)})
    _curated(tmp_path / "cur.csv", [])
    # A fake read-only 13-A package that must remain byte-identical.
    pkg = tmp_path / "pkg.json"
    pkg.write_text(json.dumps({"signal_date": "2026-05-22", "price_coverage": {"top25": 14, "top50": 24}}),
                   encoding="utf-8")
    before = pkg.read_bytes()
    MOD.run(out_dir=tmp_path / "out", panel_csv=tmp_path / "panel.csv", fund_dir=tmp_path / "fund",
            curated_csv=tmp_path / "cur.csv", pkg_json=pkg, verbose=False)
    assert pkg.read_bytes() == before                      # package untouched


def test_missing_panel_blocks_cleanly(tmp_path):
    rep = MOD.run(out_dir=tmp_path / "out", panel_csv=tmp_path / "nope.csv",
                  fund_dir=tmp_path / "f", curated_csv=tmp_path / "c.csv", verbose=False)
    assert rep["decision"] == MOD.DEC_BLOCKED_DATA
    assert rep["creates_orders"] is False
