/**
 * @file ProviderConfigView.tsx
 * @description LLM Provider configuration for the Setup Wizard config phase
 *
 * Flow:
 *   1. User picks a preset (NetMind / Anthropic+OpenAI / Claude OAuth+OpenAI / Custom)
 *   2. User enters API key(s)
 *   3. System auto-fills 3 slots (agent, embedding, helper_llm)
 *   4. User can adjust models via dropdowns
 *   5. All 3 slots must be ✅ before "onReady" is called
 */

import React, { useState, useEffect, useCallback } from 'react'

// Backend API base (backend is already running when config phase starts)
const API = 'http://localhost:8000'

type PresetType = 'netmind' | 'anthropic_openai' | 'claude_openai' | 'custom'

interface SlotStatus {
  provider_id: string
  model: string
}

interface ProviderSummary {
  provider_id: string
  name: string
  preset: string
  protocol: string
  auth_type: string
  is_active: boolean
}

interface ModelOption {
  model_id: string
  display_name: string
  slot_types: string[]
  dimensions: number | null
  is_default: boolean
}

interface ProviderConfigViewProps {
  /** Called when all 3 slots are configured and user clicks continue */
  onReady: () => void
  /** Existing Claude auth info for the OAuth option */
  claudeAuth: ClaudeAuthInfo | null
  /** Login status for Claude Code OAuth */
  loginStatus: LoginProcessStatus
  /** Trigger Claude Code login */
  onStartClaudeLogin: () => void
  /** Cancel Claude Code login */
  onCancelClaudeLogin: () => void
  /** Send auth code input */
  onSendClaudeLoginInput: (input: string) => void
}

const SLOT_LABELS: Record<string, { label: string; desc: string; protocol: string }> = {
  agent: { label: 'Agent', desc: 'Main dialogue model', protocol: 'anthropic' },
  embedding: { label: 'Embedding', desc: 'Vector search', protocol: 'openai' },
  helper_llm: { label: 'Helper LLM', desc: 'Auxiliary tasks', protocol: 'openai' },
}

const ProviderConfigView: React.FC<ProviderConfigViewProps> = ({
  onReady,
  claudeAuth,
  loginStatus,
  onStartClaudeLogin,
  onCancelClaudeLogin,
  onSendClaudeLoginInput,
}) => {
  const [preset, setPreset] = useState<PresetType>('netmind')
  const [netmindKey, setNetmindKey] = useState('')
  const [anthropicKey, setAnthropicKey] = useState('')
  const [openaiKey, setOpenaiKey] = useState('')
  const [customName, setCustomName] = useState('')
  const [customProtocol, setCustomProtocol] = useState<'openai' | 'anthropic'>('openai')
  const [customAuthType, setCustomAuthType] = useState<'api_key' | 'bearer_token'>('api_key')
  const [customKey, setCustomKey] = useState('')
  const [customUrl, setCustomUrl] = useState('')

  // Provider state from backend
  const [providers, setProviders] = useState<Record<string, ProviderSummary>>({})
  const [slots, setSlots] = useState<Record<string, { config: SlotStatus | null; required_protocols: string[] }>>({})
  const [modelCatalog, setModelCatalog] = useState<Record<string, ModelOption[]>>({})
  const [applying, setApplying] = useState(false)
  const [error, setError] = useState('')
  const [testing, setTesting] = useState<string | null>(null)
  const [testResult, setTestResult] = useState<Record<string, { ok: boolean; msg: string }>>({})

  // Load catalog on mount
  useEffect(() => {
    fetch(`${API}/api/providers/catalog`)
      .then(r => r.json())
      .then(data => {
        if (data.success) setModelCatalog(data.catalog)
      })
      .catch(() => {})
  }, [])

  // Load current provider config
  const refreshConfig = useCallback(async () => {
    try {
      const res = await fetch(`${API}/api/providers`).then(r => r.json())
      if (res.success) {
        setProviders(res.data.providers)
        setSlots(res.data.slots)
      }
    } catch {}
  }, [])

  useEffect(() => { refreshConfig() }, [refreshConfig])

  // Check if all 3 slots are configured
  const allSlotsReady = ['agent', 'embedding', 'helper_llm'].every(
    s => slots[s]?.config?.provider_id && slots[s]?.config?.model
  )

  // Apply preset
  const handleApplyPreset = async () => {
    setApplying(true)
    setError('')
    try {
      if (preset === 'netmind') {
        if (!netmindKey.trim()) { setError('Please enter your NetMind API Key'); setApplying(false); return }
        const res = await fetch(`${API}/api/providers/preset`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ preset: 'netmind', api_key: netmindKey.trim() })
        }).then(r => r.json())
        if (!res.success) { setError(res.error || 'Failed to apply preset'); setApplying(false); return }
      } else if (preset === 'anthropic_openai') {
        if (!anthropicKey.trim() || !openaiKey.trim()) { setError('Please enter both API keys'); setApplying(false); return }
        await fetch(`${API}/api/providers/preset`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ preset: 'anthropic', api_key: anthropicKey.trim() })
        })
        await fetch(`${API}/api/providers/merge`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ preset: 'openai', api_key: openaiKey.trim() })
        })
      } else if (preset === 'claude_openai') {
        if (!openaiKey.trim()) { setError('Please enter your OpenAI API Key'); setApplying(false); return }
        await fetch(`${API}/api/providers/preset`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ preset: 'claude_oauth', api_key: '' })
        })
        await fetch(`${API}/api/providers/merge`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ preset: 'openai', api_key: openaiKey.trim() })
        })
      } else if (preset === 'custom') {
        if (!customKey.trim() || !customUrl.trim()) { setError('Please fill in all custom provider fields'); setApplying(false); return }
        await fetch(`${API}/api/providers`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            name: customName || 'Custom Provider',
            protocol: customProtocol,
            auth_type: customAuthType,
            api_key: customKey.trim(),
            base_url: customUrl.trim(),
          })
        })
      }
      await refreshConfig()
    } catch (e) {
      setError('Network error. Is the backend running?')
    }
    setApplying(false)
  }

  // Test a provider
  const handleTest = async (providerId: string) => {
    setTesting(providerId)
    try {
      const res = await fetch(`${API}/api/providers/${providerId}/test`, { method: 'POST' }).then(r => r.json())
      setTestResult(prev => ({ ...prev, [providerId]: { ok: res.success, msg: res.message } }))
    } catch {
      setTestResult(prev => ({ ...prev, [providerId]: { ok: false, msg: 'Network error' } }))
    }
    setTesting(null)
  }

  // Update slot model
  const handleSlotModelChange = async (slotName: string, providerId: string, model: string) => {
    await fetch(`${API}/api/providers/slots/${slotName}`, {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ provider_id: providerId, model })
    })
    await refreshConfig()
  }

  // Get models for a provider's preset and slot
  const getModelsForSlot = (providerPreset: string, slotName: string): ModelOption[] => {
    const all = modelCatalog[providerPreset] || []
    return all.filter(m => m.slot_types.includes(slotName))
  }

  // Check if any providers exist
  const hasProviders = Object.keys(providers).length > 0

  return (
    <div className="space-y-5">
      <h2 className="text-sm font-semibold text-gray-700">LLM Provider Configuration</h2>

      {/* Step 1: Preset Selection */}
      <div className="space-y-3">
        <p className="text-xs text-gray-500">Choose how to connect to AI services:</p>

        <div className="space-y-2">
          {([
            { value: 'netmind' as PresetType, label: 'NetMind', desc: 'One API key for everything (recommended)' },
            { value: 'anthropic_openai' as PresetType, label: 'Anthropic + OpenAI', desc: 'Separate keys for each service' },
            { value: 'claude_openai' as PresetType, label: 'Claude Code Login + OpenAI', desc: 'OAuth for agent, OpenAI key for the rest' },
            { value: 'custom' as PresetType, label: 'Custom Provider', desc: 'Manual URL, key, and protocol' },
          ]).map(opt => (
            <label key={opt.value} className="titlebar-no-drag flex items-start gap-3 p-2.5 rounded-lg border cursor-pointer transition-colors hover:bg-gray-50"
              style={{ borderColor: preset === opt.value ? '#3b82f6' : '#e5e7eb', background: preset === opt.value ? '#eff6ff' : '' }}>
              <input type="radio" name="preset" checked={preset === opt.value}
                onChange={() => { setPreset(opt.value); setError('') }}
                className="mt-0.5 accent-blue-500" />
              <div>
                <span className="text-sm font-medium text-gray-700">{opt.label}</span>
                <p className="text-[11px] text-gray-400">{opt.desc}</p>
              </div>
            </label>
          ))}
        </div>
      </div>

      {/* Step 2: API Key Input (based on preset) */}
      <div className="space-y-3">
        {preset === 'netmind' && (
          <div>
            <label className="block text-xs font-medium text-gray-600 mb-1">NetMind API Key</label>
            <div className="flex gap-2">
              <input type="password" value={netmindKey}
                onChange={e => setNetmindKey(e.target.value)} placeholder="Your NetMind API Key"
                className="titlebar-no-drag flex-1 px-3 py-2 text-sm border border-gray-300 rounded-lg focus:ring-2 focus:ring-blue-500 focus:border-blue-500 outline-none" />
              <button onClick={() => window.nexus.openExternal('https://www.netmind.ai/user/dashboard')}
                className="titlebar-no-drag px-3 py-2 text-xs text-blue-600 bg-blue-50 rounded-lg hover:bg-blue-100 whitespace-nowrap">
                Get Key
              </button>
            </div>
          </div>
        )}

        {preset === 'anthropic_openai' && (
          <>
            <div>
              <label className="block text-xs font-medium text-gray-600 mb-1">Anthropic API Key</label>
              <div className="flex gap-2">
                <input type="password" value={anthropicKey}
                  onChange={e => setAnthropicKey(e.target.value)} placeholder="sk-ant-..."
                  className="titlebar-no-drag flex-1 px-3 py-2 text-sm border border-gray-300 rounded-lg focus:ring-2 focus:ring-blue-500 focus:border-blue-500 outline-none" />
                <button onClick={() => window.nexus.openExternal('https://console.anthropic.com/settings/keys')}
                  className="titlebar-no-drag px-3 py-2 text-xs text-blue-600 bg-blue-50 rounded-lg hover:bg-blue-100 whitespace-nowrap">
                  Get Key
                </button>
              </div>
            </div>
            <div>
              <label className="block text-xs font-medium text-gray-600 mb-1">OpenAI API Key</label>
              <div className="flex gap-2">
                <input type="password" value={openaiKey}
                  onChange={e => setOpenaiKey(e.target.value)} placeholder="sk-..."
                  className="titlebar-no-drag flex-1 px-3 py-2 text-sm border border-gray-300 rounded-lg focus:ring-2 focus:ring-blue-500 focus:border-blue-500 outline-none" />
                <button onClick={() => window.nexus.openExternal('https://platform.openai.com/api-keys')}
                  className="titlebar-no-drag px-3 py-2 text-xs text-blue-600 bg-blue-50 rounded-lg hover:bg-blue-100 whitespace-nowrap">
                  Get Key
                </button>
              </div>
            </div>
          </>
        )}

        {preset === 'claude_openai' && (
          <>
            {/* Claude Code Auth Panel */}
            <div className="p-3 bg-gray-50 rounded-lg border border-gray-200">
              <p className="text-xs font-medium text-gray-700 mb-2">Claude Code Authentication (Agent Slot)</p>
              {claudeAuth?.cliInstalled && (
                <div className="flex flex-wrap gap-x-4 gap-y-1 mb-2 text-[11px]">
                  <span className="flex items-center gap-1.5">
                    <span className={`inline-block w-1.5 h-1.5 rounded-full ${
                      claudeAuth.authStatus.state === 'logged_in' ? 'bg-green-500' :
                      claudeAuth.authStatus.state === 'expired' ? 'bg-yellow-500' : 'bg-gray-400'
                    }`} />
                    {claudeAuth.authStatus.state === 'logged_in' ? 'Logged in' :
                     claudeAuth.authStatus.state === 'expired' ? 'Expired' : 'Not logged in'}
                  </span>
                </div>
              )}
              {claudeAuth?.cliInstalled && claudeAuth.authStatus.state !== 'logged_in' && (
                <div className="flex items-center gap-2 mb-2">
                  <button onClick={onStartClaudeLogin} disabled={loginStatus.state === 'running'}
                    className="titlebar-no-drag px-3 py-1.5 text-xs font-medium text-white bg-indigo-500 rounded-lg hover:bg-indigo-600 disabled:opacity-40 transition-colors">
                    {loginStatus.state === 'running' ? 'Waiting...' : 'Login with Claude Code'}
                  </button>
                  {loginStatus.state === 'running' && (
                    <>
                      <div className="w-3 h-3 border-2 border-indigo-500 border-t-transparent rounded-full animate-spin" />
                      <button onClick={onCancelClaudeLogin}
                        className="titlebar-no-drag text-xs text-gray-500 hover:text-gray-700 underline">Cancel</button>
                    </>
                  )}
                  {loginStatus.state === 'success' && <span className="text-xs text-green-600">Login successful!</span>}
                  {(loginStatus.state === 'failed' || loginStatus.state === 'timeout') && (
                    <span className="text-xs text-red-500">{loginStatus.message}</span>
                  )}
                </div>
              )}
              {loginStatus.state === 'running' && (
                <div className="flex items-center gap-2 mt-2">
                  <input type="text" placeholder="Paste auth code here"
                    className="titlebar-no-drag flex-1 px-3 py-1.5 text-xs border border-gray-300 rounded-lg focus:outline-none focus:ring-1 focus:ring-indigo-400"
                    onKeyDown={e => { if (e.key === 'Enter') { const v = (e.target as HTMLInputElement).value.trim(); if (v) { onSendClaudeLoginInput(v); (e.target as HTMLInputElement).value = '' } } }}
                  />
                  <span className="text-[10px] text-gray-400 shrink-0">Enter to submit</span>
                </div>
              )}
              {!claudeAuth?.cliInstalled && (
                <p className="text-xs text-gray-400">Claude Code CLI not installed. Install it first or use another preset.</p>
              )}
              {claudeAuth?.authStatus.state === 'logged_in' && (
                <p className="text-xs text-green-600">&#10003; Claude Code ready for agent slot</p>
              )}
            </div>
            <div>
              <label className="block text-xs font-medium text-gray-600 mb-1">OpenAI API Key (Embedding + Helper LLM)</label>
              <div className="flex gap-2">
                <input type="password" value={openaiKey}
                  onChange={e => setOpenaiKey(e.target.value)} placeholder="sk-..."
                  className="titlebar-no-drag flex-1 px-3 py-2 text-sm border border-gray-300 rounded-lg focus:ring-2 focus:ring-blue-500 focus:border-blue-500 outline-none" />
                <button onClick={() => window.nexus.openExternal('https://platform.openai.com/api-keys')}
                  className="titlebar-no-drag px-3 py-2 text-xs text-blue-600 bg-blue-50 rounded-lg hover:bg-blue-100 whitespace-nowrap">
                  Get Key
                </button>
              </div>
            </div>
          </>
        )}

        {preset === 'custom' && (
          <div className="space-y-2">
            <div className="grid grid-cols-2 gap-2">
              <div>
                <label className="block text-xs font-medium text-gray-600 mb-1">Provider Name</label>
                <input type="text" value={customName} onChange={e => setCustomName(e.target.value)}
                  placeholder="My Provider"
                  className="titlebar-no-drag w-full px-3 py-1.5 text-sm border border-gray-300 rounded-lg focus:ring-2 focus:ring-blue-500 outline-none" />
              </div>
              <div>
                <label className="block text-xs font-medium text-gray-600 mb-1">Protocol</label>
                <select value={customProtocol} onChange={e => setCustomProtocol(e.target.value as 'openai' | 'anthropic')}
                  className="titlebar-no-drag w-full px-3 py-1.5 text-sm border border-gray-300 rounded-lg focus:ring-2 focus:ring-blue-500 outline-none bg-white">
                  <option value="openai">OpenAI Compatible</option>
                  <option value="anthropic">Anthropic Compatible</option>
                </select>
              </div>
            </div>
            <div>
              <label className="block text-xs font-medium text-gray-600 mb-1">Base URL</label>
              <input type="text" value={customUrl} onChange={e => setCustomUrl(e.target.value)}
                placeholder="https://api.example.com/v1"
                className="titlebar-no-drag w-full px-3 py-1.5 text-sm border border-gray-300 rounded-lg focus:ring-2 focus:ring-blue-500 outline-none" />
            </div>
            <div className="grid grid-cols-2 gap-2">
              <div>
                <label className="block text-xs font-medium text-gray-600 mb-1">Auth Type</label>
                <select value={customAuthType} onChange={e => setCustomAuthType(e.target.value as 'api_key' | 'bearer_token')}
                  className="titlebar-no-drag w-full px-3 py-1.5 text-sm border border-gray-300 rounded-lg focus:ring-2 focus:ring-blue-500 outline-none bg-white">
                  <option value="api_key">API Key</option>
                  <option value="bearer_token">Bearer Token</option>
                </select>
              </div>
              <div>
                <label className="block text-xs font-medium text-gray-600 mb-1">API Key</label>
                <input type="password" value={customKey} onChange={e => setCustomKey(e.target.value)}
                  placeholder="Your API key"
                  className="titlebar-no-drag w-full px-3 py-1.5 text-sm border border-gray-300 rounded-lg focus:ring-2 focus:ring-blue-500 outline-none" />
              </div>
            </div>
          </div>
        )}

        {/* Apply button */}
        <button onClick={handleApplyPreset} disabled={applying}
          className="titlebar-no-drag w-full py-2 text-sm font-medium text-white bg-blue-500 rounded-lg hover:bg-blue-600 disabled:opacity-40 disabled:cursor-not-allowed transition-colors">
          {applying ? 'Applying...' : hasProviders ? 'Re-configure Providers' : 'Apply Configuration'}
        </button>
        {error && <p className="text-xs text-red-500">{error}</p>}
      </div>

      {/* Step 3: Slot Configuration (only show after providers exist) */}
      {hasProviders && (
        <>
          <div className="border-t border-gray-200 my-4" />
          <div className="space-y-3">
            <h3 className="text-xs font-semibold text-gray-600 uppercase tracking-wider">Slot Configuration</h3>
            <p className="text-[11px] text-gray-400">Each slot must be assigned a provider and model before continuing.</p>

            {['agent', 'embedding', 'helper_llm'].map(slotName => {
              const slotInfo = SLOT_LABELS[slotName]
              const slotData = slots[slotName]
              const currentConfig = slotData?.config
              const isConfigured = !!(currentConfig?.provider_id && currentConfig?.model)

              // Find matching providers for this slot's protocol
              const matchingProviders = Object.entries(providers)
                .filter(([, p]) => p.protocol === slotInfo.protocol && p.is_active)
                .map(([id, p]) => ({ id, ...p }))

              const currentProvider = currentConfig?.provider_id ? providers[currentConfig.provider_id] : null
              const availableModels = currentProvider
                ? getModelsForSlot(currentProvider.preset, slotName)
                : []

              return (
                <div key={slotName} className="p-3 rounded-lg border" style={{
                  borderColor: isConfigured ? '#bbf7d0' : '#fde68a',
                  background: isConfigured ? '#f0fdf4' : '#fffbeb'
                }}>
                  <div className="flex items-center justify-between mb-2">
                    <div>
                      <span className="text-sm font-medium text-gray-700">{slotInfo.label}</span>
                      <span className="text-[10px] text-gray-400 ml-2">{slotInfo.desc}</span>
                      <span className="text-[10px] text-gray-400 ml-1">({slotInfo.protocol})</span>
                    </div>
                    {isConfigured
                      ? <span className="text-green-500 text-sm">&#10003;</span>
                      : <span className="text-yellow-500 text-xs">Not configured</span>
                    }
                  </div>

                  {matchingProviders.length > 0 ? (
                    <div className="grid grid-cols-2 gap-2">
                      <div>
                        <label className="block text-[10px] text-gray-500 mb-0.5">Provider</label>
                        <select value={currentConfig?.provider_id || ''}
                          onChange={e => {
                            const pid = e.target.value
                            const prov = providers[pid]
                            if (prov) {
                              const models = getModelsForSlot(prov.preset, slotName)
                              const defaultModel = models.find(m => m.is_default) || models[0]
                              if (defaultModel) handleSlotModelChange(slotName, pid, defaultModel.model_id)
                            }
                          }}
                          className="titlebar-no-drag w-full px-2 py-1.5 text-xs border border-gray-200 rounded-lg bg-white focus:ring-1 focus:ring-blue-400 outline-none">
                          <option value="">Select...</option>
                          {matchingProviders.map(p => (
                            <option key={p.id} value={p.id}>{p.name}</option>
                          ))}
                        </select>
                      </div>
                      <div>
                        <label className="block text-[10px] text-gray-500 mb-0.5">Model</label>
                        {currentProvider?.preset === 'custom' || currentProvider?.preset === 'claude_oauth' ? (
                          <input type="text" value={currentConfig?.model || ''}
                            onChange={e => {
                              if (currentConfig?.provider_id) handleSlotModelChange(slotName, currentConfig.provider_id, e.target.value)
                            }}
                            placeholder="Enter model name"
                            className="titlebar-no-drag w-full px-2 py-1.5 text-xs border border-gray-200 rounded-lg focus:ring-1 focus:ring-blue-400 outline-none" />
                        ) : (
                          <select value={currentConfig?.model || ''}
                            onChange={e => {
                              if (currentConfig?.provider_id) handleSlotModelChange(slotName, currentConfig.provider_id, e.target.value)
                            }}
                            className="titlebar-no-drag w-full px-2 py-1.5 text-xs border border-gray-200 rounded-lg bg-white focus:ring-1 focus:ring-blue-400 outline-none">
                            <option value="">Select...</option>
                            {availableModels.map(m => (
                              <option key={m.model_id} value={m.model_id}>
                                {m.display_name}{m.dimensions ? ` (${m.dimensions}d)` : ''}
                              </option>
                            ))}
                          </select>
                        )}
                      </div>
                    </div>
                  ) : (
                    <p className="text-xs text-red-400">
                      No {slotInfo.protocol} provider configured. Add one above.
                    </p>
                  )}

                  {/* Test button for configured slots */}
                  {isConfigured && currentConfig?.provider_id && (
                    <div className="mt-2 flex items-center gap-2">
                      <button onClick={() => handleTest(currentConfig.provider_id)}
                        disabled={testing === currentConfig.provider_id}
                        className="titlebar-no-drag text-[10px] text-blue-600 hover:text-blue-700 underline disabled:opacity-40">
                        {testing === currentConfig.provider_id ? 'Testing...' : 'Test connection'}
                      </button>
                      {testResult[currentConfig.provider_id] && (
                        <span className={`text-[10px] ${testResult[currentConfig.provider_id].ok ? 'text-green-600' : 'text-red-500'}`}>
                          {testResult[currentConfig.provider_id].msg}
                        </span>
                      )}
                    </div>
                  )}
                </div>
              )
            })}
          </div>

          {/* Continue button */}
          <button onClick={onReady} disabled={!allSlotsReady}
            className="titlebar-no-drag w-full py-2.5 text-sm font-medium text-white bg-green-500 rounded-lg hover:bg-green-600 disabled:opacity-40 disabled:cursor-not-allowed transition-colors">
            {allSlotsReady ? 'Continue to Finish Setup' : 'Configure all 3 slots to continue'}
          </button>
        </>
      )}
    </div>
  )
}

export default ProviderConfigView
