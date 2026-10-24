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

    def test_proxy_uses_actual_provider_and_model_metadata(self):
        oidc_response = Mock()
        oidc_response.raise_for_status.return_value = None
        oidc_response.json.return_value = {"value": "test-oidc-token"}
        proxy_response = Mock()
        proxy_response.ok = True
        proxy_response.json.return_value = {
            "status": "ok",
            "provider": "gemini",
            "model_id": "gemini-3.5-flash-lite",
            "latency_ms": 1234,
            "response": {
                "assessment": "mixed",
                "priority_candidate": None,
                "confidence": "low",
                "supporting_evidence": [],
                "counter_evidence": [],
                "missing_evidence": [],
                "rationale": "Mixed evidence.",
            },
        }
        session = Mock()
        session.get.return_value = oidc_response
        session.post.return_value = proxy_response
        env = {
            "HUGGINGFACE_API_KEY": "",
            "HF_TOKEN": "",
            "FRIDAY_AI_REVIEW_URL": "https://example.test/api/friday/ai-review",
            "ACTIONS_ID_TOKEN_REQUEST_URL": "https://actions.example.test/oidc",
            "ACTIONS_ID_TOKEN_REQUEST_TOKEN": "test-request-token",
        }
        with patch.dict("os.environ", env, clear=False):
            result = review_decision({}, session=session)
        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["provider"], "gemini")
        self.assertEqual(result["model_id"], "gemini-3.5-flash-lite")
        self.assertEqual(result["latency_ms"], 1234)

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
