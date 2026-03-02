/**
 * @file process-manager.ts
 * @description Backend service process lifecycle management
 *
 * Manages backend services (Backend, MCP, Poller, Job Trigger, EverMemOS):
 * start, stop, auto-restart on crash. Launches processes via child_process.spawn,
 * captures stdout/stderr output for log display.
 *
 * Also provides runAutoSetup() one-click install method for first launch
 * to auto-detect and install all dependencies.
 */

import { spawn, ChildProcess, execFile } from 'child_process'
import { join } from 'path'
import { existsSync } from 'fs'
import { EventEmitter } from 'events'
import { promisify } from 'util'
import * as net from 'net'
import { dialog } from 'electron'
import {
  SERVICES,
  ServiceDef,
  PROJECT_ROOT,
  FRONTEND_DIR,
  TABLE_MGMT_DIR,
  MAX_RESTART_ATTEMPTS,
  RESTART_BACKOFF_BASE,
  MCP_PORTS,
  EVERMEMOS_DIR,
  EVERMEMOS_GIT_URL
} from './constants'
import { isEverMemOSAvailable, startEverMemOS, resetComposeDetection } from './docker-manager'
import { getShellEnv } from './shell-env'
import { readEnv } from './env-manager'
import { getClaudeAuthInfo } from './claude-auth-manager'
import * as everMemOSEnv from './evermemos-env-manager'

const execFileAsync = promisify(execFile)

// ─── Type Definitions ───────────────────────────────────────

export type ProcessStatus = 'stopped' | 'starting' | 'running' | 'crashed'

export interface ProcessInfo {
  serviceId: string
  label: string
  status: ProcessStatus
  pid: number | null
  restartCount: number
  lastError: string | null
}

export interface LogEntry {
  serviceId: string
  timestamp: number
  stream: 'stdout' | 'stderr'
  message: string
}

/** Auto-setup step progress */
export interface SetupProgress {
  step: number
  totalSteps: number
  label: string
  status: 'running' | 'done' | 'error' | 'skipped'
  message?: string
}

// ─── ProcessManager ─────────────────────────────────

export class ProcessManager extends EventEmitter {
  private processes = new Map<string, ChildProcess>()
  private statuses = new Map<string, ProcessStatus>()
  private restartCounts = new Map<string, number>()
  private lastErrors = new Map<string, string>()
  private logs: LogEntry[] = []
  private maxLogs = 500
  private shuttingDown = false

  constructor() {
    super()
    // Initialize all service statuses
    for (const svc of SERVICES) {
      this.statuses.set(svc.id, 'stopped')
      this.restartCounts.set(svc.id, 0)
    }
  }

  /** Start a single service */
  async startService(serviceId: string): Promise<boolean> {
    const svc = SERVICES.find((s) => s.id === serviceId)
    if (!svc) return false

    // If already running, stop first
    if (this.processes.has(serviceId)) {
      await this.stopService(serviceId)
    }

    return this.spawnProcess(svc)
  }

  /** Start all services in order (clean up residual port-occupying processes first) */
  async startAll(options?: { skipEverMemOS?: boolean }): Promise<void> {
    // Stop all managed processes first to prevent exit handler auto-restart from creating duplicates
    await this.stopAll()

    this.shuttingDown = false
    // Reset all restart counts (fresh start)
    for (const svc of SERVICES) {
      this.restartCounts.set(svc.id, 0)
    }

    // Force clean residual processes on all service ports (including MCP subprocesses 7801-7805)
    await this.forceKillServicePorts()

    const sorted = [...SERVICES]
      .filter((svc) => !(options?.skipEverMemOS && svc.id === 'evermemos'))
      .sort((a, b) => a.order - b.order)
    for (const svc of sorted) {
      await this.spawnProcess(svc)
      // Give the process a moment to start
      await this.delay(500)
    }
  }

  /** Stop a single service (kill entire process group to ensure child processes are cleaned up) */
  async stopService(serviceId: string): Promise<void> {
    const proc = this.processes.get(serviceId)
    if (!proc) {
      this.statuses.set(serviceId, 'stopped')
      return
    }

    return new Promise((resolve) => {
      const timeout = setTimeout(() => {
        // Timeout: SIGKILL the entire process group
        this.killProcessGroup(proc, 'SIGKILL')
        this.processes.delete(serviceId)
        this.statuses.set(serviceId, 'stopped')
        resolve()
      }, 5000)

      proc.once('exit', () => {
        clearTimeout(timeout)
        this.processes.delete(serviceId)
        this.statuses.set(serviceId, 'stopped')
        resolve()
      })

      // First SIGTERM the entire process group (uv → python and other child processes all receive signal)
      this.killProcessGroup(proc, 'SIGTERM')
    })
  }

  /** Stop All Services */
  async stopAll(): Promise<void> {
    this.shuttingDown = true
    const stopPromises = SERVICES.map((svc) => this.stopService(svc.id))
    await Promise.all(stopPromises)
  }

  /** Restart a single service */
  async restartService(serviceId: string): Promise<boolean> {
    this.restartCounts.set(serviceId, 0) // Reset count (manual restart)
    await this.stopService(serviceId)
    return this.startService(serviceId)
  }

  /** Get all service statuses */
  getAllStatus(): ProcessInfo[] {
    return SERVICES.map((svc) => ({
      serviceId: svc.id,
      label: svc.label,
      status: this.statuses.get(svc.id) ?? 'stopped',
      pid: this.processes.get(svc.id)?.pid ?? null,
      restartCount: this.restartCounts.get(svc.id) ?? 0,
      lastError: this.lastErrors.get(svc.id) ?? null
    }))
  }

  /** Get logs */
  getLogs(serviceId?: string, limit = 100): LogEntry[] {
    let filtered = this.logs
    if (serviceId) {
      filtered = filtered.filter((l) => l.serviceId === serviceId)
    }
    return filtered.slice(-limit)
  }

  /**
   * Force clean all residual processes on service ports (no dialog)
   *
   * stopAll() can only kill Electron-managed process groups, but module_runner's
   * multiprocessing subprocesses (uvicorn 7801-7805) may not be in the same process group,
   * causing SIGTERM to not propagate. This method directly finds and SIGKILL these residual processes via lsof.
   */
  private async forceKillServicePorts(): Promise<void> {
    // Collect all ports to clean: those defined in SERVICES + all MCP module ports
    const portsSet = new Set<number>()
    for (const svc of SERVICES) {
      if (svc.port !== null) portsSet.add(svc.port)
    }
    for (const p of MCP_PORTS) {
      portsSet.add(p)
    }

    let killed = false
    for (const port of portsSet) {
      try {
        const { stdout } = await execFileAsync('lsof', ['-ti', `:${port}`], {
          timeout: 5000,
          env: getShellEnv()
        })
        const pids = stdout.trim().split('\n').filter(Boolean)
        for (const pid of pids) {
          try {
            process.kill(Number(pid), 'SIGKILL')
            this.addLog('system', 'stderr', `Killed stale process on port ${port} (PID: ${pid})`)
            killed = true
          } catch { /* process may have already exited */ }
        }
      } catch { /* Port not occupied, normal */ }
    }

    // Wait for ports to be released
    if (killed) {
      await this.delay(1000)
    }
  }

  /**
   * Detect and handle port conflicts
   *
   * Scan all service ports; if occupied, show dialog for user to confirm termination.
   * If user declines, skip (the corresponding service will report port-in-use error on start).
   */
  private async killStalePorts(): Promise<void> {
    const portsToCheck = SERVICES
      .filter((s) => s.port !== null)
      .map((s) => ({ port: s.port as number, label: s.label }))

    // Collect all conflict info
    interface Conflict { port: number; label: string; pid: string; processName: string }
    const conflicts: Conflict[] = []

    for (const { port, label } of portsToCheck) {
      try {
        const { stdout } = await execFileAsync('lsof', ['-ti', `:${port}`], {
          timeout: 5000,
          env: getShellEnv()
        })
        const pids = stdout.trim().split('\n').filter(Boolean)
        for (const pid of pids) {
          // Get process name
          let processName = 'unknown'
          try {
            const { stdout: psOut } = await execFileAsync('ps', ['-p', pid, '-o', 'comm='], {
              timeout: 3000
            })
            processName = psOut.trim() || 'unknown'
          } catch { /* ignore */ }
          conflicts.push({ port, label, pid, processName })
        }
      } catch { /* Port not occupied, normal */ }
    }

    if (conflicts.length === 0) return

    // Build prompt message
    const details = conflicts
      .map((c) => `  Port ${c.port} (${c.label}) → ${c.processName} (PID: ${c.pid})`)
      .join('\n')

    const { response } = await dialog.showMessageBox({
      type: 'warning',
      title: 'Port Conflict',
      message: 'The following ports are occupied by other processes. Kill them?',
      detail: details,
      buttons: ['Kill & Continue', 'Skip'],
      defaultId: 0,
      cancelId: 1
    })

    if (response === 0) {
      // User confirmed: kill conflicting processes
      for (const c of conflicts) {
        try {
          process.kill(Number(c.pid), 'SIGKILL')
          this.addLog('system', 'stderr', `Killed process ${c.processName} on port ${c.port} (PID: ${c.pid})`)
        } catch { /* process may have already exited */ }
      }
      // Wait for ports to be released
      await this.delay(1000)
    } else {
      this.addLog('system', 'stderr', 'User skipped port conflict resolution. Some services may fail to start.')
    }
  }

  // ─── One-Click Auto Setup ─────────────────────────────────

  /**
   * Get execution environment merged with .env (Shell env + .env key-values)
   *
   * Merge rules:
   * - Fields with non-empty values in .env → override shell env (UI-entered values take priority)
   * - Fields with empty values in .env → don't override, keep shell env values
   *   (prevents empty .env lines from overriding API keys exported in terminal)
   */
  private getExecEnv(): Record<string, string> {
    const shellEnv = getShellEnv()
    const dotEnv = readEnv()
    // Filter out empty .env values to avoid overriding valid shell env values
    const nonEmptyDotEnv: Record<string, string> = {}
    for (const [key, value] of Object.entries(dotEnv)) {
      if (value.trim()) {
        nonEmptyDotEnv[key] = value
      }
    }
    // Ensure backend services (Backend, MCP, Poller, etc.) bypass proxy for localhost.
    // System http_proxy (e.g. VPN proxy) can cause localhost requests to go through proxy and return 502.
    const noProxyHosts = 'localhost,127.0.0.1'
    return { ...shellEnv, ...nonEmptyDotEnv, NO_PROXY: noProxyHosts, no_proxy: noProxyHosts }
  }

  /** Execute command in project root directory */
  private async execInProject(
    cmd: string,
    args: string[],
    options?: { cwd?: string; timeout?: number }
  ): Promise<{ stdout: string; stderr: string }> {
    return execFileAsync(cmd, args, {
      cwd: options?.cwd ?? PROJECT_ROOT,
      timeout: options?.timeout ?? 120000,
      env: this.getExecEnv()
    })
  }

  /**
   * Execute sudo-required commands via system native privilege elevation dialog
   *
   * - macOS: osascript "do shell script ... with administrator privileges" → system password dialog
   * - Linux: pkexec → PolicyKit password dialog
   */
  private async execWithPrivileges(
    script: string,
    options?: { timeout?: number }
  ): Promise<{ stdout: string; stderr: string }> {
    if (process.platform === 'darwin') {
      const escaped = script.replace(/\\/g, '\\\\').replace(/"/g, '\\"')
      return this.execInProject('osascript', ['-e',
        `do shell script "${escaped}" with administrator privileges`
      ], options)
    } else {
      return this.execInProject('pkexec', ['sh', '-c', script], options)
    }
  }

  /**
   * Execute command and stream stdout/stderr (for real-time progress feedback during long steps)
   *
   * Unlike execInProject: uses spawn for real-time output reading,
   * pushes latest line to frontend via onOutput callback.
   */
  private spawnWithProgress(
    cmd: string,
    args: string[],
    options?: {
      cwd?: string
      timeout?: number
      onOutput?: (line: string) => void
    }
  ): Promise<{ stdout: string; stderr: string }> {
    return new Promise((resolve, reject) => {
      const cwd = options?.cwd ?? PROJECT_ROOT
      const timeout = options?.timeout ?? 120000

      const proc = spawn(cmd, args, {
        cwd,
        env: this.getExecEnv(),
        stdio: ['ignore', 'pipe', 'pipe']
      })

      let stdout = ''
      let stderr = ''

      const processData = (data: Buffer) => {
        const lines = data.toString().split('\n').filter(l => l.trim())
        if (lines.length > 0 && options?.onOutput) {
          // Push the last non-empty output line, truncated to avoid excessive length
          options.onOutput(lines[lines.length - 1].trim().substring(0, 200))
        }
      }

      proc.stdout?.on('data', (data: Buffer) => { stdout += data.toString(); processData(data) })
      proc.stderr?.on('data', (data: Buffer) => { stderr += data.toString(); processData(data) })

      const timer = setTimeout(() => {
        proc.kill('SIGTERM')
        reject(new Error(`Command timed out after ${timeout / 1000}s`))
      }, timeout)

      proc.on('close', (code) => {
        clearTimeout(timer)
        if (code === 0) resolve({ stdout, stderr })
        else reject(new Error(stderr || `Process exited with code ${code}`))
      })

      proc.on('error', (err) => {
        clearTimeout(timer)
        reject(err)
      })
    })
  }

  /**
   * Attempt to start Docker daemon (try multiple strategies in sequence)
   *
   * Strategies escalate from "no privilege needed" to "privileged installation":
   *
   * macOS strategy chain:
   *   1. open -a Docker (Docker Desktop installed but not running)
   *   2. colima start (Colima installed but VM is down)
   *   3. brew install colima docker → colima start (brew installed, docker not)
   *   4. Privileged Homebrew install → brew install → colima start (from scratch)
   *
   * Linux strategy chain:
   *   1. systemctl start docker (daemon not started, current user has permissions)
   *   2. Privileged systemctl start docker
   *   3. Privileged get.docker.com install + start (from scratch)
   */
  private async tryStartDocker(): Promise<boolean> {
    if (process.platform === 'darwin') {
      return this.tryStartDockerMacOS()
    } else {
      return this.tryStartDockerLinux()
    }
  }

  private async tryStartDockerMacOS(): Promise<boolean> {
    // Strategy 1: Launch Docker Desktop (if installed)
    this.emitSetupLog('Trying to launch Docker Desktop...')
    try {
      await this.execInProject('open', ['-a', 'Docker'], { timeout: 10000 })
      // Docker Desktop starts slowly, poll and wait
      for (let i = 0; i < 30; i++) {
        await this.delay(2000)
        try {
          await this.execInProject('docker', ['info'], { timeout: 5000 })
          return true
        } catch { /* Not ready yet, keep waiting */ }
      }
    } catch { /* Docker Desktop not installed */ }

    // Strategy 2: Start Colima (if installed)
    this.emitSetupLog('Trying colima start...')
    try {
      await this.execInProject('colima', ['start'], { timeout: 120000 })
      await this.execInProject('docker', ['info'], { timeout: 10000 })
      return true
    } catch { /* Colima not installed */ }

    // Strategy 3: Install Colima + Docker CLI via Homebrew (brew doesn't need sudo)
    try {
      await this.execInProject('brew', ['--version'], { timeout: 10000 })
      this.emitSetupLog('Installing Docker via Homebrew (colima + docker CLI)...')
      await this.execInProject('brew', ['install', 'colima', 'docker', 'docker-compose'],
        { timeout: 300000 })
      this.emitSetupLog('Starting Colima VM...')
      await this.execInProject('colima', ['start'], { timeout: 120000 })
      await this.execInProject('docker', ['info'], { timeout: 10000 })
      resetComposeDetection()
      return true
    } catch { /* brew not found or install failed */ }

    // Strategy 4: Privileged Homebrew install → Colima + Docker CLI (system password prompt)
    this.emitSetupLog('Installing Homebrew + Docker (admin privileges required)...')
    try {
      // Homebrew install requires sudo; NONINTERACTIVE=1 skips "press Enter" confirmation
      await this.execWithPrivileges(
        'NONINTERACTIVE=1 /bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"',
        { timeout: 300000 }
      )
      // Homebrew install path varies by arch: Apple Silicon → /opt/homebrew, Intel → /usr/local
      const brewPath = process.arch === 'arm64'
        ? '/opt/homebrew/bin/brew'
        : '/usr/local/bin/brew'
      this.emitSetupLog(`Installing colima + docker CLI... (brew: ${brewPath})`)
      await this.execInProject('sh', ['-c',
        `${brewPath} install colima docker docker-compose`
      ], { timeout: 300000 })
      const colimaPath = brewPath.replace('/brew', '/colima')
      this.emitSetupLog('Starting Colima VM...')
      await this.execInProject('sh', ['-c',
        `${colimaPath} start`
      ], { timeout: 120000 })
      await this.execInProject('docker', ['info'], { timeout: 10000 })
      resetComposeDetection()
      return true
    } catch { /* User cancelled password dialog or install failed */ }

    return false
  }

  private async tryStartDockerLinux(): Promise<boolean> {
    // Strategy 1: systemctl start docker (no sudo, may already be in docker group)
    this.emitSetupLog('Trying to start Docker daemon...')
    try {
      await this.execInProject('systemctl', ['start', 'docker'], { timeout: 30000 })
      await this.execInProject('docker', ['info'], { timeout: 10000 })
      return true
    } catch { /* No permissions or docker not installed */ }

    // Strategy 2: Privileged start docker daemon (PolicyKit password prompt)
    this.emitSetupLog('Starting Docker daemon (admin privileges required)...')
    try {
      await this.execWithPrivileges('systemctl start docker', { timeout: 30000 })
      await this.execInProject('docker', ['info'], { timeout: 10000 })
      return true
    } catch { /* Docker not installed */ }

    // Strategy 3: Privileged install Docker Engine + Compose plugin (get.docker.com + start daemon)
    this.emitSetupLog('Installing Docker Engine (admin privileges required)...')
    try {
      const user = process.env.USER || 'root'
      await this.execWithPrivileges(
        'curl -fsSL https://get.docker.com | sh'
        + ' && (apt-get install -y docker-compose-plugin 2>/dev/null'
        + '    || yum install -y docker-compose-plugin 2>/dev/null'
        + '    || true)'
        + ` && usermod -aG docker ${user}`
        + ' && systemctl start docker',
        { timeout: 300000 }
      )
      // Reset compose command detection cache after new install
      resetComposeDetection()
      // After usermod -aG, current process still belongs to old session, verify with privilege elevation
      try {
        await this.execInProject('docker', ['info'], { timeout: 10000 })
      } catch {
        await this.execWithPrivileges('docker info', { timeout: 10000 })
      }
      return true
    } catch { /* User cancelled or install failed */ }

    return false
  }

  /** Emit setup log (internal utility method) */
  private emitSetupLog(message: string): void {
    this.addLog('system', 'stdout', message)
  }

  /** Check if a TCP port is reachable */
  private isPortReachable(port: number, host = '127.0.0.1', timeout = 2000): Promise<boolean> {
    return new Promise((resolve) => {
      const socket = new net.Socket()
      socket.setTimeout(timeout)
      socket.on('connect', () => { socket.destroy(); resolve(true) })
      socket.on('error', () => resolve(false))
      socket.on('timeout', () => { socket.destroy(); resolve(false) })
      socket.connect(port, host)
    })
  }

  /**
   * One-click auto-setup: configure the entire runtime environment from scratch
   *
   * Steps:
   * 1. Detect/install uv
   * 2. Detect/install Claude Code
   * 3. uv sync (Python dependencies)
   * 4. Detect Docker
   * 5. Clone EverMemOS (if needed)
   * 6. docker compose up -d（MySQL + EverMemOS）
   * 7. Wait for MySQL ready
   * 8. Create database tables
   * 9. Sync table schema
   * 10. Install EverMemOS dependencies (optional)
   * 11. Build frontend (if dist/ doesn't exist)
   * 12. Start backend services
   */
  async runAutoSetup(options?: { skipEverMemOS?: boolean }): Promise<{ success: boolean; error?: string }> {
    let skipEM = options?.skipEverMemOS ?? false
    const totalSteps = 12
    let currentStep = 0

    const emitProgress = (label: string, status: SetupProgress['status'], message?: string) => {
      currentStep++
      const progress: SetupProgress = {
        step: currentStep,
        totalSteps,
        label,
        status,
        message
      }
      this.emit('setup-progress', progress)
    }

    try {
      // ─── Step 1: Detect/install uv ────────────────────────
      try {
        await this.execInProject('uv', ['--version'], { timeout: 10000 })
        emitProgress('Check system dependencies', 'done', 'uv is installed')
      } catch {
        emitProgress('Install uv', 'running', 'Installing uv...')
        try {
          // macOS/Linux: use official install script
          await this.execInProject('sh', ['-c', 'curl -LsSf https://astral.sh/uv/install.sh | sh'], { timeout: 180000 })
          emitProgress('Install uv', 'done', 'uv installed successfully')
          // Adjust step count (step 1 emitted twice)
          currentStep = 1
        } catch (err) {
          emitProgress('Install uv', 'error', `uv installation failed: ${err instanceof Error ? err.message : err}`)
          return { success: false, error: 'uv installation failed' }
        }
      }

      // ─── Step 2: Detect/install Claude Code ─────────────────
      // Read .env to check if user has configured Anthropic API Key
      const envConfig = readEnv()
      const hasAnthropicKey = !!envConfig.ANTHROPIC_API_KEY?.trim()

      let claudeInstalled = false
      try {
        await this.execInProject('claude', ['--version'], { timeout: 10000 })
        claudeInstalled = true
      } catch { /* Not installed */ }

      if (!claudeInstalled) {
        // Not installed → auto install
        emitProgress('Install Claude Code', 'running', 'Installing Claude Code...')
        try {
          await this.execInProject('sh', ['-c', 'curl -fsSL https://claude.ai/install.sh | sh'], { timeout: 180000 })
          claudeInstalled = true
          this.emit('setup-progress', {
            step: currentStep, totalSteps, label: 'Install Claude Code', status: 'done', message: 'Claude Code installed successfully'
          } as SetupProgress)
          currentStep = 2
        } catch {
          const hint = hasAnthropicKey
            ? 'Claude Code installation failed, will use API Key mode'
            : 'Claude Code installation failed (Agent features limited). Install later: curl -fsSL https://claude.ai/install.sh | sh'
          this.emit('setup-progress', {
            step: currentStep, totalSteps, label: 'Install Claude Code', status: 'done', message: hint
          } as SetupProgress)
          currentStep = 2
        }
      }

      // Detect auth status by reading files (millisecond-level, replaces old 30s claude -p hello timeout)
      const authInfo = await getClaudeAuthInfo(readEnv)

      if (authInfo.hasApiKey || authInfo.hasSetupToken) {
        emitProgress('Check Claude Code', 'done',
          claudeInstalled ? 'Claude Code ready (API Key / Token configured)' : 'Will use API Key / Token mode')
      } else if (authInfo.authStatus.state === 'logged_in' && !authInfo.authStatus.isExpired) {
        emitProgress('Check Claude Code', 'done', 'Claude Code is ready (OAuth login detected)')
      } else if (authInfo.authStatus.state === 'expired') {
        emitProgress('Check Claude Code', 'done',
          'Claude Code login expired. Please re-login or configure API Key.')
      } else if (claudeInstalled) {
        emitProgress('Check Claude Code', 'done',
          'Claude Code installed but not logged in. Please login or configure API Key.')
      } else {
        emitProgress('Check Claude Code', 'done',
          'Claude Code not available. Agent features limited.')
      }

      // ─── Step 3: uv sync ─────────────────────────────
      emitProgress('Install Python dependencies', 'running', 'Running uv sync...')
      try {
        await this.spawnWithProgress('uv', ['sync'], {
          timeout: 300000,
          onOutput: (line) => this.emit('setup-progress', {
            step: currentStep, totalSteps, label: 'Install Python dependencies', status: 'running', message: line
          } as SetupProgress)
        })
        this.emit('setup-progress', {
          step: currentStep, totalSteps, label: 'Install Python dependencies', status: 'done', message: 'Python dependencies installed'
        } as SetupProgress)
      } catch (err) {
        this.emit('setup-progress', {
          step: currentStep, totalSteps, label: 'Install Python dependencies', status: 'error',
          message: `uv sync failed: ${err instanceof Error ? err.message : err}`
        } as SetupProgress)
        return { success: false, error: 'Python dependency installation failed' }
      }

      // ─── Step 4: Detect / Start Docker ─────────────────────
      emitProgress('Check Docker', 'running', 'Detecting Docker...')
      try {
        await this.execInProject('docker', ['info'], { timeout: 10000 })
        this.emit('setup-progress', {
          step: currentStep, totalSteps, label: 'Check Docker', status: 'done', message: 'Docker is ready'
        } as SetupProgress)
      } catch {
        // daemon not running or not installed → try multiple strategies to start
        this.emit('setup-progress', {
          step: currentStep, totalSteps, label: 'Start Docker', status: 'running',
          message: 'Docker not running, attempting to start...'
        } as SetupProgress)
        const started = await this.tryStartDocker()
        if (started) {
          this.emit('setup-progress', {
            step: currentStep, totalSteps, label: 'Start Docker', status: 'done', message: 'Docker daemon started'
          } as SetupProgress)
        } else {
          const url = process.platform === 'darwin'
            ? 'https://docs.docker.com/desktop/setup/install/mac-install/'
            : 'https://docs.docker.com/desktop/setup/install/linux-install/'
          const msg = `Docker is not installed. Please install manually: ${url}`
          this.emit('setup-progress', {
            step: currentStep, totalSteps, label: 'Start Docker', status: 'error', message: msg
          } as SetupProgress)
          return { success: false, error: msg }
        }
      }

      // ─── Step 5: Clone EverMemOS ───────────────────────
      if (skipEM) {
        emitProgress('Clone EverMemOS', 'skipped', 'EverMemOS not configured, skipping')
      } else if (everMemOSEnv.isCloned()) {
        // Directory already exists (reinstall), flush in-memory staged values to disk
        everMemOSEnv.flushPendingEnv()
        emitProgress('Clone EverMemOS', 'done', 'EverMemOS already cloned')
      } else {
        emitProgress('Clone EverMemOS', 'running', 'Cloning EverMemOS repository...')
        try {
          await this.spawnWithProgress('git', ['clone', '--depth', '1', '--progress', EVERMEMOS_GIT_URL, '.evermemos'], {
            timeout: 180000,
            onOutput: (line) => this.emit('setup-progress', {
              step: currentStep, totalSteps, label: 'Clone EverMemOS', status: 'running', message: line
            } as SetupProgress)
          })
          everMemOSEnv.flushPendingEnv()
          this.emit('setup-progress', {
            step: currentStep, totalSteps, label: 'Clone EverMemOS', status: 'done', message: 'EverMemOS cloned successfully'
          } as SetupProgress)
        } catch (err) {
          // Non-blocking: degrade to skip all EverMemOS subsequent steps on clone failure
          skipEM = true
          this.emit('setup-progress', {
            step: currentStep, totalSteps, label: 'Clone EverMemOS', status: 'done',
            message: `EverMemOS clone failed (non-blocking): ${err instanceof Error ? err.message : err}`
          } as SetupProgress)
        }
      }

      // ─── Step 6: docker compose up（MySQL + EverMemOS） ──
      emitProgress('Start database', 'running', 'Starting MySQL container...')
      try {
        const dbOnOutput = (line: string) => this.emit('setup-progress', {
          step: currentStep, totalSteps, label: 'Start database', status: 'running', message: line
        } as SetupProgress)
        // Auto-detect V2 (docker compose) or V1 (docker-compose)
        let composeOk = false
        // Try V2 plugin
        try {
          await this.spawnWithProgress('docker', ['compose', 'up', '-d'], { timeout: 120000, onOutput: dbOnOutput })
          composeOk = true
        } catch { /* V2 not available or insufficient permissions */ }
        // Try V1 standalone command
        if (!composeOk) {
          try {
            await this.spawnWithProgress('docker-compose', ['up', '-d'], { timeout: 120000, onOutput: dbOnOutput })
            composeOk = true
          } catch { /* V1 also not available */ }
        }
        // Both failed → retry with privilege elevation (V2 || V1)
        if (!composeOk) {
          this.emit('setup-progress', {
            step: currentStep, totalSteps, label: 'Start database', status: 'running',
            message: 'Retrying with elevated privileges...'
          } as SetupProgress)
          await this.execWithPrivileges(`cd "${PROJECT_ROOT}" && (docker compose up -d || docker-compose up -d)`, { timeout: 120000 })
        }
        this.emit('setup-progress', {
          step: currentStep, totalSteps, label: 'Start database', status: 'done', message: 'MySQL container started'
        } as SetupProgress)
      } catch (err) {
        this.emit('setup-progress', {
          step: currentStep, totalSteps, label: 'Start database', status: 'error',
          message: `MySQL container failed to start: ${err instanceof Error ? err.message : err}`
        } as SetupProgress)
        return { success: false, error: 'MySQL container failed to start' }
      }

      // Start EverMemOS infrastructure（MongoDB, ES, Milvus, Redis）
      if (!skipEM && isEverMemOSAvailable()) {
        this.emit('setup-progress', {
          step: currentStep, totalSteps, label: 'Start database', status: 'done',
          message: 'MySQL started. Starting EverMemOS infrastructure...'
        } as SetupProgress)
        const emResult = await startEverMemOS()
        if (emResult.success) {
          this.addLog('system', 'stdout', 'EverMemOS containers started')
        } else {
          this.addLog('system', 'stderr', `EverMemOS containers failed (non-blocking): ${emResult.output}`)
        }
      }

      // ─── Step 7: Wait for MySQL ready ─────────────────────
      emitProgress('Wait for database', 'running', 'Waiting for MySQL port...')
      let mysqlReady = false
      for (let i = 0; i < 60; i++) {
        if (await this.isPortReachable(3306)) {
          mysqlReady = true
          break
        }
        if (i % 5 === 4) {
          this.emit('setup-progress', {
            step: currentStep, totalSteps, label: 'Wait for database', status: 'running',
            message: `Waiting for MySQL port... (${i + 1}s)`
          } as SetupProgress)
        }
        await this.delay(1000)
      }
      if (!mysqlReady) {
        this.emit('setup-progress', {
          step: currentStep, totalSteps, label: 'Wait for database', status: 'error',
          message: 'MySQL port timeout (60s)'
        } as SetupProgress)
        return { success: false, error: 'MySQL port timeout' }
      }
      // Port is reachable but MySQL may still be initializing, wait extra
      await this.delay(5000)
      this.emit('setup-progress', {
        step: currentStep, totalSteps, label: 'Wait for database', status: 'done', message: 'MySQL is ready'
      } as SetupProgress)

      // ─── Step 8: Create database tables (with retry) ─────────────────
      emitProgress('Create tables', 'running', 'Initializing database...')
      const scriptPath = join(TABLE_MGMT_DIR, 'create_all_tables.py')
      let tableCreated = false
      let lastTableErr = ''
      for (let attempt = 1; attempt <= 5; attempt++) {
        try {
          await this.execInProject('uv', ['run', 'python', scriptPath], { timeout: 60000 })
          tableCreated = true
          break
        } catch (err) {
          lastTableErr = err instanceof Error ? err.message : String(err)
          this.emit('setup-progress', {
            step: currentStep, totalSteps, label: 'Create tables',
            status: 'running', message: `Attempt ${attempt} failed, ${attempt < 5 ? 'retrying...' : 'max retries reached'}`
          } as SetupProgress)
          if (attempt < 5) await this.delay(5000)
        }
      }
      if (!tableCreated) {
        this.emit('setup-progress', {
          step: currentStep, totalSteps, label: 'Create tables', status: 'error',
          message: `Table creation failed: ${lastTableErr}`
        } as SetupProgress)
        return { success: false, error: 'Table creation failed' }
      }
      this.emit('setup-progress', {
        step: currentStep, totalSteps, label: 'Create tables', status: 'done', message: 'Tables created'
      } as SetupProgress)

      // ─── Step 9: Sync table schema ─────────────────────────
      emitProgress('Sync table schema', 'running', 'Syncing table schema...')
      try {
        const syncScript = join(TABLE_MGMT_DIR, 'sync_all_tables.py')
        await this.execInProject('uv', ['run', 'python', syncScript], { timeout: 60000 })
        this.emit('setup-progress', {
          step: currentStep, totalSteps, label: 'Sync table schema', status: 'done', message: 'Schema sync complete'
        } as SetupProgress)
      } catch (err) {
        // Table schema sync failure doesn't block startup (tables are already up-to-date on first install)
        this.emit('setup-progress', {
          step: currentStep, totalSteps, label: 'Sync table schema', status: 'done',
          message: `Schema sync skipped: ${err instanceof Error ? err.message : err}`
        } as SetupProgress)
      }

      // ─── Step 10: EverMemOS dependency installation ──────────────────
      if (skipEM) {
        emitProgress('EverMemOS dependencies', 'skipped', 'EverMemOS not configured, skipping')
      } else if (existsSync(EVERMEMOS_DIR)) {
        const evermemosVenv = join(EVERMEMOS_DIR, '.venv')
        if (!existsSync(evermemosVenv)) {
          emitProgress('EverMemOS dependencies', 'running', 'Installing EverMemOS Python dependencies...')
          try {
            await this.spawnWithProgress('uv', ['sync'], {
              cwd: EVERMEMOS_DIR, timeout: 300000,
              onOutput: (line) => this.emit('setup-progress', {
                step: currentStep, totalSteps, label: 'EverMemOS dependencies', status: 'running', message: line
              } as SetupProgress)
            })
            this.emit('setup-progress', {
              step: currentStep, totalSteps, label: 'EverMemOS dependencies', status: 'done', message: 'EverMemOS dependencies installed'
            } as SetupProgress)
          } catch (err) {
            // EverMemOS dependency installation failure doesn't block startup
            this.emit('setup-progress', {
              step: currentStep, totalSteps, label: 'EverMemOS dependencies', status: 'done',
              message: `EverMemOS dependency installation failed (non-blocking): ${err instanceof Error ? err.message : err}`
            } as SetupProgress)
          }
        } else {
          emitProgress('EverMemOS dependencies', 'skipped', 'EverMemOS dependencies already installed')
        }
      } else {
        emitProgress('EverMemOS dependencies', 'skipped', 'EverMemOS not configured, skipping')
      }

      // ─── Step 11: Build frontend ────────────────────────────
      const distDir = join(FRONTEND_DIR, 'dist')
      if (existsSync(join(distDir, 'index.html'))) {
        emitProgress('Build frontend', 'skipped', 'Frontend already built')
      } else {
        emitProgress('Build frontend', 'running', 'Installing npm packages...')
        try {
          const feOnOutput = (line: string) => this.emit('setup-progress', {
            step: currentStep, totalSteps, label: 'Build frontend', status: 'running', message: line
          } as SetupProgress)
          await this.spawnWithProgress('npm', ['install', '--no-audit', '--no-fund'], {
            cwd: FRONTEND_DIR, timeout: 120000, onOutput: feOnOutput
          })
          this.emit('setup-progress', {
            step: currentStep, totalSteps, label: 'Build frontend', status: 'running', message: 'Compiling frontend...'
          } as SetupProgress)
          await this.spawnWithProgress('npm', ['run', 'build'], {
            cwd: FRONTEND_DIR, timeout: 120000, onOutput: feOnOutput
          })
          this.emit('setup-progress', {
            step: currentStep, totalSteps, label: 'Build frontend', status: 'done', message: 'Frontend build complete'
          } as SetupProgress)
        } catch (err) {
          this.emit('setup-progress', {
            step: currentStep, totalSteps, label: 'Build frontend', status: 'error',
            message: `Frontend build failed: ${err instanceof Error ? err.message : err}`
          } as SetupProgress)
          return { success: false, error: 'Frontend build failed' }
        }
      }

      // ─── Step 11.5: Wait for EverMemOS infrastructure ready ────────
      let autoSetupEmInfraReady = true
      if (!skipEM && existsSync(EVERMEMOS_DIR)) {
        this.emit('setup-progress', {
          step: currentStep, totalSteps, label: 'Wait for EverMemOS infra', status: 'running',
          message: 'Waiting for EverMemOS infrastructure services...'
        } as SetupProgress)
        const autoSetupResult = await this.waitForInfraPorts(currentStep, totalSteps)
        autoSetupEmInfraReady = autoSetupResult
      }

      // ─── Step 12: Start backend services ───────────────────────
      const skipEverMemOS = skipEM || !autoSetupEmInfraReady
      emitProgress('Start services', 'running', 'Starting all services...')
      await this.startAll({ skipEverMemOS })
      this.emit('setup-progress', {
        step: currentStep, totalSteps, label: 'Start services', status: 'done', message: 'All services started'
      } as SetupProgress)

      return { success: true }
    } catch (err) {
      const message = err instanceof Error ? err.message : String(err)
      this.emit('setup-progress', {
        step: currentStep, totalSteps, label: 'Unknown error', status: 'error', message
      } as SetupProgress)
      return { success: false, error: message }
    }
  }

  /**
   * Quick start: skip install steps, only do Docker startup + service launch
   *
   * For subsequent runs (dependencies already installed), emits setup-progress events per step.
   *
   * Steps:
   * 1. Detect/start Docker
   * 2. docker compose up (MySQL + EverMemOS infrastructure)
   * 3. Wait for MySQL ready
   * 4. Wait for EverMemOS infrastructure ready (optional)
   * 5. Start backend services
   */
  async runQuickStart(options?: { skipEverMemOS?: boolean }): Promise<{ success: boolean; error?: string }> {
    const skipEM = options?.skipEverMemOS ?? false
    const totalSteps = 5
    let currentStep = 0

    const emitProgress = (label: string, status: SetupProgress['status'], message?: string) => {
      currentStep++
      this.emit('setup-progress', { step: currentStep, totalSteps, label, status, message } as SetupProgress)
    }

    try {
      // ─── Step 1: Detect/start Docker ─────────────────────
      emitProgress('Check Docker', 'running', 'Detecting Docker...')
      try {
        await this.execInProject('docker', ['info'], { timeout: 10000 })
        this.emit('setup-progress', {
          step: currentStep, totalSteps, label: 'Check Docker', status: 'done', message: 'Docker is ready'
        } as SetupProgress)
      } catch {
        this.emit('setup-progress', {
          step: currentStep, totalSteps, label: 'Start Docker', status: 'running',
          message: 'Docker not running, attempting to start...'
        } as SetupProgress)
        const started = await this.tryStartDocker()
        if (started) {
          this.emit('setup-progress', {
            step: currentStep, totalSteps, label: 'Start Docker', status: 'done', message: 'Docker daemon started'
          } as SetupProgress)
        } else {
          this.emit('setup-progress', {
            step: currentStep, totalSteps, label: 'Start Docker', status: 'error',
            message: 'Docker is not running. Please start Docker manually.'
          } as SetupProgress)
          return { success: false, error: 'Docker is not running' }
        }
      }

      // ─── Step 2: docker compose up ────────────────────
      emitProgress('Start containers', 'running', 'Starting MySQL container...')
      try {
        const onOutput = (line: string) => this.emit('setup-progress', {
          step: currentStep, totalSteps, label: 'Start containers', status: 'running', message: line
        } as SetupProgress)
        let composeOk = false
        try {
          await this.spawnWithProgress('docker', ['compose', 'up', '-d'], { timeout: 120000, onOutput })
          composeOk = true
        } catch { /* V2 not available */ }
        if (!composeOk) {
          try {
            await this.spawnWithProgress('docker-compose', ['up', '-d'], { timeout: 120000, onOutput })
            composeOk = true
          } catch { /* V1 also not available */ }
        }
        if (!composeOk) {
          await this.execWithPrivileges(`cd "${PROJECT_ROOT}" && (docker compose up -d || docker-compose up -d)`, { timeout: 120000 })
        }
        this.emit('setup-progress', {
          step: currentStep, totalSteps, label: 'Start containers', status: 'done', message: 'Containers started'
        } as SetupProgress)
      } catch (err) {
        this.emit('setup-progress', {
          step: currentStep, totalSteps, label: 'Start containers', status: 'error',
          message: `Containers failed to start: ${err instanceof Error ? err.message : err}`
        } as SetupProgress)
        return { success: false, error: 'Containers failed to start' }
      }

      // Start EverMemOS infrastructure
      if (!skipEM && isEverMemOSAvailable()) {
        this.emit('setup-progress', {
          step: currentStep, totalSteps, label: 'Start containers', status: 'done',
          message: 'MySQL started. Starting EverMemOS infrastructure...'
        } as SetupProgress)
        const emResult = await startEverMemOS()
        if (!emResult.success) {
          this.addLog('system', 'stderr', `EverMemOS containers failed (non-blocking): ${emResult.output}`)
        }
      }

      // ─── Step 3: Wait for MySQL ready ─────────────────────
      emitProgress('Wait for MySQL', 'running', 'Waiting for MySQL port...')
      let mysqlReady = false
      for (let i = 0; i < 60; i++) {
        if (await this.isPortReachable(3306)) {
          mysqlReady = true
          break
        }
        if (i % 5 === 4) {
          this.emit('setup-progress', {
            step: currentStep, totalSteps, label: 'Wait for MySQL', status: 'running',
            message: `Waiting for MySQL port... (${i + 1}s)`
          } as SetupProgress)
        }
        await this.delay(1000)
      }
      if (!mysqlReady) {
        this.emit('setup-progress', {
          step: currentStep, totalSteps, label: 'Wait for MySQL', status: 'error',
          message: 'MySQL port timeout (60s)'
        } as SetupProgress)
        return { success: false, error: 'MySQL port timeout' }
      }
      await this.delay(3000)
      this.emit('setup-progress', {
        step: currentStep, totalSteps, label: 'Wait for MySQL', status: 'done', message: 'MySQL is ready'
      } as SetupProgress)

      // ─── Step 4: Wait for EverMemOS infrastructure ready ──────────
      let emInfraReady = true
      if (!skipEM && existsSync(EVERMEMOS_DIR)) {
        emitProgress('Wait for EverMemOS infra', 'running', 'Waiting for EverMemOS infrastructure...')
        emInfraReady = await this.waitForInfraPorts(currentStep, totalSteps)
      } else {
        emitProgress('Wait for EverMemOS infra', 'skipped', 'EverMemOS not configured, skipping')
      }

      // ─── Step 5: Start backend services ─────────────────────────
      // Skip EverMemOS if infrastructure isn't ready, to avoid repeated crashes
      const skipEverMemOS = skipEM || !emInfraReady
      emitProgress('Start services', 'running', 'Starting all services...')
      await this.startAll({ skipEverMemOS })
      this.emit('setup-progress', {
        step: currentStep, totalSteps, label: 'Start services', status: 'done', message: 'All services started'
      } as SetupProgress)

      return { success: true }
    } catch (err) {
      const message = err instanceof Error ? err.message : String(err)
      this.emit('setup-progress', {
        step: currentStep, totalSteps, label: 'Unknown error', status: 'error', message
      } as SetupProgress)
      return { success: false, error: message }
    }
  }

  // ─── Internal Methods ─────────────────────────────────────

  private spawnProcess(svc: ServiceDef): boolean {
    try {
      const cwd = svc.cwd ? join(PROJECT_ROOT, svc.cwd) : PROJECT_ROOT

      // Optional service: silently skip when working directory doesn't exist
      if (svc.optional && !existsSync(cwd)) {
        this.addLog(svc.id, 'stderr', `Skipping optional service: directory not found (${cwd})`)
        this.statuses.set(svc.id, 'stopped')
        return false
      }

      const proc = spawn(svc.command, svc.args, {
        cwd,
        stdio: ['ignore', 'pipe', 'pipe'],
        env: this.getExecEnv(),
        // Create new process group so stopService can kill entire group (including child processes)
        detached: true
      })

      // Detached processes don't auto-quit with parent, need unref
      // But we manually clean up in stopAll/before-quit, so no unref

      this.processes.set(svc.id, proc)
      this.statuses.set(svc.id, 'starting')
      this.lastErrors.delete(svc.id)

      // Capture stdout
      proc.stdout?.on('data', (data: Buffer) => {
        const message = data.toString().trim()
        if (!message) return
        this.addLog(svc.id, 'stdout', message)
        // Mark as running when startup success keywords are detected
        if (this.statuses.get(svc.id) === 'starting') {
          this.statuses.set(svc.id, 'running')
          this.emit('status-change', svc.id, 'running')
        }
      })

      // Capture stderr
      proc.stderr?.on('data', (data: Buffer) => {
        const message = data.toString().trim()
        if (!message) return
        this.addLog(svc.id, 'stderr', message)
        // uvicorn etc. output startup info to stderr
        if (this.statuses.get(svc.id) === 'starting') {
          this.statuses.set(svc.id, 'running')
          this.emit('status-change', svc.id, 'running')
        }
      })

      // Process exit handling
      proc.on('exit', (code, signal) => {
        this.processes.delete(svc.id)

        if (this.shuttingDown) {
          this.statuses.set(svc.id, 'stopped')
          this.emit('status-change', svc.id, 'stopped')
          return
        }

        if (code !== 0 && code !== null) {
          this.statuses.set(svc.id, 'crashed')
          this.lastErrors.set(svc.id, `Process exited with code: ${code}`)
          this.emit('status-change', svc.id, 'crashed')
          this.addLog(svc.id, 'stderr', `Process exited (code=${code}, signal=${signal})`)
          this.tryAutoRestart(svc)
        } else {
          this.statuses.set(svc.id, 'stopped')
          this.emit('status-change', svc.id, 'stopped')
        }
      })

      proc.on('error', (err) => {
        this.processes.delete(svc.id)
        this.statuses.set(svc.id, 'crashed')
        this.lastErrors.set(svc.id, err.message)
        this.emit('status-change', svc.id, 'crashed')
        this.addLog(svc.id, 'stderr', `Failed to start: ${err.message}`)
      })

      this.emit('status-change', svc.id, 'starting')
      return true
    } catch (err) {
      const message = err instanceof Error ? err.message : String(err)
      this.statuses.set(svc.id, 'crashed')
      this.lastErrors.set(svc.id, message)
      return false
    }
  }

  /** Auto-restart after crash (exponential backoff, max MAX_RESTART_ATTEMPTS times) */
  private async tryAutoRestart(svc: ServiceDef): Promise<void> {
    const count = (this.restartCounts.get(svc.id) ?? 0) + 1
    this.restartCounts.set(svc.id, count)

    if (count > MAX_RESTART_ATTEMPTS) {
      this.addLog(
        svc.id,
        'stderr',
        `Max restart attempts reached (${MAX_RESTART_ATTEMPTS}), stopping auto-restart`
      )
      return
    }

    // EverMemOS depends on infrastructure ports, wait for infra ready before restart
    if (svc.id === 'evermemos') {
      const infraReady = await this.waitForEverMemOSInfra(svc.id)
      if (!infraReady) {
        // Infrastructure not up, don't waste restart attempts
        this.restartCounts.set(svc.id, count - 1)
        this.addLog(svc.id, 'stderr', 'Infrastructure not ready, skipping restart')
        return
      }
    }

    const waitMs = RESTART_BACKOFF_BASE * Math.pow(2, count - 1)
    this.addLog(svc.id, 'stderr', `Auto-restarting in ${waitMs}ms (attempt ${count})`)

    await this.delay(waitMs)

    if (!this.shuttingDown) {
      this.spawnProcess(svc)
    }
  }

  /** EverMemOS infrastructure port list */
  private static readonly EM_INFRA_PORTS = [
    { port: 27017, name: 'MongoDB' },
    { port: 19200, name: 'Elasticsearch' },
    { port: 19530, name: 'Milvus' },
    { port: 6379,  name: 'Redis' }
  ]

  /**
   * Wait for EverMemOS infrastructure ports to be ready (for autoSetup/quickStart install flow)
   * Updates UI progress via setup-progress events, refreshing every 5 seconds
   * Maximum wait time 180s
   */
  private async waitForInfraPorts(currentStep: number, totalSteps: number): Promise<boolean> {
    for (const { port, name } of ProcessManager.EM_INFRA_PORTS) {
      let ready = false
      for (let i = 0; i < 180; i++) {
        if (await this.isPortReachable(port)) { ready = true; break }
        if (i % 5 === 4) {
          this.emit('setup-progress', {
            step: currentStep, totalSteps, label: 'Wait for EverMemOS infra', status: 'running',
            message: `Waiting for ${name}:${port}... (${i + 1}s)`
          } as SetupProgress)
        }
        await this.delay(1000)
      }
      if (!ready) {
        this.emit('setup-progress', {
          step: currentStep, totalSteps, label: 'Wait for EverMemOS infra', status: 'error',
          message: `${name}:${port} timeout after 180s — EverMemOS will not start`
        } as SetupProgress)
        return false
      }
    }
    this.emit('setup-progress', {
      step: currentStep, totalSteps, label: 'Wait for EverMemOS infra', status: 'done',
      message: 'EverMemOS infrastructure ready'
    } as SetupProgress)
    return true
  }

  /**
   * Wait for EverMemOS infrastructure ports to be ready (for tryAutoRestart crash recovery)
   * Log progress, refresh every 10 seconds
   * Maximum wait time 180s
   */
  private async waitForEverMemOSInfra(logServiceId: string): Promise<boolean> {
    this.addLog(logServiceId, 'stderr', 'Waiting for infrastructure ports before restart...')
    for (const { port, name } of ProcessManager.EM_INFRA_PORTS) {
      let ready = false
      for (let i = 0; i < 180; i++) {
        if (this.shuttingDown) return false
        if (await this.isPortReachable(port)) { ready = true; break }
        if (i % 10 === 9) {
          this.addLog(logServiceId, 'stderr', `Still waiting for ${name}:${port}... (${i + 1}s)`)
        }
        await this.delay(1000)
      }
      if (!ready) {
        this.addLog(logServiceId, 'stderr', `${name}:${port} not reachable after 180s, aborting restart`)
        return false
      }
    }
    this.addLog(logServiceId, 'stderr', 'All infrastructure ports ready')
    return true
  }

  /** Kill process group (negative PID) to ensure child processes are cleaned up */
  private killProcessGroup(proc: ChildProcess, signal: NodeJS.Signals): void {
    if (!proc.pid) return
    try {
      // Negative PID = kill entire process group (process is group leader with detached: true)
      process.kill(-proc.pid, signal)
    } catch {
      // Process group may have already exited, fallback to single process kill
      try { proc.kill(signal) } catch { /* Already exited */ }
    }
  }

  private addLog(serviceId: string, stream: 'stdout' | 'stderr', message: string): void {
    const entry: LogEntry = {
      serviceId,
      timestamp: Date.now(),
      stream,
      message
    }
    this.logs.push(entry)

    // Prevent unbounded log growth
    if (this.logs.length > this.maxLogs) {
      this.logs = this.logs.slice(-this.maxLogs)
    }

    this.emit('log', entry)
  }

  private delay(ms: number): Promise<void> {
    return new Promise((resolve) => setTimeout(resolve, ms))
  }
}
