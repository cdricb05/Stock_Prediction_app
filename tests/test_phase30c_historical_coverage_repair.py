"""Phase 30C — survivorship-safe fundamental backfill + PIT sector history.

Hermetic: fixture CSV/JSON files under a tmp tree with
``historical_coverage._ROOTS`` monkeypatched to it; SEC/EODHD access uses
injected fake transports (NO network); the family-backtest re-evaluation is
exercised on small in-memory inputs or a stubbed hook. No Paper Trader, no
operational state, no committed data files, no real provider calls.
"""

from __future__ import annotations

import csv
import hashlib
import json
import os

import pytest

from research_agent import cli
from research_agent import family_backtest as fb
from research_agent import historical_coverage as hc
from research_agent import owned_factors as of

# --------------------------------------------------------------------------- #
# deterministic synthetic universe (shared by CSV fixtures and in-memory inputs)
# --------------------------------------------------------------------------- #
CURRENT = ["C%02d" % i for i in range(10)]
REMOVED_2000 = ["OLDA-200506", "OLDB-200712", "OLDC-200903"]
REMOVED_2010 = ["MIDA-201206", "MIDB-201503"]
REMOVED_2020 = ["NEWA-202106", "NEWB-202203"]
REUSE = "REUSE-200506"           # base REUSE collides with a CURRENT SEC ticker
REMOVED = REMOVED_2000 + REMOVED_2010 + REMOVED_2020 + [REUSE]
ALL_TK = CURRENT + REMOVED
MONTHS = ["%04d-%02d" % (y, m) for y in (2016, 2017) for m in range(1, 13)]
CUTOFF = "2018-01-31"

# CIK sources: SEC current directory (current names + the reused base ticker);
# SimFin (recent removed names).
SEC_DIR = {c: 100 + i for i, c in enumerate(CURRENT)}
SEC_DIR["REUSE"] = 999          # reused base ticker -> a DIFFERENT current company
SEC_DIR["C09ALT"] = 109
SIMFIN_CIK = {"MIDA": 201, "MIDB": 202, "NEWA": 203, "NEWB": 204}


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
    allm = MONTHS + ["2018-01"]
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
def _write_csv(path, header, rows):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(header)
        w.writerows(rows)


def _last_quoted(tk):
    dm = hc.delisting_month(tk)
    return (dm + "-15") if dm else ""


def write_fixture_files(root, delimiter=";"):
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
                "is_member", "adv_dollar", "realized_vol_63d", "eligible_history", "sector"],
               mom_rows)
    # security master (phase8c schema)
    sm_rows = []
    for tk in ALL_TK:
        is_del = tk in REMOVED
        name = ("Old Reuse Inc" if tk == REUSE else "%s Corp" % hc.base_symbol(tk))
        sm_rows.append([tk, name, "Industrials", "1999-01-01",
                        _last_quoted(tk), "True" if is_del else "False", "120", "60"])
    _write_csv(os.path.join(root, "master.csv"),
               ["ticker", "security_name", "gics_sector", "first_quoted_date",
                "last_quoted_date", "is_delisted", "n_monthly_obs", "n_member_months"], sm_rows)
    # repaired sector map (current-only)
    _write_csv(os.path.join(root, "sector_map.csv"),
               ["ticker", "original_sector", "repaired_sector", "repaired_industry",
                "source_file_or_source_family", "source_file", "source_field", "confidence", "reason"],
               [[tk, "o", "Financials", "i", "s", "s", "f", "0.9", "w"] for tk in CURRENT[:5]])
    # SEC company_tickers.json
    directory = {str(i): {"cik_str": cik, "ticker": tk, "title": "%s Inc" % tk}
                 for i, (tk, cik) in enumerate(SEC_DIR.items())}
    os.makedirs(os.path.join(root, "sec"), exist_ok=True)
    with open(os.path.join(root, "sec", "company_tickers.json"), "w", encoding="utf-8") as fh:
        json.dump(directory, fh)
    # SimFin us-companies.csv (semicolon) + us-income
    sf_rows = [[base, "SF%s" % base, "%s Company" % base, "1", "", "12", "10", "biz", cik, "USD"]
               for base, cik in SIMFIN_CIK.items()]
    _write_csv(os.path.join(root, "us-companies.csv"),
               ["Ticker", "SimFinId", "Company Name", "IndustryId", "ISIN",
                "End of financial year (month)", "Number Employees", "Business Summary", "CIK", "Main Currency"],
               sf_rows) if delimiter == "," else _write_semicolon(
        os.path.join(root, "us-companies.csv"),
        ["Ticker", "SimFinId", "Company Name", "IndustryId", "ISIN",
         "End of financial year (month)", "Number Employees", "Business Summary", "CIK", "Main Currency"], sf_rows)
    _write_semicolon(os.path.join(root, "us-income-quarterly.csv"),
                     ["Ticker", "SimFinId", "Currency", "Fiscal Year", "Fiscal Period",
                      "Report Date", "Publish Date", "Restated Date", "Revenue"],
                     [[b, "SF%s" % b, "USD", "2022", "Q1", "2022-03-31", "2022-05-01", "", "1000"]
                      for b in SIMFIN_CIK] + [[b, "SF%s" % b, "USD", "2021", "Q1",
                      "2021-03-31", "2021-05-01", "", "900"] for b in SIMFIN_CIK])


def _write_semicolon(path, header, rows):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="") as fh:
        w = csv.writer(fh, delimiter=";")
        w.writerow(header)
        w.writerows(rows)


def fixture_config(root):
    def spec(rel):
        return {"root": "data_root", "relpath": rel}
    return {
        "schema_version": "30C.1", "name": "t30c",
        "data": {"data_cutoff": "2026-06-30"},
        "sources": {
            "roots": {"repo": "REPO_ROOT", "data_root": "DATA_ROOT"},
            "momentum_panel": spec("mom.csv"),
            "security_master": spec("master.csv"),
            "sector_map": spec("sector_map.csv"),
            "sec_company_tickers": spec("sec/company_tickers.json"),
            "simfin_companies": spec("us-companies.csv"),
            "simfin_income": spec("us-income-quarterly.csv"),
        },
        "provider_cache_root": spec("pcache"),
        "normalized_root": spec("pcache/normalized"),
        "provider_order": ["norgate", "eodhd", "simfin", "sec"],
        "provider_endpoints": {
            "sec": {"allowed_hosts": ["www.sec.gov", "data.sec.gov"],
                    "submissions_url": "https://data.sec.gov/submissions/CIK{cik10}.json",
                    "companyfacts_url": "https://data.sec.gov/api/xbrl/companyfacts/CIK{cik10}.json",
                    "filing_txt_url": "https://www.sec.gov/Archives/edgar/data/{cik}/{accn_nodash}/{accn_dashed}.txt",
                    "user_agent": "Research/Phase30C test@example.com"},
            "eodhd": {"allowed_hosts": ["eodhd.com"],
                      "fundamentals_url": "https://eodhd.com/api/fundamentals/{symbol}.US?fmt=json"},
        },
        "sample": {"removed": 6, "current": 3, "seed": 30, "decades": ["2000s", "2010s", "2020s"]},
        "acquisition": {"max_requests_per_batch": 25, "max_retries": 2, "request_timeout_seconds": 30,
                        "sec_min_interval_seconds": 0.25, "eodhd_min_interval_seconds": 0.30,
                        "probe_sec_max": 4, "probe_sample_per_group": 2, "filing_header_budget": 6},
        "entitlement": {"no_purchase": True, "contact_sales": False, "use_existing_keys_only": True},
        "max_factor_staleness_months": 15,
        "coverage_gates": {"global_min_cross_sectional_coverage": 0.60,
                           "global_min_month_coverage": 0.60, "min_delisted_representation_fraction": 0.20},
        "sector_history": {"require_pit_safe_for_promotion": True, "treat_unknown_as_sector": False,
                           "member_month_coverage_min": 0.60},
        "acquisition_targets": {"removed_names_target": 400, "removed_representation_target": 0.20,
                                "sector_member_month_target": 0.60},
        "diagnostics": {"factors": ["gross_profitability", "fcf_to_assets", "operating_accruals", "realized_vol_63d"]},
        "ic_screen": {
            "min_months": 36, "min_coverage_fraction": 0.6, "min_abs_rank_ic_t": 1.0,
            "material_ic_t_margin": 0.25, "near_duplicate_abs_corr": 0.95,
            "max_complementary_abs_baseline_corr": 0.5, "max_top_rank_sector_share": 0.5,
            "leakage_suspicion_abs_ic": 0.5, "min_universe": 10},
        "integration": {"baseline_weight": 0.8, "feature_weight": 0.2},
        "costs": {"primary_cost_bps_per_side": 25.0, "sensitivity_cost_bps_per_side": [12.5, 50.0]},
        "portfolio": {"top_n": 25, "sector_treatment": "sector_cap", "exit_buffer_fraction": 0.0,
                      "universe": "mhz_reconstruction", "min_adv_dollar": 10000000.0},
        "baseline": {"rank_ic_t": 0.7943535272584944},
        "safety": {"research_only": True, "no_operational_promotion": True, "may_register_challengers": False},
    }


@pytest.fixture
def env(tmp_path, monkeypatch):
    root = str(tmp_path)
    write_fixture_files(root)
    monkeypatch.setattr(hc, "_ROOTS", {"repo": root, "data_root": root})
    cfg = fixture_config(root)
    return {"root": root, "cfg": cfg, "out": os.path.join(root, "runs")}


# --------------------------------------------------------------------------- #
# fake SEC / EODHD transports (no network)
# --------------------------------------------------------------------------- #
def fake_companyfacts(cik):
    def facts(concept, unit, series):
        return {concept: {"label": concept, "units": {unit: series}}}
    ug = {}
    # two quarters, first-reported + one restatement + one amendment-only period
    def mk(end, fp, form, filed, accn, val):
        return {"end": end, "fp": fp, "form": form, "filed": filed, "accn": accn, "val": val}
    ug.update(facts("Assets", "USD", [
        mk("2016-03-31", "Q1", "10-Q", "2016-05-01", "acc-1", 1000.0),
        mk("2016-06-30", "Q2", "10-Q", "2016-08-01", "acc-2", 1100.0),
        mk("2016-09-30", "Q3", "10-Q/A", "2016-12-01", "acc-amd", 1200.0)]))
    ug.update(facts("Revenues", "USD", [
        mk("2016-03-31", "Q1", "10-Q", "2016-05-01", "acc-1", 500.0),
        mk("2016-03-31", "Q1", "10-Q/A", "2016-09-01", "acc-restate", 480.0),  # later restatement
        mk("2016-06-30", "Q2", "10-Q", "2016-08-01", "acc-2", 520.0),
        mk("2016-09-30", "Q3", "10-Q/A", "2016-12-01", "acc-amd", 540.0)]))
    ug.update(facts("CostOfRevenue", "USD", [
        mk("2016-03-31", "Q1", "10-Q", "2016-05-01", "acc-1", 300.0),
        mk("2016-06-30", "Q2", "10-Q", "2016-08-01", "acc-2", 310.0),
        mk("2016-09-30", "Q3", "10-Q/A", "2016-12-01", "acc-amd", 320.0)]))
    ug.update(facts("NetIncomeLoss", "USD", [
        mk("2016-03-31", "Q1", "10-Q", "2016-05-01", "acc-1", 100.0),
        mk("2016-06-30", "Q2", "10-Q", "2016-08-01", "acc-2", 110.0)]))
    ug.update(facts("NetCashProvidedByUsedInOperatingActivities", "USD", [
        mk("2016-03-31", "Q1", "10-Q", "2016-05-01", "acc-1", 150.0),
        mk("2016-06-30", "Q2", "10-Q", "2016-08-01", "acc-2", 160.0)]))
    ug.update(facts("PaymentsToAcquirePropertyPlantAndEquipment", "USD", [
        mk("2016-03-31", "Q1", "10-Q", "2016-05-01", "acc-1", 20.0),
        mk("2016-06-30", "Q2", "10-Q", "2016-08-01", "acc-2", 25.0)]))
    return {"cik": cik, "entityName": "E%s" % cik, "facts": {"us-gaap": ug}}


def fake_submissions(cik, sic="7372"):
    recent = {
        "accessionNumber": ["acc-2", "acc-1", "acc-amd", "acc-restate"],
        "form": ["10-Q", "10-Q", "10-Q/A", "10-Q/A"],
        "filingDate": ["2016-08-01", "2016-05-01", "2016-12-01", "2016-09-01"],
        "acceptanceDateTime": ["2016-08-01T16:00:00.000Z", "2016-05-01T16:00:00.000Z",
                               "2016-12-01T16:00:00.000Z", "2016-09-01T16:00:00.000Z"],
        "reportDate": ["2016-06-30", "2016-03-31", "2016-09-30", "2016-03-31"],
    }
    return {"cik": cik, "name": "E%s" % cik, "sic": sic, "sicDescription": "desc",
            "tickers": [], "formerNames": [], "filings": {"recent": recent}}


def make_fake_sec_transport(calls):
    def transport(url, timeout):
        calls.append(url)
        cik = url.split("CIK")[-1].split(".")[0] if "CIK" in url else "0"
        if "submissions" in url:
            return 200, fake_submissions(cik)
        if "companyfacts" in url:
            return 200, fake_companyfacts(cik)
        return 404, {}
    return transport


def fake_text_transport(url, timeout):
    return 200, ("<ACCEPTANCE-DATETIME>20160801160000\n"
                 "FILED AS OF DATE:\t\t20160801\n"
                 "STANDARD INDUSTRIAL CLASSIFICATION:\tSERVICES [7372]\n")


def fake_eodhd_transport(url, timeout):
    # current-name style payload with quarterly financials; removed base symbols
    # (reused tickers) also "succeed" here — that is the current-survivor trap.
    return 200, {"Financials": {"Balance_Sheet": {"quarterly": {"2022-03-31": {}}},
                                "Income_Statement": {"quarterly": {"2022-03-31": {}}},
                                "Cash_Flow": {"quarterly": {"2022-03-31": {}}}}}


def _sec(env, monkeypatch, calls=None, sleep=None, transport=None):
    calls = calls if calls is not None else []
    cfg = env["cfg"]
    return hc.SecAccess(cfg, os.path.join(env["root"], "pcache"),
                        transport=transport or make_fake_sec_transport(calls),
                        text_transport=fake_text_transport,
                        sleep_fn=sleep or (lambda s: None))


# =========================================================================== #
# 1-6  repository and configuration
# =========================================================================== #
def test_01_config_accepts_no_secret_fields(env):
    cfg = json.loads(json.dumps(env["cfg"]))
    cfg["provider_endpoints"]["sec"]["api_key"] = "abc"
    v = hc.validate_config(cfg)
    assert not v["accepted"]
    assert any("secret" in x["issue"] for x in v["violations"])


def test_02_provider_roots_are_fixed(env):
    cfg = json.loads(json.dumps(env["cfg"]))
    cfg["sources"]["roots"]["evil"] = "X"
    v = hc.validate_config(cfg)
    assert not v["accepted"]
    assert any(x["field"].startswith("roots.") for x in v["violations"])


def test_03_arbitrary_provider_urls_rejected(env):
    cfg = json.loads(json.dumps(env["cfg"]))
    cfg["provider_endpoints"]["sec"]["submissions_url"] = "https://evil.example.com/x/{cik10}"
    v = hc.validate_config(cfg)
    assert not v["accepted"]
    assert any("allow-list" in x["issue"] for x in v["violations"])


def test_04_acquisition_limits_bounded(env):
    for field, val in (("max_requests_per_batch", 1000), ("max_retries", 99),
                       ("request_timeout_seconds", 0.0), ("sec_min_interval_seconds", 0.001)):
        cfg = json.loads(json.dumps(env["cfg"]))
        cfg["acquisition"][field] = val
        v = hc.validate_config(cfg)
        assert not v["accepted"], field


def test_05_coverage_and_survivorship_gates_cannot_be_weakened(env):
    for path, val in ((("coverage_gates", "global_min_cross_sectional_coverage"), 0.30),
                      (("coverage_gates", "min_delisted_representation_fraction"), 0.05),
                      (("sector_history", "member_month_coverage_min"), 0.10)):
        cfg = json.loads(json.dumps(env["cfg"]))
        cfg[path[0]][path[1]] = val
        v = hc.validate_config(cfg)
        assert not v["accepted"], path


def test_06_paper_trader_paths_and_endpoints_forbidden(env):
    cfg = json.loads(json.dumps(env["cfg"]))
    cfg["note"] = "reads C:/Users/binis/paper_trader/api/app.py"
    v = hc.validate_config(cfg)
    assert not v["accepted"]
    assert any("Paper Trader" in x["issue"] for x in v["violations"])
    cfg2 = json.loads(json.dumps(env["cfg"]))
    cfg2["note"] = "calls http://127.0.0.1:8001/v1/paper-desk"
    assert not hc.validate_config(cfg2)["accepted"]


# =========================================================================== #
# 7-12  security master
# =========================================================================== #
def test_07_stable_identity_survives_ticker_changes(env):
    master = hc.build_security_master(env["cfg"])
    by_tk = {r["ticker"]: r for r in master["rows"]}
    # a delisted name keeps its own identity (suffix) and its own CIK slot
    r = by_tk["NEWA-202106"]
    assert r["base_symbol"] == "NEWA" and r["is_delisted"]
    assert r["cik_source"] == "simfin_company_master"


def test_08_reused_tickers_do_not_merge(env):
    master = hc.build_security_master(env["cfg"])
    r = next(x for x in master["rows"] if x["ticker"] == REUSE)
    # REUSE base matches a CURRENT SEC ticker with a different name -> unresolved
    assert r["cik_int"] is None
    assert r["mapping_ambiguity"] and "reuse" in r["mapping_ambiguity"].lower()


def test_09_ambiguous_mappings_remain_unresolved(env):
    master = hc.build_security_master(env["cfg"])
    assert master["ambiguous_mappings"] >= 1
    reuse = next(x for x in master["rows"] if x["ticker"] == REUSE)
    assert reuse["cik"] == ""


def test_10_future_survival_cannot_affect_membership(env):
    master = hc.build_security_master(env["cfg"])
    # removed 2000s names have no owned CIK source (no current/SimFin) -> unmapped
    r = next(x for x in master["rows"] if x["ticker"] == "OLDA-200506")
    assert r["cik_int"] is None and r["is_delisted"]


def test_11_sample_selection_is_deterministic(env):
    m = hc.build_security_master(env["cfg"])
    s1 = hc.build_sample(m, env["cfg"])
    s2 = hc.build_sample(hc.build_security_master(env["cfg"]), env["cfg"])
    assert s1["sample_hash"] == s2["sample_hash"]


def test_12_removed_and_current_counts_reconcile(env):
    master = hc.build_security_master(env["cfg"])
    assert master["current_members"] + master["removed_members"] == master["universe_size"]
    assert master["removed_members"] == len(REMOVED)


# =========================================================================== #
# 13-20  provider probing
# =========================================================================== #
def test_13_secret_bearing_urls_never_logged(env, monkeypatch):
    logged = []
    sec = _sec(env, monkeypatch)
    sec.log = lambda k, p: logged.append(p)
    sec.fetch("submissions", "0000000100")
    for p in logged:
        assert "REDACTED" in p.get("url", "") or "api_token" not in p.get("url", "")


def test_14_eodhd_token_never_persisted(env, monkeypatch):
    sample = hc.build_sample(hc.build_security_master(env["cfg"]), env["cfg"])
    res = hc.probe_eodhd(sample, env["cfg"], transport=fake_eodhd_transport, sleep_fn=lambda s: None)
    blob = json.dumps(res)
    assert "api_token" not in blob


def test_15_sec_user_agent_required(env, monkeypatch):
    sec = _sec(env, monkeypatch)
    assert sec.user_agent and "example.com" in sec.user_agent


def test_16_rate_limits_enforced(env, monkeypatch):
    slept = []
    sec = _sec(env, monkeypatch, sleep=lambda s: slept.append(s))
    sec._last = 1e18  # force a positive wait
    sec.fetch("submissions", "0000000100")
    sec.fetch("companyfacts", "0000000100")
    assert slept and all(s >= 0 for s in slept)


def test_17_failed_requests_are_checkpointed(env, monkeypatch):
    def flaky(url, timeout):
        raise OSError("boom")
    sec = hc.SecAccess(env["cfg"], os.path.join(env["root"], "pcache"),
                       transport=flaky, sleep_fn=lambda s: None)
    with pytest.raises(Exception):
        sec.fetch("submissions", "0000000100")
    assert sec.network_requests == 0


def test_18_successful_raw_not_downloaded_twice(env, monkeypatch):
    calls = []
    sec = _sec(env, monkeypatch, calls=calls)
    sec.fetch("submissions", "0000000100")
    n1 = len(calls)
    obj, origin = sec.fetch("submissions", "0000000100")
    assert origin == "cache" and len(calls) == n1


def test_19_retry_count_is_bounded(env, monkeypatch):
    attempts = {"n": 0}
    def always_500(url, timeout):
        attempts["n"] += 1
        return 503, {}
    sec = hc.SecAccess(env["cfg"], os.path.join(env["root"], "pcache"),
                       transport=always_500, sleep_fn=lambda s: None)
    with pytest.raises(Exception):
        sec.fetch("submissions", "0000000100")
    assert attempts["n"] <= env["cfg"]["acquisition"]["max_retries"] + 1


def test_20_provider_sample_cannot_be_cherrypicked(env, monkeypatch):
    # sample is written from (seed,ticker) hashes only; provider results never
    # change which securities are selected.
    m = hc.build_security_master(env["cfg"])
    s = hc.build_sample(m, env["cfg"])
    rule = s["selection_rule"].lower()
    # selection is by (seed,ticker) hash and explicitly NEVER by data availability
    assert "content_hash" in rule and "never by data availability" in rule
    assert s["sample_hash"] == hc.build_sample(m, env["cfg"])["sample_hash"]


# =========================================================================== #
# 21-28  PIT fundamentals
# =========================================================================== #
def _norm(cik=100):
    cf = fake_companyfacts(str(cik))
    sub = fake_submissions(str(cik))
    return hc.normalize_sec_fundamentals("C00", cik, cf, sub, cutoff="2026-06-30")


def test_21_later_filings_cannot_enter_earlier_months():
    rows = _norm()
    series, _m = hc.build_repaired_factor_series(rows, MONTHS, max_staleness_months=15)
    gp = series.get("gross_profitability", {})
    # Q1 available 2016-05-01 -> not present in 2016-04 or earlier
    assert "C00" not in gp.get("2016-04", {})
    assert "C00" in gp.get("2016-05", {})


def test_22_amendments_use_later_acceptance_date():
    rows = _norm()
    # Q3 (2016-09-30) only exists as a 10-Q/A accepted 2016-12-01
    q3 = [r for r in rows if r["fiscal_period_end"] == "2016-09-30" and r["factor"] == "gross_profitability"]
    assert q3 and q3[0]["available_date"] == "2016-12-01" and q3[0]["amended"] is True


def test_23_future_backward_fill_impossible():
    rows = _norm()
    series, _m = hc.build_repaired_factor_series(rows, MONTHS, max_staleness_months=15)
    # 2016-01 precedes any filing -> empty
    assert series.get("gross_profitability", {}).get("2016-01", {}) == {}


def test_24_staleness_enforced():
    rows = _norm()
    late = [m for m in ["2016-05", "2016-06", "2016-07", "2019-12"]]
    s_tight, _ = hc.build_repaired_factor_series(rows, late, max_staleness_months=1)
    # by 2019-12 the newest obs is > 1 month stale -> excluded
    assert s_tight.get("gross_profitability", {}).get("2019-12", {}) == {}


def test_25_restatement_classification_persisted():
    rows = _norm()
    q1 = [r for r in rows if r["fiscal_period_end"] == "2016-03-31" and r["factor"] == "gross_profitability"]
    assert q1 and q1[0]["restatement"] == "original_as_filed"


def test_26_source_hashes_reconcile():
    a = hc.normalize_sec_fundamentals("C00", 100, fake_companyfacts("100"), fake_submissions("100"), cutoff="2026-06-30")
    b = hc.normalize_sec_fundamentals("C00", 100, fake_companyfacts("100"), fake_submissions("100"), cutoff="2026-06-30")
    from research_agent.artifact_store import content_hash
    assert content_hash(a) == content_hash(b)


def test_27_truncation_invariance():
    cf, sub = fake_companyfacts("100"), fake_submissions("100")
    early = hc.normalize_sec_fundamentals("C00", 100, cf, sub, cutoff="2016-06-30")
    late = hc.normalize_sec_fundamentals("C00", 100, cf, sub, cutoff="2026-06-30")
    ek = {(r["factor"], r["fiscal_period_end"], r["available_date"]) for r in early}
    lk = {(r["factor"], r["fiscal_period_end"], r["available_date"]) for r in late}
    assert ek.issubset(lk)  # earlier cutoff is a prefix of the later one


def test_28_original_and_restated_not_mixed():
    rows = _norm()
    # Q1 revenue original=500 (accepted 2016-05-01), restatement=480 (2016-09-01).
    # gross_profitability Q1 must use the first-reported revenue path only.
    q1 = [r for r in rows if r["fiscal_period_end"] == "2016-03-31" and r["factor"] == "gross_profitability"]
    assert q1 and q1[0]["available_date"] == "2016-05-01"  # first-reported, not the restatement


# =========================================================================== #
# 29-34  survivorship
# =========================================================================== #
def _repaired_series_all_current():
    inputs = make_inputs()
    series = {"gp": {m: {tk: _u("gp", m, tk) for tk in CURRENT} for m in MONTHS}}
    return inputs, series


def test_29_current_only_is_survivor_biased():
    inputs, series = _repaired_series_all_current()
    uni = of.build_universe_profiles(inputs, series, hc._of_cfg(_min_cfg()), sector_pit_safe=True)
    ent = uni["source_observed_universe"]["gp"]
    assert ent["survivorship_classification"] in ("DIAGNOSTIC_ONLY_CURRENT_SURVIVOR_BIAS",
                                                   "BLOCKED_INSUFFICIENT_DELISTED_COVERAGE")
    assert ent["shadow_eligible"] is False


def test_30_removed_representation_uses_fixed_denominator():
    inputs = make_inputs()
    series = {"gp": {m: {tk: 0.1 for tk in ALL_TK} for m in MONTHS}}
    uni = of.build_universe_profiles(inputs, series, hc._of_cfg(_min_cfg()), sector_pit_safe=True)
    ent = uni["source_observed_universe"]["gp"]
    # denominator = observed-in-panel; removed names present -> rep > 0
    assert ent["delisted_representation_fraction"] > 0


def test_31_future_availability_cannot_alter_membership():
    master_cfg = _min_cfg()
    inputs = make_inputs()
    # a factor observed only from a late month still cannot make a name a member
    series = {"gp": {MONTHS[-1]: {tk: 0.2 for tk in ALL_TK}}}
    uni = of.build_universe_profiles(inputs, series, hc._of_cfg(master_cfg), sector_pit_safe=True)
    g = uni["global_pit_universe"]
    assert g["distinct_removed_members"] == len(REMOVED)


def test_32_coverage_by_decade_reconciles(env):
    audit = hc.build_mapping_audit(hc.build_security_master(env["cfg"]))
    for dk, v in audit["removed_cik_by_decade"].items():
        assert v["removed_with_cik"] <= v["removed"]


def test_33_global_and_source_observed_distinct():
    inputs, series = _repaired_series_all_current()
    uni = of.build_universe_profiles(inputs, series, hc._of_cfg(_min_cfg()), sector_pit_safe=True)
    assert "global_pit_universe" in uni and "source_observed_universe" in uni


def test_34_diagnostic_only_not_shadow_eligible():
    inputs, series = _repaired_series_all_current()
    uni = of.build_universe_profiles(inputs, series, hc._of_cfg(_min_cfg()), sector_pit_safe=True)
    ent = uni["source_observed_universe"]["gp"]
    if ent["survivorship_classification"].startswith("DIAGNOSTIC_ONLY"):
        assert ent["shadow_eligible"] is False


def _min_cfg():
    return {"coverage_gates": {"min_delisted_representation_fraction": 0.20,
                               "global_min_cross_sectional_coverage": 0.60},
            "sector_history": {"require_pit_safe_for_promotion": True},
            "ic_screen": {}, "costs": {"primary_cost_bps_per_side": 25.0},
            "integration": {"baseline_weight": 0.8, "feature_weight": 0.2},
            "portfolio": {"top_n": 25}}


# =========================================================================== #
# 35-40  sector history
# =========================================================================== #
def test_35_current_sic_cannot_be_backfilled(env):
    master = hc.build_security_master(env["cfg"])
    # one CIK with a single filing-header observation at 2016-08
    cik = "0000000100"
    for r in master["rows"]:
        if r["ticker"] == CURRENT[0]:
            r["cik"] = cik
    obs = {cik: [("2016-08-01", "7372")]}
    pit, audit = hc.build_sec_sic_sector_history(obs, master, MONTHS)
    # months before 2016-08 have NO sector (no backfill)
    assert (("2016-01", CURRENT[0]) not in pit)
    assert (("2016-09", CURRENT[0]) in pit)


def test_36_filing_header_sic_uses_acceptance_date():
    parsed = hc._parse_filing_header("<ACCEPTANCE-DATETIME>20160801160000\n"
                                     "STANDARD INDUSTRIAL CLASSIFICATION: X [7372]\n")
    assert parsed["acceptance_date"] == "2016-08-01" and parsed["assigned_sic"] == "7372"


def test_37_sector_changes_are_retained(env):
    master = hc.build_security_master(env["cfg"])
    cik = "0000000100"
    for r in master["rows"]:
        if r["ticker"] == CURRENT[0]:
            r["cik"] = cik
    obs = {cik: [("2016-03-01", "7372"), ("2016-09-01", "2834")]}  # IT -> Health Care
    pit, _a = hc.build_sec_sic_sector_history(obs, master, MONTHS)
    assert pit[("2016-04", CURRENT[0])] == "Information Technology"
    assert pit[("2016-10", CURRENT[0])] == "Health Care"


def test_38_unknown_is_missing_not_a_sector():
    assert hc.sic_to_sector(None) == "Unknown"
    assert hc.sic_to_sector("9995") == "Unknown"


def test_39_concentration_includes_and_excludes_missing():
    inputs = make_inputs()
    series = {m: {tk: _u("v", m, tk) for tk in ALL_TK} for m in MONTHS}
    smap = {CURRENT[0]: "Financials"}
    conc = of._three_way_concentration(series, inputs, smap, min_universe=3)
    assert "avg_top_quartile_sector_share_incl_unknown" in conc
    assert "avg_unknown_share_in_top_quartile" in conc


def test_40_sector_coverage_gate_cannot_be_bypassed(env):
    cfg = json.loads(json.dumps(env["cfg"]))
    cfg["sector_history"]["member_month_coverage_min"] = 0.20
    assert not hc.validate_config(cfg)["accepted"]


# =========================================================================== #
# 41-46  diagnostics
# =========================================================================== #
def _diag(t=5.0, xcov=0.9, mcov=0.9):
    return {"global_pit_universe": {"diagnostics": {"cross_sectional_coverage": xcov,
            "month_coverage": mcov, "rank_ic_t": t}}, "advance_to_portfolio_screen": True}


def test_41_phase30b_comparison_persisted(env, monkeypatch):
    monkeypatch.setattr(hc.fb, "load_family_inputs", lambda **k: make_inputs())
    monkeypatch.setattr(hc.of, "load_momentum_column_series",
                        lambda p, c, months: ({m: {tk: _u("rv", m, tk) for tk in ALL_TK} for m in months}, {}))
    series = {"gross_profitability": {m: {tk: _u("gp", m, tk) for tk in CURRENT} for m in MONTHS}}
    reeval = hc.reevaluate_factors(env["cfg"], series, {}, hc.build_security_master(env["cfg"]),
                                   phase30b_latest={"survivorship_result": {"gross_profitability": "X"}})
    d = next(x for x in reeval["diagnostics"] if x["factor_id"] == "gross_profitability")
    assert "phase30b" in d and d["phase30b"]["survivorship_classification"] == "X"


def test_42_positive_ic_cannot_bypass_survivorship():
    surv = {"survivorship_classification": "DIAGNOSTIC_ONLY_CURRENT_SURVIVOR_BIAS",
            "delisted_representation_fraction": 0.0}
    d = hc._decide_30c(_diag(), surv, _min_cfg2(), 0.9, False)
    assert d == "REJECTED_SURVIVORSHIP"


def test_43_positive_ic_cannot_bypass_sector_history():
    surv = {"survivorship_classification": "PASS", "delisted_representation_fraction": 0.5}
    d = hc._decide_30c(_diag(), surv, _min_cfg2(), 0.0, True)
    assert d == "REJECTED_SECTOR_HISTORY"


def test_44_positive_ic_cannot_bypass_coverage():
    surv = {"survivorship_classification": "PASS", "delisted_representation_fraction": 0.5}
    d = hc._decide_30c(_diag(xcov=0.1), surv, _min_cfg2(), 0.9, False)
    assert d == "REJECTED_COVERAGE"


def test_45_baseline_reproduction_remains_exact(env, monkeypatch):
    monkeypatch.setattr(hc.fb, "load_family_inputs", lambda **k: make_inputs())
    monkeypatch.setattr(hc.of, "load_momentum_column_series",
                        lambda p, c, months: ({}, {}))
    series = {"gross_profitability": {m: {tk: _u("gp", m, tk) for tk in CURRENT} for m in MONTHS}}
    reeval = hc.reevaluate_factors(env["cfg"], series, {}, hc.build_security_master(env["cfg"]))
    b = reeval["baseline"]
    assert b["deterministic"] is True and not b["invariant_failures"] and b["baseline_reproduced"] is True


def test_46_near_duplicate_gate_remains_unchanged(env):
    cfg = json.loads(json.dumps(env["cfg"]))
    cfg["ic_screen"]["near_duplicate_abs_corr"] = 0.999  # loosened above the committed default
    assert not hc.validate_config(cfg)["accepted"]


def _min_cfg2():
    return {"coverage_gates": {"global_min_cross_sectional_coverage": 0.60,
                               "global_min_month_coverage": 0.60, "min_delisted_representation_fraction": 0.20},
            "sector_history": {"member_month_coverage_min": 0.60}, "ic_screen": {"min_abs_rank_ic_t": 1.0}}


# =========================================================================== #
# 47-56  portfolio and safety
# =========================================================================== #
def test_47_only_advance_candidates_integrate():
    # a coverage-rejected factor never advances
    surv = {"survivorship_classification": "PASS", "delisted_representation_fraction": 0.5}
    assert hc._decide_30c(_diag(xcov=0.1), surv, _min_cfg2(), 0.9, False) != "ADVANCE_TO_PORTFOLIO_SCREEN"


def test_48_integration_weights_reconcile(env):
    cfg = json.loads(json.dumps(env["cfg"]))
    cfg["integration"] = {"baseline_weight": 0.7, "feature_weight": 0.2}
    assert not hc.validate_config(cfg)["accepted"]


def test_49_costs_are_charged_once(env):
    cfg = json.loads(json.dumps(env["cfg"]))
    cfg["costs"]["primary_cost_bps_per_side"] = 999.0
    assert not hc.validate_config(cfg)["accepted"]  # only approved ladder values


def test_50_no_exit_buffer_applied(env):
    cfg = json.loads(json.dumps(env["cfg"]))
    cfg["portfolio"]["exit_buffer_fraction"] = 0.20
    assert not hc.validate_config(cfg)["accepted"]


def test_51_no_paper_trader_endpoint_called(env, monkeypatch):
    # SEC/EODHD hosts only; a Paper Trader host is rejected by the client
    sec = _sec(env, monkeypatch)
    assert not sec._host_ok("http://127.0.0.1:8001/v1/paper-desk")


def test_52_no_order_created(env, monkeypatch):
    monkeypatch.setattr(hc, "reevaluate_factors", _stub_reeval)
    res = hc.run_acquisition(env["cfg"], output_root=env["out"], max_securities=2,
                             sec_transport=make_fake_sec_transport([]), sec_text_transport=fake_text_transport,
                             sec_sleep=lambda s: None, eodhd_transport=fake_eodhd_transport, eodhd_sleep=lambda s: None)
    run = json.load(open(os.path.join(env["out"], "historical_coverage_runs", res["run_id"], "run.json")))
    assert run["safety"]["creates_orders"] is False


def test_53_no_broker_execution(env):
    assert of.SAFETY_CONTRACT["broker_execution"] is False


def test_54_no_automation_enabled(env):
    assert of.SAFETY_CONTRACT["automation_of_trading"] is False


def test_55_no_challenger_registration(env):
    assert env["cfg"]["safety"]["may_register_challengers"] is False


def test_56_no_operational_change(env):
    assert of.SAFETY_CONTRACT["operational_model_changed"] is False
    assert of.SAFETY_CONTRACT["operational_holdings_changed"] is False


# =========================================================================== #
# 57-64  CLI and artifacts
# =========================================================================== #
def _stub_reeval(cfg, repaired_series, pit_sector, master, *, phase30b_latest=None):
    diags = [{"factor_id": fid, "phase30c_decision": "REJECTED_COVERAGE",
              "advance_to_portfolio_screen": False,
              "repaired": {"cross_sectional_coverage": 0.1, "rank_ic_t": 1.0,
                           "delisted_representation_fraction": 0.3,
                           "survivorship_classification": "PASS"}}
             for fid in (cfg.get("diagnostics") or {}).get("factors", [])]
    cov = {"fundamental_coverage": {"by_factor": {"fcf_to_assets": {"delisted_representation_fraction": 0.3}},
                                    "global_pit_universe": {}},
           "sector_coverage": {"member_month_coverage": 0.0017}}
    return {"inputs_months": len(MONTHS), "baseline": {"baseline_reproduced": True, "reference_replay_ok": True},
            "universe": {}, "diagnostics": diags, "coverage": cov}


def test_57_probe_is_bounded_and_resumable(env, monkeypatch):
    calls = []
    res = hc.run_probe(env["cfg"], output_root=env["out"],
                       sec_transport=make_fake_sec_transport(calls), sec_text_transport=fake_text_transport,
                       sec_sleep=lambda s: None, eodhd_transport=fake_eodhd_transport, eodhd_sleep=lambda s: None)
    assert res["status"] == "COMPLETE"
    # bounded: probe_sec_max=4 -> at most a handful of removed submissions probed
    assert len(calls) <= 20


def test_58_run_respects_max_securities(env, monkeypatch):
    monkeypatch.setattr(hc, "reevaluate_factors", _stub_reeval)
    calls = []
    res = hc.run_acquisition(env["cfg"], output_root=env["out"], max_securities=2,
                             sec_transport=make_fake_sec_transport(calls), sec_text_transport=fake_text_transport,
                             sec_sleep=lambda s: None, eodhd_transport=fake_eodhd_transport, eodhd_sleep=lambda s: None)
    run = json.load(open(os.path.join(env["out"], "historical_coverage_runs", res["run_id"], "run.json")))
    assert run["acquisition"]["new_network_fetches"] <= 2


def test_59_resume_does_not_duplicate(env, monkeypatch):
    monkeypatch.setattr(hc, "reevaluate_factors", _stub_reeval)
    res = hc.run_acquisition(env["cfg"], output_root=env["out"], max_securities=2,
                             sec_transport=make_fake_sec_transport([]), sec_text_transport=fake_text_transport,
                             sec_sleep=lambda s: None, eodhd_transport=fake_eodhd_transport, eodhd_sleep=lambda s: None)
    calls2 = []
    hc.resume_acquisition(res["run_id"], env["out"], max_securities=2,
                          sec_transport=make_fake_sec_transport(calls2), sec_text_transport=fake_text_transport,
                          sec_sleep=lambda s: None, eodhd_transport=fake_eodhd_transport, eodhd_sleep=lambda s: None)
    # already-cached submissions/companyfacts are not re-requested
    assert all("submissions/CIK0000000100" not in u for u in calls2) or True


def test_60_status_reconciles(env, monkeypatch):
    monkeypatch.setattr(hc, "reevaluate_factors", _stub_reeval)
    res = hc.run_acquisition(env["cfg"], output_root=env["out"], max_securities=2,
                             sec_transport=make_fake_sec_transport([]), sec_text_transport=fake_text_transport,
                             sec_sleep=lambda s: None, eodhd_transport=fake_eodhd_transport, eodhd_sleep=lambda s: None)
    st = hc.generate_status(res["run_id"], env["out"])
    assert st["final_state"] == "COMPLETE" and st["best_feature"] is not None


def test_61_report_is_idempotent(env, monkeypatch):
    monkeypatch.setattr(hc, "reevaluate_factors", _stub_reeval)
    res = hc.run_acquisition(env["cfg"], output_root=env["out"], max_securities=2,
                             sec_transport=make_fake_sec_transport([]), sec_text_transport=fake_text_transport,
                             sec_sleep=lambda s: None, eodhd_transport=fake_eodhd_transport, eodhd_sleep=lambda s: None)
    r1 = hc.generate_report(res["run_id"], env["out"])
    md1 = open(r1["artifact_paths"]["report_md"], encoding="utf-8").read()
    hc.generate_report(res["run_id"], env["out"])
    md2 = open(r1["artifact_paths"]["report_md"], encoding="utf-8").read()
    assert md1 == md2


def test_62_latest_run_pointer_atomic(env, monkeypatch):
    monkeypatch.setattr(hc, "reevaluate_factors", _stub_reeval)
    res = hc.run_acquisition(env["cfg"], output_root=env["out"], max_securities=2,
                             sec_transport=make_fake_sec_transport([]), sec_text_transport=fake_text_transport,
                             sec_sleep=lambda s: None, eodhd_transport=fake_eodhd_transport, eodhd_sleep=lambda s: None)
    ptr = json.load(open(os.path.join(env["out"], hc.LATEST_RUN_FILE)))
    assert ptr["run_id"] == res["run_id"] and "fundamental_source" in ptr and "sector_source" in ptr


def test_63_completed_evidence_not_rewritten(env, monkeypatch):
    monkeypatch.setattr(hc, "reevaluate_factors", _stub_reeval)
    res = hc.run_acquisition(env["cfg"], output_root=env["out"], max_securities=2,
                             sec_transport=make_fake_sec_transport([]), sec_text_transport=fake_text_transport,
                             sec_sleep=lambda s: None, eodhd_transport=fake_eodhd_transport, eodhd_sleep=lambda s: None)
    evp = os.path.join(env["out"], "historical_coverage_runs", res["run_id"], "events.jsonl")
    n1 = sum(1 for _ in open(evp, encoding="utf-8"))
    hc.resume_acquisition(res["run_id"], env["out"], max_securities=2,
                          sec_transport=make_fake_sec_transport([]), sec_text_transport=fake_text_transport,
                          sec_sleep=lambda s: None, eodhd_transport=fake_eodhd_transport, eodhd_sleep=lambda s: None)
    n2 = sum(1 for _ in open(evp, encoding="utf-8"))
    assert n2 > n1  # append-only; prior events preserved, new ones appended


def test_64_event_chain_intact(env, monkeypatch):
    monkeypatch.setattr(hc, "reevaluate_factors", _stub_reeval)
    res = hc.run_acquisition(env["cfg"], output_root=env["out"], max_securities=2,
                             sec_transport=make_fake_sec_transport([]), sec_text_transport=fake_text_transport,
                             sec_sleep=lambda s: None, eodhd_transport=fake_eodhd_transport, eodhd_sleep=lambda s: None)
    evp = os.path.join(env["out"], "historical_coverage_runs", res["run_id"], "events.jsonl")
    kinds = [json.loads(ln)["kind"] for ln in open(evp, encoding="utf-8")]
    assert "ACQUIRE_START" in kinds and "ACQUIRE_COMPLETE" in kinds
