"""
agents/agent2/model_oracle.py — Model selection knowledge base.

Recommends models for task types based on model_index.json.
Updates recommendations over time based on real task outcomes.

Usage:
    oracle = ModelOracle()
    model = oracle.recommend("coding", {"max_cost_per_1k": 0.001})
    combo = oracle.get_best_combo("write a Python web scraper")
"""
import json
import logging
from pathlib import Path

_log = logging.getLogger("orchestrator.model_oracle")

_MODEL_INDEX_PATH = Path(__file__).parent.parent.parent / "knowledge" / "model_index.json"

# Maps task types to model strength keywords (order = preference)
_TASK_STRENGTH_MAP: dict[str, list[str]] = {
    "planning":       ["reasoning", "planning", "instruction_following"],
    "coding":         ["code_generation", "reasoning"],
    "research":       ["long_context", "research", "summarization"],
    "summarization":  ["summarization", "long_context"],
    "formatting":     ["speed", "cost", "simple_tasks"],
    "review":         ["reasoning", "code_review"],
    "quick_task":     ["speed", "cost"],
    "long_context":   ["long_context"],
    "knowledge_management": ["knowledge_management", "long_context"],
}
_REQUIRED_REGISTRY_FIELDS = {
    "id",
    "provider",
    "local_only",
    "public_api",
    "allowed_for_sensitive",
    "cost_per_1k_input",
    "cost_per_1k_output",
    "context_window",
    "latency_tier",
    "reasoning_tier",
    "coding_tier",
    "trust_tier",
    "role_tags",
}


class ModelOracle:
    def __init__(self, index_path: Path = _MODEL_INDEX_PATH):
        self._path = index_path
        self._models: list[dict] = []
        self._experience: dict[str, dict] = {}  # task_type → {model → outcomes}
        self._load()

    def _load(self) -> None:
        try:
            data = json.loads(self._path.read_text())
            self._models = [self._normalize_model(m) for m in data.get("models", [])]
        except Exception as exc:
            _log.error("Failed to load model_index.json: %s", exc)
            self._models = []

    def _normalize_model(self, model: dict) -> dict:
        normalized = dict(model)
        normalized.setdefault("local_only", False)
        normalized.setdefault("public_api", True)
        normalized.setdefault("allowed_for_sensitive", False)
        normalized.setdefault("latency_tier", "medium")
        normalized.setdefault("reasoning_tier", "medium")
        normalized.setdefault("coding_tier", "medium")
        normalized.setdefault("trust_tier", "medium")
        normalized.setdefault("role_tags", [])
        normalized.setdefault("strengths", [])
        normalized.setdefault("weaknesses", [])
        normalized.setdefault("best_for", [])
        return normalized

    def list_registry(self) -> list[dict]:
        return [dict(model) for model in self._models]

    def get_registry_entry(self, model_id: str) -> dict | None:
        return next((dict(model) for model in self._models if model.get("id") == model_id), None)

    def validate_registry(self) -> list[str]:
        errors: list[str] = []
        for model in self._models:
            missing = sorted(_REQUIRED_REGISTRY_FIELDS - set(model.keys()))
            if missing:
                errors.append(f"{model.get('id', '<unknown>')}: missing {', '.join(missing)}")
        return errors

    def eligible_models(self, task_type: str, constraints: dict | None = None) -> list[dict]:
        constraints = constraints or {}
        strengths = _TASK_STRENGTH_MAP.get(task_type, ["reasoning"])
        candidates = self._models[:]

        if "allowed_for_sensitive" in constraints:
            allowed = constraints["allowed_for_sensitive"]
            candidates = [m for m in candidates if m.get("allowed_for_sensitive") == allowed]

        if "max_cost_per_1k" in constraints:
            cap = constraints["max_cost_per_1k"]
            candidates = [m for m in candidates if m.get("cost_per_1k_input", 999) <= cap]

        if "min_context" in constraints:
            min_ctx = constraints["min_context"]
            candidates = [m for m in candidates if m.get("context_window", 0) >= min_ctx]

        scored = []
        pref = constraints.get("provider_preference", "")
        for model in candidates:
            score = self._score_model(model, strengths)
            if pref and model.get("provider") == pref:
                score += 0.3
            scored.append((score, model))
        scored.sort(key=lambda item: item[0], reverse=True)
        return [dict(model) for _, model in scored]

    def build_context_packet(self, task_type: str, constraints: dict | None = None) -> dict:
        constraints = constraints or {}
        eligible = self.eligible_models(task_type, constraints)
        recommended = eligible[0]["id"] if eligible else self.recommend(task_type, constraints)
        return {
            "task_type": task_type,
            "constraints": constraints,
            "eligible_models": eligible,
            "recommended_model": recommended,
        }

    def _score_model(self, model: dict, strengths_wanted: list[str]) -> float:
        model_strengths = set(model.get("strengths", []))
        score = sum(1.0 / (i + 1) for i, s in enumerate(strengths_wanted) if s in model_strengths)
        # Boost from positive experience
        exp = self._experience.get("__any__", {}).get(model["id"], {})
        score += exp.get("success_rate", 0.0) * 0.5
        return score

    def recommend(self, task_type: str, constraints: dict | None = None) -> str:
        """
        Return the best model id for the given task type.

        constraints keys:
          max_cost_per_1k: float — filter out models more expensive than this
          min_context: int       — filter out models with smaller context window
          provider_preference: str — boost models from this provider
        """
        constraints = constraints or {}
        strengths = _TASK_STRENGTH_MAP.get(task_type, ["reasoning"])
        candidates = self.eligible_models(task_type, constraints)

        if not candidates:
            _log.warning("No candidates after filtering — returning default worker model")
            return "claude-haiku-4-5"

        chosen = candidates[0]["id"]
        _log.debug("recommend task=%s → %s", task_type, chosen)
        return chosen

    def get_best_combo(self, task_description: str) -> dict:
        """
        Heuristically pick a planner + worker set for a free-text task description.
        Returns {"planner": model, "workers": [model, ...], "reason": str}.
        """
        desc = task_description.lower()

        if any(w in desc for w in ["code", "implement", "write a function", "class", "script"]):
            task_type = "coding"
        elif any(w in desc for w in ["research", "find", "summarize", "explain"]):
            task_type = "research"
        elif any(w in desc for w in ["review", "check", "audit", "analyse"]):
            task_type = "review"
        else:
            task_type = "planning"

        planner = self.recommend("planning")
        worker = self.recommend(task_type)
        return {
            "planner": planner,
            "workers": [worker],
            "task_type": task_type,
            "reason": f"Detected task type '{task_type}' from description",
        }

    def update_from_experience(
        self, task_type: str, model: str, outcome: dict
    ) -> None:
        """
        Update model scoring based on real outcomes.
        outcome: {"success": bool, "latency_s": float, "tokens": int}
        """
        bucket = self._experience.setdefault(task_type, {})
        stats = bucket.setdefault(model, {"runs": 0, "successes": 0})
        stats["runs"] += 1
        if outcome.get("success"):
            stats["successes"] += 1
        stats["success_rate"] = stats["successes"] / stats["runs"]
        _log.debug("experience update task=%s model=%s rate=%.2f",
                   task_type, model, stats["success_rate"])
