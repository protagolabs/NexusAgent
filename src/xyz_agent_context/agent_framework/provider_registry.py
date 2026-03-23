"""
@file_name: provider_registry.py
@author: Bin Liang
@date: 2026-03-23
@description: LLM Provider configuration management

Manages the llm_config.json file that stores provider definitions and
slot assignments. Provides preset expansion, validation, and connection
testing capabilities.

Usage:
    from xyz_agent_context.agent_framework.provider_registry import provider_registry

    # Load current config
    config = provider_registry.load()

    # Apply a preset (e.g., user enters a single NetMind key)
    config = provider_registry.apply_preset("netmind", api_key="xxx")
    provider_registry.save(config)

    # Validate all slots are configured
    errors = provider_registry.validate(config)
"""

from __future__ import annotations

import json
import secrets
import string
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from loguru import logger

from xyz_agent_context.schema.provider_schema import (
    AuthType,
    LLMConfig,
    ProviderConfig,
    ProviderPreset,
    ProviderProtocol,
    SlotConfig,
    SlotName,
    SLOT_REQUIRED_PROTOCOLS,
)
from xyz_agent_context.agent_framework.model_catalog import get_default_model


# =============================================================================
# Constants
# =============================================================================

CONFIG_DIR = Path.home() / ".nexusagent"
CONFIG_FILE = CONFIG_DIR / "llm_config.json"

# NetMind endpoint URLs
NETMIND_ANTHROPIC_BASE_URL = "https://api.netmind.ai/inference-api/anthropic"
NETMIND_OPENAI_BASE_URL = "https://api.netmind.ai/inference-api/openai/v1"


# =============================================================================
# ID Generation
# =============================================================================

def _generate_provider_id() -> str:
    """Generate a unique provider ID with 'prov_' prefix + 8 random chars"""
    chars = string.ascii_lowercase + string.digits
    suffix = "".join(secrets.choice(chars) for _ in range(8))
    return f"prov_{suffix}"


def _generate_group_id() -> str:
    """Generate a unique linked_group ID"""
    chars = string.ascii_lowercase + string.digits
    suffix = "".join(secrets.choice(chars) for _ in range(8))
    return f"grp_{suffix}"


# =============================================================================
# Preset Expansion
# =============================================================================

def _build_netmind_providers(api_key: str, group_id: str) -> list[ProviderConfig]:
    """Expand a single NetMind API key into two providers (anthropic + openai)"""
    now = datetime.now(timezone.utc)
    return [
        ProviderConfig(
            provider_id=_generate_provider_id(),
            name="NetMind (Anthropic)",
            preset=ProviderPreset.NETMIND,
            protocol=ProviderProtocol.ANTHROPIC,
            auth_type=AuthType.BEARER_TOKEN,
            api_key=api_key,
            base_url=NETMIND_ANTHROPIC_BASE_URL,
            linked_group=group_id,
            created_at=now,
            updated_at=now,
        ),
        ProviderConfig(
            provider_id=_generate_provider_id(),
            name="NetMind (OpenAI)",
            preset=ProviderPreset.NETMIND,
            protocol=ProviderProtocol.OPENAI,
            auth_type=AuthType.API_KEY,
            api_key=api_key,
            base_url=NETMIND_OPENAI_BASE_URL,
            linked_group=group_id,
            created_at=now,
            updated_at=now,
        ),
    ]


def _build_openai_provider(api_key: str) -> ProviderConfig:
    """Build a provider from an OpenAI API key"""
    now = datetime.now(timezone.utc)
    return ProviderConfig(
        provider_id=_generate_provider_id(),
        name="OpenAI",
        preset=ProviderPreset.OPENAI,
        protocol=ProviderProtocol.OPENAI,
        auth_type=AuthType.API_KEY,
        api_key=api_key,
        base_url="",
        created_at=now,
        updated_at=now,
    )


def _build_anthropic_provider(api_key: str) -> ProviderConfig:
    """Build a provider from an Anthropic API key"""
    now = datetime.now(timezone.utc)
    return ProviderConfig(
        provider_id=_generate_provider_id(),
        name="Anthropic",
        preset=ProviderPreset.ANTHROPIC,
        protocol=ProviderProtocol.ANTHROPIC,
        auth_type=AuthType.API_KEY,
        api_key=api_key,
        base_url="",
        created_at=now,
        updated_at=now,
    )


def _build_claude_oauth_provider() -> ProviderConfig:
    """Build a provider for Claude Code OAuth (no key needed)"""
    now = datetime.now(timezone.utc)
    return ProviderConfig(
        provider_id=_generate_provider_id(),
        name="Claude Code (OAuth)",
        preset=ProviderPreset.CLAUDE_OAUTH,
        protocol=ProviderProtocol.ANTHROPIC,
        auth_type=AuthType.OAUTH,
        api_key="",
        base_url="",
        created_at=now,
        updated_at=now,
    )


def _auto_assign_slots(
    providers: dict[str, ProviderConfig],
    preset: ProviderPreset,
) -> dict[str, SlotConfig]:
    """
    Auto-assign default models to slots based on preset type.

    For each slot, find a matching provider (protocol matches) and
    pick the default model from the catalog.
    """
    slots: dict[str, SlotConfig] = {}

    for slot_name in SlotName:
        required_protocols = SLOT_REQUIRED_PROTOCOLS.get(slot_name, [])

        # Find the first active provider whose protocol matches
        matching_provider: Optional[ProviderConfig] = None
        for prov in providers.values():
            if prov.is_active and prov.protocol in required_protocols:
                matching_provider = prov
                break

        if matching_provider is None:
            continue

        # Get default model from catalog
        default_model = get_default_model(matching_provider.preset, slot_name)
        if default_model is None:
            continue

        slots[slot_name.value] = SlotConfig(
            provider_id=matching_provider.provider_id,
            model=default_model.model_id,
        )

    return slots


# =============================================================================
# Provider Registry
# =============================================================================

class ProviderRegistry:
    """
    Manages the LLM provider configuration file (~/.nexusagent/llm_config.json).

    Responsible for:
    - Loading/saving the config file
    - Expanding presets into provider + slot configurations
    - Adding/removing custom providers
    - Validating slot assignments
    - Testing provider connectivity
    """

    def __init__(self, config_path: Optional[Path] = None):
        self._config_path = config_path or CONFIG_FILE

    # ---- File I/O ----

    def load(self) -> Optional[LLMConfig]:
        """
        Load configuration from disk.

        Returns:
            LLMConfig if the file exists and is valid, None otherwise
        """
        if not self._config_path.is_file():
            return None
        try:
            raw = json.loads(self._config_path.read_text(encoding="utf-8"))
            return LLMConfig.model_validate(raw)
        except Exception as e:
            logger.error(f"Failed to load LLM config from {self._config_path}: {e}")
            return None

    def save(self, config: LLMConfig) -> None:
        """Save configuration to disk."""
        self._config_path.parent.mkdir(parents=True, exist_ok=True)
        data = config.model_dump(mode="json")
        self._config_path.write_text(
            json.dumps(data, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
        logger.info(f"LLM config saved to {self._config_path}")

    def config_exists(self) -> bool:
        """Check if a configuration file exists on disk."""
        return self._config_path.is_file()

    # ---- Preset Application ----

    def apply_preset(
        self,
        preset: str | ProviderPreset,
        api_key: str = "",
    ) -> LLMConfig:
        """
        Create a complete LLMConfig from a preset type and API key.

        This replaces any existing configuration. For the NetMind preset,
        a single key generates two providers (anthropic + openai protocol).

        Args:
            preset: The preset to apply
            api_key: The API key (not needed for claude_oauth)

        Returns:
            A fully configured LLMConfig with providers and slot assignments
        """
        preset_enum = ProviderPreset(preset) if isinstance(preset, str) else preset
        providers: dict[str, ProviderConfig] = {}

        if preset_enum == ProviderPreset.NETMIND:
            group_id = _generate_group_id()
            for prov in _build_netmind_providers(api_key, group_id):
                providers[prov.provider_id] = prov

        elif preset_enum == ProviderPreset.OPENAI:
            prov = _build_openai_provider(api_key)
            providers[prov.provider_id] = prov

        elif preset_enum == ProviderPreset.ANTHROPIC:
            prov = _build_anthropic_provider(api_key)
            providers[prov.provider_id] = prov

        elif preset_enum == ProviderPreset.CLAUDE_OAUTH:
            prov = _build_claude_oauth_provider()
            providers[prov.provider_id] = prov

        # Auto-assign slots with default models
        slots = _auto_assign_slots(providers, preset_enum)

        return LLMConfig(providers=providers, slots=slots)

    def merge_preset(
        self,
        config: LLMConfig,
        preset: str | ProviderPreset,
        api_key: str = "",
    ) -> LLMConfig:
        """
        Add a preset's providers to an existing config without replacing it.

        Useful when the user has e.g. Claude OAuth for agent but wants to
        add OpenAI for embedding/helper_llm. Automatically fills any
        empty slots that the new providers can serve.

        Args:
            config: Existing configuration to merge into
            preset: The preset to add
            api_key: The API key

        Returns:
            Updated LLMConfig with new providers added and empty slots auto-filled
        """
        preset_enum = ProviderPreset(preset) if isinstance(preset, str) else preset
        new_providers: list[ProviderConfig] = []

        if preset_enum == ProviderPreset.NETMIND:
            group_id = _generate_group_id()
            new_providers = _build_netmind_providers(api_key, group_id)
        elif preset_enum == ProviderPreset.OPENAI:
            new_providers = [_build_openai_provider(api_key)]
        elif preset_enum == ProviderPreset.ANTHROPIC:
            new_providers = [_build_anthropic_provider(api_key)]
        elif preset_enum == ProviderPreset.CLAUDE_OAUTH:
            new_providers = [_build_claude_oauth_provider()]

        for prov in new_providers:
            config.providers[prov.provider_id] = prov

        # Auto-fill empty slots that the new providers can serve
        for slot_name in SlotName:
            slot_str = slot_name.value
            if slot_str in config.slots:
                continue  # Already assigned, don't override

            required_protocols = SLOT_REQUIRED_PROTOCOLS.get(slot_name, [])
            for prov in new_providers:
                if prov.is_active and prov.protocol in required_protocols:
                    default_model = get_default_model(prov.preset, slot_name)
                    if default_model:
                        config.slots[slot_str] = SlotConfig(
                            provider_id=prov.provider_id,
                            model=default_model.model_id,
                        )
                    break

        return config

    # ---- Custom Provider ----

    def add_custom_provider(
        self,
        config: LLMConfig,
        name: str,
        protocol: str | ProviderProtocol,
        auth_type: str | AuthType,
        api_key: str,
        base_url: str,
    ) -> tuple[LLMConfig, str]:
        """
        Add a user-defined custom provider.

        Args:
            config: Existing configuration
            name: Display name
            protocol: "openai" or "anthropic"
            auth_type: "api_key" or "bearer_token"
            api_key: The API key
            base_url: The API base URL

        Returns:
            (Updated config, new provider_id)
        """
        protocol_enum = ProviderProtocol(protocol) if isinstance(protocol, str) else protocol
        auth_enum = AuthType(auth_type) if isinstance(auth_type, str) else auth_type
        now = datetime.now(timezone.utc)

        prov = ProviderConfig(
            provider_id=_generate_provider_id(),
            name=name,
            preset=ProviderPreset.CUSTOM,
            protocol=protocol_enum,
            auth_type=auth_enum,
            api_key=api_key,
            base_url=base_url,
            created_at=now,
            updated_at=now,
        )
        config.providers[prov.provider_id] = prov
        return config, prov.provider_id

    # ---- Provider Removal ----

    def remove_provider(self, config: LLMConfig, provider_id: str, remove_group: bool = True) -> LLMConfig:
        """
        Remove a provider (and optionally its linked group).

        Also clears any slots that reference the removed provider(s).

        Args:
            config: Existing configuration
            provider_id: The provider to remove
            remove_group: If True, remove all providers in the same linked_group

        Returns:
            Updated LLMConfig
        """
        ids_to_remove: set[str] = set()

        if provider_id not in config.providers:
            return config

        if remove_group:
            group = config.providers[provider_id].linked_group
            if group:
                ids_to_remove = {
                    pid for pid, p in config.providers.items()
                    if p.linked_group == group
                }
        if not ids_to_remove:
            ids_to_remove = {provider_id}

        # Remove providers
        for pid in ids_to_remove:
            config.providers.pop(pid, None)

        # Clear slots that reference removed providers
        for slot_name, slot_cfg in list(config.slots.items()):
            if slot_cfg.provider_id in ids_to_remove:
                del config.slots[slot_name]

        return config

    # ---- Slot Assignment ----

    def set_slot(
        self,
        config: LLMConfig,
        slot_name: str | SlotName,
        provider_id: str,
        model: str,
    ) -> LLMConfig:
        """
        Assign a provider + model to a slot.

        Validates that the provider's protocol matches the slot's requirements.

        Raises:
            ValueError: If protocol mismatch or provider not found
        """
        slot_str = slot_name.value if isinstance(slot_name, SlotName) else slot_name

        if provider_id not in config.providers:
            raise ValueError(f"Provider '{provider_id}' not found")

        provider = config.providers[provider_id]
        required = SLOT_REQUIRED_PROTOCOLS.get(slot_str, [])
        if required and provider.protocol not in required:
            raise ValueError(
                f"Slot '{slot_str}' requires protocol {[p.value for p in required]}, "
                f"but provider '{provider.name}' uses '{provider.protocol.value}'"
            )

        config.slots[slot_str] = SlotConfig(provider_id=provider_id, model=model)
        return config

    # ---- Validation ----

    def validate(self, config: LLMConfig) -> list[str]:
        """
        Validate that all slots are properly configured.

        Returns:
            List of error messages. Empty list means all OK.
        """
        errors: list[str] = []

        for slot_name in SlotName:
            slot_str = slot_name.value
            if slot_str not in config.slots:
                errors.append(f"Slot '{slot_str}' is not configured")
                continue

            slot_cfg = config.slots[slot_str]

            # Check provider exists
            if slot_cfg.provider_id not in config.providers:
                errors.append(
                    f"Slot '{slot_str}' references non-existent provider '{slot_cfg.provider_id}'"
                )
                continue

            provider = config.providers[slot_cfg.provider_id]

            # Check provider is active
            if not provider.is_active:
                errors.append(f"Slot '{slot_str}' references disabled provider '{provider.name}'")

            # Check protocol match
            required = SLOT_REQUIRED_PROTOCOLS.get(slot_str, [])
            if required and provider.protocol not in required:
                errors.append(
                    f"Slot '{slot_str}' requires {[p.value for p in required]}, "
                    f"but provider '{provider.name}' uses '{provider.protocol.value}'"
                )

            # Check model is set
            if not slot_cfg.model:
                errors.append(f"Slot '{slot_str}' has no model specified")

            # Check API key (not required for OAuth)
            if provider.auth_type != AuthType.OAUTH and not provider.api_key:
                errors.append(f"Provider '{provider.name}' for slot '{slot_str}' has no API key")

        return errors

    # ---- Connection Test ----

    async def test_provider(self, provider: ProviderConfig) -> tuple[bool, str]:
        """
        Test connectivity to a provider with a minimal API call.

        Args:
            provider: The provider configuration to test

        Returns:
            (success: bool, message: str)
        """
        if provider.auth_type == AuthType.OAUTH:
            return True, "OAuth provider (managed by Claude Code CLI)"

        try:
            if provider.protocol == ProviderProtocol.OPENAI:
                return await self._test_openai_provider(provider)
            elif provider.protocol == ProviderProtocol.ANTHROPIC:
                return await self._test_anthropic_provider(provider)
            else:
                return False, f"Unknown protocol: {provider.protocol}"
        except Exception as e:
            return False, f"Connection failed: {e}"

    async def _test_openai_provider(self, provider: ProviderConfig) -> tuple[bool, str]:
        """Test an OpenAI-protocol provider with a minimal models list request"""
        from openai import AsyncOpenAI

        client_kwargs: dict = {"api_key": provider.api_key}
        if provider.base_url:
            client_kwargs["base_url"] = provider.base_url

        client = AsyncOpenAI(**client_kwargs)
        models = await client.models.list()
        count = len(models.data) if models.data else 0
        return True, f"Connected successfully ({count} models available)"

    async def _test_anthropic_provider(self, provider: ProviderConfig) -> tuple[bool, str]:
        """Test an Anthropic-protocol provider with a minimal request.

        Sends a tiny messages request. A 200 means full success; a 400
        (e.g., invalid model) still proves authentication works.
        Only 401/403 indicate real auth failures.
        """
        import httpx

        base_url = provider.base_url or "https://api.anthropic.com"
        url = f"{base_url}/v1/messages"

        headers: dict[str, str] = {
            "Content-Type": "application/json",
            "anthropic-version": "2023-06-01",
        }
        if provider.auth_type == AuthType.BEARER_TOKEN:
            headers["Authorization"] = f"Bearer {provider.api_key}"
        else:
            headers["X-Api-Key"] = provider.api_key

        # Use a deliberately minimal payload; we only care about auth, not model availability
        payload = {
            "model": "test-connectivity",
            "max_tokens": 1,
            "messages": [{"role": "user", "content": "hi"}],
        }

        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.post(url, json=payload, headers=headers)

        if resp.status_code == 200:
            return True, "Connected successfully"
        elif resp.status_code == 400:
            # 400 = auth passed but payload invalid (e.g., model not found) — auth works
            return True, "Authentication verified (API reachable)"
        elif resp.status_code == 401:
            return False, "Authentication failed (invalid API key)"
        elif resp.status_code == 403:
            return False, "Access denied (check API key permissions)"
        else:
            body = resp.text[:200]
            return False, f"HTTP {resp.status_code}: {body}"


# =============================================================================
# Singleton Instance
# =============================================================================

provider_registry = ProviderRegistry()
