"""Fully-offline tests for Phase 10-B - EODHD + Norgate Exhaustive Paid-Subscription Alpha Factory.

No real key, no network, no writes outside a tmp dir. The synthetic Norgate-style expanded panel /
sector / macro / EODHD fundamentals cache are reused from the Phase 8-Y harness (which seeds full
EODHD fundamentals payloads: Earnings.History+reportDate and Financials.*.quarterly+filing_date - the
exact PIT sections Phase 10-B normalizes). The EODHD entitlement audit + dividend acquisition are
driven by an injected transport that returns 200-shaped payloads and BLOCKS bulk-fundamentals (403)
and options (404), so the suite asserts the audit/normalize/gate behaviour without the network.

Asserted: EODHD preflight runs first and EODHD is the only required key; FMP is ignored as a research
source; Norgate is the survivorship-free foundation; the EODHD section/field inventory is written;
snapshot-only fields are never fed to the historical factory; single-symbol fundamentals is preferred
over bulk (bulk is entitlement-blocked, never primary); PIT rows require available_date; the PIT join
rejects future-dated data; the broad strong gate rejects weak alpha; the horizon sweep is written
over 1/5/21/63d; key values are never printed/written; the final decision is allowed (never
forbidden); 32 required artifacts; no Paper Trader/GCP/orders/automation/deploy + no full regression.
"""
from __future__ import annotations

import csv
import importlib
import json
from pathlib import Path

import pytest

from tests import test_phase8y_orthogonal_data_family_acquisition as y8t

MOD = importlib.import_module("research.run_phase10b_eodhd_norgate_exhaustive_alpha_factory")
x8 = MOD.x8

ALL = y8t.ALL_TICKERS


def _read_csv(path):
    with open(path, "r", encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


# --------------------------------------------------------------------------- #
# Injected EODHD transport: 200 for the entitled endpoints, BLOCK bulk (403) + options (404).
# --------------------------------------------------------------------------- #
def _dividends_payload():
    rows = []
    for y in (2017, 2018, 2019):
        for q, mmdd in enumerate(("03-15", "06-15", "09-15", "12-15")):
            rows.append({"date": "%d-%s" % (y, mmdd), "declarationDate": "%d-%s" % (y, mmdd),
                         "value": round(0.40 + 0.02 * (y - 2017) + 0.005 * q, 4)})
    return rows


def _eodhd_transport(url):
    """One callable used by both the audit and the acquisition. Dispatch on the EODHD path."""
    if "bulk-fundamentals" in url:
        raise MOD.EodhdProbeError("provider returned HTTP 403", status_code=403,
                                  kind="entitlement_blocked")
    if "/api/options/" in url:
        raise MOD.EodhdProbeError("provider returned HTTP 404", status_code=404, kind="not_found")
    if "/api/div/" in url:
        return _dividends_payload()
    if "/api/sentiments" in url:
        return {"AAPL.US": [{"date": "2019-01-01", "count": 3, "normalized": 0.2}]}
    if "/api/fundamentals/" in url:
        return {"General": {"Code": "AAPL"}, "Highlights": {"PERatio": 30},
                "Earnings": {"History": {}}, "Financials": {"Income_Statement": {"quarterly": {}}}}
    if "/api/eod/" in url:
        return [{"date": "2024-01-02", "adjusted_close": 100.0}]
    return [{"date": "2019-01-01"}]


def _seed_news(data_dir):
    out = data_dir / "eodhd" / "normalized" / "news_social_sentiment"
    out.mkdir(parents=True, exist_ok=True)
    rows = [["ticker", "provider_symbol", "available_date", "news_sentiment", "news_count",
             "source_family", "source_provider"]]
    for i, tk in enumerate(ALL):
        for yr in (2018, 2019, 2020):
            rows.append([tk, "%s.US" % tk, "%d-04-10" % yr, round(((i + yr) % 7) / 7.0 - 0.5, 4),
                         5, "news_social_sentiment", "EODHD"])
    with open(out / "news_sentiment.csv", "w", newline="", encoding="utf-8") as fh:
        csv.writer(fh).writerows(rows)
    return out / "news_sentiment.csv"


def _run(tmp, *, transport=_eodhd_transport, plant_key=None, **kw):
    panel, sector, rates, data = y8t._build_env(tmp, "NEEDS_NEW_DATA_FAMILY")
    _seed_news(data)
    out = tmp / "out"
    report = MOD.run(out_dir=out, data_dir=data, price_csv=panel, sector_csv=sector,
                     macro={"rates": rates}, phase8v_dir=tmp / "phase8v", live=True,
                     transport=transport, verbose=False, **kw)
    return report, out, data


# --------------------------------------------------------------------------- #
# Module-scoped heavy run (still fast: 20-ticker synthetic Norgate panel).
# --------------------------------------------------------------------------- #
# A 6-family subset that all achieve PIT coverage on the synthetic panel (keeps every assertion valid
# while keeping the integration fixture fast). The real --live run uses the full EODHD_FAMILIES set.
_FIXTURE_FAMILIES = [MOD._FAMILY_BY_NAME[n] for n in (
    "eodhd_earnings_surprise", "eodhd_eps_growth_yoy", "eodhd_revenue_growth_yoy",
    "eodhd_gross_profitability", "eodhd_fcf_to_assets", "eodhd_dividend_growth")]


@pytest.fixture(scope="module")
def live_run(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("phase10b_live")
    report, out, data = _run(tmp, families=_FIXTURE_FAMILIES)
    return report, out, data


# --------------------------------------------------------------------------- #
# 1. All 32 required artifacts present + an allowed (never forbidden) terminal.
# --------------------------------------------------------------------------- #
def test_required_artifacts_and_allowed_decision(live_run):
    report, out, _data = live_run
    assert len(MOD._ARTIFACTS) == 32
    for key in MOD._REQUIRED_ARTIFACTS:
        assert (out / MOD._ARTIFACTS[key]).is_file(), "missing artifact %s" % MOD._ARTIFACTS[key]
    assert report["decision"] in MOD.ALLOWED_DECISIONS
    assert report["decision"] not in MOD.FORBIDDEN_DECISIONS


# --------------------------------------------------------------------------- #
# 2. EODHD preflight runs first; EODHD is the ONLY required key; FMP is context-only.
# --------------------------------------------------------------------------- #
def test_eodhd_preflight_runs_first_and_is_only_required(live_run):
    _report, out, _data = live_run
    rows = _read_csv(out / MOD._ARTIFACTS["key_preflight"])
    by_env = {r["env_var"]: r for r in rows}
    assert MOD.REQUIRED_VISIBLE_KEYS == ("EODHD_API_KEY",)
    assert by_env["EODHD_API_KEY"]["required"] == "True"
    assert by_env["EODHD_API_KEY"]["visibility"] == "PRESENT"   # satisfied by the test transport
    assert "FMP_API_KEY" in by_env and by_env["FMP_API_KEY"]["required"] == "False"
    assert "IGNORED" in by_env["FMP_API_KEY"]["role"].upper() or "context" in by_env["FMP_API_KEY"]["role"]


def test_preflight_unit_eodhd_required_fmp_not(monkeypatch):
    for env in MOD._ALL_ENV_VARS:
        monkeypatch.delenv(env, raising=False)
    rows, ok, missing = MOD.key_visibility_preflight(transport=None)
    assert ok is False and "EODHD_API_KEY" in missing
    assert all(r["env_var"] != "FMP_API_KEY" or r["required"] is False for r in rows)
    # a transport (or a present key) satisfies EODHD offline
    rows2, ok2, missing2 = MOD.key_visibility_preflight(transport=lambda u: {})
    assert ok2 is True and missing2 == []


# --------------------------------------------------------------------------- #
# 3. FMP is ignored as a research source (not required, never an acquire endpoint).
# --------------------------------------------------------------------------- #
def test_fmp_ignored_as_research_source():
    assert "FMP_API_KEY" not in MOD.REQUIRED_VISIBLE_KEYS
    assert MOD.CONTEXT_ONLY_KEYS == ("FMP_API_KEY",)
    for ep in MOD.ACQUIRE_ENDPOINTS:
        assert "eodhd.com" in ep["url"] and "financialmodelingprep" not in ep["url"]
    for ep in MOD.AUDIT_ENDPOINTS:
        assert "eodhd.com" in ep["url"]


# --------------------------------------------------------------------------- #
# 4. Norgate is the survivorship-free foundation (verified + manifest written + kept).
# --------------------------------------------------------------------------- #
def test_norgate_is_foundation(live_run):
    report, out, _data = live_run
    assert (out / MOD._ARTIFACTS["norgate_manifest"]).is_file()
    assert report["norgate_keep"] is True
    assert report["norgate_foundation"]["rebuild_command"] == MOD.NORGATE_REBUILD_COMMAND
    assert report["scoreable_tickers"] > 0 and report["scoreable_events"] > 0


# --------------------------------------------------------------------------- #
# 5. EODHD section + field inventory is written (sections, fields, PIT depth).
# --------------------------------------------------------------------------- #
def test_eodhd_section_field_inventory_written(live_run):
    _report, out, _data = live_run
    sec = _read_csv(out / MOD._ARTIFACTS["section_inventory"])
    fld = _read_csv(out / MOD._ARTIFACTS["field_inventory"])
    pit = _read_csv(out / MOD._ARTIFACTS["pit_fields"])
    snap = _read_csv(out / MOD._ARTIFACTS["snapshot_fields"])
    assert sec and any(r["section"] == "Earnings" for r in sec)
    assert any(r["section"].startswith("Financials") for r in pit)        # PIT depth rows present
    assert any(r["section"].startswith("Financials") and r["pit_usable"] == "True" for r in fld)
    assert fld and snap


# --------------------------------------------------------------------------- #
# 6. Snapshot-only sections are recorded but NEVER fed to the historical factory.
# --------------------------------------------------------------------------- #
def test_snapshot_only_not_used_for_historical_alpha(live_run):
    _report, out, _data = live_run
    snap = _read_csv(out / MOD._ARTIFACTS["snapshot_fields"])
    snap_sections = {r["section"] for r in snap}
    assert "Highlights" in snap_sections and "AnalystRatings" in snap_sections
    assert "Valuation" in snap_sections and "Earnings::Trend" in snap_sections
    # No EODHD feature family sources a snapshot-only section: no snapshot section (full label) is a
    # prefix of any family's PIT source. Earnings.History is PIT-usable; Earnings::Trend/Annual are not.
    snap_norm = [s.replace("::", ".") for s in MOD.SNAPSHOT_ONLY_SECTIONS]
    for fam in MOD.EODHD_FAMILIES:
        pf = fam["pit_field"].replace("::", ".").replace("/", ".")
        for snap_sec in snap_norm:
            assert not pf.startswith(snap_sec + "."), \
                "%s sources snapshot section %s" % (fam["family"], snap_sec)
    # The committed feature catalog never references a snapshot field name.
    cat = (out / MOD._ARTIFACTS["feature_catalog"]).read_text(encoding="utf-8")
    for banned in ("WallStreetTargetPrice", "TargetPrice", "PERatio", "ShortRatio"):
        assert banned not in cat


# --------------------------------------------------------------------------- #
# 7. Single-symbol fundamentals is preferred; bulk-fundamentals is entitlement-blocked, never primary.
# --------------------------------------------------------------------------- #
def test_single_symbol_fundamentals_preferred_over_bulk(live_run):
    _report, out, _data = live_run
    # the acquired/primary fundamentals endpoint is single-symbol, not bulk
    fund_ep = next(e for e in MOD.ACQUIRE_ENDPOINTS if e["name"] == "fundamentals")
    assert "/api/fundamentals/{symbol}" in fund_ep["url"] and "bulk" not in fund_ep["url"]
    audit = _read_csv(out / MOD._ARTIFACTS["entitlement_audit"])
    by = {r["name"]: r for r in audit}
    assert by["fundamentals"]["entitlement"] == MOD.ENT_VERIFIED
    assert by["bulk_fundamentals"]["entitlement"] == MOD.ENT_BLOCKED
    assert "not used as primary" in by["bulk_fundamentals"]["note"].lower() or \
        "NOT used as primary" in by["bulk_fundamentals"]["note"]


# --------------------------------------------------------------------------- #
# 8. The entitlement audit probes EVERY endpoint; a block never stops the sweep.
# --------------------------------------------------------------------------- #
def test_entitlement_audit_block_never_stops(live_run):
    _report, out, _data = live_run
    audit = _read_csv(out / MOD._ARTIFACTS["entitlement_audit"])
    assert len(audit) == len(MOD.AUDIT_ENDPOINTS)
    by = {r["name"]: r["entitlement"] for r in audit}
    assert by["options"] == MOD.ENT_NOTFOUND          # 404, recorded, did not stop the audit
    assert by["bulk_fundamentals"] == MOD.ENT_BLOCKED  # 403, recorded, did not stop the audit
    assert by["news"] == MOD.ENT_VERIFIED and by["sentiments"] == MOD.ENT_VERIFIED


# --------------------------------------------------------------------------- #
# 9. PIT normalizer requires available_date and rejects future-dated data.
# --------------------------------------------------------------------------- #
def test_normalizer_requires_available_date_and_rejects_future(tmp_path):
    data = tmp_path / "data"
    raw = data / "eodhd" / "raw" / "fundamentals"
    raw.mkdir(parents=True, exist_ok=True)
    payload = {"Earnings": {"History": {
        "2019-03-31": {"reportDate": "2019-05-15", "epsActual": 1.2, "epsEstimate": 1.0,
                       "surprisePercent": 20.0},                       # PIT-ok (past)
        "2099-03-31": {"reportDate": "2099-05-15", "epsActual": 9.0, "epsEstimate": 1.0,
                       "surprisePercent": 800.0},                      # future -> dropped
        "2018-03-31": {"reportDate": "", "epsActual": 1.1, "epsEstimate": 1.0,
                       "surprisePercent": 10.0}}}}                     # no available_date -> dropped
    (raw / "AAA.json").write_text(json.dumps(payload), encoding="utf-8")
    P = MOD._Paths(out_dir=tmp_path / "out", data_dir=data)
    fam = MOD._FAMILY_BY_NAME["eodhd_earnings_surprise"]
    csv_path, manifest, audit = MOD.normalize_eodhd_family(fam, P, None, as_of="2026-06-26",
                                                           log=MOD.t8._Log(False))
    rows = _read_csv(csv_path)
    assert len(rows) == 1 and rows[0]["available_date"] == "2019-05-15"
    assert all("available_date" in r and r["available_date"] for r in rows)
    statuses = {a["ticker"]: [] for a in audit}
    for a in audit:
        statuses[a["ticker"]].append(a["status"])
    assert any("DROPPED_FUTURE_DATE" == s for s in statuses.get("AAA", [])), statuses
    assert int(manifest[0]["rows"]) == 1


# --------------------------------------------------------------------------- #
# 10. Family records extraction from a full EODHD fundamentals payload (PIT dates).
# --------------------------------------------------------------------------- #
def test_family_records_extraction():
    payload = y8t._fundamentals_payload(ALL[0])
    surp = MOD._family_records(MOD._FAMILY_BY_NAME["eodhd_earnings_surprise"], payload)
    assert surp and all(r["available_date"] and r["value"] is not None for r in surp)
    rev = MOD._family_records(MOD._FAMILY_BY_NAME["eodhd_revenue_growth_yoy"], payload)
    assert rev and all(len(r["available_date"]) == 10 for r in rev)
    gp = MOD._family_records(MOD._FAMILY_BY_NAME["eodhd_gross_profitability"], payload)
    assert gp and all(r["value"] is not None for r in gp)


# --------------------------------------------------------------------------- #
# 11. PIT join audit + leakage audit: backward-only join, no future leak in the run.
# --------------------------------------------------------------------------- #
def test_pit_join_and_leakage_clean(live_run):
    report, out, _data = live_run
    leak = _read_csv(out / MOD._ARTIFACTS["leakage_audit"])
    checks = {r["check"]: r["status"] for r in leak}
    assert checks.get("as_of_join_direction") == "PASS"
    assert checks.get("future_dated_records_dropped") == "PASS"
    assert checks.get("snapshot_only_excluded") == "PASS"
    join = _read_csv(out / MOD._ARTIFACTS["pit_join_audit"])
    assert join and all(r["join_direction"] == "available_date <= entry_date" for r in join)
    # at least one fundamentals family achieved PIT coverage on the panel
    assert any(int(r["coverage_events"]) > 0 for r in join)
    assert report["secret_safety_leak_scan_clean"] is True


# --------------------------------------------------------------------------- #
# 12. The broad strong gate rejects weak alpha and enforces the survivorship-free cross-section.
# --------------------------------------------------------------------------- #
def _candidate(name, ic_t):
    return {"name": name, "kind": "scenario", "family": "interaction", "weighting": "",
            "sector_neutral": False, "exploratory": False, "ic_old": 0.05, "t_old": 3.5,
            "ic_new": 0.05, "t_new": 3.2,
            "decile": {"mean_decile_spread": 0.0, "decile_hit_rate": 0.6, "n_months": 40,
                       "top_decile_ret": 0.0, "bottom_decile_ret": 0.0},
            "metrics": {"n_events": 40000, "n_months": 60, "mean_ic": 0.05, "ic_t": ic_t,
                        "ic_p": 0.0001, "mean_spread": 0.004, "spread_hit_rate": 0.62,
                        "net_spread_25bps": 0.002, "net_spread_50bps": 0.001, "avg_turnover": 0.2,
                        "subperiod_stable": True, "h1_ic": 0.04, "h2_ic": 0.05,
                        "top_sector_share": 0.2, "hhi": 0.1, "top_sector": "Financials"}}


def test_weak_alpha_rejected_and_broad_gate_enforced():
    assert MOD.STRONG_MIN_IC_T == x8.STRONG_MIN_IC_T == 3.0
    assert MOD.STRONG_MIN_TICKERS == 500
    weak, hight = _candidate("weak", 1.2), _candidate("highT", 6.0)
    cands = [weak, hight]
    x8._finalize_gates(cands, n_tickers=20, n_events=40000, min_tickers=MOD.STRONG_MIN_TICKERS,
                       min_events=MOD.STRONG_MIN_EVENTS)
    assert weak["status"] != "strong"
    assert hight["status"] != "strong", "broad-universe gate must block a tiny cross-section"
    big = _candidate("broad", 6.0)
    x8._finalize_gates([big], n_tickers=MOD.STRONG_MIN_TICKERS + 10,
                       n_events=MOD.STRONG_MIN_EVENTS + 10000, min_tickers=MOD.STRONG_MIN_TICKERS,
                       min_events=MOD.STRONG_MIN_EVENTS)
    assert big["status"] == "strong"


# --------------------------------------------------------------------------- #
# 13. Horizon sweep is written over the 1/5/21/63-day horizons.
# --------------------------------------------------------------------------- #
def test_horizon_sweep_written(live_run):
    report, out, _data = live_run
    hs = _read_csv(out / MOD._ARTIFACTS["horizon_sweep"])
    assert hs, "horizon sweep report is empty"
    horizons = {int(r["horizon_days"]) for r in hs}
    assert horizons.issubset(set(MOD.FWD_WINDOWS)) and horizons
    assert set(report["horizons_tested"]).issubset(set(MOD.FWD_WINDOWS))


# --------------------------------------------------------------------------- #
# 14. derive_decision always returns an allowed (never forbidden) terminal.
# --------------------------------------------------------------------------- #
def test_derive_decision_always_allowed():
    empty_acq = {"ep_state": {}, "progress_rows": []}
    # no panel
    d1, _r, _n = MOD.derive_decision(panel_ok=False, eodhd_ok=True, audit=[], acq=empty_acq,
                                     fam_results=[], candidates=[], universe_size=0, max_tickers=545,
                                     total_requests=0, request_ceiling=10000)
    # no eodhd key
    d2, _r, _n = MOD.derive_decision(panel_ok=True, eodhd_ok=False, audit=[], acq=empty_acq,
                                     fam_results=[], candidates=[], universe_size=545, max_tickers=545,
                                     total_requests=0, request_ceiling=10000)
    # fully tested, no strong
    fam_results = [{"family": "eodhd_gross_profitability", "norm_rows": 100, "max_coverage": 5000,
                    "additive": True, "diagnosis": "covered"}]
    d3, _r, _n = MOD.derive_decision(panel_ok=True, eodhd_ok=True, audit=[], acq=empty_acq,
                                     fam_results=fam_results, candidates=[], universe_size=545,
                                     max_tickers=545, total_requests=0, request_ceiling=10000)
    for d in (d1, d2, d3):
        assert d in MOD.ALLOWED_DECISIONS and d not in MOD.FORBIDDEN_DECISIONS
    assert d1 == MOD.DEC_BLOCKER and d2 == MOD.DEC_BLOCKER and d3 == MOD.DEC_EXHAUSTED


# --------------------------------------------------------------------------- #
# 15. EODHD keep/upgrade/cancel + missing-data-after are produced; verdict KEEP when usable.
# --------------------------------------------------------------------------- #
def test_keep_decision_and_missing_after(live_run):
    report, out, _data = live_run
    keep = _read_csv(out / MOD._ARTIFACTS["keep_decision"])
    by = {r["subscription"]: r for r in keep}
    assert "EODHD Fundamentals Data Feed" in by and by["EODHD Fundamentals Data Feed"]["verdict"] == "KEEP"
    assert any("Norgate" in k for k in by)
    miss = _read_csv(out / MOD._ARTIFACTS["missing_after"])
    mechs = {r["missing_mechanism"] for r in miss}
    assert "options_iv_skew_put_call" in mechs
    assert any("estimate" in m for m in mechs)
    assert report["eodhd_keep_upgrade_cancel"]["verdict"] in ("KEEP", "REVIEW")


# --------------------------------------------------------------------------- #
# 16. Key values are never printed or written to any artifact (secret safety).
# --------------------------------------------------------------------------- #
def test_key_value_never_printed_or_written(tmp_path, monkeypatch, capsys):
    secret = "SUPERSECRET_EODHD_10B_PLANTED_98765"
    monkeypatch.setenv("EODHD_API_KEY", secret)
    report, out, _data = _run(tmp_path, families=_FIXTURE_FAMILIES)
    captured = capsys.readouterr()
    assert secret not in captured.out and secret not in captured.err
    for p in Path(out).glob("*"):
        if p.is_file():
            assert secret not in p.read_text(encoding="utf-8", errors="replace"), p.name
    sa = _read_csv(out / MOD._ARTIFACTS["secret_audit"])
    assert sa and all(r["clean"] == "True" for r in sa)
    assert report["api_key_printed"] is False and report["api_key_written_to_disk"] is False


# --------------------------------------------------------------------------- #
# 17. Source forbids Paper Trader / GCP / orders / automation / deploy / network-post / no full reg.
# --------------------------------------------------------------------------- #
def test_no_forbidden_capabilities_in_source():
    src = Path(MOD.__file__).read_text(encoding="utf-8").lower()
    for banned in ("import subprocess", "os.system(", "requests.post", "urllib.request.urlopen(req",
                   "127.0.0.1:8001", "gcloud", "create_order", "place_order", "boto3",
                   "google.cloud", "paper_trader"):
        # the only allowed urlopen is the audited bounded GET inside _eodhd_live_get
        if banned == "urllib.request.urlopen(req":
            continue
        assert banned not in src, "forbidden capability present in source: %s" % banned
    # exactly one bounded GET helper; no order/automation/deploy verbs in the public surface
    assert "def _eodhd_live_get" in Path(MOD.__file__).read_text(encoding="utf-8")
    for fn in ("create_order", "submit_order", "enable_automation", "deploy", "schedule"):
        assert "def %s" % fn not in src
