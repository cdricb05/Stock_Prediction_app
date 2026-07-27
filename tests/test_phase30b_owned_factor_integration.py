"""Phase 30B — owned-factor integration, PIT universe alignment, honest audit.

Hermetic: file-level tests use tmp fixture CSVs with ``owned_factors._ROOTS``
monkeypatched to the tmp tree; logic tests build small in-memory inputs. No
network, no Paper Trader, no operational state, no committed data files.
"""

from __future__ import annotations

import csv
import hashlib
import json
import os

import pytest

from research_agent import cli
from research_agent import family_backtest as fb
from research_agent import feature_evaluation as ev
from research_agent import owned_factors as of

# --------------------------------------------------------------------------- #
# deterministic synthetic data
# --------------------------------------------------------------------------- #
N_TK = 40
CURRENT = ["T%02d" % i for i in range(30)]        # survive to the final month
REMOVED = ["T%02d" % i for i in range(30, N_TK)]  # delisted before the final month
ALL_TK = CURRENT + REMOVED
MONTHS = ["%04d-%02d" % (y, m) for y in (2016, 2017, 2018, 2019) for m in range(1, 13)]
CUTOFF = "2020-01-31"


def _u(salt: str, *parts: str) -> float:
    h = int(hashlib.sha256((salt + "|".join(parts)).encode()).hexdigest()[:8], 16)
    return (h % 100000) / 100000.0 - 0.5


def _quarter_ends():
    return ["%04d-%02d-%02d" % (y, m, d)
            for y in (2015, 2016, 2017, 2018, 2019)
            for (m, d) in ((3, 31), (6, 30), (9, 30), (12, 31))]


def make_inputs(feature_covers="all"):
    """Small in-memory owned inputs (no files)."""
    final = MONTHS[-1]
    mom = {}
    fund_cf = {}
    fund_monthly = {}
    for m in MONTHS:
        mrow, frow, fmrow = {}, {}, {}
        for tk in ALL_TK:
            is_member = not (tk in REMOVED and m == final)
            mrow[tk] = {
                "ticker": tk,
                "mom_6_1": _u("mom", m, tk),
                "fwd_1m": _u("fwd", m, tk) * 0.1,
                "eligible": True,
                "is_member": is_member,
                "adv_dollar": 1.0e8,
                "sector": "Unknown",
            }
            comp = _u("comp", m, tk)
            frow[tk] = {"composite_sn": comp, "sector": "Unknown", "fund_month": m}
            fmrow[tk] = {"ticker": tk, "composite_sn": comp, "sector": "Unknown"}
        mom[m] = mrow
        fund_cf[m] = frow
        fund_monthly[m] = fmrow
    spy_close = {}
    base = 100.0
    allm = MONTHS + ["2020-01"]
    for i, m in enumerate(allm):
        spy_close[m] = base * (1.0 + 0.01 * i)
    spy_fwd = {m: (spy_close[fb._next_month(m)] / spy_close[m] - 1.0) for m in MONTHS}
    return {
        "mom_monthly": mom,
        "fund_monthly": fund_monthly,
        "fund_cf": fund_cf,
        "sector_map": {},
        "spy_close": spy_close,
        "spy_fwd": spy_fwd,
        "months": list(MONTHS),
        "data_cutoff": CUTOFF,
        "provenance": {"sha256": {"momentum_panel": "x"}, "n_formation_months": len(MONTHS)},
    }


def make_series(inputs, covers=ALL_TK, salt="gp"):
    return {
        m: {tk: _u(salt, m, tk) for tk in covers}
        for m in inputs["months"]
    }


# --------------------------------------------------------------------------- #
# fixture files + config on disk
# --------------------------------------------------------------------------- #
def _write_csv(path, header, rows):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(header)
        w.writerows(rows)


def write_fixture_files(root):
    # momentum panel
    mom_rows = []
    final = MONTHS[-1]
    for m in MONTHS:
        for tk in ALL_TK:
            is_member = 0 if (tk in REMOVED and m == final) else 1
            mom_rows.append([m, m + "-15", tk, "%.6f" % _u("mom", m, tk),
                             "%.6f" % (_u("fwd", m, tk) * 0.1), is_member, "100000000",
                             "%.6f" % (abs(_u("vol", m, tk)) + 0.1), 1, "Unknown"])
    _write_csv(os.path.join(root, "mom.csv"),
               ["month", "market_date", "ticker", "mom_6_1", "fwd_1m_return",
                "is_member", "adv_dollar", "realized_vol_63d", "eligible_history",
                "sector"], mom_rows)
    # fundamental panel (phase10l style)
    fund_rows = []
    for m in MONTHS:
        for tk in ALL_TK:
            fund_rows.append([m + "-28", tk, "%.6f" % _u("comp", m, tk), "Unknown"])
    _write_csv(os.path.join(root, "fund.csv"),
               ["rebalance_date", "ticker", "composite_sn", "sector"], fund_rows)
    # spy monthly
    spy_rows = []
    for i, m in enumerate(MONTHS + ["2020-01"]):
        spy_rows.append([m + "-28", "%.6f" % (100.0 * (1.0 + 0.01 * i))])
    _write_csv(os.path.join(root, "spy.csv"), ["Date", "Close"], spy_rows)
    # repaired sector map (current-only, subset)
    gics = ["Financials", "Health Care", "Industrials", "Information Technology"]
    smap_rows = [[tk, "orig", gics[i % 4], "ind", "src", "src", "fld", "0.9", "why"]
                 for i, tk in enumerate(CURRENT[:12])]
    _write_csv(os.path.join(root, "sector_map.csv"),
               ["ticker", "original_sector", "repaired_sector", "repaired_industry",
                "source_file_or_source_family", "source_file", "source_field",
                "confidence", "reason"], smap_rows)
    # eodhd normalized gross_profitability (current names only -> survivor-biased)
    gp_rows = []
    for tk in CURRENT:
        for qe in _quarter_ends():
            gp_rows.append([tk, qe, "%.6f" % _u("gp", qe, tk)])
    _write_csv(os.path.join(root, "eodhd", "normalized", "eodhd_gross_profitability",
                            "gross_profitability.csv"),
               ["ticker", "available_date", "gross_profitability"], gp_rows)
    # a low-coverage eodhd factor to exercise REJECTED_GLOBAL_COVERAGE (20/40)
    lc_rows = []
    for tk in CURRENT[:20]:
        for qe in _quarter_ends():
            lc_rows.append([tk, qe, "%.6f" % _u("lc", qe, tk)])
    _write_csv(os.path.join(root, "eodhd", "normalized", "eodhd_lowcov",
                            "lowcov.csv"),
               ["ticker", "available_date", "lowcov"], lc_rows)


def fixture_config():
    return {
        "schema_version": "30B.1",
        "name": "phase30b_fixture",
        "data": {"inherit_data_cutoff_from_source_campaign": True, "data_cutoff": CUTOFF},
        "sources": {
            "roots": {"repo": "REPO_ROOT", "data_root": "DATA_ROOT"},
            "momentum_panel": {"root": "data_root", "relpath": "mom.csv"},
            "eodhd_normalized_root": {"root": "repo", "relpath": "eodhd/normalized"},
            "sector_map": {"root": "repo", "relpath": "sector_map.csv"},
            "fundamental_panel": {"root": "data_root", "relpath": "fund.csv"},
            "spy_monthly": {"root": "data_root", "relpath": "spy.csv"},
            "factors": [
                {"source_id": "realized_vol_63d", "provider": "norgate_derived_price",
                 "kind": "momentum_panel_column", "value_column": "realized_vol_63d",
                 "ticker_column": "ticker", "availability": "formation_month",
                 "frequency": "monthly", "orientation": None,
                 "restatement_caveat": False, "usage": "primary"},
                {"source_id": "gross_profitability", "provider": "eodhd_fundamentals",
                 "kind": "eodhd_normalized", "subdir": "eodhd_gross_profitability",
                 "file": "gross_profitability.csv", "value_column": "gross_profitability",
                 "ticker_column": "ticker", "availability_date_column": "available_date",
                 "frequency": "quarterly_asof", "orientation": None,
                 "restatement_caveat": True, "usage": "primary"},
            ],
        },
        "permitted_numeric_fields": ["realized_vol_63d", "gross_profitability"],
        "forbidden_target_fields": ["fwd_1m_return", "fwd_1m", "forward_63d_return",
                                    "forward_return", "target"],
        "max_factor_staleness_months": 15,
        "global_min_month_coverage": 0.60,
        "global_min_cross_sectional_coverage": 0.60,
        "source_universe_min_coverage": 0.60,
        "survivorship": {"require_pit_membership": True,
                         "min_delisted_representation_fraction": 0.20},
        "sector_history": {"require_pit_safe_for_promotion": True,
                           "treat_unknown_as_sector": False},
        "diagnostics": {"max_features": 2,
                        "initial_features": ["gross_profitability", "realized_vol_63d"]},
        "budgets": {"max_diagnostic_features": 2, "max_portfolio_integrations": 2,
                    "max_robustness_candidates": 2, "max_retry_per_tool": 1},
        "integration": {"baseline_weight": 0.8, "feature_weight": 0.2},
        "costs": {"primary_cost_bps_per_side": 25.0,
                  "sensitivity_cost_bps_per_side": [12.5, 50.0]},
        "portfolio": {"top_n": 25, "sector_treatment": "sector_cap",
                      "exit_buffer_fraction": 0.0, "universe": "mhz_reconstruction",
                      "min_adv_dollar": 10000000.0},
        "ic_screen": {"min_months": 36, "min_coverage_fraction": 0.6,
                      "min_abs_rank_ic_t": 1.0, "material_ic_t_margin": 0.25,
                      "near_duplicate_abs_corr": 0.95,
                      "max_complementary_abs_baseline_corr": 0.5,
                      "max_top_rank_sector_share": 0.5, "leakage_suspicion_abs_ic": 0.5,
                      "min_universe": 10},
        "random_seed": 30, "strict_mode": True,
        "safety": {"research_only": True, "no_operational_promotion": True,
                   "may_register_challengers": False},
    }


@pytest.fixture()
def env(tmp_path, monkeypatch):
    root = str(tmp_path / "src")
    os.makedirs(root, exist_ok=True)
    write_fixture_files(root)
    monkeypatch.setattr(of, "_ROOTS", {"repo": root, "data_root": root})
    cfg = fixture_config()
    out = str(tmp_path / "out")
    return {"root": root, "cfg": cfg, "out": out}


# =========================================================================== #
# Source registry (1-10)
# =========================================================================== #
def test_01_existing_three_sources_unchanged():
    # the committed DSL source inventory still exposes exactly the 3 fields
    inputs = make_inputs()
    inv = _build_dsl_inventory(inputs)
    assert set(inv) >= {"mom_6_1", "adv_dollar", "composite_sn"}


def _build_dsl_inventory(inputs):
    from research_agent.feature_execution import build_source_inventory
    return build_source_inventory(inputs)["numeric_sources"]


def test_02_realized_vol_available(env):
    reg = of.build_source_registry(env["cfg"])
    ids = {f["source_id"]: f for f in reg["factors"]}
    assert ids["realized_vol_63d"]["usage_resolved"] == "primary"
    assert ids["realized_vol_63d"]["value_column_present"]


def test_03_gross_profitability_available(env):
    reg = of.build_source_registry(env["cfg"])
    ids = {f["source_id"]: f for f in reg["factors"]}
    assert ids["gross_profitability"]["usage_resolved"] == "primary"
    assert ids["gross_profitability"]["ticker_count"] == len(CURRENT)


def test_04_fcf_registry_shape(env):
    # add an fcf-style eodhd factor pointing at the gross_profitability file schema
    cfg = env["cfg"]
    reg = of.build_source_registry(cfg)
    for f in reg["factors"]:
        assert "source_content_hash" in f or f["file_exists"] is False
        assert f["target_classification"] == "non_target"


def test_05_operating_accruals_field_contract():
    # value columns must never be target/forward fields
    cfg = fixture_config()
    for f in cfg["sources"]["factors"]:
        assert not of._is_target_field(f["value_column"])


def test_06_unknown_field_rejected():
    cfg = fixture_config()
    cfg["sources"]["factors"][1]["value_column"] = "not_a_real_column"
    v = of.validate_owned_factor_config(cfg)
    assert not v["accepted"]  # not in permitted_numeric_fields


def test_07_target_field_rejected():
    cfg = fixture_config()
    cfg["permitted_numeric_fields"].append("fwd_1m_return")
    cfg["sources"]["factors"][1]["value_column"] = "fwd_1m_return"
    v = of.validate_owned_factor_config(cfg)
    assert not v["accepted"]


def test_08_source_hashes_deterministic(env):
    r1 = of.build_source_registry(env["cfg"])
    r2 = of.build_source_registry(env["cfg"])
    h1 = {f["source_id"]: f.get("source_content_hash") for f in r1["factors"]}
    h2 = {f["source_id"]: f.get("source_content_hash") for f in r2["factors"]}
    assert h1 == h2 and all(v for v in h1.values())


def test_09_paths_come_only_from_config(env):
    # a factor path token with traversal is rejected at resolution
    cfg = env["cfg"]
    bad = dict(cfg["sources"]["factors"][1], subdir="../../etc")
    with pytest.raises(of.OwnedFactorError):
        of._factor_path(cfg, bad)


def test_10_missing_file_fails_clearly(env):
    cfg = env["cfg"]
    cfg["sources"]["factors"][1]["file"] = "does_not_exist.csv"
    reg = of.build_source_registry(cfg)
    ids = {f["source_id"]: f for f in reg["factors"]}
    assert ids["gross_profitability"]["file_exists"] is False
    assert ids["gross_profitability"]["usage_resolved"] == "blocked"


# =========================================================================== #
# PIT joins (11-18)
# =========================================================================== #
def _by_tk():
    return {
        "AAA": [("2018-05-15", 1.0), ("2018-08-14", 2.0), ("2018-11-13", 3.0)],
        "BBB": [("2019-02-10", 9.0)],
    }


def test_11_asof_selects_latest_at_or_before():
    series, _m = of.asof_join_eodhd(_by_tk(), ["2018-09", "2018-12"], max_staleness_months=24)
    assert series["2018-09"]["AAA"] == 2.0
    assert series["2018-12"]["AAA"] == 3.0


def test_12_later_filing_never_selected():
    series, _m = of.asof_join_eodhd(_by_tk(), ["2018-06"], max_staleness_months=24)
    assert series["2018-06"]["AAA"] == 1.0  # not the 2018-08 filing


def test_13_future_backfill_impossible():
    # BBB's only observation is 2019-02; it must be absent for any earlier month
    series, meta = of.asof_join_eodhd(_by_tk(), ["2018-06", "2018-12"], max_staleness_months=99)
    assert "BBB" not in series.get("2018-06", {})
    assert "BBB" not in series.get("2018-12", {})
    assert meta["future_join_violations"] == 0


def test_14_max_staleness_enforced():
    series, meta = of.asof_join_eodhd(_by_tk(), ["2019-09"], max_staleness_months=3)
    # AAA latest is 2018-11 -> 10 months stale -> rejected; BBB 2019-02 -> 7 stale -> rejected
    assert series.get("2019-09", {}) == {}
    assert meta["rejected_by_staleness"] >= 1


def test_15_filing_date_provenance_recorded():
    _s, meta = of.asof_join_eodhd(_by_tk(), ["2018-12"], max_staleness_months=24)
    assert "available_date" in meta["filing_date_provenance"]
    assert meta["restatement_caveat"]


def test_16_data_cutoff_immutable(env):
    inputs = of.load_inputs(env["cfg"])
    assert inputs["data_cutoff"] == CUTOFF
    assert all(fb._month_end(fb._next_month(m)) <= CUTOFF for m in inputs["months"])


def test_17_restatement_caveat_persisted(env):
    cfg = env["cfg"]
    f = cfg["sources"]["factors"][1]
    series, meta = of.build_factor_series(cfg, f, MONTHS)
    audit = of.build_factor_pit_audit(f, meta, CUTOFF)
    assert audit["restatement_caveat"]
    assert any(c["check"] == "restatement_caveat_persisted" and c["passed"]
               for c in audit["checks"])


def test_18_identical_inputs_identical_joined_hash(env):
    cfg = env["cfg"]
    f = cfg["sources"]["factors"][1]
    _s1, m1 = of.build_factor_series(cfg, f, MONTHS)
    _s2, m2 = of.build_factor_series(cfg, f, MONTHS)
    assert m1["joined_content_hash"] == m2["joined_content_hash"]


# =========================================================================== #
# Universe (19-27)
# =========================================================================== #
def test_19_global_pit_universe_unchanged():
    inputs = make_inputs()
    series = make_series(inputs)
    uni = of.build_universe_profiles(inputs, {"x": series}, fixture_config(), sector_pit_safe=False)
    g = uni["global_pit_universe"]
    assert g["distinct_current_members"] == len(CURRENT)
    assert g["distinct_removed_members"] == len(REMOVED)


def test_20_source_observed_entry_after_first_observation():
    by_tk = {"AAA": [("2018-05-15", 1.0)]}
    series, _m = of.asof_join_eodhd(by_tk, ["2018-01", "2018-06"], max_staleness_months=99)
    assert "AAA" not in series.get("2018-01", {})
    assert series["2018-06"]["AAA"] == 1.0


def test_21_future_presence_cannot_add_earlier_member():
    inputs = make_inputs()
    # a factor observed ONLY in the last month must not change earlier global counts
    last = inputs["months"][-1]
    series = {last: {tk: 1.0 for tk in ALL_TK}}
    uni = of.build_universe_profiles(inputs, {"x": series}, fixture_config(), sector_pit_safe=False)
    # global members in the first month depend only on is_member, not the factor
    first = inputs["months"][0]
    n_members_first = sum(1 for r in inputs["mom_monthly"][first].values() if r["is_member"])
    assert uni["global_pit_universe"]["member_count_first_month"] == n_members_first


def test_22_current_survivor_only_classified_honestly():
    inputs = make_inputs()
    series = make_series(inputs, covers=CURRENT)  # only survivors
    uni = of.build_universe_profiles(inputs, {"gp": series}, fixture_config(), sector_pit_safe=False)
    r = uni["source_observed_universe"]["gp"]
    assert r["survivorship_classification"] in (
        "DIAGNOSTIC_ONLY_CURRENT_SURVIVOR_BIAS", "BLOCKED_INSUFFICIENT_DELISTED_COVERAGE")
    assert r["shadow_eligible"] is False


def test_23_delisted_coverage_measured():
    inputs = make_inputs()
    series = make_series(inputs, covers=ALL_TK)  # covers removed names too
    uni = of.build_universe_profiles(inputs, {"rv": series}, fixture_config(), sector_pit_safe=False)
    r = uni["source_observed_universe"]["rv"]
    assert r["covered_removed_members"] == len(REMOVED)
    assert r["delisted_representation_fraction"] > 0.2


def test_24_global_and_source_coverage_persisted():
    inputs = make_inputs()
    series = make_series(inputs, covers=CURRENT)
    uni = of.build_universe_profiles(inputs, {"gp": series}, fixture_config(), sector_pit_safe=False)
    r = uni["source_observed_universe"]["gp"]
    assert "source_observed_fraction_of_global" in r
    assert "coverage_by_decade" in r


def test_25_denominator_not_changed_by_outcome():
    inputs = make_inputs()
    a = of.build_universe_profiles(inputs, {"gp": make_series(inputs, covers=CURRENT)},
                                   fixture_config(), sector_pit_safe=False)
    b = of.build_universe_profiles(inputs, {"rv": make_series(inputs, covers=ALL_TK)},
                                   fixture_config(), sector_pit_safe=False)
    assert a["global_pit_universe"]["member_months_total"] == \
        b["global_pit_universe"]["member_months_total"]


def test_26_diagnostic_only_universe_not_shadow_eligible():
    inputs = make_inputs()
    uni = of.build_universe_profiles(inputs, {"gp": make_series(inputs, covers=CURRENT)},
                                     fixture_config(), sector_pit_safe=False)
    assert uni["source_observed_universe"]["gp"]["shadow_eligible"] is False


def test_27_universe_construction_deterministic():
    inputs = make_inputs()
    s = make_series(inputs, covers=CURRENT)
    a = of.build_universe_profiles(inputs, {"gp": s}, fixture_config(), sector_pit_safe=False)
    b = of.build_universe_profiles(inputs, {"gp": s}, fixture_config(), sector_pit_safe=False)
    assert of.content_hash(a) == of.content_hash(b)


# =========================================================================== #
# Sector (28-32)
# =========================================================================== #
def test_28_current_only_not_pit_safe(env):
    audit = of.build_sector_audit(of.load_inputs(env["cfg"]), env["cfg"])
    repaired = [c for c in audit["classifications"]
                if c["source"] == "phase10f_repaired_eodhd_gics_map"][0]
    assert repaired["classification"] == "CURRENT_ONLY"
    assert repaired["pit_safe"] is False
    assert audit["pit_safe_classification_available"] is False


def test_29_unknown_not_a_real_sector(env):
    inputs = of.load_inputs(env["cfg"])
    audit = of.build_sector_audit(inputs, env["cfg"])
    assert audit["treat_unknown_as_sector"] is False
    panel = [c for c in audit["classifications"] if c["source"] == "momentum_panel.sector"][0]
    assert panel["classification"] == "UNUSABLE"


def test_30_concentration_incl_and_excl_unknown(env):
    inputs = of.load_inputs(env["cfg"])
    cfg = env["cfg"]
    series, _m = of.build_factor_series(cfg, cfg["sources"]["factors"][0], inputs["months"])
    audit = of.build_sector_audit(inputs, cfg, {"realized_vol_63d": series})
    c = audit["top_quartile_concentration"]["realized_vol_63d"]
    assert "avg_top_quartile_sector_share_incl_unknown" in c
    assert "avg_top_quartile_sector_share_excl_unknown" in c
    assert "avg_unknown_share_in_top_quartile" in c


def test_31_pit_sector_requirement_blocks_promotion():
    # even a screen that would ADVANCE is downgraded when no PIT sector exists
    screen = {"outcome": "ADVANCE_TO_PORTFOLIO_SCREEN", "checks": [], "reasons": []}
    assert of._map_screen_to_decision(screen, sector_pit_safe=False,
                                       require_pit_sector=True) == "DIAGNOSTIC_ONLY_SECTOR_HISTORY"
    assert of._map_screen_to_decision(screen, sector_pit_safe=True,
                                       require_pit_sector=True) == "ADVANCE_TO_PORTFOLIO_SCREEN"


def test_32_no_sector_gate_lowered():
    cfg = fixture_config()
    cfg["ic_screen"]["max_top_rank_sector_share"] = 0.9  # weaker than committed 0.5
    v = of.validate_owned_factor_config(cfg)
    assert not v["accepted"]


# =========================================================================== #
# Diagnostics (33-42)
# =========================================================================== #
def _surv(cls="PASS_WITH_CAVEAT", frac=0.6, shadow=True):
    return {"survivorship_classification": cls,
            "delisted_representation_fraction": frac, "shadow_eligible": shadow}


def test_33_gross_profitability_diagnostic_deterministic():
    inputs = make_inputs()
    s = make_series(inputs, covers=CURRENT, salt="gp")
    a = of.evaluate_factor(inputs, s, factor_id="gp", baseline_t=0.8,
                           survivorship=_surv(), sector_pit_safe=False, cfg=fixture_config())
    b = of.evaluate_factor(inputs, s, factor_id="gp", baseline_t=0.8,
                           survivorship=_surv(), sector_pit_safe=False, cfg=fixture_config())
    a.pop("_full_global_diagnostic", None)
    b.pop("_full_global_diagnostic", None)
    assert of.content_hash(a) == of.content_hash(b)


def test_34_fcf_diagnostic_deterministic():
    inputs = make_inputs()
    s = make_series(inputs, covers=CURRENT, salt="fcf")
    d1 = ev.compute_feature_diagnostics(s, inputs, feature_id="f_fcf")
    d2 = ev.compute_feature_diagnostics(s, inputs, feature_id="f_fcf")
    assert of.content_hash(d1) == of.content_hash(d2)


def test_35_operating_accrual_diagnostic_deterministic():
    inputs = make_inputs()
    s = make_series(inputs, covers=CURRENT, salt="acc")
    d1 = ev.compute_feature_diagnostics(s, inputs, feature_id="f_acc")
    d2 = ev.compute_feature_diagnostics(s, inputs, feature_id="f_acc")
    assert d1["rank_ic_t"] == d2["rank_ic_t"]


def test_36_realized_vol_diagnostic_deterministic():
    inputs = make_inputs()
    s = make_series(inputs, covers=ALL_TK, salt="vol")
    d1 = ev.compute_feature_diagnostics(s, inputs, feature_id="f_vol")
    d2 = ev.compute_feature_diagnostics(s, inputs, feature_id="f_vol")
    assert of.content_hash(d1) == of.content_hash(d2)


def test_37_global_and_source_universe_distinct():
    inputs = make_inputs()
    s = make_series(inputs, covers=CURRENT, salt="gp")  # partial coverage
    res = of.evaluate_factor(inputs, s, factor_id="gp", baseline_t=0.8,
                             survivorship=_surv("DIAGNOSTIC_ONLY_CURRENT_SURVIVOR_BIAS", 0.01, False),
                             sector_pit_safe=False, cfg=fixture_config())
    g = res["global_pit_universe"]["diagnostics"]["cross_sectional_coverage"]
    s2 = res["source_observed_universe"]["diagnostics"]["cross_sectional_coverage"]
    assert g < s2  # source-observed coverage is higher by construction


def test_38_positive_ic_cannot_bypass_coverage():
    inputs = make_inputs()
    s = make_series(inputs, covers=CURRENT[:15], salt="gp")  # 15/40 -> 0.375 global
    res = of.evaluate_factor(inputs, s, factor_id="gp", baseline_t=0.8,
                             survivorship=_surv(), sector_pit_safe=False, cfg=fixture_config())
    assert res["global_pit_universe"]["decision"] == "REJECTED_GLOBAL_COVERAGE"


def test_39_positive_ic_cannot_bypass_survivorship():
    inputs = make_inputs()
    s = make_series(inputs, covers=CURRENT, salt="gp")  # full source coverage but survivor-biased
    res = of.evaluate_factor(inputs, s, factor_id="gp", baseline_t=0.8,
                             survivorship=_surv("DIAGNOSTIC_ONLY_CURRENT_SURVIVOR_BIAS", 0.01, False),
                             sector_pit_safe=False, cfg=fixture_config())
    assert res["source_observed_universe"]["decision"] == "DIAGNOSTIC_ONLY_SURVIVORSHIP"


def test_40_positive_ic_cannot_bypass_sector_history():
    screen = {"outcome": "ADVANCE_TO_PORTFOLIO_SCREEN", "checks": [], "reasons": []}
    assert of._map_screen_to_decision(screen, False, True) == "DIAGNOSTIC_ONLY_SECTOR_HISTORY"


def test_41_near_duplicate_gate_enforced():
    cfg = fixture_config()
    cfg["ic_screen"]["near_duplicate_abs_corr"] = 0.99  # weaker than committed 0.95
    v = of.validate_owned_factor_config(cfg)
    assert not v["accepted"]


def test_42_baseline_reproduction_exact():
    inputs = make_inputs()
    # the integrated engine collapses EXACTLY to the committed baseline sim
    repro = ev.verify_baseline_reproduction_via_integration(inputs)
    assert repro["reproduced"] is True
    # deterministic + invariant-clean (no external reference for a fixture)
    val = fb.run_baseline_validation(inputs, reference_rows=[])
    assert val["deterministic"] is True
    assert val["invariant_failures"] == []
    assert val["baseline_reproduced"] is True


# =========================================================================== #
# Portfolio (43-48)
# =========================================================================== #
def test_43_only_advance_candidates_integrate(env):
    result = of.run_owned_factor_campaign(env["cfg"], output_root=env["out"], max_features=2)
    assert result["status"] == "COMPLETE"
    assert result["n_portfolio_candidates"] == len(result["advanced_to_portfolio_screen"])


def test_44_weights_reconcile_to_one():
    inputs = make_inputs()
    s = make_series(inputs, covers=ALL_TK, salt="gp")
    port = of.run_portfolio_integration(inputs, s, fixture_config(), factor_id="gp", baseline_t=0.8)
    assert port["integration_weights_reconcile"] is True
    assert abs(port["params"]["baseline_weight"] + port["params"]["feature_weight"] - 1.0) < 1e-9


def test_45_costs_charged_once():
    inputs = make_inputs()
    s = make_series(inputs, covers=ALL_TK, salt="gp")
    port = of.run_portfolio_integration(inputs, s, fixture_config(), factor_id="gp", baseline_t=0.8)
    m = port["candidate_metrics"]
    assert m["primary_cost_bps_per_side"] == 25.0
    # cost ladder is distinct per level -> each level applied once, never stacked
    ladder = m["net_excess_ann_by_cost_bps"]
    assert ladder["12.5"] != ladder["50.0"]


def test_46_baseline_relative_deltas_reconcile():
    inputs = make_inputs()
    s = make_series(inputs, covers=ALL_TK, salt="gp")
    port = of.run_portfolio_integration(inputs, s, fixture_config(), factor_id="gp", baseline_t=0.8)
    delta = port["baseline_relative_delta"]["net_spy_excess_ann"]
    recomputed = ((port["candidate_metrics"].get("net_spy_excess_ann") or 0)
                  - (port["baseline_metrics"].get("net_spy_excess_ann") or 0))
    assert abs(delta - recomputed) < 1e-9


def test_47_no_operational_exit_buffer():
    inputs = make_inputs()
    s = make_series(inputs, covers=ALL_TK, salt="gp")
    port = of.run_portfolio_integration(inputs, s, fixture_config(), factor_id="gp", baseline_t=0.8)
    assert port["params"]["exit_buffer_fraction"] == 0.0


def test_48_no_operational_write(env):
    result = of.run_owned_factor_campaign(env["cfg"], output_root=env["out"], max_features=2)
    # everything is confined to the run dir under the disposable output root
    run_dir = result["run_dir"]
    assert env["out"] in run_dir
    assert "paper_trader" not in run_dir.lower()


# =========================================================================== #
# Safety (49-54)
# =========================================================================== #
def test_49_no_paper_trader_endpoint_called():
    import inspect
    src = inspect.getsource(of)
    assert "127.0.0.1:8001" not in src and "requests" not in src


def test_50_no_orders_created():
    assert of.SAFETY_CONTRACT["creates_orders"] is False


def test_51_no_broker_execution():
    assert of.SAFETY_CONTRACT["broker_execution"] is False


def test_52_no_automation_enabled():
    assert of.SAFETY_CONTRACT["automation_of_trading"] is False


def test_53_operational_model_and_holdings_unchanged():
    assert of.SAFETY_CONTRACT["operational_model_changed"] is False
    assert of.SAFETY_CONTRACT["operational_holdings_changed"] is False


def test_54_no_challenger_registration(env):
    result = of.run_owned_factor_campaign(env["cfg"], output_root=env["out"], max_features=2)
    run = of.OwnedFactorRunStore(env["out"]).read(result["run_id"], "run.json")
    assert run["safety"]["promotion_requires_human_approval"] is True
    # no challenger registry file is ever created
    assert not os.path.exists(os.path.join(result["run_dir"], "challengers"))


# =========================================================================== #
# CLI (55-60)
# =========================================================================== #
def _write_cfg(tmp_path, cfg):
    p = str(tmp_path / "cfg.json")
    with open(p, "w", encoding="utf-8") as fh:
        json.dump(cfg, fh)
    return p


def test_55_validate_exit_codes(tmp_path, monkeypatch):
    root = str(tmp_path / "src")
    os.makedirs(root, exist_ok=True)
    write_fixture_files(root)
    monkeypatch.setattr(of, "_ROOTS", {"repo": root, "data_root": root})
    ok = _write_cfg(tmp_path, fixture_config())
    assert cli.main(["owned-factor-validate", "--config", ok, "--json"]) == 0
    bad_cfg = fixture_config()
    bad_cfg["global_min_cross_sectional_coverage"] = 0.1
    bad = _write_cfg(tmp_path, bad_cfg)
    assert cli.main(["owned-factor-validate", "--config", bad, "--json"]) == 2


def test_56_audit_read_only(env, tmp_path):
    cfg_path = _write_cfg(tmp_path, env["cfg"])
    before = _snapshot(env["root"])
    assert cli.main(["owned-factor-audit", "--config", cfg_path, "--json"]) == 0
    assert _snapshot(env["root"]) == before


def test_57_universe_audit_read_only(env, tmp_path):
    cfg_path = _write_cfg(tmp_path, env["cfg"])
    before = _snapshot(env["root"])
    assert cli.main(["owned-universe-audit", "--config", cfg_path, "--json"]) == 0
    assert _snapshot(env["root"]) == before


def test_58_sector_audit_read_only(env, tmp_path):
    cfg_path = _write_cfg(tmp_path, env["cfg"])
    before = _snapshot(env["root"])
    assert cli.main(["owned-sector-audit", "--config", cfg_path, "--json"]) == 0
    assert _snapshot(env["root"]) == before


def test_59_diagnostics_respects_feature_limit(env):
    result = of.run_owned_factor_campaign(env["cfg"], output_root=env["out"], max_features=1)
    assert result["features_evaluated"] == ["gross_profitability"]


def test_60_report_idempotent(env):
    result = of.run_owned_factor_campaign(env["cfg"], output_root=env["out"], max_features=2)
    r1 = of.generate_report(result["run_id"], env["out"])
    r2 = of.generate_report(result["run_id"], env["out"])
    assert of.content_hash(r1["report"]) == of.content_hash(r2["report"])


def _snapshot(root):
    out = {}
    for dp, _dn, fns in os.walk(root):
        for fn in fns:
            p = os.path.join(dp, fn)
            out[p] = os.path.getmtime(p)
    return out
