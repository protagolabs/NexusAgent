/**
 * @file claude-auth-manager.ts
 * @description Claude Code 凭证管理器
 *
 * 封装所有 Claude Code 凭证操作：
 * - 从 macOS Keychain / ~/.claude/.credentials.json 读取 OAuth 凭证
 * - 检测 CLI 安装状态和登录状态
 * - 启动 `claude auth login` 一键浏览器登录（PTY 模式优先，pipe 回退）
 * - 验证 setup-token 格式
 *
 * 参考 OpenClaw cli-credentials.ts 的凭证读取模式，
 * 参考 OpenClaw pty.ts 的 @lydell/node-pty 用法。
 */

import { execFileSync, execFile, spawn, type ChildProcess } from 'child_process'
import { readFileSync, existsSync } from 'fs'
import { join } from 'path'
import { homedir } from 'os'
import { promisify } from 'util'
import { getShellEnv } from './shell-env'

const execFileAsync = promisify(execFile)

// ─── node-pty 动态加载 ─────────────────────────────
//
// macOS 上 claude auth login 的 localhost OAuth 回调可能被浏览器阻止
// （HTTPS → HTTP 降级），导致用户必须手动粘贴 auth code。
// pipe 模式下 CLI 不读取 stdin，需要真正的 PTY 使 sendInput() 可工作。
// 参考 OpenClaw: src/process/supervisor/adapters/pty.ts

/** node-pty spawn 返回的进程句柄 */
interface PtyHandle {
  pid: number
  write: (data: string) => void
  onData: (listener: (data: string) => void) => { dispose: () => void } | void
  onExit: (listener: (event: { exitCode: number; signal?: number }) => void) => { dispose: () => void } | void
  kill: (signal?: string) => void
}

/** node-pty 的 spawn 函数签名 */
type PtySpawnFn = (
  file: string,
  args: string[],
  options: {
    name?: string
    cols?: number
    rows?: number
    cwd?: string
    env?: Record<string, string>
  }
) => PtyHandle

/**
 * 动态加载 @lydell/node-pty 的 spawn 函数
 *
 * 使用 try/catch 处理未安装的情况，回退到 pipe 模式。
 * electron-vite externalizeDepsPlugin 会保留 require 调用，
 * 运行时从 node_modules 加载原生模块。
 */
function getNodePtySpawn(): PtySpawnFn | null {
  try {
    // eslint-disable-next-line @typescript-eslint/no-var-requires
    const mod = require('@lydell/node-pty') as Record<string, unknown>
    const spawn = (mod.spawn ?? (mod.default as Record<string, unknown> | undefined)?.spawn) as PtySpawnFn | undefined
    return spawn ?? null
  } catch {
    return null
  }
}

// ─── 类型定义 ───────────────────────────────────────

/** Claude OAuth 原始数据（credentials.json 中的结构） */
interface ClaudeOAuthData {
  accessToken: string
  refreshToken?: string
  expiresAt: number
}

/** 认证状态 */
export interface ClaudeAuthStatus {
  state: 'logged_in' | 'expired' | 'not_logged_in' | 'cli_not_installed'
  method?: 'oauth' | 'token'
  expiresAt?: number
  isExpired?: boolean
}

/** 完整认证信息 */
export interface ClaudeAuthInfo {
  cliInstalled: boolean
  cliVersion: string | null
  authStatus: ClaudeAuthStatus
  hasApiKey: boolean
  hasSetupToken: boolean
}

/** 登录进程状态 */
export interface LoginProcessStatus {
  state: 'idle' | 'running' | 'success' | 'failed' | 'timeout'
  message?: string
}

// ─── 常量 ─────────────────────────────────────────

const CREDENTIALS_RELATIVE_PATH = '.claude/.credentials.json'
const KEYCHAIN_SERVICE = 'Claude Code-credentials'
const SETUP_TOKEN_PREFIX = 'sk-ant-oat01-'
const SETUP_TOKEN_MIN_LENGTH = 80
const LOGIN_POLL_INTERVAL = 2000
const DEFAULT_LOGIN_TIMEOUT = 120000
/** macOS 上等待 OAuth 回调的超时，超时后提示手动粘贴 auth code */
const MACOS_CALLBACK_HINT_DELAY = 20000

// ─── 凭证读取 ─────────────────────────────────────

/**
 * 从 macOS Keychain 读取 Claude Code 凭证
 *
 * 使用 security 命令读取 "Claude Code-credentials" 服务的密码，
 * 解析 JSON 中的 claudeAiOauth 字段。仅 macOS 可用。
 */
function readCredentialsFromKeychain(): ClaudeOAuthData | null {
  if (process.platform !== 'darwin') return null

  try {
    const result = execFileSync(
      'security',
      ['find-generic-password', '-s', KEYCHAIN_SERVICE, '-w'],
      { encoding: 'utf8', timeout: 5000, stdio: ['pipe', 'pipe', 'pipe'] }
    )

    const data = JSON.parse(result.trim())
    return parseOAuthData(data?.claudeAiOauth)
  } catch {
    return null
  }
}

/**
 * 从 ~/.claude/.credentials.json 文件读取凭证
 */
function readCredentialsFromFile(): ClaudeOAuthData | null {
  try {
    const credPath = join(homedir(), CREDENTIALS_RELATIVE_PATH)
    if (!existsSync(credPath)) return null

    const content = readFileSync(credPath, 'utf-8')
    const data = JSON.parse(content)
    return parseOAuthData(data?.claudeAiOauth)
  } catch {
    return null
  }
}

/**
 * 解析并校验 OAuth 数据
 */
function parseOAuthData(oauth: unknown): ClaudeOAuthData | null {
  if (!oauth || typeof oauth !== 'object') return null

  const obj = oauth as Record<string, unknown>
  const accessToken = obj.accessToken
  const refreshToken = obj.refreshToken
  const expiresAt = obj.expiresAt

  if (typeof accessToken !== 'string' || !accessToken) return null
  if (typeof expiresAt !== 'number' || !Number.isFinite(expiresAt) || expiresAt <= 0) return null

  return {
    accessToken,
    refreshToken: typeof refreshToken === 'string' && refreshToken ? refreshToken : undefined,
    expiresAt
  }
}

/** 判断凭证是否已过期 */
function isExpired(expiresAt: number): boolean {
  return Date.now() > expiresAt
}

/**
 * 读取 Claude CLI 凭证（主入口）
 *
 * macOS: Keychain 优先 -> 回退文件
 * Linux: 仅文件
 */
export function readClaudeCredentials(): ClaudeOAuthData | null {
  if (process.platform === 'darwin') {
    const keychainCreds = readCredentialsFromKeychain()
    if (keychainCreds) return keychainCreds
  }
  return readCredentialsFromFile()
}

// ─── 认证状态检测 ──────────────────────────────────

/**
 * 获取 Claude Code 完整认证信息
 *
 * 1. 检测 CLI 安装状态（claude --version, 10 秒超时）
 * 2. 读取凭证文件判断登录状态（毫秒级）
 * 3. 检查 .env 中的 ANTHROPIC_API_KEY
 */
export async function getClaudeAuthInfo(readEnvFn: () => Record<string, string>): Promise<ClaudeAuthInfo> {
  // 1. 检测 CLI 安装
  let cliInstalled = false
  let cliVersion: string | null = null
  try {
    const { stdout } = await execFileAsync('claude', ['--version'], {
      timeout: 10000,
      env: getShellEnv()
    })
    cliInstalled = true
    cliVersion = stdout.trim().split('\n')[0] || null
  } catch {
    // CLI 未安装
  }

  // 2. 读取凭证 → 认证状态
  let authStatus: ClaudeAuthStatus
  const creds = readClaudeCredentials()
  if (creds) {
    const expired = isExpired(creds.expiresAt)
    authStatus = {
      state: expired ? 'expired' : 'logged_in',
      method: creds.refreshToken ? 'oauth' : 'token',
      expiresAt: creds.expiresAt,
      isExpired: expired
    }
  } else if (cliInstalled) {
    authStatus = { state: 'not_logged_in' }
  } else {
    authStatus = { state: 'cli_not_installed' }
  }

  // 3. 检查 .env 中的 API Key
  const envConfig = readEnvFn()
  const apiKey = envConfig.ANTHROPIC_API_KEY?.trim() || ''
  const hasApiKey = !!apiKey
  const hasSetupToken = apiKey.startsWith(SETUP_TOKEN_PREFIX) && apiKey.length >= SETUP_TOKEN_MIN_LENGTH

  return {
    cliInstalled,
    cliVersion,
    authStatus,
    hasApiKey,
    hasSetupToken
  }
}

// ─── 一键登录 ──────────────────────────────────────

/**
 * 启动 `claude auth login` 浏览器登录流程
 *
 * 混合策略（参考 OpenClaw）：
 * 1. 优先使用 node-pty 提供真正的伪终端（macOS 上 auth code 输入可工作）
 * 2. node-pty 不可用时回退到 pipe 模式（Linux 上 localhost 回调通常自动成功）
 * 3. macOS 上若 20 秒内未检测到新凭证，提示用户手动粘贴 auth code
 *
 * - 每 2 秒轮询凭证文件检测登录完成
 * - 超时 120 秒自动终止
 * - 返回可取消的句柄
 */
export function startClaudeLogin(
  onStatusChange: (status: LoginProcessStatus) => void,
  onUrlDetected?: (url: string) => void,
  timeoutMs: number = DEFAULT_LOGIN_TIMEOUT
): { promise: Promise<LoginProcessStatus>; cancel: () => void; sendInput: (text: string) => void } {
  // ── 进程句柄（PTY 模式和 pipe 模式二选一） ──
  let ptyHandle: PtyHandle | null = null
  let childProc: ChildProcess | null = null

  // ── 定时器 ──
  let pollTimer: ReturnType<typeof setInterval> | null = null
  let timeoutTimer: ReturnType<typeof setTimeout> | null = null
  let fallbackHintTimer: ReturnType<typeof setTimeout> | null = null
  let cancelled = false

  const cleanup = () => {
    if (pollTimer) { clearInterval(pollTimer); pollTimer = null }
    if (timeoutTimer) { clearTimeout(timeoutTimer); timeoutTimer = null }
    if (fallbackHintTimer) { clearTimeout(fallbackHintTimer); fallbackHintTimer = null }
    if (ptyHandle) {
      try { ptyHandle.kill('SIGTERM') } catch { /* 已退出 */ }
      ptyHandle = null
    }
    if (childProc) {
      try { childProc.kill('SIGTERM') } catch { /* 已退出 */ }
      childProc = null
    }
  }

  const cancel = () => {
    cancelled = true
    cleanup()
    onStatusChange({ state: 'failed', message: 'Login cancelled by user' })
  }

  /**
   * 向子进程写入内容（用于传递浏览器返回的 auth code）
   *
   * PTY 模式：写入 PTY stdin（CLI 可正常读取，\r = Enter）
   * pipe 模式：写入 pipe stdin（CLI 可能不读取，但仍尝试）
   */
  const sendInput = (text: string) => {
    if (ptyHandle) {
      ptyHandle.write(text + '\r')
    } else if (childProc?.stdin?.writable) {
      childProc.stdin.write(text + '\n')
    }
  }

  const promise = new Promise<LoginProcessStatus>((resolve) => {
    // 记录登录前的凭证状态（用于对比检测新凭证）
    const credsBefore = readClaudeCredentials()

    onStatusChange({ state: 'running', message: 'Opening browser for authentication...' })

    // 构造子进程环境：基于 shell 环境，禁用颜色输出以简化 URL 解析
    const shellEnv = getShellEnv()
    const spawnEnv: Record<string, string> = {
      ...shellEnv,
      FORCE_COLOR: '0',       // 禁用颜色输出，防止 ANSI 转义码干扰 URL 解析
      NO_COLOR: '1',          // 社区标准的禁色标志
      TERM: 'dumb',           // 告知 CLI 当前不是富终端
      HOME: process.env.HOME || homedir()  // 确保 HOME 存在（Keychain/凭证读写依赖）
    }

    // ── 共享的输出处理逻辑 ──
    const stripAnsi = (s: string) => s.replace(/\x1B\[[0-9;]*[a-zA-Z]/g, '')
    const urlRegex = /https?:\/\/[^\s\x1B]+/
    let urlOpened = false
    let accumulatedOutput = ''

    const handleOutput = (raw: string) => {
      const text = stripAnsi(raw)
      console.log(`[claude-login] output: ${text.substring(0, 300)}`)
      accumulatedOutput += text

      // 把 CLI 输出实时推送到 UI，方便调试
      onStatusChange({ state: 'running', message: `CLI: ${text.substring(0, 150).trim() || '(waiting...)'}` })

      if (!urlOpened) {
        const match = text.match(urlRegex)
        if (match) {
          // 去除 URL 尾部可能沾上的标点符号
          const cleanUrl = match[0].replace(/[)>\]'",;]+$/, '')
          urlOpened = true
          onUrlDetected?.(cleanUrl)
          onStatusChange({ state: 'running', message: 'Browser opened. Waiting for authorization...' })

          // macOS 上启动回退提示计时器：
          // OAuth 回调可能因 HTTPS→HTTP 降级被浏览器阻止，
          // 此时用户需要手动粘贴浏览器页面上显示的 auth code
          if (process.platform === 'darwin') {
            fallbackHintTimer = setTimeout(() => {
              if (!cancelled) {
                const creds = readClaudeCredentials()
                const isNewCreds = creds && (!credsBefore || creds.accessToken !== credsBefore.accessToken)
                if (!isNewCreds) {
                  onStatusChange({
                    state: 'running',
                    message: 'Paste the auth code from browser into the input box below. Or run `claude setup-token` in terminal.'
                  })
                }
              }
            }, MACOS_CALLBACK_HINT_DELAY)
          }
        }
      }
    }

    /** 共享的进程退出处理 */
    const handleExit = (code: number | null) => {
      clearTimeout(noOutputTimer)
      if (cancelled) return

      // 进程退出后，从累积输出中做最后一次 URL 匹配（应对分块传输）
      if (!urlOpened) {
        const match = accumulatedOutput.match(urlRegex)
        if (match) {
          const cleanUrl = match[0].replace(/[)>\]'",;]+$/, '')
          urlOpened = true
          onUrlDetected?.(cleanUrl)
          onStatusChange({ state: 'running', message: 'Browser opened. Waiting for authorization...' })
          // 不 resolve，继续轮询等待凭证
          return
        }
      }

      // 进程正常退出后做一次最终检查
      const creds = readClaudeCredentials()
      if (creds && (!credsBefore || creds.accessToken !== credsBefore.accessToken)) {
        cleanup()
        const status: LoginProcessStatus = { state: 'success', message: 'Login successful' }
        onStatusChange(status)
        resolve(status)
      } else if (code !== 0 && code !== null) {
        cleanup()
        const status: LoginProcessStatus = {
          state: 'failed',
          message: `claude login exited with code ${code}. Output: ${accumulatedOutput.substring(0, 200)}`
        }
        onStatusChange(status)
        resolve(status)
      } else {
        // code === 0 但没检测到新凭证：可能已登录，或凭证写入有延迟
        if (creds && !isExpired(creds.expiresAt)) {
          cleanup()
          const status: LoginProcessStatus = {
            state: 'success',
            message: 'Already logged in (valid credentials found)'
          }
          onStatusChange(status)
          resolve(status)
        }
        // 否则继续轮询（凭证写入可能有延迟），最终超时
      }
    }

    // ── 启动子进程：PTY 优先，pipe 回退 ──

    const ptySpawn = getNodePtySpawn()

    if (ptySpawn) {
      // PTY 模式：提供真正的伪终端，使 CLI 的 stdin 交互可用
      // 这样 macOS 上用户可以通过 Desktop 输入框粘贴 auth code
      try {
        ptyHandle = ptySpawn('claude', ['auth', 'login'], {
          cwd: homedir(),
          env: spawnEnv,
          name: 'dumb',    // TERM=dumb，减少 ANSI 输出
          cols: 120,
          rows: 30,
        })

        console.log(`[claude-login] spawned with PTY mode (pid=${ptyHandle.pid})`)

        // PTY 合并 stdout/stderr 为单一数据流
        ptyHandle.onData((data: string) => {
          handleOutput(data)
        })

        ptyHandle.onExit((event: { exitCode: number; signal?: number }) => {
          handleExit(event.exitCode)
        })
      } catch (err) {
        // PTY spawn 失败（例如 claude 未安装），立即报错
        if (!cancelled) {
          cleanup()
          const status: LoginProcessStatus = {
            state: 'failed',
            message: `Failed to start claude login (PTY): ${err instanceof Error ? err.message : String(err)}`
          }
          onStatusChange(status)
          resolve(status)
        }
        return
      }
    } else {
      // Pipe 模式回退：node-pty 不可用时使用标准 child_process
      // Linux 上 localhost 回调通常自动成功，不需要手动粘贴 auth code
      console.log('[claude-login] node-pty not available, falling back to pipe mode')

      childProc = spawn('claude', ['auth', 'login'], {
        env: spawnEnv,
        cwd: homedir(),
        stdio: ['pipe', 'pipe', 'pipe']
      })

      childProc.stdout?.on('data', (d: Buffer) => handleOutput(d.toString()))
      childProc.stderr?.on('data', (d: Buffer) => handleOutput(d.toString()))

      childProc.on('error', (err) => {
        if (cancelled) return
        clearTimeout(noOutputTimer)
        cleanup()
        const status: LoginProcessStatus = {
          state: 'failed',
          message: `Failed to start claude login: ${err.message}`
        }
        onStatusChange(status)
        resolve(status)
      })

      childProc.on('exit', (code) => {
        handleExit(code)
      })
    }

    // ── 5 秒无输出告警 ──
    const noOutputTimer = setTimeout(() => {
      if (!urlOpened && !cancelled) {
        onStatusChange({
          state: 'running',
          message: accumulatedOutput
            ? `No URL found in output: ${accumulatedOutput.substring(0, 150)}`
            : 'claude login produced no output after 5s'
        })
      }
    }, 5000)

    // ── 轮询检测凭证变化 ──
    pollTimer = setInterval(() => {
      if (cancelled) return
      const creds = readClaudeCredentials()
      if (creds && (!credsBefore || creds.accessToken !== credsBefore.accessToken)) {
        cleanup()
        const status: LoginProcessStatus = { state: 'success', message: 'Login successful' }
        onStatusChange(status)
        resolve(status)
      }
    }, LOGIN_POLL_INTERVAL)

    // ── 超时处理 ──
    timeoutTimer = setTimeout(() => {
      if (cancelled) return
      cleanup()
      const status: LoginProcessStatus = {
        state: 'timeout',
        message: `Login timed out after ${timeoutMs / 1000}s. Please try again.`
      }
      onStatusChange(status)
      resolve(status)
    }, timeoutMs)
  })

  return { promise, cancel, sendInput }
}

// ─── Setup Token 验证 ──────────────────────────────

/**
 * 验证 setup-token 格式
 *
 * 格式要求：以 sk-ant-oat01- 开头，至少 80 个字符
 */
export function validateSetupToken(token: string): { valid: boolean; message: string } {
  const trimmed = token.trim()
  if (!trimmed) {
    return { valid: false, message: 'Token is empty' }
  }
  if (!trimmed.startsWith(SETUP_TOKEN_PREFIX)) {
    return { valid: false, message: `Token must start with ${SETUP_TOKEN_PREFIX}` }
  }
  if (trimmed.length < SETUP_TOKEN_MIN_LENGTH) {
    return { valid: false, message: `Token is too short (minimum ${SETUP_TOKEN_MIN_LENGTH} characters)` }
  }
  return { valid: true, message: 'Token format is valid' }
}
