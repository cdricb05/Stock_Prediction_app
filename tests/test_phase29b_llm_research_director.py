"""Phase 29B — LLM research director and bounded feature-hypothesis agent.

Covers the Part K contract (60 checks):
  Evidence pack   (1-6)
  Providers       (7-13)
  Proposal schema (14-20)
  Feature DSL     (21-32)
  Director policy (33-40)
  Structured memory (41-45)
  Safety          (46-52)
  CLI             (53-60)

Fully offline: the deterministic synthetic Phase 29A world runs one real
COMPLETE campaign in a tmp root; the director layer then works exclusively
from that persisted evidence with the FixtureDirectorProvider. The live
Claude CLI is only exercised through monkeypatched subprocess seams. The
Paper Trader desk directory is only ever os.stat'ed.
"""

import copy
import json
import os
import subprocess
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from test_phase29a_research_agent_kernel import (  # noqa: E402
    CUTOFF,
    TODAY,
    _build_world,
    _snapshot_dir,
    make_config,
)

from research_agent import SAFETY_CONTRACT  # noqa: E402
from research_agent import cli  # noqa: E402
from research_agent import controller as ctl  # noqa: E402
from research_agent import director as dr  # noqa: E402
from research_agent import director_provider as dp  # noqa: E402
from research_agent import family_backtest as fb  # noqa: E402
from research_agent import feature_dsl as dsl  # noqa: E402
from research_agent.artifact_store import (  # noqa: E402
    ArtifactStore,
    ImmutableArtifactError,
    find_secret_keys,
    read_jsonl,
)
from research_agent.evidence_pack import (  # noqa: E402
    EvidencePackError,
    build_evidence_pack,
)
from research_agent.prompt_templates import build_director_request  # noqa: E402

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DIRECTOR_CONFIG_PATH = os.path.join(
    REPO, "configs", "research_agent", "phase29b_research_director.json"
)
DESK_DIR = os.path.join(os.path.expanduser("~"), ".paper_trader",
                        "paper_trading_desk")

AVAILABLE = ["adv_dollar", "composite_sn", "eligible", "is_member",
             "mom_6_1", "sector"]


def _src(field):
    return {"op": "source", "field": field}


def _read_bytes(path):
    with open(path, "rb") as fh:
        return fh.read()


# --------------------------------------------------------------------------- #
# fixtures
# --------------------------------------------------------------------------- #
@pytest.fixture(scope="module")
def guard_snapshots():
    """Taken BEFORE any campaign runs; compared by the safety tests."""
    return {"desk": _snapshot_dir(DESK_DIR)}


@pytest.fixture(scope="module")
def director_cfg():
    with open(DIRECTOR_CONFIG_PATH, "r", encoding="utf-8") as fh:
        return json.load(fh)


@pytest.fixture(scope="module")
def completed(tmp_path_factory, guard_snapshots):
    """One real COMPLETE synthetic campaign in a tmp artifact root."""
    root = tmp_path_factory.mktemp("agent29b_campaign")
    world = _build_world()
    inputs = fb.load_family_inputs(
        data_cutoff=CUTOFF,
        mom_monthly=world["mom_monthly"],
        fund_monthly=world["fund_monthly"],
        sector_map={},
        spy_close=world["spy_close"],
    )
    created = ctl.create_campaign(make_config(), artifact_root=str(root),
                                  today=TODAY)
    assert created["created"], created
    cid = created["campaign_id"]
    controller = ctl.CampaignController(
        cid, artifact_root=str(root), today=TODAY, inputs=inputs,
        reference_rows=[], close_frame=world["close_frame"],
    )
    result = controller.run()
    assert result.get("final_state") == "COMPLETE", result
    store = ArtifactStore(str(root))
    # Whatever the deterministic CONTROLLER registered during the campaign is
    # the frozen reference; the director layer must never add to it.
    registry_rows = read_jsonl(store.challenger_registry_path(cid))
    return {"root": str(root), "campaign_id": cid, "store": store,
            "registry_rows": registry_rows}


@pytest.fixture(scope="module")
def pack(completed, director_cfg):
    return build_evidence_pack(
        completed["store"], completed["campaign_id"],
        director_config=director_cfg,
    )


@pytest.fixture(scope="module")
def request_doc(pack, director_cfg):
    return build_director_request(pack, director_cfg)


@pytest.fixture(scope="module")
def fixture_response(request_doc):
    return dp.build_fixture_response(request_doc, n_proposals=3)


def _policy(director_cfg, pack, **kw):
    return dr.DirectorPolicy(director_cfg, pack, **kw)


@pytest.fixture(scope="module")
def session_flow(tmp_path_factory, completed, director_cfg):
    """API-driven fixture session + one resume, with byte snapshots."""
    out_root = tmp_path_factory.mktemp("agent29b_director")
    provider = dp.get_provider("fixture", director_config=director_cfg)
    first = dr.run_director_session(
        artifact_root=completed["root"], campaign_id=completed["campaign_id"],
        director_config=director_cfg, provider=provider,
        output_root=str(out_root),
    )
    dstore = dr.DirectorStore(str(out_root))
    sdir = dstore.session_dir(first["session_id"])
    snap1 = {
        "proposals_bytes": _read_bytes(sdir / "proposals.jsonl"),
        "events_bytes": _read_bytes(sdir / "events.jsonl"),
        "rows": dstore.read_proposals(first["session_id"]),
    }
    second = dr.run_director_session(
        artifact_root=completed["root"], campaign_id=completed["campaign_id"],
        director_config=director_cfg, provider=provider,
        output_root=str(out_root),
    )
    snap2 = {
        "proposals_bytes": _read_bytes(sdir / "proposals.jsonl"),
        "events_bytes": _read_bytes(sdir / "events.jsonl"),
        "rows": dstore.read_proposals(first["session_id"]),
    }
    return {"out_root": str(out_root), "dstore": dstore,
            "first": first, "second": second,
            "snap1": snap1, "snap2": snap2,
            "session_id": first["session_id"]}


@pytest.fixture(scope="module")
def cli_flow(tmp_path_factory, completed):
    """CLI-driven director run in its own output root."""
    out_root = str(tmp_path_factory.mktemp("agent29b_cli"))
    rc_ev = cli.main([
        "director-evidence", "--campaign-id", completed["campaign_id"],
        "--artifact-root", completed["root"], "--output-root", out_root,
        "--config", DIRECTOR_CONFIG_PATH, "--json",
    ])
    rc_plan = cli.main([
        "director-plan", "--campaign-id", completed["campaign_id"],
        "--artifact-root", completed["root"],
        "--config", DIRECTOR_CONFIG_PATH,
        "--provider", "fixture", "--output-root", out_root, "--json",
    ])
    dstore = dr.DirectorStore(out_root)
    sessions = dstore.list_sessions()
    return {"out_root": out_root, "rc_evidence": rc_ev, "rc_plan": rc_plan,
            "dstore": dstore, "sessions": sessions,
            "session_id": sessions[0] if sessions else None}


def _variant_proposal(base_response, hid, window):
    """A distinct, valid proposal derived from the fixture momentum one."""
    p = copy.deepcopy(base_response["proposals"][0])
    p["hypothesis_id"] = hid
    feat = p["proposed_feature"]["features"][0]
    feat["feature_id"] = "feat_" + hid
    feat["expression"]["params"]["window"] = window
    p["duplicate_search_signature"] = dsl.expression_signature(feat)
    return p


def _make_decision(decision, hid=None):
    return {
        "decision": decision,
        "hypothesis_id": hid,
        "evidence_refs": ["binding_weaknesses.rank_ic"],
        "reasoning_summary": "bounded deterministic follow-up",
        "uncertainties": ["sample length"],
        "falsification_condition": "no rank-IC improvement on full sample",
        "next_deterministic_action": "validate DSL and queue bounded cells",
        "budget_impact": {"estimated_primary_experiments": 2},
        "safety_confirmation": {
            "research_only": True, "no_operational_action": True,
            "no_gate_change": True, "no_budget_increase": True,
        },
    }


# =========================================================================== #
# Evidence pack (1-6)
# =========================================================================== #
class TestEvidencePack:
    def test_1_completed_campaign_produces_pack(self, completed, pack,
                                                director_cfg):
        assert pack["evidence_pack_id"].startswith("ep_")
        assert pack["record_type"] == "EVIDENCE_PACK"
        assert pack["content_hash"]
        assert pack["available_features"]["momentum_sources"]
        assert pack["research_budget"] == director_cfg["budgets"]
        # a non-COMPLETE campaign can never produce a pack
        created = ctl.create_campaign(
            make_config(), artifact_root=completed["root"], today=TODAY)
        assert created["created"]
        with pytest.raises(EvidencePackError):
            build_evidence_pack(completed["store"], created["campaign_id"],
                                director_config=director_cfg)

    def test_2_pack_content_hash_deterministic(self, completed, director_cfg,
                                               pack):
        again = build_evidence_pack(
            completed["store"], completed["campaign_id"],
            director_config=director_cfg)
        assert again == pack
        assert again["content_hash"] == pack["content_hash"]
        assert again["evidence_pack_id"] == pack["evidence_pack_id"]

    def test_3_source_campaign_and_commit_preserved(self, completed, pack):
        manifest = completed["store"].read_manifest(completed["campaign_id"])
        assert pack["source_campaign"] == completed["campaign_id"]
        assert pack["code_commit"] == manifest["code_commit"]
        assert pack["config_hash"] == manifest["config_hash"]

    def test_4_exhausted_dimensions_included(self, pack):
        sigs = set(pack["exhausted_signatures"])
        assert "portfolio:blend_weights" in sigs
        assert "portfolio:blend_family_combined" in sigs
        # config-supplied exhausted axes are merged in
        assert "portfolio:phase29a3_combined_grid" in sigs
        assert "portfolio:top25_vs_top50_alone" in sigs

    def test_5_binding_rank_ic_weakness_included(self, pack):
        bw = pack["binding_weaknesses"]
        assert "rank_ic" in bw
        assert bw["rank_ic"]["baseline_rank_ic_t"] is not None
        assert "rank-IC" in bw["rank_ic"]["diagnosis"]

    def test_6_operational_data_not_copied(self, pack):
        assert pack["operational_state_excluded"] is True
        assert pack["active_operational_model"]["identity_only"] is True
        text = json.dumps(pack)
        for forbidden_key in ('"holdings"', '"cash"', '"positions"',
                              '"marks"', '"nav"', '"pnl"'):
            assert forbidden_key not in text
        assert find_secret_keys(pack) == []


# =========================================================================== #
# Providers (7-13)
# =========================================================================== #
class TestProviders:
    def test_7_fixture_provider_deterministic(self, request_doc, director_cfg):
        p1 = dp.get_provider("fixture", director_config=director_cfg)
        p2 = dp.get_provider("fixture", director_config=director_cfg)
        e1 = p1.generate_research_plan(request_doc)
        e2 = p2.generate_research_plan(request_doc)
        assert e1 == e2
        assert e1["status"] == dp.STATUS_OK
        assert e1["fixture"] is True
        assert len(e1["response"]["proposals"]) == 3

    def test_8_file_exchange_writes_only_sanitized_data(self, tmp_path,
                                                        request_doc,
                                                        fixture_response):
        provider = dp.FileExchangeDirectorProvider(str(tmp_path))
        first = provider.generate_research_plan(request_doc)
        assert first["status"] == dp.STATUS_AWAITING_MANUAL_RESPONSE
        req_path = first["request_path"]
        assert os.path.exists(req_path)
        with open(req_path, "r", encoding="utf-8") as fh:
            on_disk = json.load(fh)
        assert find_secret_keys(on_disk) == []
        assert on_disk["evidence_pack"]["operational_state_excluded"] is True
        assert '"holdings"' not in json.dumps(on_disk)
        with open(first["response_path"], "w", encoding="utf-8") as fh:
            json.dump(fixture_response, fh)
        second = provider.generate_research_plan(request_doc)
        assert second["status"] == dp.STATUS_OK
        assert second["response"] == fixture_response

    def test_9_claude_unavailable_is_structured(self, monkeypatch,
                                                request_doc):
        monkeypatch.setattr(dp.shutil, "which", lambda _name: None)
        provider = dp.ClaudeCodeDirectorProvider()
        avail = provider.check_availability()
        assert avail == {
            "provider": "claude-code",
            "available": False,
            "status": dp.STATUS_PROVIDER_UNAVAILABLE,
            "checked_executable": "claude",
            "reason": avail["reason"],
        }
        envelope = provider.generate_research_plan(request_doc)
        assert envelope["status"] == dp.STATUS_PROVIDER_UNAVAILABLE
        assert envelope["response"] is None

    def test_10_provider_execution_uses_shell_false(self, monkeypatch,
                                                    request_doc,
                                                    fixture_response):
        calls = []
        fake_exe = os.path.join("C:", os.sep, "fake", "claude.exe")

        def fake_run(argv, **kwargs):
            calls.append({"argv": argv, "kwargs": kwargs})

            class R:
                returncode = 0
                stderr = ""
                stdout = (
                    "0.0.0 (fake)" if "--version" in argv
                    else json.dumps({"result": json.dumps(fixture_response)})
                )
            return R()

        monkeypatch.setattr(dp.shutil, "which", lambda _n: fake_exe)
        monkeypatch.setattr(dp.subprocess, "run", fake_run)
        provider = dp.ClaudeCodeDirectorProvider(timeout_seconds=45)
        envelope = provider.generate_research_plan(request_doc)
        assert envelope["status"] == dp.STATUS_OK
        assert envelope["response"] == fixture_response
        assert len(calls) >= 2
        for c in calls:
            assert c["kwargs"].get("shell") is False
            assert c["argv"][0] == fake_exe
        plan_call = calls[-1]
        assert tuple(plan_call["argv"][1:]) == dp.CLAUDE_PLAN_ARGS

    def test_11_provider_timeout_enforced(self, monkeypatch, request_doc):
        fake_exe = "claude-fake"
        seen = {}

        def fake_run(argv, **kwargs):
            if "--version" in argv:
                class R:
                    returncode = 0
                    stdout = "0.0.0"
                    stderr = ""
                return R()
            seen["timeout"] = kwargs.get("timeout")
            raise subprocess.TimeoutExpired(cmd=argv, timeout=kwargs.get("timeout"))

        monkeypatch.setattr(dp.shutil, "which", lambda _n: fake_exe)
        monkeypatch.setattr(dp.subprocess, "run", fake_run)
        provider = dp.ClaudeCodeDirectorProvider(timeout_seconds=7)
        envelope = provider.generate_research_plan(request_doc)
        assert envelope["status"] == dp.STATUS_PROVIDER_ERROR
        assert envelope["timeout"] is True
        assert seen["timeout"] == 7

    def test_12_secrets_are_not_logged(self, monkeypatch, request_doc,
                                       fixture_response):
        planted = "planted-secret-value-xyz-29b"
        monkeypatch.setenv("PHASE29B_PLANTED_TOKEN", planted)

        def fake_run(argv, **kwargs):
            class R:
                returncode = 0
                stderr = ""
                stdout = (
                    "0.0.0" if "--version" in argv
                    else json.dumps({"result": json.dumps(fixture_response)})
                )
            return R()

        monkeypatch.setattr(dp.shutil, "which", lambda _n: "claude-fake")
        monkeypatch.setattr(dp.subprocess, "run", fake_run)
        provider = dp.ClaudeCodeDirectorProvider()
        envelope = provider.generate_research_plan(request_doc)
        assert planted not in json.dumps(envelope)
        assert planted not in json.dumps(request_doc)
        assert find_secret_keys(request_doc) == []
        assert find_secret_keys(envelope) == []

    def test_13_non_json_output_rejected(self, monkeypatch, request_doc):
        outputs = iter(["this is definitely not json",
                        json.dumps({"result": "still not { json"})])

        def fake_run(argv, **kwargs):
            class R:
                returncode = 0
                stderr = ""
                stdout = "0.0.0" if "--version" in argv else next(outputs)
            return R()

        monkeypatch.setattr(dp.shutil, "which", lambda _n: "claude-fake")
        monkeypatch.setattr(dp.subprocess, "run", fake_run)
        provider = dp.ClaudeCodeDirectorProvider()
        for _ in range(2):
            envelope = provider.generate_research_plan(request_doc)
            assert envelope["status"] == dp.STATUS_INVALID_RESPONSE
            assert envelope["response"] is None


# =========================================================================== #
# Proposal schema (14-20)
# =========================================================================== #
def _validate(proposal, **kw):
    args = dict(available_fields=AVAILABLE, max_feature_depth=3,
                max_interactions=4, max_primary_experiments=24, strict=True)
    args.update(kw)
    return dr.validate_proposal(proposal, **args)


class TestProposalSchema:
    def test_14_valid_proposal_accepted(self, fixture_response):
        for proposal in fixture_response["proposals"]:
            verdict = _validate(proposal)
            assert verdict["status"] is None, verdict["reasons"]
            assert verdict["signature"].startswith("dslset:")

    def test_15_unknown_fields_rejected_in_strict_mode(self, fixture_response):
        p = copy.deepcopy(fixture_response["proposals"][0])
        p["surprise_field"] = "hello"
        verdict = _validate(p)
        assert verdict["status"] == "REJECTED_SCHEMA"
        assert any("surprise_field" in r for r in verdict["reasons"])

    def test_16_missing_falsification_condition_rejected(self,
                                                         fixture_response):
        p = copy.deepcopy(fixture_response["proposals"][0])
        del p["falsification_condition"]
        verdict = _validate(p)
        assert verdict["status"] == "REJECTED_SCHEMA"
        assert any("falsification_condition" in r for r in verdict["reasons"])

    def test_17_missing_leakage_analysis_rejected(self, fixture_response):
        p = copy.deepcopy(fixture_response["proposals"][0])
        del p["leakage_analysis"]
        verdict = _validate(p)
        assert verdict["status"] == "REJECTED_SCHEMA"
        assert any("leakage_analysis" in r for r in verdict["reasons"])

    def test_18_operational_action_request_rejected(self, fixture_response):
        p = copy.deepcopy(fixture_response["proposals"][0])
        p["required_tools"] = ["register_shadow_challenger"]
        verdict = _validate(p)
        assert verdict["status"] == "REJECTED_SCHEMA"
        assert any("operational action" in r for r in verdict["reasons"])
        for decision_name in ("CREATE_ORDER", "EXECUTE_TRADE",
                              "CHANGE_ACTIVE_MODEL", "OPERATIONAL"):
            verdict = dr.validate_decision(_make_decision(decision_name))
            assert verdict["accepted"] is False
            assert any(v["severity"] == "SAFETY" for v in verdict["violations"])

    def test_19_gate_lowering_request_rejected(self, director_cfg, pack,
                                               fixture_response):
        verdict = dr.validate_decision(_make_decision("LOWER_GATE"))
        assert verdict["accepted"] is False
        response = copy.deepcopy(fixture_response)
        response["gate_overrides"] = {"rank_ic": 0.5}
        outcome = _policy(director_cfg, pack).process_response(response)
        assert outcome["response_valid"] is False
        assert any(v["field"] == "gate_overrides"
                   and v["severity"] == "SAFETY"
                   for v in outcome["response_violations"])
        assert outcome["proposals"] == []

    def test_20_budget_increase_rejected(self, director_cfg, pack,
                                         fixture_response):
        response = copy.deepcopy(fixture_response)
        response["budgets"] = {"max_proposed_hypotheses": 999}
        outcome = _policy(director_cfg, pack).process_response(response)
        assert outcome["response_valid"] is False
        assert any(v["field"] == "budgets" and v["severity"] == "SAFETY"
                   for v in outcome["response_violations"])
        # the operator config cannot raise budgets past the ceiling either
        raised = copy.deepcopy(director_cfg)
        raised["budgets"]["max_proposed_hypotheses"] = 13
        verdict = dr.validate_director_config(raised)
        assert verdict["accepted"] is False
        assert any("ceiling" in v["issue"] for v in verdict["violations"])


# =========================================================================== #
# Feature DSL (21-32)
# =========================================================================== #
def _feat(expression, fid="feat_t", family="momentum"):
    return {"feature_id": fid, "description": "test feature",
            "source_family": family, "expression": expression}


def _vspec(expression, **kw):
    return dsl.validate_feature_spec(_feat(expression),
                                     available_fields=AVAILABLE, **kw)


class TestFeatureDSL:
    def test_21_valid_lagged_transformation_accepted(self):
        v = _vspec({"op": "lag", "params": {"periods": 1},
                    "inputs": [_src("mom_6_1")]})
        assert v["accepted"], v["violations"]
        assert v["depth"] == 1
        assert v["signature"].startswith("dsl:")

    def test_22_valid_rolling_transformation_accepted(self):
        v = _vspec({"op": "rolling_mean", "params": {"window": 6},
                    "inputs": [_src("composite_sn")]})
        assert v["accepted"], v["violations"]

    def test_23_valid_bounded_interaction_accepted(self):
        v = _vspec({
            "op": "interaction", "params": {},
            "inputs": [
                {"op": "zscore", "params": {"window": 12},
                 "inputs": [_src("mom_6_1")]},
                {"op": "zscore", "params": {"window": 12},
                 "inputs": [_src("composite_sn")]},
            ]})
        assert v["accepted"], v["violations"]
        assert v["interactions"] == 1
        v2 = _vspec({
            "op": "bounded_weighted_average",
            "params": {"weights": [0.5, 0.5]},
            "inputs": [_src("mom_6_1"), _src("composite_sn")]})
        assert v2["accepted"], v2["violations"]

    def test_24_unknown_source_feature_rejected(self):
        v = _vspec({"op": "lag", "params": {"periods": 1},
                    "inputs": [_src("revenue_surprise")]})
        assert not v["accepted"]
        assert any(x["severity"] == "UNSUPPORTED" for x in v["violations"])
        # file paths can never be sources
        v2 = _vspec({"op": "lag", "params": {"periods": 1},
                     "inputs": [_src("C:\\data\\secret_panel.csv")]})
        assert not v2["accepted"]

    def test_25_negative_lag_rejected(self):
        v = _vspec({"op": "lag", "params": {"periods": -1},
                    "inputs": [_src("mom_6_1")]})
        assert not v["accepted"]
        assert v["leakage_violations"]

    def test_26_forward_shift_rejected(self):
        v = _vspec({"op": "forward_shift", "params": {"periods": 1},
                    "inputs": [_src("mom_6_1")]})
        assert not v["accepted"]
        assert v["leakage_violations"]
        # target fields are same-period leakage
        v2 = _vspec({"op": "lag", "params": {"periods": 1},
                     "inputs": [_src("fwd_1m")]})
        assert not v2["accepted"]
        assert v2["leakage_violations"]

    def test_27_centered_window_rejected(self):
        v = _vspec({"op": "rolling_mean",
                    "params": {"window": 6, "centered": True},
                    "inputs": [_src("mom_6_1")]})
        assert not v["accepted"]
        assert v["leakage_violations"]

    def test_28_arbitrary_python_rejected(self):
        v = _vspec("df['mom_6_1'].shift(-1)")
        assert not v["accepted"]
        assert any("arbitrary" in x["issue"] for x in v["violations"])
        v2 = _vspec({"op": "python", "params": {},
                     "inputs": [_src("mom_6_1")]})
        assert not v2["accepted"]
        assert any("executable content" in x["issue"] for x in v2["violations"])

    def test_29_shell_command_rejected(self):
        v = _vspec({"op": "shell", "params": {},
                    "inputs": [_src("mom_6_1")]})
        assert not v["accepted"]
        assert any("executable content" in x["issue"] for x in v["violations"])
        spec = _feat({"op": "lag", "params": {"periods": 1},
                      "inputs": [_src("mom_6_1")]})
        spec["shell_command"] = "del /f /q *"
        v2 = dsl.validate_feature_spec(spec, available_fields=AVAILABLE)
        assert not v2["accepted"]

    def test_30_cyclic_dependency_rejected(self):
        f_a = _feat({"op": "lag", "params": {"periods": 1},
                     "inputs": [{"op": "feature_ref", "feature_id": "feat_b"}]},
                    fid="feat_a")
        f_b = _feat({"op": "lag", "params": {"periods": 1},
                     "inputs": [{"op": "feature_ref", "feature_id": "feat_a"}]},
                    fid="feat_b")
        v = dsl.validate_feature_set([f_a, f_b], available_fields=AVAILABLE)
        assert not v["accepted"]
        assert any("cyclic" in x["issue"] for x in v["violations"])

    def test_31_excessive_depth_rejected(self):
        deep = _src("mom_6_1")
        for _ in range(4):
            deep = {"op": "rolling_mean", "params": {"window": 3},
                    "inputs": [deep]}
        v = _vspec(deep)
        assert not v["accepted"]
        assert any("excessive feature depth" in x["issue"]
                   for x in v["violations"])

    def test_32_excessive_interaction_count_rejected(self):
        def inter(a, b):
            return {"op": "interaction", "params": {}, "inputs": [a, b]}

        tree = inter(
            inter(inter(_src("mom_6_1"), _src("composite_sn")),
                  inter(_src("adv_dollar"), _src("mom_6_1"))),
            inter(_src("composite_sn"), _src("adv_dollar")),
        )
        v = _vspec(tree)
        assert not v["accepted"]
        assert any("excessive interaction count" in x["issue"]
                   for x in v["violations"])


# =========================================================================== #
# Director policy (33-40)
# =========================================================================== #
class TestDirectorPolicy:
    def test_33_exhausted_blend_hypothesis_rejected_duplicate(
            self, director_cfg, pack, fixture_response):
        response = copy.deepcopy(fixture_response)
        blend = response["proposals"][0]
        blend["hypothesis_id"] = "hyp_retry_blend_grid"
        blend["duplicate_search_signature"] = "portfolio:blend_weights"
        outcome = _policy(director_cfg, pack).process_response(response)
        row = next(r for r in outcome["proposals"]
                   if r["hypothesis_id"] == "hyp_retry_blend_grid")
        assert row["status"] == "REJECTED_DUPLICATE"
        assert "exhausted" in row["reasons"][0]
        assert any(g["reason"] == "duplicate_of_exhausted_branch"
                   for g in outcome["graveyard_candidates"])

    def test_34_feature_level_hypothesis_may_be_queued(
            self, director_cfg, pack, fixture_response):
        outcome = _policy(director_cfg, pack).process_response(fixture_response)
        assert outcome["response_valid"] is True
        statuses = {r["hypothesis_id"]: r["status"]
                    for r in outcome["proposals"]}
        assert statuses["hyp_29b_momentum_persistence"] == "QUEUED"
        assert statuses["hyp_29b_fundamental_change"] == "QUEUED"

    def test_35_cheap_ablation_precedes_interaction(
            self, director_cfg, pack, fixture_response):
        outcome = _policy(director_cfg, pack).process_response(fixture_response)
        ranked = sorted(
            (r for r in outcome["proposals"]
             if r.get("information_gain_rank")),
            key=lambda r: r["information_gain_rank"],
        )
        names = [r["hypothesis_id"] for r in ranked]
        assert names[-1] == "hyp_29b_quality_momentum_interaction"
        assert names[0] in ("hyp_29b_momentum_persistence",
                            "hyp_29b_fundamental_change")
        assert outcome["queued"][-1] == "hyp_29b_quality_momentum_interaction"

    def test_36_duplicate_signatures_are_idempotent(
            self, director_cfg, pack, fixture_response):
        feat = fixture_response["proposals"][0]["proposed_feature"]["features"][0]
        shuffled = copy.deepcopy(feat)
        expr = shuffled["expression"]
        shuffled["expression"] = {"inputs": expr["inputs"],
                                  "params": dict(expr["params"]),
                                  "op": expr["op"]}
        assert dsl.expression_signature(feat) == dsl.expression_signature(shuffled)
        first = _policy(director_cfg, pack).process_response(fixture_response)
        prior = [r["signature"] for r in first["proposals"] if r["signature"]]
        second = _policy(director_cfg, pack,
                         prior_signatures=prior).process_response(fixture_response)
        assert all(r["status"] == "REJECTED_DUPLICATE"
                   for r in second["proposals"])

    def test_37_graveyard_prevents_repeated_failed_branches(
            self, director_cfg, pack, fixture_response):
        sig = dsl.feature_set_signature(
            fixture_response["proposals"][0]["proposed_feature"]["features"])
        graveyard = [{"signature": sig, "reason": "repeated_failure"}]
        outcome = _policy(director_cfg, pack,
                          graveyard=graveyard).process_response(fixture_response)
        row = outcome["proposals"][0]
        assert row["status"] == "REJECTED_DUPLICATE"
        assert "graveyard" in row["reasons"][0]

    def test_38_max_proposed_hypothesis_count_enforced(
            self, director_cfg, pack, fixture_response):
        response = copy.deepcopy(fixture_response)
        response["proposals"] = [
            _variant_proposal(fixture_response, "hyp_var_%02d" % i, 3 + i)
            for i in range(13)
        ]
        response["decisions"] = []
        outcome = _policy(director_cfg, pack).process_response(response)
        assert len(outcome["proposals"]) == 13
        overflow = [r for r in outcome["proposals"] if r["index"] >= 12]
        assert overflow and all(r["status"] == "REJECTED_SCHEMA"
                                for r in overflow)
        assert all("max_proposed_hypotheses" in r["reasons"][0]
                   for r in overflow)

    def test_39_max_accepted_hypothesis_count_enforced(
            self, director_cfg, pack, fixture_response):
        cfg = copy.deepcopy(director_cfg)
        cfg["budgets"]["max_accepted_hypotheses"] = 2
        response = copy.deepcopy(fixture_response)
        response["proposals"] = [
            _variant_proposal(fixture_response, "hyp_acc_%02d" % i, 3 + i)
            for i in range(5)
        ]
        response["decisions"] = []
        outcome = _policy(cfg, pack).process_response(response)
        statuses = [r["status"] for r in outcome["proposals"]]
        assert statuses.count("QUEUED") == 2
        assert statuses.count("VALIDATED") == 3
        assert len(outcome["queued"]) == 2

    def test_40_information_gain_ranking_deterministic(
            self, director_cfg, pack, fixture_response):
        orders = []
        for perm in ([0, 1, 2], [2, 0, 1], [1, 2, 0]):
            response = copy.deepcopy(fixture_response)
            response["proposals"] = [response["proposals"][i] for i in perm]
            outcome = _policy(director_cfg, pack).process_response(response)
            orders.append(outcome["queued"])
        assert orders[0] == orders[1] == orders[2]


# =========================================================================== #
# Structured memory and graveyard (41-45)
# =========================================================================== #
class TestDirectorMemory:
    def test_41_director_session_is_append_only(self, session_flow):
        s1, s2 = session_flow["snap1"], session_flow["snap2"]
        assert s2["proposals_bytes"] == s1["proposals_bytes"]
        assert s2["events_bytes"].startswith(s1["events_bytes"])
        chain = session_flow["dstore"].verify_session_chain(
            session_flow["session_id"])
        assert chain["intact"] is True

    def test_42_evidence_pack_remains_immutable(self, session_flow, pack):
        dstore = session_flow["dstore"]
        assert dstore.save_evidence_pack(pack) == pack["evidence_pack_id"]
        mutated = dict(pack)
        mutated["data_cutoff"] = "1999-12-31"
        with pytest.raises(ImmutableArtifactError):
            dstore.save_evidence_pack(mutated)
        on_disk = dstore.load_evidence_pack(pack["evidence_pack_id"])
        assert on_disk == pack

    def test_43_rejected_hypotheses_are_preserved(self, tmp_path, completed,
                                                  director_cfg,
                                                  fixture_response):
        response = copy.deepcopy(fixture_response)
        bad = copy.deepcopy(response["proposals"][0])
        bad["hypothesis_id"] = "hyp_bad_no_falsification"
        del bad["falsification_condition"]
        response["proposals"].append(bad)
        provider = dp.FixtureDirectorProvider(fixture_response=response)
        out_root = str(tmp_path)
        result = dr.run_director_session(
            artifact_root=completed["root"],
            campaign_id=completed["campaign_id"],
            director_config=director_cfg, provider=provider,
            output_root=out_root)
        dstore = dr.DirectorStore(out_root)
        rows = dstore.read_proposals(result["session_id"])
        rejected = [r for r in rows if r["status"] == "REJECTED_SCHEMA"]
        assert rejected and rejected[0]["hypothesis_id"] == \
            "hyp_bad_no_falsification"
        again = dr.run_director_session(
            artifact_root=completed["root"],
            campaign_id=completed["campaign_id"],
            director_config=director_cfg, provider=provider,
            output_root=out_root)
        assert again["resumed"] is True
        assert dstore.read_proposals(result["session_id"]) == rows

    def test_44_branch_stop_decisions_persist(self, tmp_path, completed,
                                              director_cfg, fixture_response):
        response = copy.deepcopy(fixture_response)
        response["decisions"] = list(response["decisions"]) + [
            _make_decision("STOP_BRANCH", "hyp_29b_momentum_persistence")]
        provider = dp.FixtureDirectorProvider(fixture_response=response)
        out_root = str(tmp_path)
        result = dr.run_director_session(
            artifact_root=completed["root"],
            campaign_id=completed["campaign_id"],
            director_config=director_cfg, provider=provider,
            output_root=out_root)
        dstore = dr.DirectorStore(out_root)
        decisions = dstore.read_decisions(result["session_id"])
        stops = [d for d in decisions if d["decision"] == "STOP_BRANCH"]
        assert len(stops) == 1 and stops[0]["accepted"] is True
        dr.run_director_session(
            artifact_root=completed["root"],
            campaign_id=completed["campaign_id"],
            director_config=director_cfg, provider=provider,
            output_root=out_root)
        assert dstore.read_decisions(result["session_id"]) == decisions

    def test_45_resume_does_not_duplicate_proposals(self, session_flow):
        assert session_flow["second"]["resumed"] is True
        assert session_flow["second"]["counts"] == \
            session_flow["first"]["counts"]
        rows = session_flow["snap2"]["rows"]
        ids = [r["hypothesis_id"] for r in rows]
        assert len(ids) == len(set(ids)) == 3


# =========================================================================== #
# Safety (46-52)
# =========================================================================== #
class TestSafety:
    def test_46_no_paper_trader_write_occurs(self, guard_snapshots,
                                             session_flow, cli_flow):
        assert _snapshot_dir(DESK_DIR) == guard_snapshots["desk"]

    def test_47_no_orders_are_created(self, session_flow):
        assert session_flow["first"]["safety"]["creates_orders"] is False
        text = json.dumps(session_flow["snap2"]["rows"])
        assert '"order_id"' not in text

    def test_48_no_broker_execution_occurs(self, session_flow):
        status = dr.build_session_status(session_flow["dstore"],
                                         session_flow["session_id"])
        assert status["safety"]["broker_execution"] is False

    def test_49_no_trading_automation_enabled(self, session_flow):
        status = dr.build_session_status(session_flow["dstore"],
                                         session_flow["session_id"])
        assert status["safety"]["automation_of_trading"] is False
        assert status["safety"]["research_only"] is True

    def test_50_operational_model_unchanged(self, completed, pack,
                                            session_flow):
        manifest = completed["store"].read_manifest(completed["campaign_id"])
        assert manifest["baseline_model"] == \
            pack["active_operational_model"]["model_id"]
        assert pack["active_operational_model"]["identity_only"] is True
        assert manifest["safety"]["operational_model_changed"] is False

    def test_51_fixture_provider_cannot_register_challenger(
            self, completed, session_flow):
        assert dp.FixtureDirectorProvider.may_register_challengers is False
        with pytest.raises(dr.DirectorSafetyError):
            dr.register_challenger(candidate_id="cand_x", stage="SHADOW_ELIGIBLE")
        # the fixture-driven director sessions added NOTHING to the campaign's
        # challenger registry: it is byte-for-byte what the deterministic
        # controller left behind
        registry_path = completed["store"].challenger_registry_path(
            completed["campaign_id"])
        assert read_jsonl(registry_path) == completed["registry_rows"]

    def test_52_director_cannot_activate_shadow_model(self):
        for decision_name in ("SHADOW_ACTIVE", "PROMOTION_CANDIDATE"):
            verdict = dr.validate_decision(_make_decision(decision_name))
            assert verdict["accepted"] is False
            assert any(v["severity"] == "SAFETY"
                       for v in verdict["violations"])
        assert "SHADOW_ACTIVE" not in dr.DIRECTOR_DECISIONS
        assert "SHADOW_ACTIVE" in dr.FORBIDDEN_DIRECTOR_DECISIONS


# =========================================================================== #
# CLI (53-60)
# =========================================================================== #
class TestCLI:
    def test_53_provider_check_returns_structured_output(self, capsys):
        rc = cli.main(["provider-check", "--provider", "claude-code", "--json"])
        assert rc == 0
        doc = json.loads(capsys.readouterr().out)
        assert doc["provider"] == "claude-code"
        assert isinstance(doc["available"], bool)
        assert doc["status"] in (dp.STATUS_OK, dp.STATUS_PROVIDER_UNAVAILABLE)
        rc = cli.main(["provider-check", "--provider", "no-such", "--json"])
        assert rc == 2

    def test_54_director_validate_exit_codes(self, tmp_path, capsys,
                                             director_cfg):
        rc = cli.main(["director-validate", "--config", DIRECTOR_CONFIG_PATH,
                       "--json"])
        assert rc == 0
        assert json.loads(capsys.readouterr().out)["accepted"] is True
        broken = copy.deepcopy(director_cfg)
        broken["strict_mode"] = False
        bad_path = tmp_path / "bad_director.json"
        bad_path.write_text(json.dumps(broken), encoding="utf-8")
        rc = cli.main(["director-validate", "--config", str(bad_path),
                       "--json"])
        assert rc == 2

    def test_55_director_evidence_writes_pack(self, cli_flow, capsys, pack):
        assert cli_flow["rc_evidence"] == 0
        path = dr.DirectorStore(cli_flow["out_root"]).evidence_pack_path(
            pack["evidence_pack_id"])
        assert path.exists()
        on_disk = json.loads(path.read_text(encoding="utf-8"))
        assert on_disk["content_hash"] == pack["content_hash"]

    def test_56_director_plan_with_fixture_creates_session(self, cli_flow):
        assert cli_flow["rc_plan"] == 0
        assert cli_flow["session_id"] is not None
        manifest = cli_flow["dstore"].read_session_manifest(
            cli_flow["session_id"])
        assert manifest["session_state"] == "COMPLETE"
        assert manifest["provider"] == "fixture"
        assert manifest["safety"] == SAFETY_CONTRACT

    def test_57_director_status_returns_reconciled_counts(self, cli_flow,
                                                          capsys):
        rc = cli.main(["director-status", "--session-id",
                       cli_flow["session_id"], "--output-root",
                       cli_flow["out_root"], "--json"])
        assert rc == 0
        doc = json.loads(capsys.readouterr().out)
        recon = doc["reconciliation"]
        assert recon["recorded_equals_counted"] is True
        assert recon["queued_within_budget"] is True
        assert recon["proposals_recorded"] == 3
        assert doc["event_chain"]["intact"] is True

    def test_58_director_report_contains_hypotheses_and_safety(self, cli_flow,
                                                               capsys):
        rc = cli.main(["director-report", "--session-id",
                       cli_flow["session_id"], "--output-root",
                       cli_flow["out_root"], "--json"])
        assert rc == 0
        doc = json.loads(capsys.readouterr().out)
        report = doc["report"]
        assert len(report["hypotheses"]) == 3
        assert report["safety"] == SAFETY_CONTRACT
        for path in doc["artifact_paths"]:
            assert os.path.exists(path)

    def test_59_invalid_campaign_id_fails_clearly(self, cli_flow, completed,
                                                  capsys):
        rc = cli.main(["director-evidence", "--campaign-id",
                       "no_such_campaign", "--artifact-root",
                       completed["root"], "--output-root",
                       cli_flow["out_root"], "--json"])
        assert rc == 3
        assert "unknown campaign id" in capsys.readouterr().err

    def test_60_invalid_session_id_fails_clearly(self, cli_flow, capsys):
        rc = cli.main(["director-status", "--session-id", "ds_nonexistent",
                       "--output-root", cli_flow["out_root"], "--json"])
        assert rc == 3
        assert "unknown session_id" in capsys.readouterr().err
