"""
Model Router — GLM-5.1 conductor that routes tasks to optimal models.

The conductor pattern:
  1. GLM-5.1 receives the task + available model catalog
  2. It decides which model to use for each subtask
  3. The router validates the choice and executes
  4. Results flow back to the conductor for next decision

Routing heuristics (used when conductor doesn't explicitly specify):
  - Planning/decomposition    → GLM-5.1 (flagship)
  - Multi-file editing        → GLM-5 or GLM-5-Turbo (strong)
  - Simple tool calls         → GLM-4.7-Flash (fast, cheapest)
  - Screenshot/UI analysis    → GLM-5V-Turbo (vision)
  - Code search/quick fixes   → GLM-4.7 (fast)
  - Image generation          → GLM-Image (image)

Fallback chain:
  If the primary model is rate-limited or fails, the router tries:
  1. Same-tier alternative (e.g. GLM-5-Turbo if GLM-5 fails)
  2. Next tier down (e.g. GLM-4.7 if GLM-5 fails)
  3. OpenRouter free tier (via existing /api/llm/stream proxy)
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any

from .models import (
    GLMModel, ModelTier, ModelCapability,
    get_model, get_models_by_tier, get_chat_models,
    MODELS,
)


class TaskCategory(str, Enum):
    PLANNING = "planning"           # Long-horizon reasoning
    CODING = "coding"               # Multi-file editing
    TOOL_CALL = "tool_call"         # Simple tool invocation
    SEARCH = "search"               # Code search, grep
    VISION = "vision"               # Screenshot/image analysis
    QUICK_FIX = "quick_fix"         # Small edits, completions
    REFACTOR = "refactor"           # Structural code changes
    DEBUG = "debug"                 # Error analysis, tracing
    REVIEW = "review"               # Code review, quality check
    CHAT = "chat"                   # General conversation
    IMAGE_GEN = "image_gen"         # Image generation
    TRANSCRIPTION = "transcription" # Audio → text


# Task category → preferred model mapping
CATEGORY_ROUTING: dict[TaskCategory, list[str]] = {
    TaskCategory.PLANNING:      ["glm-5.1"],
    TaskCategory.CODING:        ["glm-5", "glm-5-turbo", "glm-5.1"],
    TaskCategory.TOOL_CALL:     ["glm-4.7-flash", "glm-4.7-flashx", "glm-4.7"],
    TaskCategory.SEARCH:        ["glm-4.7", "glm-4.7-flash", "glm-5-turbo"],
    TaskCategory.VISION:        ["glm-5v-turbo", "glm-4.6v-flash"],
    TaskCategory.QUICK_FIX:     ["glm-4.7-flash", "glm-4.7", "glm-5-turbo"],
    TaskCategory.REFACTOR:      ["glm-5", "glm-5.1", "glm-5-turbo"],
    TaskCategory.DEBUG:         ["glm-5", "glm-5.1", "glm-5-turbo"],
    TaskCategory.REVIEW:        ["glm-5-turbo", "glm-5", "glm-4.7"],
    TaskCategory.CHAT:          ["glm-4.7-flash", "glm-4.7", "glm-5-turbo"],
    TaskCategory.IMAGE_GEN:     ["glm-image"],
    TaskCategory.TRANSCRIPTION: ["glm-asr-2512"],
}

# Fallback chain by tier
TIER_FALLBACK: dict[ModelTier, list[ModelTier]] = {
    ModelTier.FLAGSHIP: [ModelTier.STRONG, ModelTier.FAST],
    ModelTier.STRONG:   [ModelTier.FAST],
    ModelTier.FAST:     [ModelTier.STRONG],
    ModelTier.VISION:   [],
    ModelTier.AUDIO:    [],
    ModelTier.IMAGE:    [],
    ModelTier.TOOL:     [],
}


@dataclass
class RoutingDecision:
    model_id: str
    model_name: str
    tier: ModelTier
    reason: str
    fallback_from: str = ""   # Set if this is a fallback choice
    alternatives: list[str] = None  # type: ignore[assignment]

    def to_dict(self) -> dict[str, Any]:
        return {
            "model_id": self.model_id,
            "model_name": self.model_name,
            "tier": self.tier.value,
            "reason": self.reason,
            "fallback_from": self.fallback_from,
            "alternatives": self.alternatives or [],
        }


class ModelRouter:
    """Routes tasks to the optimal GLM model."""

    def __init__(self) -> None:
        self._usage_counts: dict[str, int] = {}
        self._fail_counts: dict[str, int] = {}

    def route(
        self,
        category: TaskCategory,
        preferred_model: str = "",
        exclude: list[str] | None = None,
    ) -> RoutingDecision:
        """Route a task to the best available model."""
        exclude = exclude or []

        # If conductor explicitly requested a model, use it
        if preferred_model:
            model = get_model(preferred_model)
            if model and ModelCapability.CHAT in model.capabilities:
                if preferred_model not in exclude:
                    return RoutingDecision(
                        model_id=preferred_model,
                        model_name=model.name,
                        tier=model.tier,
                        reason=f"Explicit conductor choice: {model.name}",
                    )

        # Use category-based routing
        candidates = CATEGORY_ROUTING.get(category, ["glm-4.7-flash"])
        for model_id in candidates:
            if model_id in exclude:
                continue
            model = get_model(model_id)
            if not model:
                continue
            # Check if model has failed too many times recently
            if self._fail_counts.get(model_id, 0) > 3:
                continue
            return RoutingDecision(
                model_id=model_id,
                model_name=model.name,
                tier=model.tier,
                reason=f"Category routing: {category.value} → {model.name}",
                alternatives=[m for m in candidates if m != model_id],
            )

        # Fallback: first available chat model not in exclude list
        for model in get_chat_models():
            if model.id not in exclude:
                return RoutingDecision(
                    model_id=model.id,
                    model_name=model.name,
                    tier=model.tier,
                    reason=f"Fallback: first available chat model",
                    fallback_from="all",
                )

        # Should never reach here but just in case
        return RoutingDecision(
            model_id="glm-4.7-flash",
            model_name="GLM-4.7-Flash",
            tier=ModelTier.FAST,
            reason="Emergency fallback",
            fallback_from="all",
        )

    def route_with_fallback(
        self,
        category: TaskCategory,
        preferred_model: str = "",
        failed_models: list[str] | None = None,
    ) -> RoutingDecision:
        """Route with explicit fallback from failed models."""
        exclude = failed_models or []
        decision = self.route(category, preferred_model, exclude)

        # If we got a fallback, mark it
        if decision.fallback_from:
            return decision

        # Check if primary failed and try fallback tier
        if preferred_model and preferred_model in (failed_models or []):
            model = get_model(preferred_model)
            if model:
                for fallback_tier in TIER_FALLBACK.get(model.tier, []):
                    for fb_model in get_models_by_tier(fallback_tier):
                        if fb_model.id not in exclude:
                            return RoutingDecision(
                                model_id=fb_model.id,
                                model_name=fb_model.name,
                                tier=fb_model.tier,
                                reason=f"Fallback from {model.name} to {fb_model.name}",
                                fallback_from=preferred_model,
                            )

        return decision

    def record_usage(self, model_id: str) -> None:
        self._usage_counts[model_id] = self._usage_counts.get(model_id, 0) + 1

    def record_failure(self, model_id: str) -> None:
        self._fail_counts[model_id] = self._fail_counts.get(model_id, 0) + 1

    def get_routing_stats(self) -> dict[str, Any]:
        return {
            "usage_counts": self._usage_counts,
            "fail_counts": self._fail_counts,
        }

    @staticmethod
    def get_catalog() -> list[dict[str, Any]]:
        """Full model catalog for the conductor to choose from."""
        from .models import model_list_api
        return model_list_api()

