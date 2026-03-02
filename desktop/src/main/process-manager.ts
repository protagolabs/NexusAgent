/**
 * @file process-manager.ts
 * @description 后台服务进程的生命周期管理
 *
 * 管理后台服务（Backend、MCP、Poller、Job Trigger、EverMemOS）的
 * 启动、停止、崩溃自动重启。通过 child_process.spawn 启动进程，
 * 捕获 stdout/stderr 输出用于日志展示。
 *
 * 同时提供 runAutoSetup() 一键安装方法，用于首次启动时
 * 自动检测并安装所有依赖。
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

// ─── 类型定义 ───────────────────────────────────────

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

/** 自动安装步骤进度 */
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
    // 初始化所有服务状态
    for (const svc of SERVICES) {
      this.statuses.set(svc.id, 'stopped')
      this.restartCounts.set(svc.id, 0)
    }
  }

  /** 启动单个服务 */
  async startService(serviceId: string): Promise<boolean> {
    const svc = SERVICES.find((s) => s.id === serviceId)
    if (!svc) return false

    // 如果已在运行，先停止
    if (this.processes.has(serviceId)) {
      await this.stopService(serviceId)
    }

    return this.spawnProcess(svc)
  }

  /** 按顺序启动所有服务（启动前清理残留进程占用的端口） */
  async startAll(options?: { skipEverMemOS?: boolean }): Promise<void> {
    // 先停止所有已管理的进程，防止 exit handler 触发自动重启产生重复实例
    await this.stopAll()

    this.shuttingDown = false
    // 重置所有重启计数（全新启动）
    for (const svc of SERVICES) {
      this.restartCounts.set(svc.id, 0)
    }

    // 强制清理所有服务端口上的残留进程（含 MCP 子进程 7801-7805）
    await this.forceKillServicePorts()

    const sorted = [...SERVICES]
      .filter((svc) => !(options?.skipEverMemOS && svc.id === 'evermemos'))
      .sort((a, b) => a.order - b.order)
    for (const svc of sorted) {
      await this.spawnProcess(svc)
      // 给进程一点时间来启动
      await this.delay(500)
    }
  }

  /** 停止单个服务（杀掉整个进程组，确保子进程也被清理） */
  async stopService(serviceId: string): Promise<void> {
    const proc = this.processes.get(serviceId)
    if (!proc) {
      this.statuses.set(serviceId, 'stopped')
      return
    }

    return new Promise((resolve) => {
      const timeout = setTimeout(() => {
        // 超时：SIGKILL 整个进程组
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

      // 先 SIGTERM 整个进程组（uv → python 等子进程一并收到信号）
      this.killProcessGroup(proc, 'SIGTERM')
    })
  }

  /** 停止所有服务 */
  async stopAll(): Promise<void> {
    this.shuttingDown = true
    const stopPromises = SERVICES.map((svc) => this.stopService(svc.id))
    await Promise.all(stopPromises)
  }

  /** 重启单个服务 */
  async restartService(serviceId: string): Promise<boolean> {
    this.restartCounts.set(serviceId, 0) // 重置计数（手动重启）
    await this.stopService(serviceId)
    return this.startService(serviceId)
  }

  /** 获取所有服务状态 */
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

  /** 获取日志 */
  getLogs(serviceId?: string, limit = 100): LogEntry[] {
    let filtered = this.logs
    if (serviceId) {
      filtered = filtered.filter((l) => l.serviceId === serviceId)
    }
    return filtered.slice(-limit)
  }

  /**
   * 强制清理所有服务端口上的残留进程（不弹窗）
   *
   * stopAll() 只能杀掉 Electron 管理的进程组，但 module_runner 的
   * multiprocessing 子进程（uvicorn 7801-7805）可能不在同一进程组中，
   * 导致 SIGTERM 无法传递。此方法直接通过 lsof 查找并 SIGKILL 这些残留进程。
   */
  private async forceKillServicePorts(): Promise<void> {
    // 收集所有需要清理的端口：SERVICES 中定义的 + MCP 模块全部端口
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
          } catch { /* 进程可能已退出 */ }
        }
      } catch { /* 端口未被占用，正常 */ }
    }

    // 等待端口释放
    if (killed) {
      await this.delay(1000)
    }
  }

  /**
   * 检测并处理端口冲突
   *
   * 扫描所有服务端口，如果发现被占用，弹窗让用户确认是否终止。
   * 用户拒绝则跳过（对应服务启动时会报端口占用错误）。
   */
  private async killStalePorts(): Promise<void> {
    const portsToCheck = SERVICES
      .filter((s) => s.port !== null)
      .map((s) => ({ port: s.port as number, label: s.label }))

    // 收集所有冲突信息
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
          // 获取进程名
          let processName = 'unknown'
          try {
            const { stdout: psOut } = await execFileAsync('ps', ['-p', pid, '-o', 'comm='], {
              timeout: 3000
            })
            processName = psOut.trim() || 'unknown'
          } catch { /* 忽略 */ }
          conflicts.push({ port, label, pid, processName })
        }
      } catch { /* 端口未被占用，正常 */ }
    }

    if (conflicts.length === 0) return

    // 构造提示信息
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
      // 用户确认：杀掉冲突进程
      for (const c of conflicts) {
        try {
          process.kill(Number(c.pid), 'SIGKILL')
          this.addLog('system', 'stderr', `Killed process ${c.processName} on port ${c.port} (PID: ${c.pid})`)
        } catch { /* 进程可能已退出 */ }
      }
      // 等待端口释放
      await this.delay(1000)
    } else {
      this.addLog('system', 'stderr', 'User skipped port conflict resolution. Some services may fail to start.')
    }
  }

  // ─── 一键自动安装 ─────────────────────────────────

  /**
   * 获取合并了 .env 的执行环境（Shell 环境 + .env 键值）
   *
   * 合并规则：
   * - .env 中有值（非空）的字段 → 覆盖 shell 环境（用户在 UI 填写的优先）
   * - .env 中值为空的字段 → 不覆盖，保留 shell 环境中的值
   *   （防止 .env 的空行吞掉 terminal 里 export 的 API Key）
   */
  private getExecEnv(): Record<string, string> {
    const shellEnv = getShellEnv()
    const dotEnv = readEnv()
    // 过滤掉 .env 中的空值，避免覆盖 shell 环境里的有效值
    const nonEmptyDotEnv: Record<string, string> = {}
    for (const [key, value] of Object.entries(dotEnv)) {
      if (value.trim()) {
        nonEmptyDotEnv[key] = value
      }
    }
    // 确保后台服务（Backend、MCP、Poller 等）绕过代理直连 localhost。
    // 系统若设置了 http_proxy（如 VPN 代理），会导致 localhost 请求走代理返回 502。
    const noProxyHosts = 'localhost,127.0.0.1'
    return { ...shellEnv, ...nonEmptyDotEnv, NO_PROXY: noProxyHosts, no_proxy: noProxyHosts }
  }

  /** 在项目根目录执行命令 */
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
   * 通过系统原生提权弹窗执行需要 sudo 的命令
   *
   * - macOS: osascript "do shell script ... with administrator privileges" → 系统密码框
   * - Linux: pkexec → PolicyKit 密码框
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
   * 执行命令并流式推送 stdout/stderr（用于耗时步骤的实时进度反馈）
   *
   * 与 execInProject 不同：使用 spawn 实时读取输出，
   * 通过 onOutput 回调推送最新一行给前端显示。
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
          // 推送最后一行非空输出，截断避免过长
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
   * 尝试拉起 Docker daemon（多种策略逐一尝试）
   *
   * 策略从"无需提权"到"需要提权安装"依次升级：
   *
   * macOS 策略链：
   *   1. open -a Docker（Docker Desktop 已装但未运行）
   *   2. colima start（Colima 已装但 VM 关了）
   *   3. brew install colima docker → colima start（brew 已装、docker 没装）
   *   4. 提权安装 Homebrew → brew install → colima start（完全从零开始）
   *
   * Linux 策略链：
   *   1. systemctl start docker（daemon 未启动，当前用户有权限）
   *   2. 提权 systemctl start docker
   *   3. 提权 get.docker.com 安装 + 启动（完全从零开始）
   */
  private async tryStartDocker(): Promise<boolean> {
    if (process.platform === 'darwin') {
      return this.tryStartDockerMacOS()
    } else {
      return this.tryStartDockerLinux()
    }
  }

  private async tryStartDockerMacOS(): Promise<boolean> {
    // 策略 1: 启动 Docker Desktop（如果已安装）
    this.emitSetupLog('Trying to launch Docker Desktop...')
    try {
      await this.execInProject('open', ['-a', 'Docker'], { timeout: 10000 })
      // Docker Desktop 启动较慢，轮询等待
      for (let i = 0; i < 30; i++) {
        await this.delay(2000)
        try {
          await this.execInProject('docker', ['info'], { timeout: 5000 })
          return true
        } catch { /* 还没准备好，继续等 */ }
      }
    } catch { /* Docker Desktop 未安装 */ }

    // 策略 2: 启动 Colima（如果已安装）
    this.emitSetupLog('Trying colima start...')
    try {
      await this.execInProject('colima', ['start'], { timeout: 120000 })
      await this.execInProject('docker', ['info'], { timeout: 10000 })
      return true
    } catch { /* Colima 未安装 */ }

    // 策略 3: 通过 Homebrew 安装 Colima + Docker CLI（brew 不需要 sudo）
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
    } catch { /* brew 不存在或安装失败 */ }

    // 策略 4: 提权安装 Homebrew → Colima + Docker CLI（弹出系统密码框）
    this.emitSetupLog('Installing Homebrew + Docker (admin privileges required)...')
    try {
      // Homebrew 安装需要 sudo；NONINTERACTIVE=1 跳过 "按回车" 确认
      await this.execWithPrivileges(
        'NONINTERACTIVE=1 /bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"',
        { timeout: 300000 }
      )
      // Homebrew 安装路径因架构而异：Apple Silicon → /opt/homebrew, Intel → /usr/local
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
    } catch { /* 用户取消密码框或安装失败 */ }

    return false
  }

  private async tryStartDockerLinux(): Promise<boolean> {
    // 策略 1: systemctl start docker（无 sudo，可能已在 docker 组）
    this.emitSetupLog('Trying to start Docker daemon...')
    try {
      await this.execInProject('systemctl', ['start', 'docker'], { timeout: 30000 })
      await this.execInProject('docker', ['info'], { timeout: 10000 })
      return true
    } catch { /* 无权限或 docker 未安装 */ }

    // 策略 2: 提权启动 docker daemon（弹出 PolicyKit 密码框）
    this.emitSetupLog('Starting Docker daemon (admin privileges required)...')
    try {
      await this.execWithPrivileges('systemctl start docker', { timeout: 30000 })
      await this.execInProject('docker', ['info'], { timeout: 10000 })
      return true
    } catch { /* docker 未安装 */ }

    // 策略 3: 提权安装 Docker Engine + Compose 插件（get.docker.com + 启动 daemon）
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
      // 新安装后重置 compose 命令检测缓存
      resetComposeDetection()
      // usermod -aG 后当前进程仍属于旧 session，用提权方式验证
      try {
        await this.execInProject('docker', ['info'], { timeout: 10000 })
      } catch {
        await this.execWithPrivileges('docker info', { timeout: 10000 })
      }
      return true
    } catch { /* 用户取消或安装失败 */ }

    return false
  }

  /** 发送安装日志（内部工具方法） */
  private emitSetupLog(message: string): void {
    this.addLog('system', 'stdout', message)
  }

  /** 检查 TCP 端口是否可连接 */
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
   * 一键自动安装：从零开始配置整个运行环境
   *
   * 步骤：
   * 1. 检测/安装 uv
   * 2. 检测/安装 Claude Code
   * 3. uv sync（Python 依赖）
   * 4. 检测 Docker
   * 5. Clone EverMemOS（按需）
   * 6. docker compose up -d（MySQL + EverMemOS）
   * 7. 等待 MySQL 就绪
   * 8. 创建数据表
   * 9. 同步表结构
   * 10. 安装 EverMemOS 依赖（可选）
   * 11. 构建前端（如果 dist/ 不存在）
   * 12. 启动后台服务
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
      // ─── Step 1: 检测/安装 uv ────────────────────────
      try {
        await this.execInProject('uv', ['--version'], { timeout: 10000 })
        emitProgress('Check system dependencies', 'done', 'uv is installed')
      } catch {
        emitProgress('Install uv', 'running', 'Installing uv...')
        try {
          // macOS/Linux: 使用官方安装脚本
          await this.execInProject('sh', ['-c', 'curl -LsSf https://astral.sh/uv/install.sh | sh'], { timeout: 180000 })
          emitProgress('Install uv', 'done', 'uv installed successfully')
          // 调整 step 计数（step 1 发了两次）
          currentStep = 1
        } catch (err) {
          emitProgress('Install uv', 'error', `uv installation failed: ${err instanceof Error ? err.message : err}`)
          return { success: false, error: 'uv installation failed' }
        }
      }

      // ─── Step 2: 检测/安装 Claude Code ─────────────────
      // 读取 .env 判断用户是否已配置 Anthropic API Key
      const envConfig = readEnv()
      const hasAnthropicKey = !!envConfig.ANTHROPIC_API_KEY?.trim()

      let claudeInstalled = false
      try {
        await this.execInProject('claude', ['--version'], { timeout: 10000 })
        claudeInstalled = true
      } catch { /* 未安装 */ }

      if (!claudeInstalled) {
        // 未安装 → 自动安装
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

      // 通过文件读取检测认证状态（毫秒级，取代旧的 claude -p hello 30 秒超时）
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

      // ─── Step 4: 检测 / 启动 Docker ─────────────────────
      emitProgress('Check Docker', 'running', 'Detecting Docker...')
      try {
        await this.execInProject('docker', ['info'], { timeout: 10000 })
        this.emit('setup-progress', {
          step: currentStep, totalSteps, label: 'Check Docker', status: 'done', message: 'Docker is ready'
        } as SetupProgress)
      } catch {
        // daemon 未运行或未安装 → 多策略尝试拉起
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
        // 目录已存在（重新安装），将内存暂存值 flush 到磁盘
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
          // 非阻塞：clone 失败时降级跳过所有 EverMemOS 后续步骤
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
        // 自动检测 V2 (docker compose) 或 V1 (docker-compose)
        let composeOk = false
        // 尝试 V2 插件
        try {
          await this.spawnWithProgress('docker', ['compose', 'up', '-d'], { timeout: 120000, onOutput: dbOnOutput })
          composeOk = true
        } catch { /* V2 不可用或权限不足 */ }
        // 尝试 V1 独立命令
        if (!composeOk) {
          try {
            await this.spawnWithProgress('docker-compose', ['up', '-d'], { timeout: 120000, onOutput: dbOnOutput })
            composeOk = true
          } catch { /* V1 也不可用 */ }
        }
        // 都失败 → 提权重试（V2 || V1）
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

      // 启动 EverMemOS 基础设施（MongoDB, ES, Milvus, Redis）
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

      // ─── Step 7: 等待 MySQL 就绪 ─────────────────────
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
      // 端口通了但 MySQL 可能还在初始化，额外等待
      await this.delay(5000)
      this.emit('setup-progress', {
        step: currentStep, totalSteps, label: 'Wait for database', status: 'done', message: 'MySQL is ready'
      } as SetupProgress)

      // ─── Step 8: 创建数据表（带重试） ─────────────────
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

      // ─── Step 9: 同步表结构 ─────────────────────────
      emitProgress('Sync table schema', 'running', 'Syncing table schema...')
      try {
        const syncScript = join(TABLE_MGMT_DIR, 'sync_all_tables.py')
        await this.execInProject('uv', ['run', 'python', syncScript], { timeout: 60000 })
        this.emit('setup-progress', {
          step: currentStep, totalSteps, label: 'Sync table schema', status: 'done', message: 'Schema sync complete'
        } as SetupProgress)
      } catch (err) {
        // 表结构同步失败不阻塞启动（首次安装时表已是最新的）
        this.emit('setup-progress', {
          step: currentStep, totalSteps, label: 'Sync table schema', status: 'done',
          message: `Schema sync skipped: ${err instanceof Error ? err.message : err}`
        } as SetupProgress)
      }

      // ─── Step 10: EverMemOS 依赖安装 ──────────────────
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
            // EverMemOS 依赖安装失败不阻塞启动
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

      // ─── Step 11: 构建前端 ────────────────────────────
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

      // ─── Step 11.5: 等待 EverMemOS 基础设施就绪 ────────
      let autoSetupEmInfraReady = true
      if (!skipEM && existsSync(EVERMEMOS_DIR)) {
        this.emit('setup-progress', {
          step: currentStep, totalSteps, label: 'Wait for EverMemOS infra', status: 'running',
          message: 'Waiting for EverMemOS infrastructure services...'
        } as SetupProgress)
        const autoSetupResult = await this.waitForInfraPorts(currentStep, totalSteps)
        autoSetupEmInfraReady = autoSetupResult
      }

      // ─── Step 12: 启动后台服务 ───────────────────────
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
   * 快速启动：跳过安装步骤，只做 Docker 拉起 + 服务启动
   *
   * 适用于非首次运行场景（依赖已安装），每步都发射 setup-progress 事件。
   *
   * 步骤：
   * 1. 检测/启动 Docker
   * 2. docker compose up（MySQL + EverMemOS 基础设施）
   * 3. 等待 MySQL 就绪
   * 4. 等待 EverMemOS 基础设施就绪（可选）
   * 5. 启动后台服务
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
      // ─── Step 1: 检测/启动 Docker ─────────────────────
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
        } catch { /* V2 不可用 */ }
        if (!composeOk) {
          try {
            await this.spawnWithProgress('docker-compose', ['up', '-d'], { timeout: 120000, onOutput })
            composeOk = true
          } catch { /* V1 也不可用 */ }
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

      // 启动 EverMemOS 基础设施
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

      // ─── Step 3: 等待 MySQL 就绪 ─────────────────────
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

      // ─── Step 4: 等待 EverMemOS 基础设施就绪 ──────────
      let emInfraReady = true
      if (!skipEM && existsSync(EVERMEMOS_DIR)) {
        emitProgress('Wait for EverMemOS infra', 'running', 'Waiting for EverMemOS infrastructure...')
        emInfraReady = await this.waitForInfraPorts(currentStep, totalSteps)
      } else {
        emitProgress('Wait for EverMemOS infra', 'skipped', 'EverMemOS not configured, skipping')
      }

      // ─── Step 5: 启动后台服务 ─────────────────────────
      // 基础设施没 ready 时跳过 EverMemOS，避免反复 crash
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

  // ─── 内部方法 ─────────────────────────────────────

  private spawnProcess(svc: ServiceDef): boolean {
    try {
      const cwd = svc.cwd ? join(PROJECT_ROOT, svc.cwd) : PROJECT_ROOT

      // 可选服务：工作目录不存在时静默跳过
      if (svc.optional && !existsSync(cwd)) {
        this.addLog(svc.id, 'stderr', `Skipping optional service: directory not found (${cwd})`)
        this.statuses.set(svc.id, 'stopped')
        return false
      }

      const proc = spawn(svc.command, svc.args, {
        cwd,
        stdio: ['ignore', 'pipe', 'pipe'],
        env: this.getExecEnv(),
        // 创建新进程组，stopService 时可以杀掉整个组（含子进程）
        detached: true
      })

      // detached 进程不会随父进程自动退出，需要 unref
      // 但我们在 stopAll/before-quit 中手动清理，所以不 unref

      this.processes.set(svc.id, proc)
      this.statuses.set(svc.id, 'starting')
      this.lastErrors.delete(svc.id)

      // 捕获 stdout
      proc.stdout?.on('data', (data: Buffer) => {
        const message = data.toString().trim()
        if (!message) return
        this.addLog(svc.id, 'stdout', message)
        // 检测到成功启动的关键词时标记为 running
        if (this.statuses.get(svc.id) === 'starting') {
          this.statuses.set(svc.id, 'running')
          this.emit('status-change', svc.id, 'running')
        }
      })

      // 捕获 stderr
      proc.stderr?.on('data', (data: Buffer) => {
        const message = data.toString().trim()
        if (!message) return
        this.addLog(svc.id, 'stderr', message)
        // uvicorn 等将启动信息输出到 stderr
        if (this.statuses.get(svc.id) === 'starting') {
          this.statuses.set(svc.id, 'running')
          this.emit('status-change', svc.id, 'running')
        }
      })

      // 进程退出处理
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

  /** 崩溃后自动重启（指数退避，最多 MAX_RESTART_ATTEMPTS 次） */
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

    // EverMemOS 依赖基础设施端口，重启前先等 infra ready
    if (svc.id === 'evermemos') {
      const infraReady = await this.waitForEverMemOSInfra(svc.id)
      if (!infraReady) {
        // 基础设施没起来，不浪费重启次数
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

  /** EverMemOS 基础设施端口列表 */
  private static readonly EM_INFRA_PORTS = [
    { port: 27017, name: 'MongoDB' },
    { port: 19200, name: 'Elasticsearch' },
    { port: 19530, name: 'Milvus' },
    { port: 6379,  name: 'Redis' }
  ]

  /**
   * 等待 EverMemOS 基础设施端口就绪（用于 autoSetup/quickStart 安装流程）
   * 通过 setup-progress 事件更新 UI 进度，每 5 秒刷新一次
   * 最多等待 180s
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
   * 等待 EverMemOS 基础设施端口就绪（用于 tryAutoRestart 崩溃重启）
   * 通过日志输出进度，每 10 秒刷新一次
   * 最多等待 180s
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

  /** 杀掉进程组（负 PID），确保子进程也被清理 */
  private killProcessGroup(proc: ChildProcess, signal: NodeJS.Signals): void {
    if (!proc.pid) return
    try {
      // 负 PID = 杀掉整个进程组（detached: true 时进程是组长）
      process.kill(-proc.pid, signal)
    } catch {
      // 进程组可能已退出，回退到单进程 kill
      try { proc.kill(signal) } catch { /* 已退出 */ }
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

    // 防止日志无限增长
    if (this.logs.length > this.maxLogs) {
      this.logs = this.logs.slice(-this.maxLogs)
    }

    this.emit('log', entry)
  }

  private delay(ms: number): Promise<void> {
    return new Promise((resolve) => setTimeout(resolve, ms))
  }
}
