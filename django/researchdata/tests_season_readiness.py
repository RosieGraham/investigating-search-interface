"""
Failing contract: query-free release readiness fails closed.
"""

from django.test import SimpleTestCase, TestCase


class ReleaseReadinessTests(TestCase):
    def test_readiness_endpoint_exists_and_is_query_free(self):
        resp = self.client.get("/data/release/ready/")
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertIn("ready", body)
        self.assertEqual(resp.get("Cache-Control"), "no-store")
        self.assertNotIn("canaries", body)
        self.assertNotIn("user_search_query", str(body))

    def test_evaluate_readiness_helper_exists(self):
        try:
            from researchdata.release_readiness import evaluate_readiness
        except ImportError as exc:
            self.fail(f"evaluate_readiness is required: {exc}")
        result = evaluate_readiness()
        self.assertIn("ready", result)
        self.assertIn("failures", result)


class ReleaseReadinessFailureInjectionTests(SimpleTestCase):
    def test_missing_model_is_not_ready(self):
        from researchdata.release_readiness import evaluate_readiness

        result = evaluate_readiness(
            model_path="/tmp/isi-missing-model.onnx",
            effective_config={
                "threshold": 0.35,
                "margin": 0.00,
                "trigger_fallback": False,
                "placeholders": False,
                "research_writes_enabled": False,
            },
        )
        self.assertFalse(result["ready"])
        self.assertTrue(result["failures"])

    def test_wrong_config_is_not_ready(self):
        from researchdata.release_readiness import evaluate_readiness

        result = evaluate_readiness(effective_config={
            "threshold": 0.40,
            "margin": 0.00,
            "trigger_fallback": False,
            "placeholders": False,
            "research_writes_enabled": False,
        })
        self.assertFalse(result["ready"])

    def test_research_writes_enabled_is_not_ready(self):
        from researchdata.release_readiness import evaluate_readiness

        result = evaluate_readiness(effective_config={
            "threshold": 0.35,
            "margin": 0.00,
            "trigger_fallback": False,
            "placeholders": False,
            "research_writes_enabled": True,
        })
        self.assertFalse(result["ready"])

    def test_inverted_readiness_must_not_ignore_broken_dependency(self):
        from researchdata.release_readiness import evaluate_readiness

        result = evaluate_readiness(
            model_ok=False,
            ignore_model=True,
            effective_config={
                "threshold": 0.35,
                "margin": 0.00,
                "trigger_fallback": False,
                "placeholders": False,
                "research_writes_enabled": False,
            },
        )
        self.assertFalse(
            result["ready"],
            "planted fault: readiness must stay red when the model check is broken or ignored",
        )
