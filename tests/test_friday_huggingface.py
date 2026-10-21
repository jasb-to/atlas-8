import json
import unittest
from unittest.mock import Mock

from research.friday_huggingface import review_decision


class FridayHuggingFaceTests(unittest.TestCase):
    def test_missing_configuration_is_safe_and_skipped(self):
        result = review_decision({}, token="", model_id="", session=Mock())
        self.assertEqual(result["status"], "not_configured")

    def test_valid_review_is_parsed_and_marked_advisory(self):
        response = Mock()
        response.raise_for_status.return_value = None
        response.json.return_value = {"choices": [{"message": {"content": json.dumps({
            "assessment": "mixed", "confidence": "low",
            "supporting_evidence": ["closed break"], "counter_evidence": [],
            "missing_evidence": ["volume"], "rationale": "Mixed evidence."
        })}}]}
        session = Mock()
        session.post.return_value = response
        result = review_decision({"state": "WATCHING"}, token="test-token", model_id="test/model", session=session)
        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["response"]["assessment"], "mixed")
        sent = session.post.call_args.kwargs["json"]
        self.assertEqual(sent["model"], "test/model")
        self.assertNotIn("test-token", str(sent))

    def test_bad_model_output_fails_soft(self):
        response = Mock()
        response.raise_for_status.return_value = None
        response.json.return_value = {"choices": [{"message": {"content": "not json"}}]}
        session = Mock()
        session.post.return_value = response
        result = review_decision({}, token="test-token", model_id="test/model", session=session)
        self.assertEqual(result["status"], "error")
        self.assertIsNone(result["response"])


if __name__ == "__main__":
    unittest.main()
