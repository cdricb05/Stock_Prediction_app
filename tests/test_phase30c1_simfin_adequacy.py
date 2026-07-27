"""Phase 30C.1 — local SimFin adequacy + free-data coverage ceiling.

Hermetic: fixture CSV/JSON files under a tmp tree with ``historical_coverage.
_ROOTS`` monkeypatched to it. No network (the module has no HTTP client at all),
no SimFin API key, no Paper Trader, no operational state, no committed data files.
The family-backtest evaluation is exercised on small in-memory inputs injected in
place of ``family_backtest.load_family_inputs``.
"""

from __future__ import annotations

import csv
import hashlib
import json
import os
import zipfile

import pytest

from research_agent import cli
from research_agent import family_backtest as fb
from research_agent import historical_coverage as hc
from research_agent import owned_factors as of
from research_agent import simfin_adequacy as sa

# --------------------------------------------------------------------------- #
# synthetic universe
# --------------------------------------------------------------------------- #
CURRENT = ["AA", "BB", "CC", "DD", "EE"]
SEC_DIR = {c: 101 + i for i, c in enumerate(CURRENT)}   # AA=101..EE=105
SEC_DIR["RE"] = 900                                      # reused base ticker -> current co
SEC_DIR["BK"] = 140
GG = "GG-202106"    # removed, in-window (2020s) -> statements present
HH = "HH-200506"    # removed, pre-window        -> mapped, NO statements
RE = "RE-200506"    # removed, ticker reused by a CURRENT company -> unresolved
BK = "BK"           # bank (current) -> excluded
REMOVED = [GG, HH, RE]
ALL_TK = CURRENT + REMOVED + [BK]

# SimFin us-companies rows: base -> (SimFinId, CIK, name). NOTE: RE is intentionally
# absent so the removed RE (whose base ticker only matches a CURRENT SEC ticker)
# stays unresolved. EE additionally has a SECOND SimFin row under the SAME CIK
# (see EE_DUP_ROW) -> that CIK maps to multiple SimFinIds -> ambiguous/unresolved.
SIMFIN_CO = {
    "AA": ("SF101", 101, "AA Corp"), "BB": ("SF102", 102, "BB Corp"),
    "CC": ("SF103", 103, "CC Corp"), "DD": ("SF104", 104, "DD Corp"),
    "EE": ("SF105", 105, "EE Corp"),
    "GG": ("SF120", 120, "GG Corp"), "HH": ("SF130", 130, "HH Corp"),
    "BK": ("SF140", 140, "BK Bancorp"),
}
EE_DUP_ROW = ["EE", "SF106", "EE Holdings", "1", "", "12", "10", "biz", "us", 105, "USD"]
# companies WITH standard statements in the SimFin window
STD_STMT_SIDS = {"SF101", "SF102", "SF103", "SF104", "SF105", "SF120"}
BANK_SIDS = {"SF140"}

MONTHS = ["%04d-%02d" % (y, m) for y in (2019, 2020, 2021, 2022) for m in range(1, 13)]
CUTOFF = "2023-06-30"


def _u(salt, *parts):
    h = int(hashlib.sha256((salt + "|".join(parts)).encode()).hexdigest()[:8], 16)
    return (h % 100000) / 100000.0 - 0.5


def make_inputs():
    """Small in-memory owned inputs (composite_sn present -> baseline runs)."""
    final = MONTHS[-1]
    mom, fund_cf, fund_monthly = {}, {}, {}
    for m in MONTHS:
        mrow, frow, fmrow = {}, {}, {}
        for tk in ALL_TK:
            is_member = not (tk in REMOVED and m == final)
            mrow[tk] = {"ticker": tk, "mom_6_1": _u("mom", m, tk),
                        "fwd_1m": _u("fwd", m, tk) * 0.1, "eligible": True,
                        "is_member": is_member, "adv_dollar": 1.0e8, "sector": "Unknown"}
            comp = _u("comp", m, tk)
            frow[tk] = {"composite_sn": comp, "sector": "Unknown", "fund_month": m}
            fmrow[tk] = {"ticker": tk, "composite_sn": comp, "sector": "Unknown"}
        mom[m], fund_cf[m], fund_monthly[m] = mrow, frow, fmrow
    spy_close = {}
    allm = MONTHS + ["2023-01"]
    for i, m in enumerate(allm):
        spy_close[m] = 100.0 * (1.0 + 0.01 * i)
    spy_fwd = {m: (spy_close[fb._next_month(m)] / spy_close[m] - 1.0) for m in MONTHS}
    return {"mom_monthly": mom, "fund_monthly": fund_monthly, "fund_cf": fund_cf,
            "sector_map": {}, "spy_close": spy_close, "spy_fwd": spy_fwd,
            "months": list(MONTHS), "data_cutoff": CUTOFF,
            "provenance": {"sha256": {"momentum_panel": "x"}}}


# --------------------------------------------------------------------------- #
# fixture files
# --------------------------------------------------------------------------- #
def _write_csv(path, header, rows, delimiter=","):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="") as fh:
        w = csv.writer(fh, delimiter=delimiter)
        w.writerow(header)
        w.writerows(rows)


_INCOME_HDR = ["Ticker", "SimFinId", "Currency", "Fiscal Year", "Fiscal Period",
               "Report Date", "Publish Date", "Restated Date", "Revenue",
               "Cost of Revenue", "Gross Profit", "Net Income"]
_BAL_HDR = ["Ticker", "SimFinId", "Currency", "Fiscal Year", "Fiscal Period",
            "Report Date", "Publish Date", "Restated Date", "Total Assets"]
_CF_HDR = ["Ticker", "SimFinId", "Currency", "Fiscal Year", "Fiscal Period",
           "Report Date", "Publish Date", "Restated Date",
           "Net Cash from Operating Activities", "Change in Fixed Assets & Intangibles"]
_BANK_INC_HDR = ["Ticker", "SimFinId", "Currency", "Fiscal Year", "Fiscal Period",
                 "Report Date", "Publish Date", "Restated Date", "Revenue", "Net Income"]


def _stmt_rows(base, sid, cik, kind):
    rows = []
    for fy in (2019, 2020, 2021, 2022):
        rd = "%d-12-31" % fy
        pub = "%d-05-01" % (fy + 1)
        # SF101 FY2019 income is a later restatement (restated > publish)
        restated = "2022-05-01" if (sid == "SF101" and fy == 2019 and kind == "income") else ""
        if kind == "income":
            rows.append([base, sid, "USD", str(fy), "Q4", rd, pub, restated,
                         "1000", "-600", "400", "120"])
        elif kind == "balance":
            rows.append([base, sid, "USD", str(fy), "Q4", rd, pub, restated, "5000"])
        elif kind == "cashflow":
            rows.append([base, sid, "USD", str(fy), "Q4", rd, pub, restated, "300", "-40"])
    return rows


def write_fixtures(root):
    sd = os.path.join(root, "research", "data", "simfin")
    # us-companies (semicolon)
    co_rows = [[base, sid, name, "1", "", "12", "10", "biz", "us", cik, "USD"]
               for base, (sid, cik, name) in SIMFIN_CO.items()]
    co_rows.append(EE_DUP_ROW)   # a second SimFinId under EE's CIK 105 -> ambiguous
    _write_csv(os.path.join(sd, "us-companies.csv"),
               ["Ticker", "SimFinId", "Company Name", "IndustryId", "ISIN",
                "End of financial year (month)", "Number Employees", "Business Summary",
                "Market", "CIK", "Main Currency"], co_rows, delimiter=";")
    # standard statements (only STD_STMT_SIDS have rows; HH/SF130 & bank excluded)
    inc, bal, cf = [], [], []
    for base, (sid, cik, _n) in SIMFIN_CO.items():
        if sid in STD_STMT_SIDS:
            inc += _stmt_rows(base, sid, cik, "income")
            bal += _stmt_rows(base, sid, cik, "balance")
            cf += _stmt_rows(base, sid, cik, "cashflow")
    _write_csv(os.path.join(sd, "us-income-quarterly.csv"), _INCOME_HDR, inc, delimiter=";")
    _write_csv(os.path.join(sd, "us-balance-quarterly.csv"), _BAL_HDR, bal, delimiter=";")
    _write_csv(os.path.join(sd, "us-cashflow-quarterly.csv"), _CF_HDR, cf, delimiter=";")
    # bank income (SF140) -> excluded
    _write_csv(os.path.join(sd, "us-income-banks-quarterly.csv"), _BANK_INC_HDR,
               [["BK", "SF140", "USD", "2022", "Q4", "2022-12-31", "2023-03-01", "", "900", "80"]],
               delimiter=";")
    # a ZIP archive (read-only manifest inspection)
    os.makedirs(os.path.join(sd, "download"), exist_ok=True)
    with zipfile.ZipFile(os.path.join(sd, "download", "us-companies.zip"), "w") as zf:
        zf.write(os.path.join(sd, "us-companies.csv"), "us-companies.csv")

    # momentum panel
    final = MONTHS[-1]
    mom_rows = []
    for m in MONTHS:
        for tk in ALL_TK:
            is_member = 0 if (tk in REMOVED and m == final) else 1
            mom_rows.append([m, m + "-15", tk, "%.6f" % _u("mom", m, tk),
                             "%.6f" % (_u("fwd", m, tk) * 0.1), is_member, "100000000",
                             "%.6f" % (abs(_u("vol", m, tk)) + 0.1), 1, "Unknown"])
    _write_csv(os.path.join(root, "mom.csv"),
               ["month", "market_date", "ticker", "mom_6_1", "fwd_1m_return",
                "is_member", "adv_dollar", "realized_vol_63d", "eligible_history", "sector"], mom_rows)
    # security master (phase8c schema)
    sm_rows = []
    for tk in ALL_TK:
        is_del = tk in REMOVED
        name = ("Old Reuse Inc" if tk == RE else "%s Corp" % hc.base_symbol(tk))
        dm = hc.delisting_month(tk)
        lq = (dm + "-15") if dm else ""
        sm_rows.append([tk, name, "Industrials", "1999-01-01", lq,
                        "True" if is_del else "False", "120", "60"])
    _write_csv(os.path.join(root, "master.csv"),
               ["ticker", "security_name", "gics_sector", "first_quoted_date",
                "last_quoted_date", "is_delisted", "n_monthly_obs", "n_member_months"], sm_rows)
    _write_csv(os.path.join(root, "sector_map.csv"),
               ["ticker", "original_sector", "repaired_sector", "repaired_industry",
                "source_file_or_source_family", "source_file", "source_field", "confidence", "reason"],
               [[tk, "o", "Financials", "i", "s", "s", "f", "0.9", "w"] for tk in CURRENT])
    # SEC company_tickers.json (current directory)
    directory = {str(i): {"cik_str": cik, "ticker": tk, "title": "%s Inc" % tk}
                 for i, (tk, cik) in enumerate(SEC_DIR.items())}
    os.makedirs(os.path.join(root, "sec"), exist_ok=True)
    with open(os.path.join(root, "sec", "company_tickers.json"), "w", encoding="utf-8") as fh:
        json.dump(directory, fh)

    # a Phase 30C pointer + sample manifest (reused sample)
    runs = os.path.join(root, "hcruns")
    os.makedirs(os.path.join(runs, "hcov_test"), exist_ok=True)
    sample = {"seed": 30, "sample_hash": "abc",
              "removed": [{"ticker": GG}, {"ticker": HH}, {"ticker": RE}],
              "current": [{"ticker": "AA"}, {"ticker": "BB"}]}
    with open(os.path.join(runs, "hcov_test", "sample_manifest.json"), "w", encoding="utf-8") as fh:
        json.dump(sample, fh)
    with open(os.path.join(root, "phase30c_latest_run.json"), "w", encoding="utf-8") as fh:
        json.dump({"run_id": "hcov_test"}, fh)

    # synthetic SEC cache (companyfacts + submissions) for AA (CIK 101)
    for kind in ("companyfacts", "submissions"):
        os.makedirs(os.path.join(root, "seccache", kind), exist_ok=True)
    with open(os.path.join(root, "seccache", "companyfacts", "CIK0000000101.json"), "w", encoding="utf-8") as fh:
        json.dump(_fake_companyfacts(101), fh)
    with open(os.path.join(root, "seccache", "submissions", "CIK0000000101.json"), "w", encoding="utf-8") as fh:
        json.dump(_fake_submissions(101), fh)


def _fake_companyfacts(cik):
    def facts(concept, unit, series):
        return {concept: {"label": concept, "units": {unit: series}}}

    def mk(end, fp, form, filed, accn, val):
        return {"end": end, "fp": fp, "form": form, "filed": filed, "accn": accn, "val": val}
    ug = {}
    ug.update(facts("Assets", "USD", [mk("2020-12-31", "FY", "10-K", "2021-02-01", "a1", 5000.0),
                                      mk("2021-12-31", "FY", "10-K", "2022-02-01", "a2", 5200.0)]))
    ug.update(facts("Revenues", "USD", [mk("2020-12-31", "FY", "10-K", "2021-02-01", "a1", 1000.0),
                                        mk("2021-12-31", "FY", "10-K", "2022-02-01", "a2", 1100.0)]))
    ug.update(facts("CostOfRevenue", "USD", [mk("2020-12-31", "FY", "10-K", "2021-02-01", "a1", 600.0),
                                             mk("2021-12-31", "FY", "10-K", "2022-02-01", "a2", 650.0)]))
    ug.update(facts("NetIncomeLoss", "USD", [mk("2020-12-31", "FY", "10-K", "2021-02-01", "a1", 120.0),
                                             mk("2021-12-31", "FY", "10-K", "2022-02-01", "a2", 130.0)]))
    ug.update(facts("NetCashProvidedByUsedInOperatingActivities", "USD",
                    [mk("2020-12-31", "FY", "10-K", "2021-02-01", "a1", 300.0),
                     mk("2021-12-31", "FY", "10-K", "2022-02-01", "a2", 320.0)]))
    return {"cik": cik, "entityName": "E%s" % cik, "facts": {"us-gaap": ug}}


def _fake_submissions(cik, sic="7372"):
    recent = {"accessionNumber": ["a2", "a1"], "form": ["10-K", "10-K"],
              "filingDate": ["2022-02-01", "2021-02-01"],
              "acceptanceDateTime": ["2022-02-01T16:00:00.000Z", "2021-02-01T16:00:00.000Z"],
              "reportDate": ["2021-12-31", "2020-12-31"]}
    return {"cik": cik, "name": "E%s" % cik, "sic": sic, "tickers": [], "formerNames": [],
            "filings": {"recent": recent}}


def fixture_config(root):
    def spec(rel):
        return {"root": "data_root", "relpath": rel}
    return {
        "schema_version": "30C1.1", "name": "t30c1",
        "data": {"data_cutoff": CUTOFF},
        "sources": {
            "roots": {"repo": "REPO_ROOT", "data_root": "DATA_ROOT"},
            "momentum_panel": spec("mom.csv"),
            "security_master": spec("master.csv"),
            "sector_map": spec("sector_map.csv"),
            "sec_company_tickers": spec("sec/company_tickers.json"),
            "simfin_companies": spec("research/data/simfin/us-companies.csv"),
            "simfin_income": spec("research/data/simfin/us-income-quarterly.csv"),
            "simfin_balance": spec("research/data/simfin/us-balance-quarterly.csv"),
            "simfin_cashflow": spec("research/data/simfin/us-cashflow-quarterly.csv"),
            "simfin_income_banks": spec("research/data/simfin/us-income-banks-quarterly.csv"),
            "simfin_download_dir": spec("research/data/simfin/download"),
            "sec_cache_companyfacts_dir": spec("seccache/companyfacts"),
            "sec_cache_submissions_dir": spec("seccache/submissions"),
            "phase30c_pointer": spec("phase30c_latest_run.json"),
            "phase30c_runs_root": spec("hcruns"),
        },
        "entitlement": {"no_purchase": True, "contact_sales": False, "use_existing_keys_only": True},
        "max_factor_staleness_months": 15,
        "coverage_gates": {"global_min_cross_sectional_coverage": 0.60,
                           "global_min_month_coverage": 0.60, "min_delisted_representation_fraction": 0.20},
        "sector_history": {"require_pit_safe_for_promotion": True, "treat_unknown_as_sector": False,
                           "member_month_coverage_min": 0.60},
        "diagnostics": {"factors": ["gross_profitability", "fcf_to_assets", "operating_accruals"]},
        "ic_screen": {"min_months": 36, "min_coverage_fraction": 0.6, "min_abs_rank_ic_t": 1.0,
                      "material_ic_t_margin": 0.25, "near_duplicate_abs_corr": 0.95,
                      "max_complementary_abs_baseline_corr": 0.5, "max_top_rank_sector_share": 0.5,
                      "leakage_suspicion_abs_ic": 0.5, "min_universe": 10},
        "integration": {"baseline_weight": 0.8, "feature_weight": 0.2},
        "costs": {"primary_cost_bps_per_side": 25.0, "sensitivity_cost_bps_per_side": [12.5, 50.0]},
        "portfolio": {"top_n": 25, "sector_treatment": "sector_cap", "exit_buffer_fraction": 0.0,
                      "universe": "mhz_reconstruction", "min_adv_dollar": 10000000.0},
        "baseline": {"rank_ic_t": 0.7943535272584944},
        "random_seed": 30, "strict_mode": True,
        "safety": {"research_only": True, "no_operational_promotion": True, "may_register_challengers": False},
    }


@pytest.fixture
def env(tmp_path, monkeypatch):
    root = str(tmp_path)
    write_fixtures(root)
    monkeypatch.setattr(hc, "_ROOTS", {"repo": root, "data_root": root})
    cfg = fixture_config(root)
    return {"root": root, "cfg": cfg, "out": os.path.join(root, "runs")}


def _mapping(env):
    master = hc.build_security_master(env["cfg"])
    index = sa.build_simfin_company_index(env["cfg"])
    return master, sa.build_simfin_mapping(master, index)


# =========================================================================== #
# 1-7  configuration and safety
# =========================================================================== #
def test_01_config_has_no_credentials(env):
    assert sa.validate_config(env["cfg"])["accepted"]
    bad = dict(env["cfg"]); bad["simfin_api_key"] = "x"
    assert not sa.validate_config(bad)["accepted"]


def test_02_only_fixed_local_paths_accepted(env):
    bad = json.loads(json.dumps(env["cfg"]))
    bad["sources"]["momentum_panel"] = {"root": "c_drive", "relpath": "x.csv"}
    v = sa.validate_config(bad)
    assert not v["accepted"]
    assert any("sources.momentum_panel" in x["field"] for x in v["violations"])


def test_03_network_urls_rejected(env):
    bad = json.loads(json.dumps(env["cfg"]))
    bad["sources"]["simfin_income"] = {"root": "data_root", "relpath": "x.csv"}
    bad["note"] = "https://simfin.com/api/bulk"
    v = sa.validate_config(bad)
    assert not v["accepted"]
    assert any("network URL is forbidden" in x["issue"] for x in v["violations"])


def test_04_simfin_network_calls_impossible():
    src = open(sa.__file__, "r", encoding="utf-8").read()
    assert "urlopen(" not in src and "import urllib" not in src
    assert not hasattr(sa, "urllib")


def test_05_paper_trader_paths_rejected(env):
    bad = json.loads(json.dumps(env["cfg"]))
    bad["oops"] = "operational_book write"
    assert not sa.validate_config(bad)["accepted"]


def test_06_coverage_gates_cannot_be_weakened(env):
    bad = json.loads(json.dumps(env["cfg"]))
    bad["coverage_gates"]["global_min_cross_sectional_coverage"] = 0.4
    assert not sa.validate_config(bad)["accepted"]
    bad2 = json.loads(json.dumps(env["cfg"]))
    bad2["coverage_gates"]["min_delisted_representation_fraction"] = 0.1
    assert not sa.validate_config(bad2)["accepted"]


def test_07_no_order_broker_automation_promotion(env):
    bad = json.loads(json.dumps(env["cfg"]))
    bad["safety"]["may_register_challengers"] = True
    assert not sa.validate_config(bad)["accepted"]
    assert sa.SAFETY_CONTRACT["creates_orders"] is False
    assert sa.SAFETY_CONTRACT["broker_execution"] is False
    assert sa.SAFETY_CONTRACT["automation_of_trading"] is False
    assert sa.SAFETY_CONTRACT["promotion_requires_human_approval"] is True


# =========================================================================== #
# 8-13  inventory
# =========================================================================== #
def test_08_missing_required_files_fail_clearly(env):
    bad = json.loads(json.dumps(env["cfg"]))
    bad["sources"]["simfin_income"] = {"root": "data_root", "relpath": "nope/us-income.csv"}
    inv = sa.inventory_local_files(bad)
    assert "simfin_income" in inv["missing_required_files"]


def test_09_delimiter_detection_deterministic(env):
    path = hc._resolve_path_spec(env["cfg"]["sources"]["simfin_income"])
    assert sa._sniff_delimiter(path) == ";"
    assert sa._sniff_delimiter(path) == ";"


def test_10_file_hashes_deterministic(env):
    inv1 = sa.inventory_local_files(env["cfg"])
    inv2 = sa.inventory_local_files(env["cfg"])
    h1 = inv1["files"]["simfin_income"]["content_hash"]
    h2 = inv2["files"]["simfin_income"]["content_hash"]
    assert h1 == h2 and len(h1) == 64


def test_11_zip_manifest_inspection_read_only(env):
    zp = os.path.join(env["root"], "research", "data", "simfin", "download", "us-companies.zip")
    before = os.path.getsize(zp)
    inv = sa.inventory_local_files(env["cfg"])
    assert inv["zip_archives"] and inv["zip_archives"][0]["entries"]
    assert os.path.getsize(zp) == before   # untouched


def test_12_extracted_vs_archive_timestamps_reported(env):
    inv = sa.inventory_local_files(env["cfg"])
    assert inv["extract_vs_archive"]
    row = inv["extract_vs_archive"][0]
    assert "archive_csv_size" in row and "extracted_csv_size" in row


def test_13_duplicate_statement_keys_detected(env, tmp_path):
    p = os.path.join(env["root"], "dup.csv")
    with open(p, "w", encoding="utf-8", newline="") as fh:
        w = csv.writer(fh, delimiter=";")
        w.writerow(_INCOME_HDR)
        w.writerow(["AA", "SF101", "USD", "2022", "Q4", "2022-12-31", "2023-05-01", "", "1", "-1", "1", "1"])
        w.writerow(["AA", "SF101", "USD", "2022", "Q4", "2022-12-31", "2023-05-01", "", "1", "-1", "1", "1"])
    meta = sa._inventory_one(p, is_company=False, delimiter=";")
    assert meta["duplicate_key_count"] == 1


# =========================================================================== #
# 14-20  date semantics / PIT
# =========================================================================== #
def test_14_publish_date_defines_availability():
    assert sa._availability("2022-05-01", "") == ("2022-05-01", "original_as_reported")


def test_15_restated_date_cannot_replace_original(env):
    # restated <= publish -> keep publish, still original
    assert sa._availability("2022-05-01", "2022-01-01") == ("2022-05-01", "original_as_reported")


def test_16_restated_observations_use_later_date():
    assert sa._availability("2019-05-01", "2022-05-01") == ("2022-05-01", "restated")


def test_17_fiscal_period_end_not_used_as_publish(env):
    _master, mapping = _mapping(env)
    norm = sa.normalize_simfin_fundamentals(env["cfg"], mapping)
    for r in norm["rows"]:
        assert r["available_date"] != r["fiscal_period_end"]
        assert r["available_date"] >= r["fiscal_period_end"]


def test_18_future_filings_cannot_enter_prior_months():
    rows = [{"factor": "fcf_to_assets", "ticker": "AA", "available_date": "2022-05-01", "value": 0.1}]
    series, _ = hc.build_repaired_factor_series(rows, MONTHS, max_staleness_months=15)
    s = series.get("fcf_to_assets", {})
    assert all(m >= "2022-05" for m in s)   # nothing in 2019-2021


def test_19_max_staleness_enforced():
    rows = [{"factor": "fcf_to_assets", "ticker": "AA", "available_date": "2019-05-01", "value": 0.1}]
    series, _ = hc.build_repaired_factor_series(rows, MONTHS, max_staleness_months=3)
    s = series.get("fcf_to_assets", {})
    assert not any(m > "2019-09" for m in s)   # stale drop


def test_20_truncation_invariance():
    rows = [{"factor": "fcf_to_assets", "ticker": "AA", "available_date": "2020-05-01", "value": 0.1}]
    full, _ = hc.build_repaired_factor_series(rows, MONTHS, max_staleness_months=99)
    trunc, _ = hc.build_repaired_factor_series(rows, MONTHS[:30], max_staleness_months=99)
    for m in MONTHS[:30]:
        assert full.get("fcf_to_assets", {}).get(m) == trunc.get("fcf_to_assets", {}).get(m)


def test_21_date_semantics_classified(env):
    inv = sa.inventory_local_files(env["cfg"])
    ds = sa.classify_date_semantics(inv)
    assert ds["per_file"]["simfin_income"]["classification"] == "PIT_SAFE_WITH_RESTATEMENT_CAVEAT"


# =========================================================================== #
# 22-28  identity / mapping
# =========================================================================== #
def test_22_cik_mapping_has_priority(env):
    _master, mapping = _mapping(env)
    row = next(r for r in mapping["rows"] if r["ticker"] == "AA")
    assert row["simfin_id"] == "SF101" and row["map_source"] == "cik"


def test_23_simfin_id_mapping_stable(env):
    _m, m1 = _mapping(env)
    _m2, m2 = _mapping(env)
    assert m1["sid_to_ticker"] == m2["sid_to_ticker"]


def test_24_removed_cannot_map_by_base_ticker_alone(env):
    _master, mapping = _mapping(env)
    row = next(r for r in mapping["rows"] if r["ticker"] == RE)
    # RE removed base ticker only matches a CURRENT SEC ticker -> never mapped
    assert row["simfin_id"] is None


def test_25_reused_tickers_remain_distinct(env):
    _master, mapping = _mapping(env)
    # no current company's SimFinId is inherited by the removed RE
    removed = next(r for r in mapping["rows"] if r["ticker"] == RE)
    assert removed["simfin_id"] is None


def test_26_ambiguous_names_remain_unresolved(env):
    _master, mapping = _mapping(env)
    # EE's CIK maps to two SimFinIds -> ambiguous, left unresolved
    ee = next(r for r in mapping["rows"] if r["ticker"] == "EE")
    assert ee["simfin_id"] is None and ee["ambiguity"] is not None
    assert mapping["ambiguous_rate"] > 0.0


def test_27_future_survival_cannot_affect_mapping(env):
    # GG is delisted (did not survive) yet still maps by CIK -> survival irrelevant
    _master, mapping = _mapping(env)
    gg = next(r for r in mapping["rows"] if r["ticker"] == GG)
    assert gg["simfin_id"] == "SF120" and gg["map_source"] == "cik"


def test_28_mapping_output_deterministic(env):
    _m, m1 = _mapping(env)
    _m2, m2 = _mapping(env)
    assert [r["simfin_id"] for r in m1["rows"]] == [r["simfin_id"] for r in m2["rows"]]


# =========================================================================== #
# 29-35  factor reconstruction
# =========================================================================== #
def _norm(env):
    _master, mapping = _mapping(env)
    return sa.normalize_simfin_fundamentals(env["cfg"], mapping)


def test_29_gross_profitability_formula_reused(env):
    norm = _norm(env)
    r = next(x for x in norm["rows"] if x["factor"] == "gross_profitability" and x["ticker"] == "AA")
    assert abs(r["value"] - (400.0 / 5000.0)) < 1e-9


def test_30_fcf_to_assets_formula_reused(env):
    norm = _norm(env)
    r = next(x for x in norm["rows"] if x["factor"] == "fcf_to_assets" and x["ticker"] == "BB")
    # (ocf - capex)/assets, capex = -(-40) = 40 -> (300 - 40)/5000
    assert abs(r["value"] - ((300.0 - 40.0) / 5000.0)) < 1e-9


def test_31_operating_accruals_formula_reused(env):
    norm = _norm(env)
    r = next(x for x in norm["rows"] if x["factor"] == "operating_accruals" and x["ticker"] == "CC")
    assert abs(r["value"] - ((120.0 - 300.0) / 5000.0)) < 1e-9


def test_32_standard_and_bank_schemas_distinguished(env):
    norm = _norm(env)
    assert "SF140" in set(norm["bank_sids"])
    assert all(r["simfin_id"] != "SF140" for r in norm["rows"])   # bank excluded
    assert norm["audit"]["bank_handling"]["decision"] == "EXCLUDED"


def test_33_missing_components_produce_missing_not_zero(env):
    # HH (SF130) is mapped but has NO statements -> no factor rows (not zero rows)
    norm = _norm(env)
    assert all(r["ticker"] != HH for r in norm["rows"])


def test_34_source_fields_and_hashes_persisted(env):
    norm = _norm(env)
    assert norm["audit"]["simfin_field_map"]["assets"] == "Total Assets"
    assert "definitions_reused" in norm["audit"]
    r = norm["rows"][0]
    assert r["provider"].startswith("SimFin") and r["simfin_id"]


def test_35_original_and_restated_not_mixed(env):
    norm = _norm(env)
    # SF101 FY2019 income is a later restatement -> that gp obs is 'restated'
    aa = [r for r in norm["rows"] if r["ticker"] == "AA" and r["factor"] == "gross_profitability"]
    classes = {r["restatement"] for r in aa}
    assert "restated" in classes and "original_as_reported" in classes


# =========================================================================== #
# 36-44  coverage / union / decision gates
# =========================================================================== #
def test_36_global_denominator_fixed(env):
    inputs = make_inputs()
    master = hc.build_security_master(env["cfg"])
    b1 = sa._coverage_breakdowns({"fcf_to_assets": {}}, inputs, master)
    rows = [{"factor": "fcf_to_assets", "ticker": "AA", "available_date": "2019-01-01", "value": 0.1}]
    s = hc.build_repaired_factor_series(rows, inputs["months"], max_staleness_months=99)[0]
    b2 = sa._coverage_breakdowns(s, inputs, master)
    assert b1["fcf_to_assets"]["member_months_total"] == b2["fcf_to_assets"]["member_months_total"]


def test_37_current_and_removed_coverage_reconcile(env):
    inputs = make_inputs()
    master = hc.build_security_master(env["cfg"])
    rows = [{"factor": "fcf_to_assets", "ticker": t, "available_date": "2019-01-01", "value": 0.1}
            for t in ("AA", GG)]
    s = hc.build_repaired_factor_series(rows, inputs["months"], max_staleness_months=99)[0]
    b = sa._coverage_breakdowns(s, inputs, master)["fcf_to_assets"]
    assert b["current_member_month_coverage"] > 0 and b["removed_member_month_coverage"] > 0


def test_38_coverage_by_decade_reconciles(env):
    inputs = make_inputs()
    master = hc.build_security_master(env["cfg"])
    rows = [{"factor": "fcf_to_assets", "ticker": "AA", "available_date": "2019-01-01", "value": 0.1}]
    s = hc.build_repaired_factor_series(rows, inputs["months"], max_staleness_months=99)[0]
    b = sa._coverage_breakdowns(s, inputs, master)["fcf_to_assets"]
    dsum = sum(v["member_months"] for v in b["by_decade"].values())
    assert dsum == b["member_months_total"]


def test_39_sec_only_and_simfin_only_distinct():
    sec = {"f": {"2020-01": {"AA": 1.0}}}
    sf = {"f": {"2020-02": {"BB": 2.0}}}
    u = sa.build_union(sec, sf)
    assert u["provenance"]["sec_only"] == 1 and u["provenance"]["simfin_only"] == 1


def test_40_union_does_not_double_count_overlap():
    sec = {"f": {"2020-01": {"AA": 1.0}}}
    sf = {"f": {"2020-01": {"AA": 1.0}}}   # same value -> overlap, no conflict
    u = sa.build_union(sec, sf)
    assert u["provenance"]["overlap"] == 1
    assert u["provenance"]["sec_only"] == 0 and u["provenance"]["simfin_only"] == 0
    assert u["series"]["f"]["2020-01"] == {"AA": 1.0}   # counted once


def test_41_conflicts_reported_and_sec_kept():
    sec = {"f": {"2020-01": {"AA": 1.0}}}
    sf = {"f": {"2020-01": {"AA": 2.0}}}   # differ > 5%
    u = sa.build_union(sec, sf)
    assert u["provenance"]["conflicts"] == 1
    assert u["series"]["f"]["2020-01"]["AA"] == 1.0   # SEC as-filed kept


def test_42_positive_ic_cannot_bypass_coverage():
    diag = {"global_pit_universe": {"diagnostics": {
        "cross_sectional_coverage": 0.10, "month_coverage": 0.95, "rank_ic_t": 9.9}},
        "advance_to_portfolio_screen": True}
    surv = {"survivorship_classification": "PASS", "delisted_representation_fraction": 0.9}
    d = hc._decide_30c(diag, surv, {"coverage_gates": {"global_min_cross_sectional_coverage": 0.60,
                                                       "global_min_month_coverage": 0.60}}, 0.0, False)
    assert d == "REJECTED_COVERAGE"


def test_43_positive_ic_cannot_bypass_pit():
    # a value only available in the future must never appear in an earlier month
    rows = [{"factor": "f", "ticker": "AA", "available_date": "2022-12-01", "value": 5.0}]
    s = hc.build_repaired_factor_series(rows, MONTHS, max_staleness_months=999)[0]
    assert all(m >= "2022-12" for m in s.get("f", {}))


def test_44_positive_ic_cannot_bypass_survivorship():
    diag = {"global_pit_universe": {"diagnostics": {
        "cross_sectional_coverage": 0.9, "month_coverage": 0.9, "rank_ic_t": 9.9}},
        "advance_to_portfolio_screen": True}
    surv = {"survivorship_classification": "DIAGNOSTIC_ONLY_CURRENT_SURVIVOR_BIAS",
            "delisted_representation_fraction": 0.01}
    d = hc._decide_30c(diag, surv, {"coverage_gates": {"global_min_cross_sectional_coverage": 0.60,
                                                       "global_min_month_coverage": 0.60,
                                                       "min_delisted_representation_fraction": 0.20}}, 0.0, False)
    assert d == "REJECTED_SURVIVORSHIP"


# =========================================================================== #
# 45-52  CLI, run, artifacts, immutability
# =========================================================================== #
def test_45_validate_returns_exit_codes(env, tmp_path, capsys):
    cfgp = os.path.join(env["root"], "cfg.json")
    with open(cfgp, "w", encoding="utf-8") as fh:
        json.dump(env["cfg"], fh)
    assert cli.main(["simfin-adequacy-validate", "--config", cfgp, "--json"]) == cli.EXIT_OK
    bad = json.loads(json.dumps(env["cfg"])); bad["coverage_gates"]["global_min_cross_sectional_coverage"] = 0.1
    badp = os.path.join(env["root"], "bad.json")
    with open(badp, "w", encoding="utf-8") as fh:
        json.dump(bad, fh)
    assert cli.main(["simfin-adequacy-validate", "--config", badp, "--json"]) == cli.EXIT_INVALID


def test_46_reconstruct_sec_rows_from_cache(env, monkeypatch):
    master = hc.build_security_master(env["cfg"])
    recon = sa.reconstruct_sec_rows(env["cfg"], master)
    assert recon["n_ciks_reconstructed"] >= 1 and recon["n_observations"] > 0


@pytest.fixture
def ran(env, monkeypatch):
    monkeypatch.setattr(fb, "load_family_inputs", lambda **kw: make_inputs())
    res = sa.run_adequacy(env["cfg"], output_root=env["out"])
    return env, res


def test_47_run_completes_and_is_deterministic(env, monkeypatch):
    monkeypatch.setattr(fb, "load_family_inputs", lambda **kw: make_inputs())
    r1 = sa.run_adequacy(env["cfg"], output_root=env["out"])
    r2 = sa.run_adequacy(env["cfg"], output_root=env["out"])
    assert r1["status"] == "COMPLETE"
    assert r1["run_id"] == r2["run_id"]
    assert r1["simfin_only_global_coverage"] == r2["simfin_only_global_coverage"]
    assert r1["simfin_decision"] in sa.SIMFIN_DECISIONS


def test_48_status_reconciles_artifacts(ran):
    env, res = ran
    st = sa.generate_status(res["run_id"], env["out"])
    assert st["final_state"] == "COMPLETE"
    assert st["simfin_decision"] == res["simfin_decision"]


def test_49_report_is_idempotent(ran):
    env, res = ran
    a = sa.generate_report(res["run_id"], env["out"])["report"]
    b = sa.generate_report(res["run_id"], env["out"])["report"]
    assert a == b
    assert a["simfin_decision"] in sa.SIMFIN_DECISIONS


def test_50_latest_pointer_atomic_and_complete(ran):
    env, res = ran
    ptr = json.load(open(os.path.join(env["out"], sa.LATEST_RUN_FILE)))
    for k in ("run_id", "code_commit", "config_hash", "data_cutoff", "final_state",
              "simfin_decision", "next_data_action", "simfin_only_global_coverage",
              "sec_only_global_coverage", "union_global_coverage",
              "simfin_removed_names_mapped", "simfin_removed_names_with_statements",
              "earliest_usable_date", "latest_usable_date", "best_feature",
              "best_feature_decision", "generated_at"):
        assert k in ptr


def test_51_completed_phase30c_evidence_not_modified(ran):
    env, res = ran
    # the run must never write into the Phase 30C run store or its sample
    sm = os.path.join(env["root"], "hcruns", "hcov_test", "sample_manifest.json")
    data = json.load(open(sm))
    assert data == {"seed": 30, "sample_hash": "abc",
                    "removed": [{"ticker": GG}, {"ticker": HH}, {"ticker": RE}],
                    "current": [{"ticker": "AA"}, {"ticker": "BB"}]}   # untouched
    assert "run_id" not in data   # the read-time _run_id is never persisted back
    # everything the run wrote lives under simfin_adequacy_runs or the 30C1 pointer
    assert os.path.isdir(os.path.join(env["out"], sa.RUNS_SUBDIR, res["run_id"]))


def test_52_run_safety_contract_persisted(ran):
    env, res = ran
    run_doc = json.load(open(os.path.join(env["out"], sa.RUNS_SUBDIR, res["run_id"], "run.json")))
    s = run_doc["safety"]
    assert s["creates_orders"] is False and s["research_only"] is True
    assert run_doc["advanced_to_portfolio_screen"] == [] and run_doc["n_portfolio_candidates"] == 0
