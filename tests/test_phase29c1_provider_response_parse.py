"""Phase 29C.1 — live-provider response transport parsing.

First live campaign finding: the real claude-code provider produced a fully
valid feedback-decision JSON object but prefixed it with a short prose
paragraph, and ``_parse_json_only`` (whole-string ``json.loads``) refused the
reply as INVALID_RESPONSE even though the payload itself was strict JSON
(campaign phase29c_feature_campaign_20260726T223314Z, request
fbreq_ff38b383d4675532).

These tests pin the hardened transport contract: the SAME single strict JSON
object is accepted when surrounded by prose or markdown fences, while
non-JSON replies, bare scalars, and JSON arrays are still refused. Only the
wrapper tolerance changes — every downstream response validation (strict
schema, contract-protected fields, safety confirmations, secret scan) is
unchanged and still applies to the extracted object.
"""
import json
import os
import sys
import unittest

sys.path.insert(0, os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..")))

from research_agent.director_provider import (  # noqa: E402
    ClaudeCodeDirectorProvider,
)

PARSE = ClaudeCodeDirectorProvider._parse_json_only

PAYLOAD = {
    "schema_version": "29C.1",
    "provider": "claude-code",
    "feedback_decisions": [
        {"decision": "STOP_BRANCH", "hypothesis_id": "hyp_x_v1"},
    ],
    "notes": "no gate or budget change requested",
}


def envelope(result_text):
    """Wrap a model reply the way `claude -p --output-format json` does."""
    return json.dumps({
        "type": "result",
        "subtype": "success",
        "is_error": False,
        "num_turns": 12,
        "result": result_text,
    })


class TestProviderResponseParse(unittest.TestCase):
    def test_strict_json_result_still_parses(self):
        got = PARSE(envelope(json.dumps(PAYLOAD, sort_keys=True)))
        self.assertEqual(got, PAYLOAD)

    def test_prose_prefixed_json_object_parses(self):
        # exact live failure shape: prose paragraph, blank line, JSON object
        reply = (
            "All contract requirements are confirmed: the two rejected "
            "hypotheses each defined two terminal features "
            "(feature_execution.py:857). I therefore revise both branches.\n\n"
            + json.dumps(PAYLOAD, sort_keys=True)
        )
        self.assertEqual(PARSE(envelope(reply)), PAYLOAD)

    def test_prose_suffixed_and_fenced_json_object_parses(self):
        fenced = "Decisions follow.\n```json\n%s\n```\nEnd of reply." % (
            json.dumps(PAYLOAD, sort_keys=True))
        self.assertEqual(PARSE(envelope(fenced)), PAYLOAD)

    def test_largest_object_wins_over_incidental_braces(self):
        # prose that itself contains a small brace blob must not shadow the
        # real payload
        reply = 'note: params were {"window": 3} as proposed.\n\n%s' % (
            json.dumps(PAYLOAD, sort_keys=True))
        self.assertEqual(PARSE(envelope(reply)), PAYLOAD)

    def test_pure_prose_is_still_refused(self):
        self.assertIsNone(PARSE(envelope("I could not produce decisions.")))

    def test_json_array_result_is_still_refused(self):
        self.assertIsNone(PARSE(envelope(json.dumps([PAYLOAD]))))

    def test_non_json_stdout_without_envelope_is_refused(self):
        self.assertIsNone(PARSE("plain text, no envelope, no object"))

    def test_direct_json_object_stdout_still_parses(self):
        # a provider printing the payload directly (no claude envelope)
        self.assertEqual(PARSE(json.dumps(PAYLOAD, sort_keys=True)), PAYLOAD)


if __name__ == "__main__":
    unittest.main()
