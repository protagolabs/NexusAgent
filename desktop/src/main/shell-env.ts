/**
 * @file shell-env.ts
 * @description 登录 Shell 环境变量解析器
 *
 * macOS 双击 .app 启动时，Electron 只继承 launchd 的极简环境变量，
 * 用户在 ~/.zshrc 等配置中设置的 PATH、API Key 等全部不可见。
 *
 * 本模块在启动时执行一次用户的登录 Shell（`shell -ilc 'env -0'`），
 * 解析完整环境变量并缓存，供所有子进程使用。
 * Linux 直接使用 process.env（终端启动已继承完整环境）。
 */

import { execFile } from 'child_process'
import { promisify } from 'util'
import { join } from 'path'

const execFileAsync = promisify(execFile)

/** 缓存的环境变量 */
let cachedEnv: Record<string, string> | null = null

/**
 * 启动时调用一次，解析用户登录 Shell 的完整环境变量并缓存。
 * 仅 macOS 执行 Shell 解析，Linux 直接使用 process.env。
 */
export async function initShellEnv(): Promise<void> {
  if (process.platform !== 'darwin') {
    // Linux：终端启动已继承完整环境，直接使用
    cachedEnv = { ...process.env } as Record<string, string>
    console.log(`[shell-env] Linux detected, using process.env (${Object.keys(cachedEnv).length} vars)`)
    return
  }

  try {
    const shell = process.env.SHELL || '/bin/zsh'
    console.log(`[shell-env] Resolving login shell env via: ${shell}`)

    const { stdout } = await execFileAsync(shell, ['-ilc', 'env -0'], {
      timeout: 10000,
      env: process.env,
      // 避免 stdin 阻塞
      maxBuffer: 10 * 1024 * 1024
    })

    // NUL 分隔解析（env -0 输出 KEY=VALUE\0KEY=VALUE\0...）
    const parsed: Record<string, string> = {}
    const entries = stdout.split('\0')
    for (const entry of entries) {
      if (!entry) continue
      const eqIndex = entry.indexOf('=')
      if (eqIndex === -1) continue
      const key = entry.substring(0, eqIndex)
      const value = entry.substring(eqIndex + 1)
      parsed[key] = value
    }

    cachedEnv = parsed
    console.log(`[shell-env] Resolved ${Object.keys(cachedEnv).length} vars from login shell`)
  } catch (err) {
    console.warn(`[shell-env] Failed to resolve login shell env, using fallback:`, err)
    cachedEnv = buildFallbackEnv()
    console.log(`[shell-env] Fallback env has ${Object.keys(cachedEnv).length} vars`)
  }
}

/**
 * 同步获取已缓存的环境变量，供所有子进程的 env 参数使用。
 * 若 initShellEnv 尚未调用，返回 fallback 环境。
 */
export function getShellEnv(): Record<string, string> {
  if (cachedEnv) return cachedEnv
  // 尚未初始化时使用 fallback（不应该发生，但做防御）
  console.warn('[shell-env] getShellEnv() called before initShellEnv(), using fallback')
  cachedEnv = buildFallbackEnv()
  return cachedEnv
}

/** 构建 fallback 环境：process.env + 常用工具路径 */
function buildFallbackEnv(): Record<string, string> {
  const home = process.env.HOME || ''
  const extraPaths = [
    '/usr/local/bin',
    '/opt/homebrew/bin',
    join(home, '.local', 'bin'),            // Claude Code CLI 默认安装路径 (~/.local/bin/claude)
    join(home, '.cargo', 'bin'),
    join(home, '.nvm', 'versions', 'node'),  // nvm 常见路径
  ]
  const currentPath = process.env.PATH || '/usr/bin:/bin'
  const enhancedPath = [currentPath, ...extraPaths].join(':')

  return {
    ...(process.env as Record<string, string>),
    PATH: enhancedPath
  }
}
