/**
 * @file docker-manager.ts
 * @description Docker Compose 容器生命周期管理
 *
 * 管理 MySQL（主数据库）和 EverMemOS（可选）的 Docker 容器。
 * 通过 child_process 调用 docker compose 命令。
 */

import { execFile } from 'child_process'
import { promisify } from 'util'
import { existsSync } from 'fs'
import { DOCKER_COMPOSE_PATH, EVERMEMOS_COMPOSE_PATH, PORTS } from './constants'
import { getShellEnv } from './shell-env'

const execFileAsync = promisify(execFile)

// ─── 类型定义 ───────────────────────────────────────

export interface ContainerStatus {
  name: string
  state: 'running' | 'stopped' | 'not_found'
  ports: string
}

export interface DockerGroupStatus {
  id: string
  label: string
  containers: ContainerStatus[]
  composePath: string
  available: boolean
}

// ─── 工具函数 ───────────────────────────────────────

/** 安全执行命令（使用登录 Shell 环境确保能找到 docker） */
async function execSafe(
  cmd: string,
  args: string[],
  options: { timeout?: number } = {}
): Promise<{ stdout: string; stderr: string; success: boolean }> {
  try {
    const result = await execFileAsync(cmd, args, {
      timeout: options.timeout ?? 30000,
      env: getShellEnv()
    })
    return { ...result, success: true }
  } catch (err: unknown) {
    const message = err instanceof Error ? err.message : String(err)
    return { stdout: '', stderr: message, success: false }
  }
}

/**
 * 检测可用的 compose 命令风格（缓存结果）
 *
 * - V2 插件: docker compose（Docker Desktop 或 docker-compose-plugin 包）
 * - V1 独立: docker-compose（Homebrew 或 pip 安装）
 */
let composeStyle: 'v2' | 'v1' | null = null

async function detectComposeStyle(): Promise<'v2' | 'v1'> {
  if (composeStyle) return composeStyle

  const v2 = await execSafe('docker', ['compose', 'version'], { timeout: 5000 })
  if (v2.success) { composeStyle = 'v2'; return 'v2' }

  const v1 = await execSafe('docker-compose', ['version'], { timeout: 5000 })
  if (v1.success) { composeStyle = 'v1'; return 'v1' }

  // 都不可用时默认 V2，让错误信息更明确
  composeStyle = 'v2'
  return 'v2'
}

/** 重置缓存（Docker 安装后需要重新检测） */
export function resetComposeDetection(): void {
  composeStyle = null
}

/** 获取 docker compose 命令前缀（自动适配 V1/V2） */
async function composeCmd(composePath: string): Promise<{ cmd: string; baseArgs: string[] }> {
  const style = await detectComposeStyle()
  return style === 'v1'
    ? { cmd: 'docker-compose', baseArgs: ['-f', composePath] }
    : { cmd: 'docker', baseArgs: ['compose', '-f', composePath] }
}

// ─── Docker 状态检测 ─────────────────────────────────

/** 检测 Docker daemon 是否就绪 */
export async function isDockerReady(): Promise<boolean> {
  const result = await execSafe('docker', ['info'], { timeout: 10000 })
  return result.success
}

/**
 * 确保 Docker daemon 可用。
 * Dashboard "Start All" 前调用，避免重启后 daemon 不在的问题。
 *
 * macOS: Docker Desktop → Colima
 * Linux: systemctl → pkexec systemctl
 */
export async function ensureDockerDaemon(): Promise<boolean> {
  if (await isDockerReady()) return true

  if (process.platform === 'darwin') {
    // 策略 1: 启动 Docker Desktop
    try {
      await execSafe('open', ['-a', 'Docker'], { timeout: 10000 })
      for (let i = 0; i < 30; i++) {
        await new Promise((r) => setTimeout(r, 2000))
        if (await isDockerReady()) return true
      }
    } catch { /* Docker Desktop 未安装 */ }

    // 策略 2: 启动 Colima
    const result = await execSafe('colima', ['start'], { timeout: 120000 })
    if (result.success && await isDockerReady()) return true
  } else {
    // Linux 策略 1: 直接 systemctl（可能已在 docker 组）
    await execSafe('systemctl', ['start', 'docker'], { timeout: 30000 })
    if (await isDockerReady()) return true

    // Linux 策略 2: 提权 systemctl（pkexec 弹密码框）
    await execSafe('pkexec', ['systemctl', 'start', 'docker'], { timeout: 30000 })
    if (await isDockerReady()) return true
  }

  return false
}

// ─── MySQL（主数据库） ──────────────────────────────

/** 启动 MySQL 容器 */
export async function startMySQL(): Promise<{ success: boolean; output: string }> {
  const { cmd, baseArgs } = await composeCmd(DOCKER_COMPOSE_PATH)
  const result = await execSafe(cmd, [...baseArgs, 'up', '-d'], { timeout: 60000 })
  return {
    success: result.success,
    output: result.success ? result.stdout : result.stderr
  }
}

/** 停止 MySQL 容器 */
export async function stopMySQL(): Promise<{ success: boolean; output: string }> {
  const { cmd, baseArgs } = await composeCmd(DOCKER_COMPOSE_PATH)
  const result = await execSafe(cmd, [...baseArgs, 'down'], { timeout: 30000 })
  return {
    success: result.success,
    output: result.success ? result.stdout : result.stderr
  }
}

/** 获取 MySQL 容器状态 */
export async function getMySQLStatus(): Promise<ContainerStatus[]> {
  return getComposeStatus(DOCKER_COMPOSE_PATH)
}

/** 检测 MySQL 端口是否可连接 */
export async function isMySQLReady(): Promise<boolean> {
  return checkPort(PORTS.MYSQL)
}

// ─── EverMemOS（可选） ──────────────────────────────

/** 检查 EverMemOS 配置是否存在 */
export function isEverMemOSAvailable(): boolean {
  return existsSync(EVERMEMOS_COMPOSE_PATH)
}

/** 启动 EverMemOS 容器 */
export async function startEverMemOS(): Promise<{ success: boolean; output: string }> {
  if (!isEverMemOSAvailable()) {
    return { success: false, output: 'EverMemOS config file not found' }
  }
  const { cmd, baseArgs } = await composeCmd(EVERMEMOS_COMPOSE_PATH)
  const result = await execSafe(cmd, [...baseArgs, 'up', '-d'], { timeout: 120000 })
  return {
    success: result.success,
    output: result.success ? result.stdout : result.stderr
  }
}

/** 停止 EverMemOS 容器 */
export async function stopEverMemOS(): Promise<{ success: boolean; output: string }> {
  if (!isEverMemOSAvailable()) return { success: true, output: '' }
  const { cmd, baseArgs } = await composeCmd(EVERMEMOS_COMPOSE_PATH)
  const result = await execSafe(cmd, [...baseArgs, 'down'], { timeout: 30000 })
  return {
    success: result.success,
    output: result.success ? result.stdout : result.stderr
  }
}

// ─── 通用操作 ───────────────────────────────────────

/** 启动所有 Docker 容器（MySQL + 可选 EverMemOS） */
export async function startAll(): Promise<{ mysql: boolean; evermemos: boolean }> {
  // 确保 Docker daemon 可用（重启电脑后 Colima 可能未启动）
  await ensureDockerDaemon()

  const mysqlResult = await startMySQL()
  let evermemosResult = { success: true }
  if (isEverMemOSAvailable()) {
    evermemosResult = await startEverMemOS()
  }
  return {
    mysql: mysqlResult.success,
    evermemos: evermemosResult.success
  }
}

/** 停止所有 Docker 容器 */
export async function stopAll(): Promise<void> {
  await Promise.all([stopMySQL(), stopEverMemOS()])
}

/** 获取所有 Docker 组状态 */
export async function getAllStatus(): Promise<DockerGroupStatus[]> {
  const groups: DockerGroupStatus[] = [
    {
      id: 'mysql',
      label: 'MySQL',
      containers: await getComposeStatus(DOCKER_COMPOSE_PATH),
      composePath: DOCKER_COMPOSE_PATH,
      available: existsSync(DOCKER_COMPOSE_PATH)
    }
  ]

  if (isEverMemOSAvailable()) {
    groups.push({
      id: 'evermemos',
      label: 'EverMemOS',
      containers: await getComposeStatus(EVERMEMOS_COMPOSE_PATH),
      composePath: EVERMEMOS_COMPOSE_PATH,
      available: true
    })
  }

  return groups
}

// ─── 内部工具 ───────────────────────────────────────

/** 获取指定 compose 文件的容器状态 */
async function getComposeStatus(composePath: string): Promise<ContainerStatus[]> {
  if (!existsSync(composePath)) return []

  const { cmd, baseArgs } = await composeCmd(composePath)
  const result = await execSafe(cmd, [
    ...baseArgs,
    'ps',
    '--format',
    'json'
  ])

  if (!result.success || !result.stdout.trim()) return []

  try {
    // docker compose ps --format json 每行输出一个 JSON 对象
    const lines = result.stdout.trim().split('\n')
    return lines.map((line) => {
      const data = JSON.parse(line)
      return {
        name: data.Name || data.Service || 'unknown',
        state: (data.State === 'running' ? 'running' : 'stopped') as 'running' | 'stopped',
        ports: data.Ports || ''
      }
    })
  } catch {
    return []
  }
}

/** 检查端口是否可连接 */
async function checkPort(port: number, host = '127.0.0.1'): Promise<boolean> {
  return new Promise((resolve) => {
    const net = require('net')
    const socket = new net.Socket()
    socket.setTimeout(2000)
    socket.on('connect', () => {
      socket.destroy()
      resolve(true)
    })
    socket.on('error', () => resolve(false))
    socket.on('timeout', () => {
      socket.destroy()
      resolve(false)
    })
    socket.connect(port, host)
  })
}
