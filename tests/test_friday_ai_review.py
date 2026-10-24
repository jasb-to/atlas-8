import unittest
from unittest.mock import Mock, patch

from research.friday_ai_review import review_decision


class FridayAIReviewTests(unittest.TestCase):
    def test_missing_oidc_configuration_fails_soft(self):
        session = Mock()
        with patch.dict(
            "os.environ",
            {"ACTIONS_ID_TOKEN_REQUEST_URL": "", "ACTIONS_ID_TOKEN_REQUEST_TOKEN": ""},
            clear=False,
        ):
            result = review_decision({}, session=session)
        self.assertEqual(result["status"], "not_configured")
        session.get.assert_not_called()
        session.post.assert_not_called()

    def test_proxy_uses_actual_gemini_or_groq_provider_metadata(self):
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
            "FRIDAY_AI_REVIEW_URL": "https://example.test/api/friday/ai-review",
            "ACTIONS_ID_TOKEN_REQUEST_URL": "https://actions.example.test/oidc",
            "ACTIONS_ID_TOKEN_REQUEST_TOKEN": "test-request-token",
        }
        with patch.dict("os.environ", env, clear=False):
            result = review_decision({"state": "WATCHING"}, session=session)
        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["provider"], "gemini")
        self.assertEqual(result["model_id"], "gemini-3.5-flash-lite")
        self.assertEqual(result["latency_ms"], 1234)
        self.assertIn("audience=friday-ai-review", session.get.call_args.args[0])
        sent = session.post.call_args.kwargs["json"]
        self.assertEqual(sent, {"decision": {"state": "WATCHING"}})
        self.assertNotIn("test-request-token", str(sent))

    def test_proxy_error_preserves_provider_and_category(self):
        oidc_response = Mock()
        oidc_response.raise_for_status.return_value = None
        oidc_response.json.return_value = {"value": "test-oidc-token"}
        proxy_response = Mock()
        proxy_response.ok = False
        proxy_response.status_code = 502
        proxy_response.text = "upstream error"
        proxy_response.json.return_value = {
            "status": "error",
            "provider": "groq",
            "model_id": "openai/gpt-oss-120b",
            "error_category": "provider_unavailable",
            "latency_ms": 9876,
            "error": "Groq returned HTTP 503",
        }
        session = Mock()
        session.get.return_value = oidc_response
        session.post.return_value = proxy_response
        with patch.dict(
            "os.environ",
            {
                "FRIDAY_AI_REVIEW_URL": "https://example.test/api/friday/ai-review",
                "ACTIONS_ID_TOKEN_REQUEST_URL": "https://actions.example.test/oidc",
                "ACTIONS_ID_TOKEN_REQUEST_TOKEN": "test-request-token",
            },
            clear=False,
        ):
            result = review_decision({}, session=session)
        self.assertEqual(result["status"], "error")
        self.assertEqual(result["provider"], "groq")
        self.assertEqual(result["model_id"], "openai/gpt-oss-120b")
        self.assertEqual(result["error_category"], "provider_unavailable")
        self.assertEqual(result["latency_ms"], 9876)
        self.assertIn("HTTP 502", result["error"])

    def test_invalid_proxy_payload_fails_soft(self):
        oidc_response = Mock()
        oidc_response.raise_for_status.return_value = None
        oidc_response.json.return_value = {"value": "test-oidc-token"}
        proxy_response = Mock()
        proxy_response.ok = True
        proxy_response.json.return_value = {"status": "ok", "response": {"assessment": "not-valid"}}
        session = Mock()
        session.get.return_value = oidc_response
        session.post.return_value = proxy_response
        with patch.dict(
            "os.environ",
            {
                "ACTIONS_ID_TOKEN_REQUEST_URL": "https://actions.example.test/oidc",
                "ACTIONS_ID_TOKEN_REQUEST_TOKEN": "test-request-token",
            },
            clear=False,
        ):
            result = review_decision({}, session=session)
        self.assertEqual(result["status"], "error")
        self.assertIsNone(result["response"])


if __name__ == "__main__":
    unittest.main()
