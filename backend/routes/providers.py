"""
@file_name: providers.py
@author: Bin Liang
@date: 2026-03-23
@description: REST API routes for LLM provider and slot configuration

Provides endpoints for:
- GET    /api/providers           - Get current provider & slot config
- POST   /api/providers/preset    - Apply a preset (netmind / openai / anthropic / claude_oauth)
- POST   /api/providers           - Add a custom provider
- DELETE /api/providers/{id}      - Remove a provider (and its linked group)
- POST   /api/providers/{id}/test - Test provider connectivity
- PUT    /api/slots/{slot_name}   - Update slot assignment (provider + model)
- GET    /api/slots/validate      - Validate all slots are configured
- GET    /api/providers/models/{provider_id} - Get available models for a provider
- GET    /api/providers/catalog    - Get full model catalog for all presets
"""

from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, BackgroundTasks, HTTPException
from loguru import logger
from pydantic import BaseModel

from xyz_agent_context.agent_framework.provider_registry import provider_registry
from xyz_agent_context.agent_framework.model_catalog import (
    get_all_presets_summary,
    get_models_for_slot,
)
from xyz_agent_context.schema.provider_schema import (
    LLMConfig,
    ProviderPreset,
    SlotName,
    SLOT_REQUIRED_PROTOCOLS,
)

router = APIRouter()


# =============================================================================
# Request/Response Models
# =============================================================================

class ApplyPresetRequest(BaseModel):
    preset: str  # "netmind" | "openai" | "anthropic" | "claude_oauth"
    api_key: str = ""


class MergePresetRequest(BaseModel):
    preset: str
    api_key: str = ""


class AddCustomProviderRequest(BaseModel):
    name: str
    protocol: str  # "openai" | "anthropic"
    auth_type: str  # "api_key" | "bearer_token"
    api_key: str
    base_url: str


class SetSlotRequest(BaseModel):
    provider_id: str
    model: str


# =============================================================================
# Helper
# =============================================================================

def _get_or_empty_config() -> LLMConfig:
    """Load existing config or return an empty one."""
    config = provider_registry.load()
    return config if config is not None else LLMConfig()


def _config_to_response(config: LLMConfig) -> dict:
    """Convert LLMConfig to API response dict with slot protocol info."""
    # Build providers list with masked api_key
    providers = {}
    for pid, prov in config.providers.items():
        d = prov.model_dump(mode="json")
        # Mask API key for security (show last 4 chars)
        if d["api_key"] and len(d["api_key"]) > 4:
            d["api_key_masked"] = "***" + d["api_key"][-4:]
        else:
            d["api_key_masked"] = "***"
        del d["api_key"]
        providers[pid] = d

    # Build slots with required protocol info
    slots = {}
    for slot_name in SlotName:
        slot_str = slot_name.value
        required = [p.value for p in SLOT_REQUIRED_PROTOCOLS.get(slot_str, [])]
        slot_cfg = config.slots.get(slot_str)
        slots[slot_str] = {
            "required_protocols": required,
            "config": slot_cfg.model_dump() if slot_cfg else None,
        }

    return {
        "version": config.version,
        "providers": providers,
        "slots": slots,
    }


# =============================================================================
# Endpoints
# =============================================================================

@router.get("")
async def get_providers():
    """Get current provider and slot configuration."""
    config = _get_or_empty_config()
    return {"success": True, "data": _config_to_response(config)}


@router.post("/preset")
async def apply_preset(req: ApplyPresetRequest):
    """
    Apply a preset configuration (replaces existing config).

    This is the "quick setup" path: user picks a provider type,
    enters one API key, and the system auto-configures everything.
    """
    try:
        config = provider_registry.apply_preset(req.preset, req.api_key)
        provider_registry.save(config)
        errors = provider_registry.validate(config)
        return {
            "success": True,
            "data": _config_to_response(config),
            "validation_errors": errors,
        }
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/merge")
async def merge_preset(req: MergePresetRequest):
    """
    Add a preset's providers to the existing config without replacing it.

    Useful when the user has e.g. Claude OAuth for agent slot but wants
    to add OpenAI for embedding/helper_llm slots.
    """
    try:
        config = _get_or_empty_config()
        config = provider_registry.merge_preset(config, req.preset, req.api_key)
        provider_registry.save(config)
        return {"success": True, "data": _config_to_response(config)}
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("")
async def add_custom_provider(req: AddCustomProviderRequest):
    """Add a user-defined custom provider."""
    try:
        config = _get_or_empty_config()
        config, provider_id = provider_registry.add_custom_provider(
            config,
            name=req.name,
            protocol=req.protocol,
            auth_type=req.auth_type,
            api_key=req.api_key,
            base_url=req.base_url,
        )
        provider_registry.save(config)
        return {
            "success": True,
            "provider_id": provider_id,
            "data": _config_to_response(config),
        }
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.delete("/{provider_id}")
async def remove_provider(provider_id: str):
    """Remove a provider and its linked group. Clears affected slots."""
    config = _get_or_empty_config()
    if provider_id not in config.providers:
        raise HTTPException(status_code=404, detail=f"Provider '{provider_id}' not found")

    config = provider_registry.remove_provider(config, provider_id)
    provider_registry.save(config)
    return {"success": True, "data": _config_to_response(config)}


@router.post("/{provider_id}/test")
async def test_provider(provider_id: str):
    """Test connectivity to a provider with a minimal API call."""
    config = _get_or_empty_config()
    if provider_id not in config.providers:
        raise HTTPException(status_code=404, detail=f"Provider '{provider_id}' not found")

    prov = config.providers[provider_id]
    success, message = await provider_registry.test_provider(prov)
    return {"success": success, "message": message}


@router.put("/slots/{slot_name}")
async def set_slot(slot_name: str, req: SetSlotRequest):
    """Assign a provider + model to a slot."""
    try:
        config = _get_or_empty_config()
        config = provider_registry.set_slot(config, slot_name, req.provider_id, req.model)
        provider_registry.save(config)
        errors = provider_registry.validate(config)
        return {
            "success": True,
            "data": _config_to_response(config),
            "validation_errors": errors,
        }
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.get("/slots/validate")
async def validate_slots():
    """Validate that all slots are properly configured."""
    config = _get_or_empty_config()
    errors = provider_registry.validate(config)
    return {
        "success": len(errors) == 0,
        "errors": errors,
        "all_configured": len(errors) == 0,
    }


@router.get("/models/{provider_id}")
async def get_provider_models(provider_id: str, slot: Optional[str] = None):
    """
    Get available models for a provider.

    For preset providers, returns models from the static catalog.
    For custom providers, returns an empty list (user specifies manually).
    Optionally filter by slot type.
    """
    config = _get_or_empty_config()
    if provider_id not in config.providers:
        raise HTTPException(status_code=404, detail=f"Provider '{provider_id}' not found")

    prov = config.providers[provider_id]
    preset = prov.preset.value if hasattr(prov.preset, "value") else prov.preset

    if preset == "custom":
        return {"success": True, "models": []}

    if slot:
        models = get_models_for_slot(preset, slot)
    else:
        # Return all models for this preset
        from xyz_agent_context.agent_framework.model_catalog import _CATALOG
        models = _CATALOG.get(preset, [])

    return {
        "success": True,
        "models": [
            {
                "model_id": m.model_id,
                "display_name": m.display_name,
                "slot_types": m.slot_types,
                "dimensions": m.dimensions,
                "is_default": m.is_default,
            }
            for m in models
        ],
    }


@router.get("/catalog")
async def get_catalog():
    """Get the full model catalog for all presets (for frontend dropdown population)."""
    return {
        "success": True,
        "catalog": get_all_presets_summary(),
        "slot_protocols": {
            slot.value: [p.value for p in protos]
            for slot, protos in SLOT_REQUIRED_PROTOCOLS.items()
        },
    }


# =============================================================================
# Claude Code Auth Status
# =============================================================================

@router.get("/claude-status")
async def get_claude_status():
    """
    Check if Claude Code CLI is logged in by inspecting local credentials.

    Returns login state so the web frontend can show guidance.
    """
    import json as _json
    from pathlib import Path

    claude_dir = Path.home() / ".claude"
    creds_file = claude_dir / ".credentials.json"

    result = {
        "cli_installed": False,
        "logged_in": False,
        "expires_at": None,
    }

    # Check CLI installed
    import shutil
    if shutil.which("claude"):
        result["cli_installed"] = True

    # Check credentials file
    if creds_file.is_file():
        try:
            data = _json.loads(creds_file.read_text(encoding="utf-8"))
            # credentials.json has various formats; look for any OAuth token
            if isinstance(data, dict):
                # Check for oauth tokens
                for key in ("accessToken", "oauthToken", "claudeAiOauth"):
                    if data.get(key):
                        result["logged_in"] = True
                        result["expires_at"] = data.get("expiresAt")
                        break
                # Also check nested format
                if not result["logged_in"] and data.get("oauth"):
                    result["logged_in"] = True
                    result["expires_at"] = data["oauth"].get("expiresAt")
        except Exception:
            pass

    return {"success": True, "data": result}


# =============================================================================
# Embedding Migration Endpoints
# =============================================================================

@router.get("/embeddings/status")
async def get_embedding_status():
    """
    Get embedding migration status for the current model.

    Returns per-entity-type counts of total/migrated/missing vectors,
    plus any in-progress migration progress.
    """
    from xyz_agent_context.utils.db_factory import get_db_client
    from xyz_agent_context.services.embedding_migration_service import EmbeddingMigrationService

    db = await get_db_client()
    service = EmbeddingMigrationService(db)
    status = await service.get_status()
    return {"success": True, "data": status}


@router.post("/embeddings/rebuild")
async def rebuild_embeddings(background_tasks: BackgroundTasks):
    """
    Trigger embedding rebuild for the current model.

    Runs in the background. Poll GET /embeddings/status for progress.
    Returns immediately with the initial status.
    """
    from xyz_agent_context.utils.db_factory import get_db_client
    from xyz_agent_context.services.embedding_migration_service import (
        EmbeddingMigrationService,
        get_migration_progress,
    )

    progress = get_migration_progress()
    if progress.is_running:
        return {
            "success": False,
            "error": "Migration already in progress",
            "data": progress.to_dict(),
        }

    db = await get_db_client()
    service = EmbeddingMigrationService(db)

    # Run in background
    background_tasks.add_task(service.rebuild_all)

    return {
        "success": True,
        "message": "Embedding rebuild started",
    }
