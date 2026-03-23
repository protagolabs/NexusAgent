"""
@file_name: api_config.py
@author: Bin Liang
@date: 2026-03-23
@description: Centralized LLM API configuration for all agent framework components

All API keys, base URLs, and model names used by the agent framework are defined
here. Components (Claude SDK, OpenAI Agents SDK, Gemini SDK, Embedding Client)
should read from this module instead of accessing settings/os.environ directly.

Configuration priority:
    1. ~/.nexusagent/llm_config.json (managed by provider_registry)
    2. .env / settings.py (legacy fallback for existing users)

Usage:
    from xyz_agent_context.agent_framework.api_config import (
        claude_config,
        openai_config,
        gemini_config,
        embedding_config,
    )

    # Access config values
    model = openai_config.model
    api_key = embedding_config.api_key
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from loguru import logger

from xyz_agent_context.schema.provider_schema import (
    AuthType,
    ProviderProtocol,
    SlotName,
)


# =============================================================================
# Configuration Dataclasses (public interface, unchanged)
# =============================================================================

@dataclass(frozen=True)
class ClaudeConfig:
    """Claude API configuration (passed to Claude Code CLI subprocess)"""
    api_key: str = ""
    base_url: str = ""
    model: str = ""          # Empty = let Claude Code CLI use its default model
    auth_type: str = "api_key"  # "api_key" | "bearer_token" | "oauth"

    def to_cli_env(self) -> dict[str, str]:
        """Build env vars dict for Claude Code CLI subprocess.

        Only includes non-empty values to avoid overriding CLI defaults.
        Uses ANTHROPIC_AUTH_TOKEN for bearer_token auth, ANTHROPIC_API_KEY otherwise.
        """
        env: dict[str, str] = {}
        if self.api_key:
            if self.auth_type == "bearer_token":
                env["ANTHROPIC_AUTH_TOKEN"] = self.api_key
            else:
                env["ANTHROPIC_API_KEY"] = self.api_key
        if self.base_url:
            env["ANTHROPIC_BASE_URL"] = self.base_url
        return env


@dataclass(frozen=True)
class OpenAIConfig:
    """OpenAI Chat Completions API configuration (used by helper_llm slot)"""
    api_key: str = ""
    base_url: str = ""  # Empty = default https://api.openai.com/v1
    model: str = "gpt-5.1-2025-11-13"


@dataclass(frozen=True)
class GeminiConfig:
    """Google Gemini API configuration"""
    api_key: str = ""
    model: str = "gemini-2.5-flash"


@dataclass(frozen=True)
class EmbeddingConfig:
    """OpenAI Embedding API configuration"""
    api_key: str = ""
    base_url: str = ""  # Empty = default https://api.openai.com/v1
    model: str = "text-embedding-3-small"
    dimensions: Optional[int] = None  # None = use model default


# =============================================================================
# Config Loading
# =============================================================================

def _load_from_llm_config() -> Optional[tuple[ClaudeConfig, OpenAIConfig, EmbeddingConfig]]:
    """
    Try to load configuration from ~/.nexusagent/llm_config.json.

    Returns:
        Tuple of (claude_config, openai_config, embedding_config) if successful,
        None if the file doesn't exist or is invalid.
    """
    from xyz_agent_context.agent_framework.provider_registry import provider_registry

    config = provider_registry.load()
    if config is None:
        return None

    # Per-slot loading: use whatever slots ARE configured, leave the rest
    # as empty defaults. The caller merges with .env fallback per-slot.
    errors = provider_registry.validate(config)
    if errors:
        logger.info(f"llm_config.json partial config ({len(config.slots)}/3 slots): {errors}")

    # Build ClaudeConfig from agent slot
    agent_slot = config.slots.get(SlotName.AGENT) or config.slots.get("agent")
    agent_provider = config.providers.get(agent_slot.provider_id) if agent_slot else None

    if agent_provider:
        claude = ClaudeConfig(
            api_key=agent_provider.api_key,
            base_url=agent_provider.base_url,
            model=agent_slot.model,
            auth_type=agent_provider.auth_type.value if isinstance(agent_provider.auth_type, AuthType) else agent_provider.auth_type,
        )
    else:
        claude = ClaudeConfig()

    # Build OpenAIConfig from helper_llm slot
    helper_slot = config.slots.get(SlotName.HELPER_LLM) or config.slots.get("helper_llm")
    helper_provider = config.providers.get(helper_slot.provider_id) if helper_slot else None

    if helper_provider:
        openai_cfg = OpenAIConfig(
            api_key=helper_provider.api_key,
            base_url=helper_provider.base_url,
            model=helper_slot.model,
        )
    else:
        openai_cfg = OpenAIConfig()

    # Build EmbeddingConfig from embedding slot
    emb_slot = config.slots.get(SlotName.EMBEDDING) or config.slots.get("embedding")
    emb_provider = config.providers.get(emb_slot.provider_id) if emb_slot else None

    if emb_provider:
        from xyz_agent_context.agent_framework.model_catalog import get_embedding_dimensions
        dims = get_embedding_dimensions(emb_slot.model)
        embedding = EmbeddingConfig(
            api_key=emb_provider.api_key,
            base_url=emb_provider.base_url,
            model=emb_slot.model,
            dimensions=dims,
        )
    else:
        embedding = EmbeddingConfig()

    logger.info("LLM config loaded from llm_config.json")
    return claude, openai_cfg, embedding


def _load_from_settings() -> tuple[ClaudeConfig, OpenAIConfig, EmbeddingConfig]:
    """
    Fallback: load configuration from .env / settings.py (legacy path).
    """
    from xyz_agent_context.settings import settings

    claude = ClaudeConfig(
        api_key=settings.anthropic_api_key,
        base_url=settings.anthropic_base_url,
        model=settings.anthropic_model,
    )

    openai_cfg = OpenAIConfig(
        api_key=settings.openai_api_key,
    )

    embedding = EmbeddingConfig(
        api_key=settings.openai_api_key,
        model=settings.openai_embedding_model,
    )

    return claude, openai_cfg, embedding


def _load_gemini_config() -> GeminiConfig:
    """Load Gemini config (always from settings, not part of the slot system yet)."""
    from xyz_agent_context.settings import settings
    return GeminiConfig(api_key=settings.google_api_key)


# =============================================================================
# Initialize: per-slot merge (llm_config.json preferred, .env fallback)
# =============================================================================

_json_result = _load_from_llm_config()
_env_claude, _env_openai, _env_embedding = _load_from_settings()

if _json_result is not None:
    _json_claude, _json_openai, _json_embedding = _json_result
    # Per-slot: use json config if it has a non-empty api_key (or is OAuth), else fallback
    claude_config = _json_claude if (_json_claude.api_key or _json_claude.auth_type == "oauth") else _env_claude
    openai_config = _json_openai if _json_openai.api_key else _env_openai
    embedding_config = _json_embedding if _json_embedding.api_key else _env_embedding
else:
    claude_config, openai_config, embedding_config = _env_claude, _env_openai, _env_embedding

gemini_config = _load_gemini_config()
