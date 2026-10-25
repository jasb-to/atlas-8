import json
import unittest
from unittest.mock import Mock, patch

import httpx

from research.friday_ai_review import review_decision


VALID = {
    "assessment": "mixed",
    "priority_candidate": None,
    "supporting_evidence": ["weekly aligned"],
    "counter_evidence": [],
    "missing_evidence": [],
    "rationale": "Mixed evidence.",
}


class FridayAIReviewTests(unittest.TestCase):
    def test_disabled_review_makes_no_network_calls(self):
        client = Mock()
        with patch.dict("os.environ", {"FRIDAY_AI_REVIEW_ENABLED": "false"}, clear=False):
            result = review_decision({"state": "WATCHING"}, client=client)
        self.assertEqual(result["status"], "disabled")
        client.post.assert_not_called()

    def test_groq_is_called_directly_without_proxy_or_oidc(self):
        response = Mock()
        response.raise_for_status.return_value = None
        response.json.return_value = {"choices": [{"message": {"content": json.dumps(VALID)}}]}
        client = Mock()
        client.post.return_value = response
        env = {"FRIDAY_AI_REVIEW_ENABLED": "true", "GROQ_API_KEY": "gsk-test", "GEMINI_API_KEY": "gem-test"}
        with patch.dict("os.environ", env, clear=False):
            result = review_decision({"state": "WATCHING"}, client=client)
        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["provider"], "groq")
        self.assertEqual(client.post.call_args.args[0], "https://api.groq.com/openai/v1/chat/completions")
        self.assertNotIn("vercel", client.post.call_args.args[0])
        self.assertNotIn("HUGGINGFACE", str(client.post.call_args.kwargs).upper())

    def test_gemini_fallback_after_groq_failure(self):
        failed = Mock()
        failed.raise_for_status.side_effect = httpx.HTTPStatusError(
            "Groq unavailable", request=Mock(), response=Mock()
        )
        good = Mock()
        good.raise_for_status.return_value = None
        good.json.return_value = {"candidates": [{"content": {"parts": [{"text": json.dumps(VALID)}]}}]}
        client = Mock()
        client.post.side_effect = [failed, good]
        env = {"FRIDAY_AI_REVIEW_ENABLED": "true", "GROQ_API_KEY": "gsk-test", "GEMINI_API_KEY": "gem-test"}
        with patch.dict("os.environ", env, clear=False):
            result = review_decision({"state": "WATCHING"}, client=client)
        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["provider"], "gemini")
        self.assertEqual(client.post.call_count, 2)
        self.assertIn("generativelanguage.googleapis.com", client.post.call_args.args[0])

    def test_all_provider_failures_fail_soft(self):
        client = Mock()
        client.post.side_effect = RuntimeError("provider offline")
        env = {"FRIDAY_AI_REVIEW_ENABLED": "true", "GROQ_API_KEY": "gsk-test", "GEMINI_API_KEY": "gem-test"}
        with patch.dict("os.environ", env, clear=False):
            result = review_decision({}, client=client)
        self.assertEqual(result["status"], "error")
        self.assertEqual(result["error_category"], "all_providers_failed")
        self.assertIsNone(result["response"])


if __name__ == "__main__":
    unittest.main()
