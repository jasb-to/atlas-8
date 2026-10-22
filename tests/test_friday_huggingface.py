import json
import unittest
from unittest.mock import Mock, patch

from research.friday_huggingface import DEFAULT_ENDPOINT, DEFAULT_MODEL_ID, review_decision


class FridayHuggingFaceTests(unittest.TestCase):
    def test_missing_token_is_safe_and_skipped(self):
        with patch.dict("os.environ", {"HUGGINGFACE_API_KEY": "", "HF_TOKEN": "", "FRIDAY_HF_MODEL_ID": ""}, clear=False):
            result = review_decision({}, token="", model_id="", session=Mock())
        self.assertEqual(result["status"], "not_configured")

    def test_free_account_default_configuration_is_available_without_model_vars(self):
        self.assertEqual(DEFAULT_ENDPOINT, "https://router.huggingface.co/v1/chat/completions")
        self.assertEqual(DEFAULT_MODEL_ID, "openai/gpt-oss-120b:cheapest")
        response = Mock()
        response.raise_for_status.return_value = None
        response.json.return_value = {"choices": [{"message": {"content": json.dumps({
            "assessment": "mixed", "confidence": "low",
            "supporting_evidence": ["closed break"], "counter_evidence": [],
            "missing_evidence": ["volume"], "rationale": "Mixed evidence."
        })}}]}
        session = Mock()
        session.post.return_value = response
        with patch.dict("os.environ", {"FRIDAY_HF_MODEL_ID": "", "FRIDAY_HF_ENDPOINT": ""}, clear=False):
            result = review_decision({"state": "WATCHING"}, token="test-token", session=session)
        self.assertEqual(result["status"], "ok")
        self.assertEqual(session.post.call_args.args[0], DEFAULT_ENDPOINT)
        self.assertEqual(session.post.call_args.kwargs["json"]["model"], DEFAULT_MODEL_ID)

    def test_valid_review_is_parsed_and_advisory(self):
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
