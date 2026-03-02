/**
 * @file env-manager.ts
 * @description .env 文件的读写与验证
 *
 * 管理项目根目录的 .env 文件，支持从 .env.example 初始化、
 * 读取键值对、写入键值对、验证必填字段。
 */

import { readFileSync, writeFileSync, existsSync, copyFileSync } from 'fs'
import { ENV_FILE_PATH, ENV_EXAMPLE_PATH } from './constants'

// ─── 类型定义 ───────────────────────────────────────

export interface EnvConfig {
  [key: string]: string
}

export interface EnvValidation {
  valid: boolean
  missing: string[]
  warnings: string[]
}

/** 必填字段 */
const REQUIRED_KEYS = ['OPENAI_API_KEY']

/** 可选但推荐的字段 */
const OPTIONAL_KEYS = ['GOOGLE_API_KEY', 'ADMIN_SECRET_KEY', 'ANTHROPIC_API_KEY', 'ANTHROPIC_BASE_URL']

// ─── 解析与序列化 ───────────────────────────────────

/** 解析 .env 文件内容为键值对 */
export function parseEnvContent(content: string): EnvConfig {
  const config: EnvConfig = {}
  const lines = content.split('\n')

  for (const line of lines) {
    const trimmed = line.trim()
    // 跳过空行和注释
    if (!trimmed || trimmed.startsWith('#')) continue

    const eqIndex = trimmed.indexOf('=')
    if (eqIndex === -1) continue

    const key = trimmed.substring(0, eqIndex).trim()
    let value = trimmed.substring(eqIndex + 1).trim()

    // 去除引号包裹
    if ((value.startsWith('"') && value.endsWith('"')) ||
        (value.startsWith("'") && value.endsWith("'"))) {
      value = value.slice(1, -1)
    }

    config[key] = value
  }

  return config
}

/** 将键值对序列化为 .env 文件内容（保留注释和结构） */
export function serializeEnv(config: EnvConfig, template?: string): string {
  if (!template) {
    return Object.entries(config)
      .map(([key, value]) => `${key}="${value}"`)
      .join('\n') + '\n'
  }

  // 基于模板文件保留注释结构
  const lines = template.split('\n')
  const result: string[] = []
  const writtenKeys = new Set<string>()

  for (const line of lines) {
    const trimmed = line.trim()

    // 保留空行和注释
    if (!trimmed || trimmed.startsWith('#')) {
      result.push(line)
      continue
    }

    const eqIndex = trimmed.indexOf('=')
    if (eqIndex === -1) {
      result.push(line)
      continue
    }

    const key = trimmed.substring(0, eqIndex).trim()
    if (key in config) {
      result.push(`${key}="${config[key]}"`)
      writtenKeys.add(key)
    } else {
      result.push(line)
      writtenKeys.add(key)
    }
  }

  // 追加模板中没有的新键
  for (const [key, value] of Object.entries(config)) {
    if (!writtenKeys.has(key)) {
      result.push(`${key}="${value}"`)
    }
  }

  return result.join('\n')
}

// ─── 公开 API ───────────────────────────────────────

/** 确保 .env 文件存在（从 .env.example 复制或创建空文件） */
export function ensureEnvFile(): void {
  if (existsSync(ENV_FILE_PATH)) return

  if (existsSync(ENV_EXAMPLE_PATH)) {
    copyFileSync(ENV_EXAMPLE_PATH, ENV_FILE_PATH)
  } else {
    writeFileSync(ENV_FILE_PATH, '', 'utf-8')
  }
}

/** 读取 .env 文件，返回键值对 */
export function readEnv(): EnvConfig {
  ensureEnvFile()
  const content = readFileSync(ENV_FILE_PATH, 'utf-8')
  return parseEnvContent(content)
}

/** 写入键值对到 .env 文件（合并模式，不覆盖未指定的键） */
export function writeEnv(updates: EnvConfig): void {
  ensureEnvFile()
  const current = readEnv()
  const merged = { ...current, ...updates }

  // 尝试使用 .env.example 作为模板保留格式
  let template: string | undefined
  if (existsSync(ENV_EXAMPLE_PATH)) {
    template = readFileSync(ENV_EXAMPLE_PATH, 'utf-8')
  }

  const content = serializeEnv(merged, template)
  writeFileSync(ENV_FILE_PATH, content, 'utf-8')
}

/** 验证 .env 配置是否完整 */
export function validateEnv(): EnvValidation {
  const config = readEnv()
  const missing: string[] = []
  const warnings: string[] = []

  for (const key of REQUIRED_KEYS) {
    if (!config[key] || config[key].trim() === '') {
      missing.push(key)
    }
  }

  for (const key of OPTIONAL_KEYS) {
    if (!config[key] || config[key].trim() === '') {
      warnings.push(key)
    }
  }

  return {
    valid: missing.length === 0,
    missing,
    warnings
  }
}

/** 获取所有可配置的 key 及其描述 */
export function getEnvFields(): Array<{
  key: string
  label: string
  required: boolean
  placeholder: string
}> {
  return [
    {
      key: 'OPENAI_API_KEY',
      label: 'OpenAI API Key',
      required: true,
      placeholder: 'sk-...'
    },
    {
      key: 'GOOGLE_API_KEY',
      label: 'Google API Key',
      required: false,
      placeholder: 'AIza...'
    },
    {
      key: 'NETMIND_API_KEY',
      label: 'NetMind API Key (for EverMemOS)',
      required: false,
      placeholder: 'Your NetMind API key'
    },
    {
      key: 'ANTHROPIC_API_KEY',
      label: 'Anthropic API Key (optional)',
      required: false,
      placeholder: 'sk-ant-... (leave empty to use claude CLI login)'
    },
    {
      key: 'ANTHROPIC_BASE_URL',
      label: 'Anthropic Base URL (optional)',
      required: false,
      placeholder: 'https://api.anthropic.com (leave empty for default)'
    },
    {
      key: 'ADMIN_SECRET_KEY',
      label: 'Admin Secret Key',
      required: false,
      placeholder: 'Custom admin secret'
    },
    {
      key: 'DB_HOST',
      label: 'Database Host',
      required: false,
      placeholder: '127.0.0.1'
    },
    {
      key: 'DB_PORT',
      label: 'Database Port',
      required: false,
      placeholder: '3306'
    },
    {
      key: 'DB_NAME',
      label: 'Database Name',
      required: false,
      placeholder: 'xyz_agent_context'
    },
    {
      key: 'DB_USER',
      label: 'Database User',
      required: false,
      placeholder: 'root'
    },
    {
      key: 'DB_PASSWORD',
      label: 'Database Password',
      required: false,
      placeholder: 'xyz_root_pass'
    }
  ]
}
