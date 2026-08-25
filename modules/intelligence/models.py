"""
Z.ai GLM Model Registry — complete catalog of all GLM models.

OpenAI-compatible endpoint: api.z.ai/api/coding/paas/v4/chat/completions
All models use the same Chat Completions API shape.
Coding Plan subscribers get unlimited usage across all models.

Model tiers (for routing):
 旗舰 (Flagship):  GLM-5.1 — long-horizon reasoning, orchestration, 200K ctx
 强 (Strong):       GLM-5, GLM-5-Turbo — coding, multi-file editing
 快 (Fast):         GLM-4.7, GLM-4.7-Flash, GLM-4.7-FlashX — tool calls, subagents
 视觉 (Vision):     GLM-5V-Turbo, GLM-4.6V, GLM-4.6V-Flash — image/screenshot analysis
 语音 (Audio):      GLM-ASR-2512 — transcription
 图像 (Image):      GLM-Image, CogView-4 — image generation
 工具 (Tool):       GLM-OCR — document parsing
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any


class ModelTier(str, Enum):
    FLAGSHIP = "flagship"   # GLM-5.1 — conductor
    STRONG = "strong"       # GLM-5, GLM-5-Turbo — coding
    FAST = "fast"           # GLM-4.7 series — tool calls
    VISION = "vision"       # VLM models — image understanding
    AUDIO = "audio"         # ASR — transcription
    IMAGE = "image"         # Image generation
    TOOL = "tool"           # OCR, parsing


class ModelCapability(str, Enum):
    CHAT = "chat"
    CODE = "code"
    TOOL_USE = "tool_use"
    VISION = "vision"
    STREAMING = "streaming"
    THINKING = "thinking"
    STRUCTURED_OUTPUT = "structured_output"
    FUNCTION_CALLING = "function_calling"


@dataclass(frozen=True)
class GLMModel:
    id: str                          # API model ID (e.g. "glm-5.1")
    name: str                        # Display name
    tier: ModelTier
    ctx_in: int                      # Max input context tokens
    ctx_out: int                     # Max output tokens
    capabilities: tuple[ModelCapability, ...]
    description: str = ""
    thinking_modes: tuple[str, ...] = ()   # e.g. ("interleaved", "preserved", "turn-level")


# ---------------------------------------------------------------------------
# Full GLM catalog
# ---------------------------------------------------------------------------

MODELS: dict[str, GLMModel] = {
    # ── Flagship ──────────────────────────────────────────────────────
    "glm-5.1": GLMModel(
        id="glm-5.1",
        name="GLM-5.1",
        tier=ModelTier.FLAGSHIP,
        ctx_in=200_000,
        ctx_out=65_536,
        capabilities=(ModelCapability.CHAT, ModelCapability.CODE, ModelCapability.TOOL_USE,
                      ModelCapability.THINKING, ModelCapability.STRUCTURED_OUTPUT,
                      ModelCapability.FUNCTION_CALLING),
        description="Flagship reasoning model. Long-horizon planning, orchestration, autonomous sessions.",
        thinking_modes=("interleaved", "preserved", "turn-level"),
    ),

    # ── Strong ────────────────────────────────────────────────────────
    "glm-5": GLMModel(
        id="glm-5",
        name="GLM-5",
        tier=ModelTier.STRONG,
        ctx_in=204_000,
        ctx_out=131_072,
        capabilities=(ModelCapability.CHAT, ModelCapability.CODE, ModelCapability.TOOL_USE,
                      ModelCapability.THINKING, ModelCapability.FUNCTION_CALLING),
        description="Strong coding model. Multi-file editing, sustained engineering.",
        thinking_modes=("interleaved", "preserved", "turn-level"),
    ),
    "glm-5-turbo": GLMModel(
        id="glm-5-turbo",
        name="GLM-5-Turbo",
        tier=ModelTier.STRONG,
        ctx_in=204_000,
        ctx_out=131_072,
        capabilities=(ModelCapability.CHAT, ModelCapability.CODE, ModelCapability.TOOL_USE,
                      ModelCapability.THINKING, ModelCapability.FUNCTION_CALLING),
        description="Faster GLM-5 variant. Same quality, lower latency.",
        thinking_modes=("interleaved", "preserved"),
    ),

    # ── Fast ──────────────────────────────────────────────────────────
    "glm-4.7": GLMModel(
        id="glm-4.7",
        name="GLM-4.7",
        tier=ModelTier.FAST,
        ctx_in=203_000,
        ctx_out=16_384,
        capabilities=(ModelCapability.CHAT, ModelCapability.CODE, ModelCapability.TOOL_USE,
                      ModelCapability.FUNCTION_CALLING),
        description="Balanced fast model. Tool calls, completions.",
    ),
    "glm-4.7-flash": GLMModel(
        id="glm-4.7-flash",
        name="GLM-4.7-Flash",
        tier=ModelTier.FAST,
        ctx_in=203_000,
        ctx_out=16_384,
        capabilities=(ModelCapability.CHAT, ModelCapability.CODE, ModelCapability.TOOL_USE,
                      ModelCapability.FUNCTION_CALLING),
        description="Fastest tool-call model. Subagent dispatch, simple completions.",
    ),
    "glm-4.7-flashx": GLMModel(
        id="glm-4.7-flashx",
        name="GLM-4.7-FlashX",
        tier=ModelTier.FAST,
        ctx_in=203_000,
        ctx_out=16_384,
        capabilities=(ModelCapability.CHAT, ModelCapability.CODE, ModelCapability.TOOL_USE,
                      ModelCapability.FUNCTION_CALLING),
        description="Ultra-fast Flash variant. Maximum throughput.",
    ),

    # ── Legacy Fast ───────────────────────────────────────────────────
    "glm-4.6": GLMModel(
        id="glm-4.6",
        name="GLM-4.6",
        tier=ModelTier.FAST,
        ctx_in=128_000,
        ctx_out=16_384,
        capabilities=(ModelCapability.CHAT, ModelCapability.CODE, ModelCapability.FUNCTION_CALLING),
        description="Previous-gen fast model. Still solid for simple tasks.",
    ),
    "glm-4.5": GLMModel(
        id="glm-4.5",
        name="GLM-4.5",
        tier=ModelTier.FAST,
        ctx_in=128_000,
        ctx_out=16_384,
        capabilities=(ModelCapability.CHAT, ModelCapability.CODE, ModelCapability.FUNCTION_CALLING),
        description="Legacy fast model.",
    ),
    "glm-4.5-air": GLMModel(
        id="glm-4.5-air",
        name="GLM-4.5-Air",
        tier=ModelTier.FAST,
        ctx_in=128_000,
        ctx_out=8_192,
        capabilities=(ModelCapability.CHAT, ModelCapability.CODE),
        description="Lightweight legacy model.",
    ),
    "glm-4.5-airx": GLMModel(
        id="glm-4.5-airx",
        name="GLM-4.5-AirX",
        tier=ModelTier.FAST,
        ctx_in=128_000,
        ctx_out=8_192,
        capabilities=(ModelCapability.CHAT, ModelCapability.CODE),
        description="Extended lightweight legacy model.",
    ),
    "glm-4.5-flash": GLMModel(
        id="glm-4.5-flash",
        name="GLM-4.5-Flash",
        tier=ModelTier.FAST,
        ctx_in=128_000,
        ctx_out=8_192,
        capabilities=(ModelCapability.CHAT, ModelCapability.CODE),
        description="Flash legacy model.",
    ),
    "glm-4.5-x": GLMModel(
        id="glm-4.5-x",
        name="GLM-4.5-X",
        tier=ModelTier.FAST,
        ctx_in=128_000,
        ctx_out=16_384,
        capabilities=(ModelCapability.CHAT, ModelCapability.CODE, ModelCapability.FUNCTION_CALLING),
        description="Extended legacy model.",
    ),
    "glm-4-32b-0414-128k": GLMModel(
        id="glm-4-32b-0414-128k",
        name="GLM-4-32B-0414-128K",
        tier=ModelTier.FAST,
        ctx_in=128_000,
        ctx_out=8_192,
        capabilities=(ModelCapability.CHAT, ModelCapability.CODE),
        description="Open-weight 32B model, 128K context.",
    ),

    # ── Vision ────────────────────────────────────────────────────────
    "glm-5v-turbo": GLMModel(
        id="glm-5v-turbo",
        name="GLM-5V-Turbo",
        tier=ModelTier.VISION,
        ctx_in=128_000,
        ctx_out=16_384,
        capabilities=(ModelCapability.VISION, ModelCapability.CHAT, ModelCapability.TOOL_USE),
        description="Vision model. Screenshot analysis, UI debugging, document reading.",
    ),
    "glm-4.6v": GLMModel(
        id="glm-4.6v",
        name="GLM-4.6V",
        tier=ModelTier.VISION,
        ctx_in=128_000,
        ctx_out=8_192,
        capabilities=(ModelCapability.VISION, ModelCapability.CHAT),
        description="Previous-gen vision model.",
    ),
    "glm-4.6v-flash": GLMModel(
        id="glm-4.6v-flash",
        name="GLM-4.6V-Flash",
        tier=ModelTier.VISION,
        ctx_in=128_000,
        ctx_out=8_192,
        capabilities=(ModelCapability.VISION, ModelCapability.CHAT),
        description="Fast vision model.",
    ),
    "glm-4.6v-flashx": GLMModel(
        id="glm-4.6v-flashx",
        name="GLM-4.6V-FlashX",
        tier=ModelTier.VISION,
        ctx_in=128_000,
        ctx_out=8_192,
        capabilities=(ModelCapability.VISION, ModelCapability.CHAT),
        description="Ultra-fast vision model.",
    ),
    "glm-4.5v": GLMModel(
        id="glm-4.5v",
        name="GLM-4.5V",
        tier=ModelTier.VISION,
        ctx_in=64_000,
        ctx_out=4_096,
        capabilities=(ModelCapability.VISION, ModelCapability.CHAT),
        description="Legacy vision model.",
    ),

    # ── Tool ──────────────────────────────────────────────────────────
    "glm-ocr": GLMModel(
        id="glm-ocr",
        name="GLM-OCR",
        tier=ModelTier.TOOL,
        ctx_in=0,
        ctx_out=0,
        capabilities=(ModelCapability.VISION,),
        description="Document layout parsing and OCR.",
    ),

    # ── Audio ─────────────────────────────────────────────────────────
    "glm-asr-2512": GLMModel(
        id="glm-asr-2512",
        name="GLM-ASR-2512",
        tier=ModelTier.AUDIO,
        ctx_in=0,
        ctx_out=0,
        capabilities=(),
        description="Audio transcription model.",
    ),

    # ── Image ─────────────────────────────────────────────────────────
    "glm-image": GLMModel(
        id="glm-image",
        name="GLM-Image",
        tier=ModelTier.IMAGE,
        ctx_in=0,
        ctx_out=0,
        capabilities=(),
        description="Image generation model.",
    ),
}


def get_model(model_id: str) -> GLMModel | None:
    return MODELS.get(model_id)


def get_models_by_tier(tier: ModelTier) -> list[GLMModel]:
    return [m for m in MODELS.values() if m.tier == tier]


def get_models_by_capability(cap: ModelCapability) -> list[GLMModel]:
    return [m for m in MODELS.values() if cap in m.capabilities]


def get_chat_models() -> list[GLMModel]:
    """All models that can handle chat completions (the main API surface)."""
    return [m for m in MODELS.values() if ModelCapability.CHAT in m.capabilities]


def get_tool_capable_models() -> list[GLMModel]:
    """All models that support tool/function calling."""
    return [m for m in MODELS.values() if ModelCapability.TOOL_USE in m.capabilities
            or ModelCapability.FUNCTION_CALLING in m.capabilities]


def model_list_api() -> list[dict[str, Any]]:
    """Return model list in OpenAI /v1/models format."""
    return [
        {
            "id": m.id,
            "name": m.name,
            "tier": m.tier.value,
            "ctx_in": m.ctx_in,
            "ctx_out": m.ctx_out,
            "capabilities": [c.value for c in m.capabilities],
            "description": m.description,
        }
        for m in MODELS.values()
    ]

