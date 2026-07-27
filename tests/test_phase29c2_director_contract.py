"""Phase 29C.2 — director-contract hardening exposed by the first live run.

Two small contract gaps surfaced in Phase 29C.1:

1. The live director repeatedly proposed hypotheses with TWO independent
   terminal features (a disguised sweep) that the executor rejected
   structurally (feature_execution.py one-terminal rule). The Phase 29B
   prompt never stated that rule, so the model could not have known. These
   tests pin that the generated provider request now states it explicitly.

2. A full live plan/feedback request needs ~270-300s while the CLI defaulted
   to 180s. These tests pin the bounded, transport-only
   ``--provider-timeout-seconds`` override: valid values reach the provider's
   subprocess timeout, out-of-range values fail clearly, and the override
   never touches budgets, gates, prompts, tools, safety, or the data cutoff
   (the fixed argument vector and shell=False are unchanged).
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..")))

from research_agent import director_provider as dp  # noqa: E402
from research_agent.director_provider import (  # noqa: E402
    DEFAULT_TIMEOUT_SECONDS,
    PROVIDER_TIMEOUT_MAX_SECONDS,
    PROVIDER_TIMEOUT_MIN_SECONDS,
    ProviderError,
    get_provider,
    validate_transport_timeout,
)
from research_agent.prompt_templates import (  # noqa: E402
    DIRECTOR_SYSTEM_CONTRACT,
    RESPONSE_CONTRACT,
    build_director_request,
)

MINIMAL_PACK = {"evidence_pack_id": "ep_contract_test", "research_budget": {}}
MINIMAL_CONFIG = {
    "objective": "raise the monthly rank-IC t-stat",
    "primary_target": {"metric": "rank_ic_t"},
    "secondary_constraints": ["research only"],
}


class TestOneTerminalRuleInRequest(unittest.TestCase):
    def setUp(self):
        self.request = build_director_request(MINIMAL_PACK, MINIMAL_CONFIG)
        # collapse the contract's line-wrapping so multi-word phrase checks are
        # robust to where the source wraps a rule across lines
        self.contract = " ".join(
            self.request["system_contract"].split()).lower()

    def test_request_carries_the_system_contract(self):
        self.assertEqual(self.request["system_contract"],
                         DIRECTOR_SYSTEM_CONTRACT)

    def test_contract_states_exactly_one_terminal_feature(self):
        self.assertIn("exactly one terminal", self.contract)

    def test_contract_requires_separate_hypotheses_for_two_variants(self):
        self.assertIn("separate hypotheses", self.contract)

    def test_contract_states_structural_rejection_before_evaluation(self):
        self.assertIn("rejected structurally", self.contract)
        self.assertIn("before any", self.contract)

    def test_contract_states_two_experiments_per_hypothesis(self):
        self.assertIn("at most two experiments per hypothesis", self.contract)

    def test_contract_allows_internal_components_feeding_one_output(self):
        self.assertIn("internal component features are allowed", self.contract)

    def test_response_contract_hint_reinforces_one_terminal(self):
        hint = RESPONSE_CONTRACT["proposals"][0]["proposed_feature"]["features"]
        self.assertIn("exactly one terminal", " ".join(hint).lower())


class TestTransportTimeoutValidator(unittest.TestCase):
    def test_none_is_passthrough(self):
        self.assertIsNone(validate_transport_timeout(None))

    def test_typical_live_override_accepted(self):
        self.assertEqual(validate_transport_timeout(540), 540)

    def test_inclusive_lower_and_upper_bounds_accepted(self):
        self.assertEqual(validate_transport_timeout(PROVIDER_TIMEOUT_MIN_SECONDS),
                         PROVIDER_TIMEOUT_MIN_SECONDS)
        self.assertEqual(validate_transport_timeout(PROVIDER_TIMEOUT_MAX_SECONDS),
                         PROVIDER_TIMEOUT_MAX_SECONDS)

    def test_below_range_rejected_clearly(self):
        with self.assertRaises(ProviderError) as ctx:
            validate_transport_timeout(PROVIDER_TIMEOUT_MIN_SECONDS - 1)
        self.assertIn("outside the allowed bounded range", str(ctx.exception))

    def test_above_range_rejected(self):
        with self.assertRaises(ProviderError):
            validate_transport_timeout(PROVIDER_TIMEOUT_MAX_SECONDS + 1)

    def test_non_integer_rejected(self):
        with self.assertRaises(ProviderError):
            validate_transport_timeout("540")

    def test_bool_is_not_a_valid_timeout(self):
        with self.assertRaises(ProviderError):
            validate_transport_timeout(True)


class TestGetProviderTimeout(unittest.TestCase):
    def test_default_timeout_when_unset(self):
        prov = get_provider("claude-code")
        self.assertEqual(prov.timeout_seconds, DEFAULT_TIMEOUT_SECONDS)

    def test_config_timeout_applies(self):
        prov = get_provider("claude-code", director_config={
            "provider": {"claude_cli": {"timeout_seconds": 300}}})
        self.assertEqual(prov.timeout_seconds, 300)

    def test_cli_override_wins_over_config(self):
        prov = get_provider("claude-code", director_config={
            "provider": {"claude_cli": {"timeout_seconds": 300}}},
            timeout_override=540)
        self.assertEqual(prov.timeout_seconds, 540)

    def test_out_of_range_override_rejected(self):
        with self.assertRaises(ProviderError):
            get_provider("claude-code", timeout_override=5)


class TestTimeoutIsTransportOnly(unittest.TestCase):
    def test_fixed_argument_vector_has_no_timeout_token(self):
        # the override never enters the argv: argv is a module constant
        self.assertEqual(dp.CLAUDE_PLAN_ARGS, ("-p", "--output-format", "json"))
        for tok in dp.CLAUDE_PLAN_ARGS:
            self.assertNotIn("timeout", tok.lower())
            self.assertNotIn(str(540), tok)

    def test_override_changes_only_the_stored_transport_timeout(self):
        base = get_provider("claude-code", timeout_override=540)
        other = get_provider("claude-code", timeout_override=300)
        self.assertEqual(base.name, other.name)
        self.assertNotEqual(base.timeout_seconds, other.timeout_seconds)

    def test_override_does_not_change_the_request_document(self):
        # the request is built independently of any provider/timeout
        req_a = build_director_request(MINIMAL_PACK, MINIMAL_CONFIG)
        req_b = build_director_request(MINIMAL_PACK, MINIMAL_CONFIG)
        self.assertEqual(req_a, req_b)
        self.assertNotIn("timeout", req_a)
        self.assertNotIn("provider_timeout_seconds", req_a)


if __name__ == "__main__":
    unittest.main()
