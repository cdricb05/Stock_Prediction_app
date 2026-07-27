"""Phase 30C.2 — historical-fundamentals vendor evaluation pack exporter.

Hermetic: a synthetic Phase 30C sample (60 removed + 20 current) plus the
matching Phase 30C.1 adequacy, security master, SEC as-filed manifest and
companyfacts stubs are written under a tmp tree, with ``historical_coverage.
_ROOTS`` monkeypatched to it. No network, no vendor, no Paper Trader, no
committed data files. A handful of the sample records are hand-crafted to
exercise each availability branch; the rest are generic filler.
"""

from __future__ import annotations

import csv
import json
import os

import pytest

from research_agent import cli
from research_agent import historical_coverage as hc
from research_agent import vendor_evaluation_pack as vp

C30_RUN = "hcov_TEST0001"
C31_RUN = "sfadq_TEST0001"
SAMPLE_HASH = "fixturehash_deadbeef00000000000000000000000000000000000000000000"
MASTER_HASH = "fixturemasterhash_1111111111111111111111111111111111111111111111"

# Decade -> delisting month used for the 60 removed names (20 per decade).
_DECADE_MONTH = {0: "2005-07", 1: "2015-07", 2: "2022-07"}


def _current_records():
    """20 current controls; indices 0..2 are hand-crafted, 3..19 generic."""
    recs = []
    for i in range(20):
        base = "CUR%02d" % i
        cik = "%010d" % (1000 + i)
        rec = {
            "base_symbol": base, "ticker": base, "cik": cik,
            "cik_confidence": "high", "cik_source": "sec_current_directory",
            "current_gics_sector": "Industrials", "delisting_month": None,
            "first_member_month": "2005-06", "last_member_month": "2026-07",
            "is_delisted": False, "is_current_member": True, "last_quoted_date": "",
            "mapping_ambiguity": None, "security_name": "%s Corp Common" % base,
            "simfin_id": "SF%04d" % (1000 + i),
        }
        recs.append(rec)
    return recs


def _removed_records():
    """60 removed names, 20 per decade; indices 0..4 hand-crafted."""
    recs = []
    for i in range(60):
        decade = i // 20
        dmonth = _DECADE_MONTH[decade]
        base = "REM%02d" % i
        ticker = "%s-%s" % (base, dmonth.replace("-", ""))
        cik = "%010d" % (5000 + i)
        rec = {
            "base_symbol": base, "ticker": ticker, "cik": cik,
            "cik_confidence": "medium", "cik_source": "simfin_company_master",
            "current_gics_sector": "Industrials", "delisting_month": dmonth,
            "first_member_month": "2000-08",
            "last_member_month": dmonth[:4] + "-06",
            "is_delisted": True, "is_current_member": False,
            "last_quoted_date": dmonth + "-15",
            "mapping_ambiguity": None, "security_name": "%s Corp Common" % base,
            "simfin_id": "SF%04d" % (5000 + i),
        }
        recs.append(rec)
    # index 0: no CIK -> NO_SEC_IDENTITY + UNMAPPED
    recs[0]["cik"] = ""
    recs[0]["cik_source"] = None
    recs[0]["simfin_id"] = ""
    # index 4: share-class base symbol -> base_ticker strips the class
    recs[4]["base_symbol"] = "REMX.B"
    return recs


def _adequacy(current, removed):
    """Per-ticker Phase 30C.1 adequacy consistent with the crafted records."""
    def entry(rec, is_removed, simfin_id, usable, failure, conf, ambig):
        return {"ticker": rec["ticker"], "is_removed": is_removed, "cik": rec["cik"],
                "simfin_id": simfin_id, "map_source": "cik" if simfin_id else None,
                "map_confidence": conf, "map_ambiguity": ambig,
                "usable_factors": usable, "failure_reason": failure}

    THREE = ["fcf_to_assets", "gross_profitability", "operating_accruals"]
    cur = []
    for i, rec in enumerate(current):
        if i == 1:  # UNMAPPED
            cur.append(entry(rec, False, None, [], "UNMAPPED", "none", None))
        elif i == 2:  # mapped, no statements
            cur.append(entry(rec, False, rec["simfin_id"], [], "MAPPED_NO_STATEMENTS_IN_WINDOW", "high", None))
        else:  # usable
            cur.append(entry(rec, False, rec["simfin_id"], THREE, None, "high", None))
    rem = []
    for i, rec in enumerate(removed):
        if i == 0:  # no cik -> unmapped
            rem.append(entry(rec, True, None, [], "UNMAPPED", "none", None))
        elif i == 1:  # as-filed reconstructed (mapped, no simfin statements)
            rem.append(entry(rec, True, rec["simfin_id"], [], "MAPPED_NO_STATEMENTS_IN_WINDOW", "high", None))
        elif i == 2:  # bank excluded
            rem.append(entry(rec, True, rec["simfin_id"], [], "BANK_EXCLUDED", "high", None))
        elif i == 3:  # ambiguous CIK -> unmapped
            rem.append(entry(rec, True, None, [], "UNMAPPED", "none", "one CIK maps to multiple SimFinIds"))
        else:
            rem.append(entry(rec, True, rec["simfin_id"], THREE, None, "high", None))
    return {"schema_version": "30C1.1", "record_type": "SAMPLE_ADEQUACY",
            "reused_phase30c_sample": True, "phase30c_run_id": C30_RUN,
            "sample_hash": SAMPLE_HASH, "current": cur, "removed": rem}


def _write(path, obj):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(obj, fh)


def build_fixture(root):
    current = _current_records()
    removed = _removed_records()
    dr = root  # data_root == repo == root for the fixture

    # Phase 30C sample manifest + pointer
    sample_manifest = {
        "record_type": "SAMPLE_MANIFEST", "schema_version": "30C.1",
        "current": current, "removed": removed,
        "current_by_decade": {"2000s": 8, "2010s": 6, "2020s": 6},
        "removed_by_decade": {"2000s": 20, "2010s": 20, "2020s": 20},
        "decades": ["2000s", "2010s", "2020s"], "seed": 30,
        "requested": {"current": 20, "removed": 60},
        "selected": {"current": 20, "removed": 60},
        "sample_hash": SAMPLE_HASH, "security_master_hash": MASTER_HASH,
    }
    _write(os.path.join(dr, "research_agent", "phase30c_latest_run.json"), {"run_id": C30_RUN})
    runs30 = os.path.join(dr, "research_agent", "historical_coverage_runs", C30_RUN)
    _write(os.path.join(runs30, "sample_manifest.json"), sample_manifest)
    _write(os.path.join(runs30, "security_master.json"),
           {"record_type": "SECURITY_MASTER", "rows": current + removed})
    # SEC as-filed: removed index 1 is "reconstructed" (in the set but not cached)
    _write(os.path.join(runs30, "normalized_manifests", "sec_asfiled.json"),
           {"record_type": "NORMALIZED_MANIFEST",
            "removed_tickers_acquired": [removed[1]["ticker"]]})

    # Phase 30C.1 adequacy + pointer
    _write(os.path.join(dr, "research_agent", "phase30c1_latest_run.json"), {"run_id": C31_RUN})
    runs31 = os.path.join(dr, "research_agent", "simfin_adequacy_runs", C31_RUN)
    _write(os.path.join(runs31, "sample_adequacy.json"), _adequacy(current, removed))

    # companyfacts stubs: current[0] big (cached), current[2] tiny stub,
    # removed[2] big (bank still has facts). removed[1] intentionally absent.
    cf = os.path.join(dr, "phase7j_broad_universe_signal_retest", "sec_companyfacts")
    os.makedirs(cf, exist_ok=True)
    big = "{\"facts\": \"" + ("x" * 2000) + "\"}"
    with open(os.path.join(cf, "CIK%s.json" % current[0]["cik"]), "w", encoding="utf-8") as fh:
        fh.write(big)
    with open(os.path.join(cf, "CIK%s.json" % current[2]["cik"]), "w", encoding="utf-8") as fh:
        fh.write("{}")  # tiny stub
    with open(os.path.join(cf, "CIK%s.json" % removed[2]["cik"]), "w", encoding="utf-8") as fh:
        fh.write(big)
    os.makedirs(os.path.join(dr, "provider_cache", "phase30c", "sec", "companyfacts"), exist_ok=True)

    # tiny SimFin csvs (only need to exist to be hashed for the manifest)
    sd = os.path.join(dr, "research", "data", "simfin")
    os.makedirs(sd, exist_ok=True)
    for name in ("us-companies.csv", "us-income-quarterly.csv",
                 "us-balance-quarterly.csv", "us-cashflow-quarterly.csv"):
        with open(os.path.join(sd, name), "w", encoding="utf-8") as fh:
            fh.write("SimFinId;Ticker\n1;AA\n")
    return current, removed


def fixture_config():
    def s(rel, root="data_root"):
        return {"relpath": rel, "root": root}
    return {
        "schema_version": "30C2.1", "name": "phase30c2_vendor_evaluation_pack_test",
        "data": {"data_cutoff": "2026-06-30"},
        "generated_at": "2026-07-27T00:00:00Z",
        "vendors": ["Vendor A", "Vendor B"],
        "entitlement": {"no_purchase": True, "contact_sales": False,
                        "use_existing_keys_only": True, "local_files_only": True},
        "coverage_gates": {"global_min_cross_sectional_coverage": 0.6,
                           "global_min_month_coverage": 0.6,
                           "min_delisted_representation_fraction": 0.2},
        "sector_history": {"member_month_coverage_min": 0.6},
        "safety": {"research_only": True, "may_register_challengers": False,
                   "no_operational_promotion": True},
        "output_subdir": "vendor_evaluation_pack",
        "sources": {
            "roots": {"data_root": "DATA_ROOT", "repo": "REPO_ROOT"},
            "phase30c_pointer": s("research_agent/phase30c_latest_run.json"),
            "phase30c_runs_root": s("research_agent/historical_coverage_runs"),
            "phase30c1_pointer": s("research_agent/phase30c1_latest_run.json"),
            "phase30c1_runs_root": s("research_agent/simfin_adequacy_runs"),
            "owned_sec_companyfacts_dir": s("phase7j_broad_universe_signal_retest/sec_companyfacts"),
            "sec_cache_companyfacts_dir": s("provider_cache/phase30c/sec/companyfacts"),
            "simfin_companies": s("research/data/simfin/us-companies.csv", "repo"),
            "simfin_income": s("research/data/simfin/us-income-quarterly.csv", "repo"),
            "simfin_balance": s("research/data/simfin/us-balance-quarterly.csv", "repo"),
            "simfin_cashflow": s("research/data/simfin/us-cashflow-quarterly.csv", "repo"),
        },
    }


@pytest.fixture
def env(tmp_path, monkeypatch):
    root = str(tmp_path / "data")
    os.makedirs(root, exist_ok=True)
    current, removed = build_fixture(root)
    monkeypatch.setattr(hc, "_ROOTS", {"repo": root, "data_root": root})
    out_root = str(tmp_path / "out")
    cfg = fixture_config()
    return {"root": root, "out_root": out_root, "cfg": cfg,
            "current": current, "removed": removed}


def _read_csv(path):
    with open(path, "r", encoding="utf-8", newline="") as fh:
        return list(csv.DictReader(fh))


def _build(env):
    return vp.build_pack(env["cfg"], output_root=env["out_root"])


def _out(env, name):
    return os.path.join(env["out_root"], "vendor_evaluation_pack", name)


# --------------------------------------------------------------------------- #
# config validation (1-9)
# --------------------------------------------------------------------------- #
def test_01_config_accepts_good():
    assert vp.validate_config(fixture_config())["accepted"] is True


def test_02_config_rejects_wrong_schema():
    cfg = fixture_config(); cfg["schema_version"] = "29A.1"
    assert vp.validate_config(cfg)["accepted"] is False


def test_03_config_rejects_secret_key():
    cfg = fixture_config(); cfg["api_key"] = "zzz"
    assert vp.validate_config(cfg)["accepted"] is False


def test_04_config_rejects_url():
    cfg = fixture_config(); cfg["note"] = "https://simfin.com/api"
    assert vp.validate_config(cfg)["accepted"] is False


def test_05_config_rejects_paper_trader():
    cfg = fixture_config(); cfg["note"] = "reads operational_book"
    assert vp.validate_config(cfg)["accepted"] is False


def test_06_config_rejects_weakened_coverage_gate():
    cfg = fixture_config(); cfg["coverage_gates"]["global_min_cross_sectional_coverage"] = 0.4
    assert vp.validate_config(cfg)["accepted"] is False


def test_07_config_rejects_weakened_removed_representation():
    cfg = fixture_config(); cfg["coverage_gates"]["min_delisted_representation_fraction"] = 0.1
    assert vp.validate_config(cfg)["accepted"] is False


def test_08_config_rejects_missing_pointer():
    cfg = fixture_config(); del cfg["sources"]["phase30c1_pointer"]
    assert vp.validate_config(cfg)["accepted"] is False


def test_09_config_rejects_contact_sales_and_bad_safety():
    cfg = fixture_config(); cfg["entitlement"]["contact_sales"] = True
    assert vp.validate_config(cfg)["accepted"] is False
    cfg2 = fixture_config(); cfg2["safety"]["may_register_challengers"] = True
    assert vp.validate_config(cfg2)["accepted"] is False


# --------------------------------------------------------------------------- #
# build + files (10-14)
# --------------------------------------------------------------------------- #
def test_10_build_ready(env):
    r = _build(env)
    assert r["status"] == "READY"


def test_11_all_seven_files_written(env):
    _build(env)
    for name in vp.DELIVERABLES:
        assert os.path.isfile(_out(env, name)), name


def test_12_build_rejects_invalid_config(env):
    cfg = dict(env["cfg"]); cfg["schema_version"] = "bad"
    r = vp.build_pack(cfg, output_root=env["out_root"])
    assert r["status"] == "INVALID_CONFIG"


def test_13_counts_exactly_60_removed_20_current(env):
    r = _build(env)
    assert r["row_counts"]["trial_sample_removed"] == 60
    assert r["row_counts"]["trial_sample_current"] == 20
    assert r["row_counts"]["trial_sample_total"] == 80


def test_14_trial_sample_header_matches(env):
    _build(env)
    with open(_out(env, vp.TRIAL_SAMPLE_CSV), "r", encoding="utf-8") as fh:
        header = fh.readline().strip().split(",")
    assert header == list(vp.TRIAL_SAMPLE_COLUMNS)


# --------------------------------------------------------------------------- #
# sample preservation (15-17)
# --------------------------------------------------------------------------- #
def test_15_sample_hash_carried_through(env):
    _build(env)
    manifest = json.load(open(_out(env, vp.PACKAGE_MANIFEST_JSON)))
    assert manifest["sample_hash"] == SAMPLE_HASH


def test_16_sample_tickers_preserved_verbatim(env):
    _build(env)
    rows = _read_csv(_out(env, vp.TRIAL_SAMPLE_CSV))
    got = {r["canonical_security_id"] for r in rows}
    want = {r["ticker"] for r in env["current"]} | {r["ticker"] for r in env["removed"]}
    assert got == want
    assert len(rows) == 80


def test_17_no_inconvenient_name_dropped(env):
    _build(env)
    rows = {r["canonical_security_id"]: r for r in _read_csv(_out(env, vp.TRIAL_SAMPLE_CSV))}
    # the no-CIK unmapped removed name must still be present
    assert env["removed"][0]["ticker"] in rows
    assert rows[env["removed"][0]["ticker"]]["current_failure_reason"] == "UNMAPPED"


# --------------------------------------------------------------------------- #
# identifier + availability derivation (18-25)
# --------------------------------------------------------------------------- #
def test_18_norgate_equals_canonical(env):
    _build(env)
    for r in _read_csv(_out(env, vp.TRIAL_SAMPLE_CSV)):
        assert r["norgate_identifier"] == r["canonical_security_id"]


def test_19_base_ticker_strips_class(env):
    _build(env)
    rows = {r["canonical_security_id"]: r for r in _read_csv(_out(env, vp.TRIAL_SAMPLE_CSV))}
    rec = env["removed"][4]  # base_symbol REMX.B
    assert rows[rec["ticker"]]["historical_ticker"] == "REMX.B"
    assert rows[rec["ticker"]]["base_ticker"] == "REMX"


def test_20_base_root_helper():
    assert vp._base_root("AGR.B") == "AGR"
    assert vp._base_root("SE") == "SE"


def test_21_sec_availability_cached_and_stub(env):
    _build(env)
    rows = {r["canonical_security_id"]: r for r in _read_csv(_out(env, vp.TRIAL_SAMPLE_CSV))}
    assert rows[env["current"][0]["ticker"]]["sec_statement_availability"] == "SEC_COMPANYFACTS_CACHED"
    assert rows[env["current"][2]["ticker"]]["sec_statement_availability"] == "SEC_COMPANYFACTS_EMPTY_STUB"


def test_22_sec_availability_no_identity(env):
    _build(env)
    rows = {r["canonical_security_id"]: r for r in _read_csv(_out(env, vp.TRIAL_SAMPLE_CSV))}
    assert rows[env["removed"][0]["ticker"]]["sec_statement_availability"] == "NO_SEC_IDENTITY"


def test_23_sec_availability_asfiled_reconstructed(env):
    _build(env)
    rows = {r["canonical_security_id"]: r for r in _read_csv(_out(env, vp.TRIAL_SAMPLE_CSV))}
    assert rows[env["removed"][1]["ticker"]]["sec_statement_availability"] == "SEC_ASFILED_RECONSTRUCTED"


def test_24_simfin_availability_branches(env):
    _build(env)
    rows = {r["canonical_security_id"]: r for r in _read_csv(_out(env, vp.TRIAL_SAMPLE_CSV))}
    assert rows[env["current"][0]["ticker"]]["simfin_statement_availability"] == "STATEMENTS_IN_WINDOW"
    assert rows[env["current"][1]["ticker"]]["simfin_statement_availability"] == "UNMAPPED"
    assert rows[env["current"][2]["ticker"]]["simfin_statement_availability"] == "MAPPED_NO_STATEMENTS_IN_WINDOW"
    assert rows[env["removed"][2]["ticker"]]["simfin_statement_availability"] == "BANK_SCHEMA_EXCLUDED"


def test_25_ambiguity_and_decade(env):
    _build(env)
    rows = {r["canonical_security_id"]: r for r in _read_csv(_out(env, vp.TRIAL_SAMPLE_CSV))}
    assert rows[env["removed"][3]["ticker"]]["mapping_ambiguity"] == "one CIK maps to multiple SimFinIds"
    assert rows[env["removed"][0]["ticker"]]["removal_decade"] == "2000s"
    assert rows[env["removed"][59]["ticker"]]["removal_decade"] == "2020s"


# --------------------------------------------------------------------------- #
# static deliverables (26-31)
# --------------------------------------------------------------------------- #
def test_26_required_fields_csv(env):
    _build(env)
    rows = _read_csv(_out(env, vp.REQUIRED_FIELDS_CSV))
    assert len(rows) == 22
    assert set(rows[0].keys()) == set(vp.REQUIRED_FIELDS_COLUMNS)
    for r in rows:
        assert r["mandatory_or_optional"] in ("mandatory", "optional")
        assert r["accepted_aliases"]


def test_27_required_fields_cover_core_statements(env):
    _build(env)
    names = {r["field"] for r in _read_csv(_out(env, vp.REQUIRED_FIELDS_CSV))}
    for f in ("cik", "fiscal_period_end", "filing_publication_acceptance_date",
              "original_as_reported_vs_restated_indicator", "revenue", "cost_of_revenue",
              "net_income", "total_assets", "operating_cash_flow", "capital_expenditures",
              "sector", "sector_effective_from_date", "sector_effective_through_date"):
        assert f in names, f


def test_28_acceptance_gates_unchanged(env):
    _build(env)
    g = json.load(open(_out(env, vp.ACCEPTANCE_GATES_JSON)))["gates"]
    assert g["removed_security_identity_mapping_min"]["threshold"] == 0.75
    assert g["removed_security_usable_statements_min"]["threshold"] == 0.70
    assert g["availability_timestamp_completeness_min"]["threshold"] == 0.95
    assert g["stable_identifier_collision_rate_max"]["threshold"] == 0.0
    assert g["projected_global_member_month_coverage_min"]["threshold"] == 0.60
    assert g["removed_name_representation_min"]["threshold"] == 0.20
    assert g["material_coverage_all_decades"]["threshold"] == ["2000s", "2010s", "2020s"]


def test_29_response_template_header_only(env):
    _build(env)
    with open(_out(env, vp.RESPONSE_TEMPLATE_CSV), "r", encoding="utf-8") as fh:
        lines = [ln for ln in fh.read().splitlines() if ln.strip()]
    assert len(lines) == 1
    assert lines[0].split(",") == list(vp.RESPONSE_TEMPLATE_COLUMNS)


def test_30_scoring_md_has_sections(env):
    _build(env)
    md = open(_out(env, vp.SCORING_MD), "r", encoding="utf-8").read()
    for kw in ("Identity resolution", "Point-in-time", "Statement completeness",
               "Coverage by decade", "Factor reconstructability", "Pass / fail"):
        assert kw in md, kw


def test_31_email_is_vendor_neutral(env):
    _build(env)
    txt = open(_out(env, vp.REQUEST_EMAIL_TXT), "r", encoding="utf-8").read()
    assert txt.startswith("Subject:")
    assert "Hello,\n" in txt
    for v in ("Intrinio", "Sharadar", "Nasdaq"):
        assert v not in txt


# --------------------------------------------------------------------------- #
# manifest, determinism, verify, safety (32-40)
# --------------------------------------------------------------------------- #
def test_32_manifest_has_provenance(env):
    _build(env)
    m = json.load(open(_out(env, vp.PACKAGE_MANIFEST_JSON)))
    assert m["source_run_ids"] == {"phase30c": C30_RUN, "phase30c1": C31_RUN}
    assert m["code_commit"]
    assert m["package_content_hash"]
    assert m["sample_hash"] == SAMPLE_HASH


def test_33_manifest_source_hashes(env):
    _build(env)
    sh = json.load(open(_out(env, vp.PACKAGE_MANIFEST_JSON)))["source_hashes"]
    for k in ("phase30c_sample_manifest", "phase30c_security_master",
              "phase30c1_sample_adequacy", "simfin_companies"):
        assert sh.get(k)


def test_34_deterministic_rebuild(env):
    r1 = _build(env)
    import research_agent.family_backtest as fb
    h1 = {n: fb._file_sha256(_out(env, n)) for n in vp.DELIVERABLES}
    r2 = _build(env)
    h2 = {n: fb._file_sha256(_out(env, n)) for n in vp.DELIVERABLES}
    assert h1 == h2
    assert r1["package_content_hash"] == r2["package_content_hash"]


def test_35_verify_ok(env):
    _build(env)
    v = vp.verify_pack(env["out_root"], env["cfg"])
    assert v["ok"] is True
    assert v["sample_hash_matches_phase30c"] is True
    assert v["trial_sample_counts"] == {"removed": 60, "current": 20}


def test_36_verify_detects_tamper(env):
    _build(env)
    with open(_out(env, vp.TRIAL_SAMPLE_CSV), "a", encoding="utf-8") as fh:
        fh.write("tampered,row\n")
    v = vp.verify_pack(env["out_root"], env["cfg"])
    assert v["ok"] is False


def test_37_no_secrets_in_pack(env):
    _build(env)
    v = vp.verify_pack(env["out_root"], env["cfg"])
    assert v["no_secrets"] is True
    assert v["no_paper_trader"] is True


def test_38_secret_scanner_catches_value():
    assert vp._scan_text_for_secrets("api_key: ABC123")
    assert vp._scan_text_for_secrets("password=hunter2")
    assert not vp._scan_text_for_secrets("sha256 hash column of statement rows")


def test_39_paper_trader_scanner_catches_token():
    assert vp._scan_text_for_paper_trader("this touches operational_book here")
    assert not vp._scan_text_for_paper_trader("ordinary research prose")


def test_40_manifest_safety_contract(env):
    _build(env)
    m = json.load(open(_out(env, vp.PACKAGE_MANIFEST_JSON)))
    assert m["safety"]["research_only"] is True
    assert m["safety"]["creates_orders"] is False
    assert m["sensitive_content_scan"]["clean"] is True


# --------------------------------------------------------------------------- #
# CLI (41-43)
# --------------------------------------------------------------------------- #
def _cfg_file(tmp_path, cfg):
    p = str(tmp_path / "cfg.json")
    with open(p, "w", encoding="utf-8") as fh:
        json.dump(cfg, fh)
    return p


def test_41_cli_validate(env, tmp_path):
    p = _cfg_file(tmp_path, env["cfg"])
    assert cli.main(["vendor-eval-pack-validate", "--config", p, "--json"]) == 0


def test_42_cli_build_and_verify(env, tmp_path):
    p = _cfg_file(tmp_path, env["cfg"])
    assert cli.main(["vendor-eval-pack-build", "--config", p,
                     "--output-root", env["out_root"], "--json"]) == 0
    assert cli.main(["vendor-eval-pack-verify", "--config", p,
                     "--output-root", env["out_root"], "--json"]) == 0


def test_43_cli_report(env, tmp_path):
    p = _cfg_file(tmp_path, env["cfg"])
    cli.main(["vendor-eval-pack-build", "--config", p, "--output-root", env["out_root"]])
    assert cli.main(["vendor-eval-pack-report", "--config", p,
                     "--output-root", env["out_root"], "--json"]) == 0


# --------------------------------------------------------------------------- #
# integration on the real committed evidence (44-45; skipped if absent)
# --------------------------------------------------------------------------- #
def _real_cfg_path():
    return os.path.join(hc.fb.REPO_ROOT, "configs", "research_agent",
                        "phase30c2_vendor_evaluation_pack.json")


@pytest.mark.skipif(not os.path.isfile(
    os.path.join(hc.fb.DATA_ROOT, "research_agent", "phase30c_latest_run.json")),
    reason="real Phase 30C evidence not present")
def test_44_real_sample_hash_and_counts(tmp_path):
    cfg = vp.load_config(_real_cfg_path())
    r = vp.build_pack(cfg, output_root=str(tmp_path))
    assert r["status"] == "READY"
    assert r["sample_hash"] == "997064376ee27cda54d172b7d38697c817fe6d75a12a66dd4dc7fb4f468ede7b"
    assert r["row_counts"]["trial_sample_removed"] == 60
    assert r["row_counts"]["trial_sample_current"] == 20


@pytest.mark.skipif(not os.path.isfile(
    os.path.join(hc.fb.DATA_ROOT, "research_agent", "phase30c1_latest_run.json")),
    reason="real Phase 30C.1 evidence not present")
def test_45_real_verify_ok(tmp_path):
    cfg = vp.load_config(_real_cfg_path())
    vp.build_pack(cfg, output_root=str(tmp_path))
    v = vp.verify_pack(str(tmp_path), cfg)
    assert v["ok"] is True
    assert v["no_secrets"] and v["no_paper_trader"]
