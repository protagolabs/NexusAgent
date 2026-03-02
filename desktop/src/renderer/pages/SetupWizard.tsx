/**
 * @file SetupWizard.tsx
 * @description Single-page environment configuration — .env form + one-click install progress bar
 *
 * After the user fills in API Keys and database configuration, clicking "Apply & Start",
 * automatically executes all environment installation and service startup procedures.
 */

import React, { useEffect, useState } from 'react'
import AsciiBanner from '../components/AsciiBanner'

interface SetupWizardProps {
  onComplete: () => void
}

/** External link mapping */
const KEY_LINKS: Record<string, { label: string; url: string }> = {
  OPENAI_API_KEY: { label: 'Get Key', url: 'https://platform.openai.com/api-keys' },
  GOOGLE_API_KEY: { label: 'Get Key', url: 'https://aistudio.google.com/apikey' },
  NETMIND_API_KEY: { label: 'Get Key', url: 'https://www.netmind.ai' },
  ANTHROPIC_API_KEY: { label: 'Get Key', url: 'https://console.anthropic.com/settings/keys' },
  LLM_API_KEY: { label: 'Get Key', url: 'https://openrouter.ai/keys' }
}

/** NetMind one-click configuration presets (consistent with run.sh auto-configure logic) */
const NETMIND_PRESETS: Record<string, string> = {
  LLM_MODEL: 'deepseek-ai/DeepSeek-V3.2',
  LLM_BASE_URL: 'https://api.netmind.ai/inference-api/openai/v1',
  VECTORIZE_PROVIDER: 'deepinfra',
  VECTORIZE_MODEL: 'BAAI/bge-m3',
  VECTORIZE_BASE_URL: 'https://api.netmind.ai/inference-api/openai/v1',
  RERANK_PROVIDER: 'none',
  RERANK_API_KEY: 'EMPTY',
  RERANK_BASE_URL: '',
  RERANK_MODEL: ''
}

/** EverMemOS group configuration */
const EM_GROUP_LABELS: Record<string, string> = {
  llm: 'LLM',
  vectorize: 'Embedding',
  rerank: 'Rerank',
  infrastructure: 'Infrastructure (usually no changes needed)',
  other: 'Other'
}

/** Render URLs in text as clickable links */
const renderWithLinks = (text: string): React.ReactNode => {
  const urlRegex = /(https?:\/\/[^\s]+)/g
  const parts = text.split(urlRegex)
  if (parts.length === 1) return text
  return parts.map((part, i) =>
    urlRegex.test(part) ? (
      <a
        key={i}
        onClick={(e) => { e.preventDefault(); window.nexus.openExternal(part) }}
        className="underline text-blue-600 hover:text-blue-800 cursor-pointer"
      >
        {part}
      </a>
    ) : (
      <React.Fragment key={i}>{part}</React.Fragment>
    )
  )
}

const SetupWizard: React.FC<SetupWizardProps> = ({ onComplete }) => {
  const [fields, setFields] = useState<EnvField[]>([])
  const [values, setValues] = useState<Record<string, string>>({})
  const [running, setRunning] = useState(false)
  const [steps, setSteps] = useState<SetupProgress[]>([])
  const [error, setError] = useState<string | null>(null)

  // Claude Code authentication state
  const [claudeAuth, setClaudeAuth] = useState<ClaudeAuthInfo | null>(null)
  const [loginStatus, setLoginStatus] = useState<LoginProcessStatus>({ state: 'idle' })
  const [setupToken, setSetupToken] = useState('')
  const [tokenResult, setTokenResult] = useState<{ valid: boolean; message: string } | null>(null)

  // EverMemOS state
  const [emFields, setEmFields] = useState<EverMemOSEnvField[]>([])
  const [emValues, setEmValues] = useState<Record<string, string>>({})
  const [emAdvancedOpen, setEmAdvancedOpen] = useState(false)
  const [emMode, setEmMode] = useState<'netmind' | 'custom'>('netmind')

  // Load .env configuration
  useEffect(() => {
    window.nexus.getEnv().then(({ config, fields: f }) => {
      setFields(f)
      setValues(config)
    })

    // Load EverMemOS .env configuration (always displayed, no need to check if directory exists)
    window.nexus.getEverMemOSEnv().then(({ config, fields: f }) => {
      setEmFields(f)
      setEmValues(config)
    })
  }, [])

  // Load Claude Code authentication status
  useEffect(() => {
    window.nexus.getClaudeAuthInfo().then(setClaudeAuth)
  }, [])

  // Listen for Claude Code login status updates
  useEffect(() => {
    const unsub = window.nexus.onClaudeLoginStatus((status: LoginProcessStatus) => {
      setLoginStatus(status)
      if (status.state === 'success') {
        window.nexus.getClaudeAuthInfo().then(setClaudeAuth)
      }
    })
    return unsub
  }, [])

  // Listen for installation progress
  useEffect(() => {
    const unsubscribe = window.nexus.onSetupProgress((progress: SetupProgress) => {
      setSteps((prev) => {
        // Replace progress for the same step (status update)
        const existing = prev.findIndex((s) => s.step === progress.step)
        if (existing >= 0) {
          const next = [...prev]
          next[existing] = progress
          return next
        }
        return [...prev, progress]
      })
    })
    return unsubscribe
  }, [])

  /** Assemble the final EverMemOS configuration to be written */
  const buildFinalEmValues = (): Record<string, string> => {
    if (emMode === 'netmind') {
      return {
        ...emValues,
        ...NETMIND_PRESETS,
        LLM_API_KEY: values['NETMIND_API_KEY'] || '',
        VECTORIZE_API_KEY: values['NETMIND_API_KEY'] || ''
      }
    }
    return emValues
  }

  /** Full installation flow: save .env -> execute autoSetup (12 steps) */
  const handleInstallAndStart = async () => {
    setRunning(true)
    setError(null)
    setSteps([])

    await window.nexus.setEnv(values)
    await window.nexus.setEverMemOSEnv(buildFinalEmValues())

    const result = await window.nexus.autoSetup({ skipEverMemOS })

    if (result.success) {
      await window.nexus.setSetupComplete()
      onComplete()
    } else {
      setError(result.error ?? 'An unknown error occurred during setup')
      setRunning(false)
    }
  }

  /** Quick start: skip installation steps, bring up Docker + services (with progress feedback) */
  const handleStartOnly = async () => {
    setRunning(true)
    setError(null)
    setSteps([])

    await window.nexus.setEnv(values)
    await window.nexus.setEverMemOSEnv(buildFinalEmValues())

    const result = await window.nexus.quickStart({ skipEverMemOS })
    if (result.success) {
      await window.nexus.setSetupComplete()
      onComplete()
    } else {
      setError(result.error ?? 'Start failed. Try "Install & Start" if this is the first run.')
      setRunning(false)
    }
  }

  // Check if required fields are filled
  const PLACEHOLDER_VALUES = ['sk-or-v1-xxxx', 'xxxxx']
  const requiredFilled = fields
    .filter((f) => f.required)
    .every((f) => values[f.key]?.trim())

  // Check if EverMemOS is configured
  const emConfigured = emMode === 'netmind'
    ? !!values['NETMIND_API_KEY']?.trim()
    : emFields.filter((f) => f.required).every((f) => {
        const v = emValues[f.key]?.trim()
        return v && !PLACEHOLDER_VALUES.includes(v)
      })
  const skipEverMemOS = !emConfigured

  // Group: API Keys and Database configuration
  const apiFields = fields.filter(
    (f) => f.key.includes('API_KEY') || f.key.includes('SECRET') || f.key.includes('BASE_URL')
  )
  const dbFields = fields.filter(
    (f) => f.key.startsWith('DB_')
  )

  return (
    <div className="flex flex-col h-screen bg-white">
      {/* Title bar drag area */}
      <div className="h-8 titlebar-drag shrink-0" />

      {/* Banner */}
      <div className="px-8 pt-2 pb-4">
        <AsciiBanner />
      </div>

      {/* Form content */}
      <div className="flex-1 overflow-y-auto px-8 pb-6">
        {/* API Keys */}
        <div className="space-y-3">
          {apiFields.map((field) => (
            <div key={field.key}>
              <label className="block text-sm font-medium text-gray-700 mb-1">
                {field.label}
                {field.required && <span className="text-red-500 ml-0.5">*</span>}
              </label>
              <div className="flex gap-2">
                <input
                  type="password"
                  value={values[field.key] || ''}
                  onChange={(e) => setValues((v) => ({ ...v, [field.key]: e.target.value }))}
                  placeholder={field.placeholder}
                  disabled={running}
                  className="titlebar-no-drag flex-1 px-3 py-2 text-sm border border-gray-300 rounded-lg focus:ring-2 focus:ring-blue-500 focus:border-blue-500 outline-none transition-all disabled:bg-gray-50 disabled:text-gray-400"
                />
                {KEY_LINKS[field.key] && (
                  <button
                    onClick={() => window.nexus.openExternal(KEY_LINKS[field.key].url)}
                    className="titlebar-no-drag px-3 py-2 text-xs text-blue-600 bg-blue-50 rounded-lg hover:bg-blue-100 whitespace-nowrap transition-colors"
                  >
                    {KEY_LINKS[field.key].label}
                  </button>
                )}
              </div>
              {(field.key === 'ANTHROPIC_API_KEY' || field.key === 'ANTHROPIC_BASE_URL') && (
                <p className="text-xs text-gray-400 mt-1">
                  If you have already logged in via Claude Code CLI locally, you can leave this empty.
                </p>
              )}
            </div>
          ))}
        </div>

        {/* Claude Code authentication panel */}
        <div className="mt-4 p-4 bg-gray-50 rounded-lg border border-gray-200">
          <p className="text-sm font-medium text-gray-700 mb-3">Claude Code Authentication</p>

          {/* Status indicator — only show when CLI is installed */}
          {claudeAuth?.cliInstalled && (
            <div className="flex flex-wrap gap-x-6 gap-y-1 mb-3 text-xs">
              <span className="flex items-center gap-1.5">
                <span className="inline-block w-1.5 h-1.5 rounded-full bg-green-500" />
                CLI: {claudeAuth.cliVersion || 'installed'}
              </span>
              <span className="flex items-center gap-1.5">
                <span className={`inline-block w-1.5 h-1.5 rounded-full ${
                  claudeAuth.authStatus.state === 'logged_in' ? 'bg-green-500' :
                  claudeAuth.authStatus.state === 'expired' ? 'bg-yellow-500' :
                  'bg-gray-400'
                }`} />
                {claudeAuth.authStatus.state === 'logged_in' && (
                  <>Logged in{claudeAuth.authStatus.expiresAt
                    ? ` (expires: ${new Date(claudeAuth.authStatus.expiresAt).toLocaleDateString()})`
                    : ''
                  }</>
                )}
                {claudeAuth.authStatus.state === 'expired' && 'Login expired'}
                {claudeAuth.authStatus.state === 'not_logged_in' && 'Not logged in'}
              </span>
              {claudeAuth.hasApiKey && (
                <span className="flex items-center gap-1.5">
                  <span className="inline-block w-1.5 h-1.5 rounded-full bg-green-500" />
                  API Key configured
                </span>
              )}
            </div>
          )}

          {/* Option 1: Browser OAuth login — only show when CLI is installed */}
          {claudeAuth?.cliInstalled && (
            <div className="mb-3">
              <p className="text-xs text-gray-500 mb-2">
                Option 1 (Recommended): Click below to open browser and authorize with your Anthropic account.
              </p>
              {/* Button row */}
              <div className="flex items-center gap-2">
                <button
                  onClick={() => {
                    setLoginStatus({ state: 'running', message: 'Opening browser...' })
                    window.nexus.startClaudeLogin()
                  }}
                  disabled={running || loginStatus.state === 'running'}
                  className="titlebar-no-drag px-4 py-1.5 text-xs font-medium text-white bg-indigo-500 rounded-lg hover:bg-indigo-600 disabled:opacity-40 disabled:cursor-not-allowed transition-colors"
                >
                  {loginStatus.state === 'running' ? 'Waiting for browser...' :
                   claudeAuth?.authStatus.state === 'logged_in' ? 'Re-login' :
                   'Login with Claude Code'}
                </button>
                {loginStatus.state === 'running' && (
                  <>
                    <div className="w-3 h-3 border-2 border-indigo-500 border-t-transparent rounded-full animate-spin" />
                    <button
                      onClick={() => window.nexus.cancelClaudeLogin()}
                      className="titlebar-no-drag text-xs text-gray-500 hover:text-gray-700 underline"
                    >
                      Cancel
                    </button>
                  </>
                )}
                {loginStatus.state === 'success' && (
                  <span className="text-xs text-green-600 font-medium">Login successful!</span>
                )}
                {(loginStatus.state === 'failed' || loginStatus.state === 'timeout') && (
                  <span className="text-xs text-red-500">{loginStatus.message}</span>
                )}
              </div>
              {/* Running: status message + auth code input (below button row) */}
              {loginStatus.state === 'running' && (
                <div className="mt-2">
                  {loginStatus.message && (
                    <p className="text-xs text-gray-500 mb-1.5">{loginStatus.message}</p>
                  )}
                  <div className="flex items-center gap-2">
                    <input
                      type="text"
                      placeholder="Paste the auth code from browser here"
                      className="titlebar-no-drag flex-1 px-3 py-1.5 text-xs border border-gray-300 rounded-lg focus:outline-none focus:ring-1 focus:ring-indigo-400"
                      onKeyDown={(e) => {
                        if (e.key === 'Enter') {
                          const value = (e.target as HTMLInputElement).value.trim()
                          if (value) {
                            window.nexus.sendClaudeLoginInput(value)
                            ;(e.target as HTMLInputElement).value = ''
                          }
                        }
                      }}
                    />
                    <span className="text-[10px] text-gray-400 shrink-0">Press Enter to submit</span>
                  </div>
                </div>
              )}
            </div>
          )}

          {/* Hint when CLI is not installed */}
          {!claudeAuth?.cliInstalled && (
            <p className="text-xs text-gray-400 mb-3">
              OAuth login will be available after Claude Code is installed during setup. You can use Option 1/2 below for now.
            </p>
          )}

          {/* Option: Paste Setup Token */}
          <div className="mb-2">
            <p className="text-xs text-gray-500 mb-1.5">
              Option {claudeAuth?.cliInstalled ? '2' : '1'}: Run <code className="px-1 py-0.5 bg-gray-200 rounded text-[11px]">claude setup-token</code> on
              another machine and paste the token below.
            </p>
            <div className="flex gap-2">
              <input
                type="password"
                value={setupToken}
                onChange={(e) => { setSetupToken(e.target.value); setTokenResult(null) }}
                placeholder="sk-ant-oat01-..."
                disabled={running}
                className="titlebar-no-drag flex-1 px-3 py-1.5 text-sm border border-gray-300 rounded-lg focus:ring-2 focus:ring-indigo-500 focus:border-indigo-500 outline-none transition-all disabled:bg-gray-50 disabled:text-gray-400"
              />
              <button
                onClick={async () => {
                  const result = await window.nexus.saveSetupToken(setupToken)
                  setTokenResult(result)
                  if (result.valid) {
                    window.nexus.getClaudeAuthInfo().then(setClaudeAuth)
                    // Sync the API Key input field value above
                    setValues((v) => ({ ...v, ANTHROPIC_API_KEY: setupToken.trim() }))
                    setSetupToken('')
                  }
                }}
                disabled={running || !setupToken.trim()}
                className="titlebar-no-drag px-3 py-1.5 text-xs font-medium text-indigo-600 bg-indigo-50 rounded-lg hover:bg-indigo-100 disabled:opacity-40 disabled:cursor-not-allowed transition-colors"
              >
                Save
              </button>
            </div>
            {tokenResult && (
              <p className={`text-xs mt-1 ${tokenResult.valid ? 'text-green-600' : 'text-red-500'}`}>
                {tokenResult.valid ? 'Token saved!' : tokenResult.message}
              </p>
            )}
          </div>

          {/* Option: Fill API Key directly */}
          <p className="text-xs text-gray-400">
            Option {claudeAuth?.cliInstalled ? '3' : '2'}: Fill in the Anthropic API Key field above directly.
          </p>
        </div>

        {/* Divider */}
        <div className="my-5 border-t border-gray-200" />

        {/* Database configuration */}
        <div>
          <p className="text-xs text-gray-400 mb-3">
            Database (defaults usually work fine)
          </p>
          <div className="grid grid-cols-2 gap-3">
            {dbFields.map((field) => (
              <div key={field.key}>
                <label className="block text-xs font-medium text-gray-500 mb-1">
                  {field.label}
                </label>
                <input
                  type={field.key === 'DB_PASSWORD' ? 'password' : 'text'}
                  value={values[field.key] || ''}
                  onChange={(e) => setValues((v) => ({ ...v, [field.key]: e.target.value }))}
                  placeholder={field.placeholder}
                  disabled={running}
                  className="titlebar-no-drag w-full px-3 py-1.5 text-sm border border-gray-200 rounded-lg focus:ring-2 focus:ring-blue-500 focus:border-blue-500 outline-none transition-all disabled:bg-gray-50 disabled:text-gray-400"
                />
              </div>
            ))}
          </div>
        </div>

        {/* EverMemOS configuration */}
        <>
          <div className="my-5 border-t border-gray-200" />
          <div>
              <h2 className="text-sm font-semibold text-gray-700 mb-3">
                EverMemOS Memory System
              </h2>

              {/* Mode toggle */}
              <div className="flex gap-4 mb-4">
                <label className="titlebar-no-drag flex items-center gap-2 cursor-pointer">
                  <input
                    type="radio"
                    name="emMode"
                    checked={emMode === 'netmind'}
                    onChange={() => setEmMode('netmind')}
                    disabled={running}
                    className="accent-blue-500"
                  />
                  <span className="text-sm font-medium text-gray-700">NetMind.AI Power</span>
                </label>
                <label className="titlebar-no-drag flex items-center gap-2 cursor-pointer">
                  <input
                    type="radio"
                    name="emMode"
                    checked={emMode === 'custom'}
                    onChange={() => setEmMode('custom')}
                    disabled={running}
                    className="accent-blue-500"
                  />
                  <span className="text-sm font-medium text-gray-700">Custom Configuration</span>
                </label>
              </div>

              {/* NetMind mode */}
              {emMode === 'netmind' && (
                <>
                  {skipEverMemOS ? (
                    <div className="mb-4 p-3 bg-amber-50 border border-amber-200 rounded-lg">
                      <p className="text-xs text-amber-700">
                        NetMind API Key is not configured above. Fill it in to enable EverMemOS memory features.
                      </p>
                    </div>
                  ) : (
                    <div className="mb-4 p-3 bg-green-50 border border-green-200 rounded-lg">
                      <p className="text-xs text-green-700">
                        Will use the NetMind API Key above to power: LLM (DeepSeek-V3.2), Embedding (bge-m3). Rerank disabled. No extra configuration needed.
                      </p>
                    </div>
                  )}
                </>
              )}

              {/* Custom mode */}
              {emMode === 'custom' && (
                <>
                  {skipEverMemOS && (
                    <div className="mb-4 p-3 bg-amber-50 border border-amber-200 rounded-lg">
                      <p className="text-xs text-amber-700">
                        LLM API Key is not configured. EverMemOS will not be cloned, its dependencies will not be installed, and the memory system will not start.
                        To enable memory features, fill in the LLM API Key below.
                      </p>
                    </div>
                  )}

                  {/* Render fields by group */}
                  {(['llm', 'vectorize', 'rerank', 'other'] as const).map((group) => {
                    const groupFields = emFields
                      .filter((f) => f.group === group)
                      .sort((a, b) => a.order - b.order)
                    if (groupFields.length === 0) return null

                    return (
                      <div key={group} className="mb-4">
                        <p className="text-xs font-medium text-gray-500 mb-2">
                          {EM_GROUP_LABELS[group]}
                        </p>
                        <div className="space-y-2">
                          {groupFields.map((field) => (
                            <div key={field.key}>
                              <label className="block text-xs font-medium text-gray-600 mb-1">
                                {field.label}
                                {field.required && <span className="text-red-500 ml-0.5">*</span>}
                              </label>
                              <div className="flex gap-2">
                                {field.inputType === 'select' ? (
                                  <select
                                    value={emValues[field.key] || ''}
                                    onChange={(e) => setEmValues((v) => ({ ...v, [field.key]: e.target.value }))}
                                    disabled={running}
                                    className="titlebar-no-drag flex-1 px-3 py-1.5 text-sm border border-gray-300 rounded-lg focus:ring-2 focus:ring-blue-500 focus:border-blue-500 outline-none transition-all disabled:bg-gray-50 disabled:text-gray-400 bg-white"
                                  >
                                    <option value="">{field.placeholder}</option>
                                    {field.options?.map((opt) => (
                                      <option key={opt} value={opt}>{opt}</option>
                                    ))}
                                  </select>
                                ) : (
                                  <input
                                    type={field.inputType}
                                    value={emValues[field.key] || ''}
                                    onChange={(e) => setEmValues((v) => ({ ...v, [field.key]: e.target.value }))}
                                    placeholder={field.placeholder}
                                    disabled={running}
                                    className="titlebar-no-drag flex-1 px-3 py-1.5 text-sm border border-gray-300 rounded-lg focus:ring-2 focus:ring-blue-500 focus:border-blue-500 outline-none transition-all disabled:bg-gray-50 disabled:text-gray-400"
                                  />
                                )}
                                {KEY_LINKS[field.key] && (
                                  <button
                                    onClick={() => window.nexus.openExternal(KEY_LINKS[field.key].url)}
                                    className="titlebar-no-drag px-3 py-1.5 text-xs text-blue-600 bg-blue-50 rounded-lg hover:bg-blue-100 whitespace-nowrap transition-colors"
                                  >
                                    {KEY_LINKS[field.key].label}
                                  </button>
                                )}
                              </div>
                            </div>
                          ))}
                        </div>
                      </div>
                    )
                  })}

                  {/* Infrastructure configuration (collapsible) */}
                  {(() => {
                    const infraFields = emFields
                      .filter((f) => f.group === 'infrastructure')
                      .sort((a, b) => a.order - b.order)
                    if (infraFields.length === 0) return null

                    return (
                      <div className="mb-4">
                        <button
                          onClick={() => setEmAdvancedOpen((v) => !v)}
                          className="titlebar-no-drag flex items-center gap-1 text-xs font-medium text-gray-500 hover:text-gray-700 transition-colors mb-2"
                        >
                          <span className={`inline-block transition-transform ${emAdvancedOpen ? 'rotate-90' : ''}`}>
                            &#9654;
                          </span>
                          {EM_GROUP_LABELS.infrastructure}
                        </button>
                        {emAdvancedOpen && (
                          <div className="grid grid-cols-2 gap-2">
                            {infraFields.map((field) => (
                              <div key={field.key}>
                                <label className="block text-xs font-medium text-gray-500 mb-1">
                                  {field.label}
                                </label>
                                <input
                                  type={field.inputType}
                                  value={emValues[field.key] || ''}
                                  onChange={(e) => setEmValues((v) => ({ ...v, [field.key]: e.target.value }))}
                                  placeholder={field.placeholder}
                                  disabled={running}
                                  className="titlebar-no-drag w-full px-3 py-1.5 text-sm border border-gray-200 rounded-lg focus:ring-2 focus:ring-blue-500 focus:border-blue-500 outline-none transition-all disabled:bg-gray-50 disabled:text-gray-400"
                                />
                              </div>
                            ))}
                          </div>
                        )}
                      </div>
                    )
                  })()}
                </>
              )}
            </div>
        </>

        {/* Action buttons */}
        <div className="mt-6 flex gap-3">
          <button
            onClick={handleStartOnly}
            disabled={running || !requiredFilled}
            className="titlebar-no-drag flex-1 py-2.5 text-sm font-medium text-green-700 bg-green-50 border border-green-200 rounded-lg hover:bg-green-100 disabled:opacity-40 disabled:cursor-not-allowed transition-colors"
          >
            {running ? 'Starting...' : 'Start Only'}
          </button>
          <button
            onClick={handleInstallAndStart}
            disabled={running || !requiredFilled}
            className="titlebar-no-drag flex-1 py-2.5 text-sm font-medium text-white bg-blue-500 rounded-lg hover:bg-blue-600 disabled:opacity-40 disabled:cursor-not-allowed transition-colors"
          >
            {running ? 'Installing...' : 'Install & Start'}
          </button>
        </div>
        {!requiredFilled && !running && (
          <p className="text-xs text-red-400 mt-1.5 text-center">
            Please fill in required fields (marked with *)
          </p>
        )}

        {/* Installation progress */}
        {steps.length > 0 && (
          <div className="mt-5 p-4 bg-gray-50 rounded-lg">
            <p className="text-xs font-medium text-gray-500 mb-2">Setup Progress</p>
            <div className="space-y-1.5">
              {steps.map((s) => (
                <div key={s.step} className="flex items-start gap-2">
                  {/* Status icon */}
                  {s.status === 'done' && (
                    <span className="text-green-500 text-sm leading-5">&#10003;</span>
                  )}
                  {s.status === 'running' && (
                    <div className="w-3.5 h-3.5 mt-0.5 border-2 border-blue-500 border-t-transparent rounded-full animate-spin" />
                  )}
                  {s.status === 'error' && (
                    <span className="text-red-500 text-sm leading-5">&#10007;</span>
                  )}
                  {s.status === 'skipped' && (
                    <span className="text-gray-400 text-sm leading-5">&#8212;</span>
                  )}

                  <div className="flex-1 min-w-0">
                    <p className={`text-sm leading-5 ${
                      s.status === 'error' ? 'text-red-600' :
                      s.status === 'running' ? 'text-blue-600 font-medium' :
                      s.status === 'skipped' ? 'text-gray-400' :
                      'text-gray-600'
                    }`}>
                      {s.label}
                    </p>
                    {s.message && s.status === 'running' && (
                      <p className="text-xs text-gray-400 mt-0.5 truncate">{s.message}</p>
                    )}
                    {s.message && s.status === 'error' && (
                      <p className="text-xs text-red-500 mt-0.5 break-words">{renderWithLinks(s.message)}</p>
                    )}
                  </div>
                </div>
              ))}
            </div>
          </div>
        )}

        {/* Error message */}
        {error && (
          <div className="mt-4 p-3 bg-red-50 rounded-lg">
            <p className="text-sm text-red-700">{renderWithLinks(error)}</p>
            <button
              onClick={() => { setError(null); setSteps([]) }}
              className="titlebar-no-drag mt-2 text-xs text-red-600 hover:text-red-800 underline"
            >
              Retry
            </button>
          </div>
        )}
      </div>
    </div>
  )
}

export default SetupWizard
