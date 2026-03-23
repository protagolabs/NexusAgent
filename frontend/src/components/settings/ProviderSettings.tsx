/**
 * ProviderSettings - LLM Provider configuration for the web frontend
 *
 * Allows users to configure providers and slots after initial setup.
 * Uses the bioluminescent terminal design system.
 */

import { useState, useEffect, useCallback } from 'react'
import { cn } from '@/lib/utils'

const API_BASE = ''  // Relative — Vite proxy handles /api/*

type PresetType = 'netmind' | 'anthropic_openai' | 'claude_openai' | 'custom'

interface ProviderSummary {
  provider_id: string
  name: string
  preset: string
  protocol: string
  auth_type: string
  is_active: boolean
  api_key_masked?: string
}

interface SlotConfig {
  provider_id: string
  model: string
}

interface SlotData {
  config: SlotConfig | null
  required_protocols: string[]
}

interface ModelOption {
  model_id: string
  display_name: string
  slot_types: string[]
  dimensions: number | null
  is_default: boolean
}

const SLOT_META: Record<string, { label: string; desc: string; protocol: string }> = {
  agent: { label: 'Agent', desc: 'Main dialogue', protocol: 'anthropic' },
  embedding: { label: 'Embedding', desc: 'Vector search', protocol: 'openai' },
  helper_llm: { label: 'Helper LLM', desc: 'Auxiliary tasks', protocol: 'openai' },
}

const PRESETS: { value: PresetType; label: string; desc: string }[] = [
  { value: 'netmind', label: 'NetMind', desc: 'One key for everything' },
  { value: 'anthropic_openai', label: 'Anthropic + OpenAI', desc: 'Separate keys' },
  { value: 'claude_openai', label: 'Claude OAuth + OpenAI', desc: 'OAuth + API key' },
  { value: 'custom', label: 'Custom', desc: 'Manual configuration' },
]

export function ProviderSettings() {
  const [preset, setPreset] = useState<PresetType>('netmind')
  const [netmindKey, setNetmindKey] = useState('')
  const [anthropicKey, setAnthropicKey] = useState('')
  const [openaiKey, setOpenaiKey] = useState('')
  const [customName, setCustomName] = useState('')
  const [customProtocol, setCustomProtocol] = useState<'openai' | 'anthropic'>('openai')
  const [customAuthType, setCustomAuthType] = useState<'api_key' | 'bearer_token'>('api_key')
  const [customKey, setCustomKey] = useState('')
  const [customUrl, setCustomUrl] = useState('')

  const [providers, setProviders] = useState<Record<string, ProviderSummary>>({})
  const [slots, setSlots] = useState<Record<string, SlotData>>({})
  const [catalog, setCatalog] = useState<Record<string, ModelOption[]>>({})
  const [applying, setApplying] = useState(false)
  const [error, setError] = useState('')
  const [collapsed, setCollapsed] = useState(true)  // Start collapsed if already configured

  // Load
  const refreshConfig = useCallback(async () => {
    try {
      const [cfgRes, catRes] = await Promise.all([
        fetch(`${API_BASE}/api/providers`).then(r => r.json()),
        fetch(`${API_BASE}/api/providers/catalog`).then(r => r.json()),
      ])
      if (cfgRes.success) {
        setProviders(cfgRes.data.providers)
        setSlots(cfgRes.data.slots)
        // Auto-collapse if already configured
        const allReady = ['agent', 'embedding', 'helper_llm'].every(
          s => cfgRes.data.slots[s]?.config?.provider_id && cfgRes.data.slots[s]?.config?.model
        )
        if (allReady) setCollapsed(true)
      }
      if (catRes.success) setCatalog(catRes.catalog)
    } catch {}
  }, [])

  useEffect(() => { refreshConfig() }, [refreshConfig])

  const hasProviders = Object.keys(providers).length > 0
  const allSlotsReady = ['agent', 'embedding', 'helper_llm'].every(
    s => slots[s]?.config?.provider_id && slots[s]?.config?.model
  )

  const handleApply = async () => {
    setApplying(true); setError('')
    try {
      if (preset === 'netmind') {
        if (!netmindKey.trim()) { setError('Enter NetMind API Key'); setApplying(false); return }
        await fetch(`${API_BASE}/api/providers/preset`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ preset: 'netmind', api_key: netmindKey.trim() }) })
      } else if (preset === 'anthropic_openai') {
        if (!anthropicKey.trim() || !openaiKey.trim()) { setError('Enter both keys'); setApplying(false); return }
        await fetch(`${API_BASE}/api/providers/preset`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ preset: 'anthropic', api_key: anthropicKey.trim() }) })
        await fetch(`${API_BASE}/api/providers/merge`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ preset: 'openai', api_key: openaiKey.trim() }) })
      } else if (preset === 'claude_openai') {
        if (!openaiKey.trim()) { setError('Enter OpenAI API Key'); setApplying(false); return }
        await fetch(`${API_BASE}/api/providers/preset`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ preset: 'claude_oauth', api_key: '' }) })
        await fetch(`${API_BASE}/api/providers/merge`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ preset: 'openai', api_key: openaiKey.trim() }) })
      } else if (preset === 'custom') {
        if (!customKey.trim() || !customUrl.trim()) { setError('Fill all fields'); setApplying(false); return }
        await fetch(`${API_BASE}/api/providers`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ name: customName || 'Custom', protocol: customProtocol, auth_type: customAuthType, api_key: customKey.trim(), base_url: customUrl.trim() }) })
      }
      await refreshConfig()
      setCollapsed(false)  // Show slots after applying
    } catch { setError('Network error') }
    setApplying(false)
  }

  const handleSlotChange = async (slotName: string, providerId: string, model: string) => {
    await fetch(`${API_BASE}/api/providers/slots/${slotName}`, {
      method: 'PUT', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ provider_id: providerId, model })
    })
    await refreshConfig()
  }

  const getModels = (provPreset: string, slot: string): ModelOption[] =>
    (catalog[provPreset] || []).filter(m => m.slot_types.includes(slot))

  // Collapsed summary view
  if (collapsed && allSlotsReady) {
    return (
      <div className="p-3 rounded-xl border border-[var(--border-subtle)] bg-[var(--bg-elevated)]">
        <div className="flex items-center justify-between">
          <div className="flex items-center gap-2">
            <div className="w-5 h-5 rounded-full bg-[var(--color-success)]/20 flex items-center justify-center">
              <span className="text-[var(--color-success)] text-xs">{'\u2713'}</span>
            </div>
            <span className="text-xs text-[var(--text-secondary)]">LLM Providers configured</span>
          </div>
          <button onClick={() => setCollapsed(false)}
            className="text-[10px] text-[var(--accent-primary)] hover:underline">
            Edit
          </button>
        </div>
        <div className="mt-2 flex flex-wrap gap-2">
          {['agent', 'embedding', 'helper_llm'].map(s => {
            const cfg = slots[s]?.config
            const prov = cfg?.provider_id ? providers[cfg.provider_id] : null
            return (
              <span key={s} className="text-[10px] px-2 py-0.5 rounded-full bg-[var(--bg-tertiary)] text-[var(--text-tertiary)]">
                {SLOT_META[s].label}: {prov?.name || '?'} / {cfg?.model || '?'}
              </span>
            )
          })}
        </div>
      </div>
    )
  }

  return (
    <div className="p-3 rounded-xl border border-[var(--border-subtle)] bg-[var(--bg-elevated)] space-y-3">
      <div className="flex items-center justify-between">
        <span className="text-xs font-medium text-[var(--text-primary)]">LLM Providers</span>
        {allSlotsReady && (
          <button onClick={() => setCollapsed(true)}
            className="text-[10px] text-[var(--text-tertiary)] hover:text-[var(--text-secondary)]">
            Collapse
          </button>
        )}
      </div>

      {/* Preset selector */}
      <div className="grid grid-cols-2 gap-1.5">
        {PRESETS.map(p => (
          <button key={p.value} onClick={() => { setPreset(p.value); setError('') }}
            className={cn(
              'p-2 rounded-lg border text-left transition-colors',
              preset === p.value
                ? 'border-[var(--accent-primary)]/50 bg-[var(--accent-glow)]'
                : 'border-[var(--border-subtle)] hover:border-[var(--border-default)]'
            )}>
            <span className="text-[11px] font-medium text-[var(--text-primary)]">{p.label}</span>
            <p className="text-[9px] text-[var(--text-tertiary)]">{p.desc}</p>
          </button>
        ))}
      </div>

      {/* Key inputs */}
      <div className="space-y-2">
        {preset === 'netmind' && (
          <input type="password" value={netmindKey} onChange={e => setNetmindKey(e.target.value)}
            placeholder="NetMind API Key"
            className="w-full px-3 py-1.5 text-xs rounded-lg border border-[var(--border-default)] bg-[var(--bg-primary)] text-[var(--text-primary)] outline-none focus:border-[var(--accent-primary)]" />
        )}
        {preset === 'anthropic_openai' && (
          <>
            <input type="password" value={anthropicKey} onChange={e => setAnthropicKey(e.target.value)}
              placeholder="Anthropic API Key (sk-ant-...)"
              className="w-full px-3 py-1.5 text-xs rounded-lg border border-[var(--border-default)] bg-[var(--bg-primary)] text-[var(--text-primary)] outline-none focus:border-[var(--accent-primary)]" />
            <input type="password" value={openaiKey} onChange={e => setOpenaiKey(e.target.value)}
              placeholder="OpenAI API Key (sk-...)"
              className="w-full px-3 py-1.5 text-xs rounded-lg border border-[var(--border-default)] bg-[var(--bg-primary)] text-[var(--text-primary)] outline-none focus:border-[var(--accent-primary)]" />
          </>
        )}
        {preset === 'claude_openai' && (
          <input type="password" value={openaiKey} onChange={e => setOpenaiKey(e.target.value)}
            placeholder="OpenAI API Key (sk-...)"
            className="w-full px-3 py-1.5 text-xs rounded-lg border border-[var(--border-default)] bg-[var(--bg-primary)] text-[var(--text-primary)] outline-none focus:border-[var(--accent-primary)]" />
        )}
        {preset === 'custom' && (
          <>
            <div className="grid grid-cols-2 gap-1.5">
              <input type="text" value={customName} onChange={e => setCustomName(e.target.value)}
                placeholder="Provider name"
                className="px-3 py-1.5 text-xs rounded-lg border border-[var(--border-default)] bg-[var(--bg-primary)] text-[var(--text-primary)] outline-none focus:border-[var(--accent-primary)]" />
              <select value={customProtocol} onChange={e => setCustomProtocol(e.target.value as 'openai' | 'anthropic')}
                className="px-2 py-1.5 text-xs rounded-lg border border-[var(--border-default)] bg-[var(--bg-primary)] text-[var(--text-primary)] outline-none">
                <option value="openai">OpenAI</option>
                <option value="anthropic">Anthropic</option>
              </select>
            </div>
            <div className="grid grid-cols-2 gap-1.5">
              <select value={customAuthType} onChange={e => setCustomAuthType(e.target.value as 'api_key' | 'bearer_token')}
                className="px-2 py-1.5 text-xs rounded-lg border border-[var(--border-default)] bg-[var(--bg-primary)] text-[var(--text-primary)] outline-none">
                <option value="api_key">API Key</option>
                <option value="bearer_token">Bearer Token</option>
              </select>
            </div>
            <input type="text" value={customUrl} onChange={e => setCustomUrl(e.target.value)}
              placeholder="Base URL (https://...)"
              className="w-full px-3 py-1.5 text-xs rounded-lg border border-[var(--border-default)] bg-[var(--bg-primary)] text-[var(--text-primary)] outline-none focus:border-[var(--accent-primary)]" />
            <input type="password" value={customKey} onChange={e => setCustomKey(e.target.value)}
              placeholder="API Key"
              className="w-full px-3 py-1.5 text-xs rounded-lg border border-[var(--border-default)] bg-[var(--bg-primary)] text-[var(--text-primary)] outline-none focus:border-[var(--accent-primary)]" />
          </>
        )}

        <button onClick={handleApply} disabled={applying}
          className={cn(
            'w-full py-1.5 text-xs font-medium rounded-lg transition-all',
            'bg-[var(--accent-primary)]/10 text-[var(--accent-primary)]',
            'hover:bg-[var(--accent-primary)]/20 border border-[var(--accent-primary)]/20',
            'disabled:opacity-40'
          )}>
          {applying ? 'Applying...' : hasProviders ? 'Re-configure' : 'Apply'}
        </button>
        {error && <p className="text-[10px] text-[var(--color-error)]">{error}</p>}
      </div>

      {/* Slot configuration */}
      {hasProviders && (
        <div className="space-y-2 pt-2 border-t border-[var(--border-subtle)]">
          <span className="text-[10px] text-[var(--text-tertiary)] uppercase tracking-wider">Slots</span>
          {['agent', 'embedding', 'helper_llm'].map(slotName => {
            const meta = SLOT_META[slotName]
            const slotData = slots[slotName]
            const cfg = slotData?.config
            const isReady = !!(cfg?.provider_id && cfg?.model)
            const matchingProvs = Object.entries(providers)
              .filter(([, p]) => p.protocol === meta.protocol && p.is_active)
            const currentProv = cfg?.provider_id ? providers[cfg.provider_id] : null
            const models = currentProv ? getModels(currentProv.preset, slotName) : []

            return (
              <div key={slotName} className={cn(
                'p-2 rounded-lg border',
                isReady ? 'border-[var(--color-success)]/20 bg-[var(--color-success)]/5' : 'border-[var(--color-warning)]/20 bg-[var(--color-warning)]/5'
              )}>
                <div className="flex items-center justify-between mb-1.5">
                  <span className="text-[11px] font-medium text-[var(--text-primary)]">
                    {meta.label} <span className="text-[var(--text-tertiary)] font-normal">({meta.desc})</span>
                  </span>
                  {isReady ? <span className="text-[var(--color-success)] text-xs">{'\u2713'}</span> : <span className="text-[10px] text-[var(--color-warning)]">Needed</span>}
                </div>
                {matchingProvs.length > 0 ? (
                  <div className="grid grid-cols-2 gap-1.5">
                    <select value={cfg?.provider_id || ''}
                      onChange={e => {
                        const pid = e.target.value
                        const prov = providers[pid]
                        if (prov) {
                          const ms = getModels(prov.preset, slotName)
                          const def = ms.find(m => m.is_default) || ms[0]
                          if (def) handleSlotChange(slotName, pid, def.model_id)
                        }
                      }}
                      className="px-2 py-1 text-[10px] rounded border border-[var(--border-default)] bg-[var(--bg-primary)] text-[var(--text-primary)] outline-none">
                      <option value="">Provider...</option>
                      {matchingProvs.map(([id, p]) => <option key={id} value={id}>{p.name}</option>)}
                    </select>
                    {currentProv?.preset === 'custom' || currentProv?.preset === 'claude_oauth' ? (
                      <input type="text" value={cfg?.model || ''}
                        onChange={e => { if (cfg?.provider_id) handleSlotChange(slotName, cfg.provider_id, e.target.value) }}
                        placeholder="Model name"
                        className="px-2 py-1 text-[10px] rounded border border-[var(--border-default)] bg-[var(--bg-primary)] text-[var(--text-primary)] outline-none" />
                    ) : (
                      <select value={cfg?.model || ''}
                        onChange={e => { if (cfg?.provider_id) handleSlotChange(slotName, cfg.provider_id, e.target.value) }}
                        className="px-2 py-1 text-[10px] rounded border border-[var(--border-default)] bg-[var(--bg-primary)] text-[var(--text-primary)] outline-none">
                        <option value="">Model...</option>
                        {models.map(m => <option key={m.model_id} value={m.model_id}>{m.display_name}</option>)}
                      </select>
                    )}
                  </div>
                ) : (
                  <p className="text-[10px] text-[var(--color-error)]">No {meta.protocol} provider. Add one above.</p>
                )}
              </div>
            )
          })}
        </div>
      )}
    </div>
  )
}
