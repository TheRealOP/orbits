import unittest

from orchestrator.agents.agent2.model_oracle import ModelOracle


class TestModelOracle(unittest.TestCase):
    def setUp(self):
        self.oracle = ModelOracle()

    def test_registry_entries_have_required_fields(self):
        self.assertEqual(self.oracle.validate_registry(), [])

    def test_can_lookup_registry_entry(self):
        entry = self.oracle.get_registry_entry("gpt-5.4")
        self.assertIsNotNone(entry)
        self.assertEqual(entry["provider"], "openai")
        self.assertIn("executor", entry["role_tags"])

    def test_build_context_packet_returns_eligible_models(self):
        packet = self.oracle.build_context_packet("coding", {"provider_preference": "google"})
        self.assertEqual(packet["task_type"], "coding")
        self.assertTrue(packet["eligible_models"])
        self.assertIn("recommended_model", packet)

    def test_eligible_models_can_filter_sensitive_models(self):
        models = self.oracle.eligible_models("coding", {"allowed_for_sensitive": True})
        self.assertTrue(models)
        self.assertTrue(all(model["allowed_for_sensitive"] for model in models))


if __name__ == "__main__":
    unittest.main()
