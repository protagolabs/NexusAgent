/**
 * @file env.d.ts
 * @description Renderer process global type declarations
 */

/** Nexus API types exposed by Preload */
interface NexusAPI {
  checkDependencies: () => Promise<DependencyStatus[]>
  installDependency: (depId: string) => Promise<{ success: boolean; output: string }>
  getEnv: () => Promise<{ config: Record<string, string>; fields: EnvField[] }>
  setEnv: (updates: Record<string, string>) => Promise<{ success: boolean }>
  validateEnv: () => Promise<{ valid: boolean; missing: string[]; warnings: string[] }>
  getEverMemOSEnv: () => Promise<{ available: boolean; config: Record<string, string>; fields: EverMemOSEnvField[] }>
  setEverMemOSEnv: (updates: Record<string, string>) => Promise<{ success: boolean }>
  validateEverMemOSEnv: () => Promise<{ valid: boolean; missing: string[]; warnings: string[] }>
  getDockerStatus: () => Promise<{ dockerReady: boolean; groups: DockerGroupStatus[] }>
  startDocker: () => Promise<{ mysql: boolean; evermemos: boolean }>
  stopDocker: () => Promise<{ success: boolean }>
  startAllServices: () => Promise<{ success: boolean }>
  stopAllServices: () => Promise<{ success: boolean }>
  restartService: (serviceId: string) => Promise<{ success: boolean }>
  getServiceStatus: () => Promise<ProcessInfo[]>
  getHealthStatus: () => Promise<OverallHealth>
  initDatabase: () => Promise<{ success: boolean; output: string }>
  autoSetup: (options?: { skipEverMemOS?: boolean }) => Promise<{ success: boolean; error?: string }>
  quickStart: (options?: { skipEverMemOS?: boolean }) => Promise<{ success: boolean; error?: string }>
  onSetupProgress: (callback: (progress: SetupProgress) => void) => () => void
  getClaudeAuthInfo: () => Promise<ClaudeAuthInfo>
  startClaudeLogin: () => Promise<LoginProcessStatus>
  cancelClaudeLogin: () => Promise<{ success: boolean }>
  sendClaudeLoginInput: (text: string) => Promise<{ success: boolean }>
  saveSetupToken: (token: string) => Promise<{ valid: boolean; message: string }>
  onClaudeLoginStatus: (callback: (status: LoginProcessStatus) => void) => () => void
  openExternal: (url: string) => Promise<void>
  getSetupState: () => Promise<{ setupComplete: boolean }>
  setSetupComplete: () => Promise<{ success: boolean }>
  getLogs: (serviceId?: string) => Promise<LogEntry[]>
  onLog: (callback: (entry: LogEntry) => void) => () => void
  onHealthUpdate: (callback: (status: OverallHealth) => void) => () => void
  onTrayAction: (callback: (action: string) => void) => () => void
  onNavigate: (callback: (page: string) => void) => () => void
}

interface DependencyStatus {
  id: string
  name: string
  required: boolean
  installed: boolean
  version: string | null
  minVersion: string | null
  installHint: string
  autoInstallCommand: string[] | null
  downloadUrl: string | null
}

interface EnvField {
  key: string
  label: string
  required: boolean
  placeholder: string
}

interface EverMemOSEnvField {
  key: string
  label: string
  required: boolean
  placeholder: string
  inputType: 'text' | 'password' | 'select'
  options?: string[]
  group: 'llm' | 'vectorize' | 'rerank' | 'infrastructure' | 'other'
  order: number
}

interface DockerGroupStatus {
  id: string
  label: string
  containers: Array<{ name: string; state: string; ports: string }>
  composePath: string
  available: boolean
}

interface ProcessInfo {
  serviceId: string
  label: string
  status: 'stopped' | 'starting' | 'running' | 'crashed'
  pid: number | null
  restartCount: number
  lastError: string | null
}

interface ServiceHealth {
  serviceId: string
  label: string
  state: 'healthy' | 'unhealthy' | 'unknown'
  port: number | null
  lastChecked: number
  message: string
}

interface OverallHealth {
  services: ServiceHealth[]
  infrastructure: ServiceHealth[]
  mysql: 'healthy' | 'unhealthy' | 'unknown'
  allHealthy: boolean
}

interface LogEntry {
  serviceId: string
  timestamp: number
  stream: 'stdout' | 'stderr'
  message: string
}

interface SetupProgress {
  step: number
  totalSteps: number
  label: string
  status: 'running' | 'done' | 'error' | 'skipped'
  message?: string
}

interface ClaudeAuthStatus {
  state: 'logged_in' | 'expired' | 'not_logged_in' | 'cli_not_installed'
  method?: 'oauth' | 'token'
  expiresAt?: number
  isExpired?: boolean
}

interface ClaudeAuthInfo {
  cliInstalled: boolean
  cliVersion: string | null
  authStatus: ClaudeAuthStatus
  hasApiKey: boolean
  hasSetupToken: boolean
}

interface LoginProcessStatus {
  state: 'idle' | 'running' | 'success' | 'failed' | 'timeout'
  message?: string
}

interface Window {
  nexus: NexusAPI
}
