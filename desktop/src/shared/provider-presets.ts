/**
 * @file provider-presets.ts
 * @description Data-driven preset provider registry for quick setup
 *
 * Each preset defines a provider that can fulfill one or more protocol endpoints
 * with a single API key. This registry is extensible — add new entries to
 * PRESET_PROVIDERS to make them available in the setup wizard's "Quick Setup" flow.
 *
 * Design: providers are protocol-agnostic containers. Each preset declares which
 * protocols it supports (anthropic / openai) along with the base_url and default
 * models for each. The setup wizard uses this metadata to auto-create the correct
 * backend provider entries.
 */

// =============================================================================
// Types
// =============================================================================

/** A protocol endpoint that a preset provider supports */
export interface PresetProtocolEndpoint {
  /** The LLM protocol identifier */
  protocol: 'anthropic' | 'openai'
  /** Base URL for this protocol's API */
  base_url: string
  /** Default LLM model IDs available through this endpoint */
  models: string[]
  /** Embedding model IDs (only relevant for openai protocol endpoints) */
  embedding_models?: string[]
}

/** A preset provider — one API key that covers one or more protocol endpoints */
export interface PresetProvider {
  /** Unique identifier (used as card_type when calling the backend) */
  id: string
  /** Display name shown to users */
  name: string
  /** Short tagline explaining the value proposition */
  tagline: string
  /** Longer description for tooltip / help text */
  description: string
  /** URL where the user can obtain an API key */
  get_key_url: string
  /** Protocol endpoints this provider supports */
  endpoints: PresetProtocolEndpoint[]
  /** Whether this preset covers all 3 required slots (agent + embedding + helper_llm) */
  covers_all_slots: boolean
}

// =============================================================================
// Registry
// =============================================================================

/**
 * Preset providers available in the "Quick Setup" flow.
 *
 * To add a new provider:
 *   1. Append a new PresetProvider object to this array
 *   2. Ensure the backend's POST /api/providers handler supports the card_type
 *   3. That's it — the setup wizard picks it up automatically
 */
export const PRESET_PROVIDERS: PresetProvider[] = [
  {
    id: 'netmind',
    name: 'NetMind.AI Power',
    tagline: 'One key, full coverage',
    description:
      'A single API key that provides both Anthropic-compatible and OpenAI-compatible endpoints. ' +
      'Covers Agent, Embedding, and Helper LLM — no extra configuration needed.',
    get_key_url: 'https://www.netmind.ai/user/dashboard',
    endpoints: [
      {
        protocol: 'anthropic',
        base_url: 'https://api.netmind.ai/inference-api/anthropic/v1',
        models: ['claude-sonnet-4-20250514'],
      },
      {
        protocol: 'openai',
        base_url: 'https://api.netmind.ai/inference-api/openai/v1',
        models: ['deepseek-ai/DeepSeek-V3', 'Qwen/Qwen3-235B-A22B'],
        embedding_models: ['BAAI/bge-m3'],
      },
    ],
    covers_all_slots: true,
  },
  // ── Add more preset providers below ──────────────────────────────────
  // Example:
  // {
  //   id: 'openrouter',
  //   name: 'OpenRouter',
  //   tagline: 'Access 100+ models with one key',
  //   description: 'OpenRouter provides a unified API for many LLM providers.',
  //   get_key_url: 'https://openrouter.ai/keys',
  //   endpoints: [
  //     { protocol: 'openai', base_url: 'https://openrouter.ai/api/v1', models: [...], embedding_models: [...] },
  //   ],
  //   covers_all_slots: false,
  // },
]
