"""Fully-offline tests for Phase 10-A - Missing Alpha Data Direct Acquisition + Strong Alpha Search.

No real key, no network, no writes outside a tmp dir. The synthetic expanded universe / panel /
sector / macro / earnings-cache builders are reused from the Phase 8-Y test harness; the four missing
alpha families (analyst estimate revisions, price-target revisions, short interest / days-to-cover,
options IV-skew / put-call) are driven by injected per-provider transports that also CAPTURE every
request URL so the suite can assert the probe/acquire behaviour without touching the network.

Asserted: key-visibility preflight runs first; missing ORATS/INTRINIO/BENZINGA never blocks the
phase; an FMP key that is entitlement-blocked is classified blocked (not missing); all four families
are attempted; alternate providers are tried after an entitlement block; exact missing env vars are
produced; raw payloads gitignored; normalized rows require available_date; the PIT normalizer rejects
future-dated data; a usable synthetic family triggers Phase 10-B readiness; the broad strong gate
rejects weak alpha; the horizon-sweep artifact is written; key values are never printed/written; the
final decision is allowed; no Paper Trader/GCP/orders/automation/deploy + no full-regression call.
"""
from __future__ import annotations

import importlib
import json
import urllib.parse
from pathlib import Path

import pytest

from tests import test_phase8y_orthogonal_data_family_acquisition as y8t

MOD = importlib.import_module("research.run_phase10a_missing_alpha_data_acquisition")
o8 = MOD.o8
x8 = MOD.x8

ALL = y8t.ALL_TICKERS


def _read_csv(path):
    return y8t._read_csv(path)


def _seed(url):
    return sum(ord(c) for c in url) % 7


def _records(seed):
    return {"records": [
        {"available_date": "2017-03-15", "value": float(seed % 5 - 2)},
        {"available_date": "2018-03-15", "value": float((seed * 2) % 5 - 2)},
        {"available_date": "2019-03-15", "value": float((seed * 3) % 7 - 3)}]}


class _Capture:
    """Per-provider transports for the four families; record every request URL (no network, no key).
    Each returns the uniform {"records":[{available_date,value}]} shape the normalizer accepts, with
    PAST dates that overlap the synthetic earnings panel so the families achieve PIT coverage."""

    def __init__(self, blocked=None):
        self.urls = []
        self.blocked = set(blocked or ())          # provider display names that raise a 403 block

    def _t(self, provider):
        def transport(url):
            self.urls.append(url)
            if provider in self.blocked:
                raise o8.ProbeError("provider returned HTTP 403", status_code=403,
                                    error_type="http_error")
            return _records(_seed(url))
        return transport

    def map(self, providers):
        return {p: self._t(p) for p in providers}


ALL_PROVIDERS = (MOD.PROV_FMP, MOD.PROV_FINNHUB, MOD.PROV_AV, MOD.PROV_POLYGON, MOD.PROV_NASDAQ)


def _clear_keys(monkeypatch):
    for env in MOD._ALL_ENV_VARS:
        monkeypatch.delenv(env, raising=False)


def _run(tmp, transports=None, flat=False, **kw):
    panel, sector, rates, data = y8t._build_env(tmp, "NEEDS_NEW_DATA_FAMILY", flat=flat)
    out = tmp / "out"
    report = MOD.run(out_dir=out, data_dir=data, price_csv=panel, sector_csv=sector,
                     macro={"rates": rates}, phase8v_dir=tmp / "phase8v", live=True,
                     transports=transports, verbose=False, **kw)
    return report, out, data


# --------------------------------------------------------------------------- #
# Module-scoped heavy run: all four families acquired via verified transports.
# --------------------------------------------------------------------------- #
@pytest.fixture(scope="module")
def live_run(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("phase10a_live")
    cap = _Capture()
    report, out, data = _run(tmp, transports=cap.map(ALL_PROVIDERS))
    return report, out, data, cap


# --------------------------------------------------------------------------- #
# 1. All 30 required artifacts present + an allowed (never forbidden) terminal.
# --------------------------------------------------------------------------- #
def test_required_artifacts_and_allowed_decision(live_run):
    report, out, _data, _cap = live_run
    for key in MOD._REQUIRED_ARTIFACTS:
        assert (out / MOD._ARTIFACTS[key]).is_file(), "missing artifact %s" % MOD._ARTIFACTS[key]
    assert len(MOD._ARTIFACTS) == 30
    assert report["decision"] in MOD.ALLOWED_DECISIONS
    assert report["decision"] not in MOD.FORBIDDEN_DECISIONS


# --------------------------------------------------------------------------- #
# 2. Key-visibility preflight runs (and is recorded) before live acquisition.
# --------------------------------------------------------------------------- #
def test_key_visibility_preflight_runs_first(live_run):
    _report, out, _data, _cap = live_run
    rows = _read_csv(out / MOD._ARTIFACTS["key_preflight"])
    envs = {r["env_var"] for r in rows}
    for req in MOD.REQUIRED_VISIBLE_KEYS:
        assert req in envs, "preflight missing required key %s" % req
    for opt in MOD.OPTIONAL_MISSING_KEYS:
        assert opt in envs


def test_preflight_unit_no_keys_and_transport(monkeypatch):
    _clear_keys(monkeypatch)
    rows, ok, missing = MOD.key_visibility_preflight(transports=None)
    assert ok is False and set(missing) == set(MOD.REQUIRED_VISIBLE_KEYS)
    # a transport for FMP satisfies FMP_API_KEY offline.
    rows2, ok2, missing2 = MOD.key_visibility_preflight(transports={MOD.PROV_FMP: lambda u: {}})
    assert "FMP_API_KEY" not in missing2


# --------------------------------------------------------------------------- #
# 3. Missing ORATS / INTRINIO / BENZINGA never blocks the phase; exact actions produced.
# --------------------------------------------------------------------------- #
def test_missing_specialist_keys_do_not_block(live_run):
    report, out, _data, _cap = live_run
    rows = _read_csv(out / MOD._ARTIFACTS["missing_keys"])
    envs = {r["env_var"]: r for r in rows}
    for e in ("ORATS_API_KEY", "INTRINIO_API_KEY", "BENZINGA_API_KEY"):
        assert e in envs, "missing-key action not recorded for %s" % e
        assert "set $env:" in envs[e]["exact_action"]
    # the run still reached an allowed terminal despite those keys being absent.
    assert report["decision"] in MOD.ALLOWED_DECISIONS


# --------------------------------------------------------------------------- #
# 4. FMP present-but-entitlement-blocked is classified blocked (not missing); alt providers tried.
# --------------------------------------------------------------------------- #
def test_fmp_blocked_is_entitlement_not_missing_and_alt_provider_used(tmp_path, monkeypatch):
    _clear_keys(monkeypatch)
    cap = _Capture(blocked=(MOD.PROV_FMP,))             # FMP 403 on every endpoint; others verified
    report, out, _data = _run(tmp_path, transports=cap.map(ALL_PROVIDERS))
    attempts = _read_csv(out / MOD._ARTIFACTS["provider_attempts"])
    fmp_est = [r for r in attempts if r["provider"] == MOD.PROV_FMP
               and r["family"] == "analyst_estimate_revisions"]
    assert fmp_est and fmp_est[0]["entitlement"] == MOD.ENT_BLOCKED
    assert fmp_est[0]["entitlement"] != MOD.ENT_MISSING
    assert str(fmp_est[0]["http_status"]) == "403"
    # estimate-revisions falls through to Finnhub (next priority) and is still acquired.
    fin_est = [r for r in attempts if r["provider"] == MOD.PROV_FINNHUB
               and r["family"] == "analyst_estimate_revisions"]
    assert fin_est and fin_est[0]["entitlement"] == MOD.ENT_VERIFIED
    assert "analyst_estimate_revisions" in report["data_families_acquired"]
    # the blocked FMP attempt is recorded in entitlement_blockers.csv.
    blockers = _read_csv(out / MOD._ARTIFACTS["entitlement_blockers"])
    assert any(b["provider"] == MOD.PROV_FMP and b["family"] == "analyst_estimate_revisions"
               for b in blockers)


# --------------------------------------------------------------------------- #
# 5. All four missing-alpha families are attempted.
# --------------------------------------------------------------------------- #
def test_all_four_families_attempted(live_run):
    _report, out, _data, _cap = live_run
    attempts = _read_csv(out / MOD._ARTIFACTS["provider_attempts"])
    fams = {r["family"] for r in attempts}
    for f in ("analyst_estimate_revisions", "price_target_revisions",
              "short_interest_days_to_cover", "options_iv_skew_put_call"):
        assert f in fams, "family not attempted: %s" % f


# --------------------------------------------------------------------------- #
# 6. Raw provider payloads remain gitignored.
# --------------------------------------------------------------------------- #
def test_raw_payloads_gitignored(live_run):
    _report, _out, data, _cap = live_run
    # at least one acquired provider's data dir is force-gitignored.
    found = False
    for slug in ("fmp", "finnhub", "nasdaq", "polygon", "alpha"):
        gi = data / slug / ".gitignore"
        if gi.is_file():
            assert "raw/" in gi.read_text(encoding="utf-8")
            found = True
    assert found, "no acquired provider .gitignore written"


# --------------------------------------------------------------------------- #
# 7. The PIT normalizer requires available_date and rejects future-dated data.
# --------------------------------------------------------------------------- #
def test_normalizer_requires_available_date_and_rejects_future(tmp_path):
    log = MOD.t8._Log(False)
    fam = MOD._FAMILY_BY_NAME["analyst_estimate_revisions"]
    prov_dir = tmp_path / "fmp"
    raw = prov_dir / "raw" / fam["family"]
    raw.mkdir(parents=True, exist_ok=True)
    # one valid past record, one undated, one future-dated (after the as-of).
    (raw / "AAA.json").write_text(json.dumps({"records": [
        {"available_date": "2019-03-15", "value": 1.5},
        {"available_date": "", "value": 2.0},
        {"available_date": "2099-03-15", "value": 9.0}]}), encoding="utf-8")
    csv_path, manifest, audit = MOD.normalize_family_pit(fam, MOD.PROV_FMP, raw, prov_dir,
                                                         MOD.AS_OF, log)
    rows = _read_csv(csv_path)
    assert len(rows) == 1 and rows[0]["available_date"][:4] == "2019"
    assert all("available_date" in r and r["available_date"] for r in rows)
    statuses = {a["status"] for a in audit}
    assert "DROPPED_FUTURE_DATE" in statuses
    assert ("DROPPED_NO_PIT_DATE" in statuses or "DROPPED_NO_VALUE" in statuses)
    assert manifest[0]["rows"] == 1


# --------------------------------------------------------------------------- #
# 8. A usable synthetic family triggers Phase 10-B readiness (acquired families + plan + terminal).
# --------------------------------------------------------------------------- #
def test_usable_family_triggers_phase10b_readiness(live_run):
    report, out, _data, _cap = live_run
    assert report["data_families_acquired"], "no usable family acquired"
    usable = _read_csv(out / MOD._ARTIFACTS["usable_families"])
    assert any(int(r["coverage_events"]) > 0 for r in usable)
    assert (out / MOD._ARTIFACTS["next_plan"]).is_file()
    plan = json.loads((out / MOD._ARTIFACTS["next_plan"]).read_text(encoding="utf-8"))
    assert plan["next_phase"] == "10-B"
    assert report["decision"] in (MOD.DEC_STRONG, MOD.DEC_NEXT_BATCH, MOD.DEC_EXHAUSTED)


def test_next_batch_when_universe_exceeds_max_tickers(tmp_path):
    cap = _Capture()
    report, _out, _data = _run(tmp_path, transports=cap.map(ALL_PROVIDERS), max_tickers=2)
    # universe (synthetic) > 2 -> more universe remains -> resumable next batch (or strong, never blocked).
    assert report["decision"] in (MOD.DEC_NEXT_BATCH, MOD.DEC_STRONG)


# --------------------------------------------------------------------------- #
# 9. The broad strong gate rejects weak alpha and enforces the broad-universe floor.
# --------------------------------------------------------------------------- #
def _candidate(name, ic_t, n_events_metric=40000):
    return {"name": name, "kind": "scenario", "family": "interaction", "weighting": "",
            "sector_neutral": False, "exploratory": False, "ic_old": 0.05, "t_old": 3.5,
            "ic_new": 0.05, "t_new": 3.2,
            "decile": {"mean_decile_spread": 0.0, "decile_hit_rate": 0.6, "n_months": 40,
                       "top_decile_ret": 0.0, "bottom_decile_ret": 0.0},
            "metrics": {"n_events": n_events_metric, "n_months": 60, "mean_ic": 0.05, "ic_t": ic_t,
                        "ic_p": 0.0001, "mean_spread": 0.004, "spread_hit_rate": 0.62,
                        "net_spread_25bps": 0.002, "net_spread_50bps": 0.001, "avg_turnover": 0.2,
                        "subperiod_stable": True, "h1_ic": 0.04, "h2_ic": 0.05,
                        "top_sector_share": 0.2, "hhi": 0.1, "top_sector": "Financials"}}


def test_weak_alpha_rejected_and_broad_gate_enforced():
    assert MOD.STRONG_MIN_IC_T == x8.STRONG_MIN_IC_T == 3.0
    weak = _candidate("weak_ix", ic_t=1.2)
    hight = _candidate("highT_ix", ic_t=6.0)
    cands = [weak, hight]
    MOD.x8._finalize_gates(cands, n_tickers=20, n_events=40000,
                           min_tickers=MOD.STRONG_MIN_TICKERS, min_events=MOD.STRONG_MIN_EVENTS)
    assert weak["status"] != "strong"
    assert hight["status"] != "strong", "broad-universe gate must block a tiny cross-section"
    big = _candidate("broad_ix", ic_t=6.0)
    MOD.x8._finalize_gates([big], n_tickers=MOD.STRONG_MIN_TICKERS + 10,
                           n_events=MOD.STRONG_MIN_EVENTS + 10000,
                           min_tickers=MOD.STRONG_MIN_TICKERS, min_events=MOD.STRONG_MIN_EVENTS)
    assert big["status"] == "strong"


# --------------------------------------------------------------------------- #
# 10. The horizon-sweep artifact is written and covers the 1/5/21/63-day horizons.
# --------------------------------------------------------------------------- #
def test_horizon_sweep_artifact_written(live_run):
    report, out, _data, _cap = live_run
    hs = _read_csv(out / MOD._ARTIFACTS["horizon_sweep"])
    assert hs, "horizon sweep report is empty"
    horizons = {int(r["horizon_days"]) for r in hs}
    assert horizons.issubset(set(MOD.FWD_WINDOWS))
    assert horizons & set(MOD.FWD_WINDOWS), "no configured horizons present"
    assert set(report["horizons_tested"]).issubset(set(MOD.FWD_WINDOWS))


# --------------------------------------------------------------------------- #
# 11. derive_decision always returns an allowed (never forbidden) terminal.
# --------------------------------------------------------------------------- #
def test_derive_decision_is_always_allowed():
    attempts_blocked = [{"family": f["family"], "provider": "FMP", "entitlement": MOD.ENT_BLOCKED,
                         "endpoint_redacted": "", "http_status": 403, "note": "blocked"}
                        for f in MOD.MISSING_ALPHA_FAMILIES]
    acq = {"fam_state": {f["family"]: {"provider": "FMP", "requests": 0, "acquired": 0,
                                       "progress": []} for f in MOD.MISSING_ALPHA_FAMILIES}}
    fam = [{"family": f["family"], "norm_rows": 0, "max_coverage": 0, "diagnosis": "x",
            "feature": f["feature"]} for f in MOD.MISSING_ALPHA_FAMILIES]
    dec, _r, nrows = MOD.derive_decision(panel_ok=True, attempts=attempts_blocked, acq=acq,
                                         fam_results=fam, candidates=[], universe_size=545,
                                         max_tickers=545, total_requests=0, request_ceiling=8000)
    assert dec == MOD.DEC_ALL_BLOCKED and nrows
    assert dec in MOD.ALLOWED_DECISIONS and dec not in MOD.FORBIDDEN_DECISIONS


# --------------------------------------------------------------------------- #
# 12. Key values are NEVER printed or written to disk.
# --------------------------------------------------------------------------- #
def test_key_value_never_printed_or_written(tmp_path, monkeypatch, capsys):
    _clear_keys(monkeypatch)
    secret = "SUPERSECRET_MISSING_ALPHA_10A_98765"
    for env in MOD.REQUIRED_VISIBLE_KEYS:
        monkeypatch.setenv(env, secret)
    cap = _Capture()
    report, out, data = _run(tmp_path, transports=cap.map(ALL_PROVIDERS))
    printed = capsys.readouterr()
    assert secret not in printed.out and secret not in printed.err
    for root in (out, data):
        for p in root.rglob("*"):
            if p.is_file():
                assert secret not in p.read_text(encoding="utf-8", errors="replace"), \
                    "secret leaked into %s" % p
    assert report["api_key_printed"] is False
    assert report["api_key_written_to_disk"] is False
    assert report["secret_safety_leak_scan_clean"] is True


# --------------------------------------------------------------------------- #
# 13. No Paper Trader / GCP / order / automation / deploy logic; no full-regression invocation.
# --------------------------------------------------------------------------- #
def test_no_forbidden_logic_in_source():
    src = Path(MOD.__file__).read_text(encoding="utf-8").lower()
    for pat in ("place_order", "submit_order", "create_order(", "enable_automation",
                "broker_execution(", "gcp_deploy", "deploy_to_gcp", "import paper_trader",
                "from paper_trader"):
        assert pat not in src, "forbidden pattern present: %s" % pat
    for pat in ("pytest.main", "run_all_phases", "full_regression("):
        assert pat not in src, "full-regression invocation present: %s" % pat
