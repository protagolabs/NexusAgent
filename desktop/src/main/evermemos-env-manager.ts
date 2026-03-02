/**
 * @file evermemos-env-manager.ts
 * @description EverMemOS .env 文件的读写、验证与字段元数据
 *
 * 管理 .evermemos/.env 文件，复用 env-manager 的解析工具函数。
 * 提供字段分组元数据，供设置 UI 渲染表单。
 */

import { readFileSync, writeFileSync, existsSync, copyFileSync } from 'fs'
import { join } from 'path'
import { EVERMEMOS_DIR } from './constants'
import { parseEnvContent, serializeEnv, EnvConfig, EnvValidation } from './env-manager'

// ─── 路径 ─────────────────────────────────────────────

const ENV_PATH = join(EVERMEMOS_DIR, '.env')
const TEMPLATE_PATH = join(EVERMEMOS_DIR, 'env.template')

// ─── 字段元数据类型 ───────────────────────────────────

export interface EverMemOSEnvField {
  key: string
  label: string
  required: boolean
  placeholder: string
  inputType: 'text' | 'password' | 'select'
  options?: string[]
  group: 'llm' | 'vectorize' | 'rerank' | 'infrastructure' | 'other'
  order: number
}

// ─── 字段定义 ─────────────────────────────────────────

const FIELDS: EverMemOSEnvField[] = [
  // ── LLM ──
  { key: 'LLM_PROVIDER', label: 'LLM Provider', required: false, placeholder: 'openai', inputType: 'select', options: ['openai', 'anthropic', 'google'], group: 'llm', order: 1 },
  { key: 'LLM_MODEL', label: 'LLM Model', required: false, placeholder: 'x-ai/grok-4-fast', inputType: 'text', group: 'llm', order: 2 },
  { key: 'LLM_BASE_URL', label: 'LLM Base URL', required: false, placeholder: 'https://openrouter.ai/api/v1', inputType: 'text', group: 'llm', order: 3 },
  { key: 'LLM_API_KEY', label: 'LLM API Key', required: true, placeholder: 'sk-or-v1-xxxx', inputType: 'password', group: 'llm', order: 4 },
  { key: 'LLM_TEMPERATURE', label: 'Temperature', required: false, placeholder: '0.3', inputType: 'text', group: 'llm', order: 5 },
  { key: 'LLM_MAX_TOKENS', label: 'Max Tokens', required: false, placeholder: '32768', inputType: 'text', group: 'llm', order: 6 },

  // ── Vectorize (Embedding) ──
  { key: 'VECTORIZE_PROVIDER', label: 'Embedding Provider', required: false, placeholder: 'vllm', inputType: 'select', options: ['vllm', 'deepinfra'], group: 'vectorize', order: 1 },
  { key: 'VECTORIZE_API_KEY', label: 'Embedding API Key', required: false, placeholder: 'EMPTY (use EMPTY for vllm)', inputType: 'password', group: 'vectorize', order: 2 },
  { key: 'VECTORIZE_BASE_URL', label: 'Embedding Base URL', required: false, placeholder: 'http://localhost:8000/v1', inputType: 'text', group: 'vectorize', order: 3 },
  { key: 'VECTORIZE_MODEL', label: 'Embedding Model', required: false, placeholder: 'Qwen/Qwen3-Embedding-4B', inputType: 'text', group: 'vectorize', order: 4 },
  { key: 'VECTORIZE_DIMENSIONS', label: 'Vector Dimensions', required: false, placeholder: '1024', inputType: 'text', group: 'vectorize', order: 5 },

  // ── Rerank ──
  { key: 'RERANK_PROVIDER', label: 'Rerank Provider', required: false, placeholder: 'vllm', inputType: 'select', options: ['vllm', 'deepinfra'], group: 'rerank', order: 1 },
  { key: 'RERANK_API_KEY', label: 'Rerank API Key', required: false, placeholder: 'EMPTY (use EMPTY for vllm)', inputType: 'password', group: 'rerank', order: 2 },
  { key: 'RERANK_BASE_URL', label: 'Rerank Base URL', required: false, placeholder: 'http://localhost:12000/v1/rerank', inputType: 'text', group: 'rerank', order: 3 },
  { key: 'RERANK_MODEL', label: 'Rerank Model', required: false, placeholder: 'Qwen/Qwen3-Reranker-4B', inputType: 'text', group: 'rerank', order: 4 },

  // ── Infrastructure ──
  { key: 'REDIS_HOST', label: 'Redis Host', required: false, placeholder: 'localhost', inputType: 'text', group: 'infrastructure', order: 1 },
  { key: 'REDIS_PORT', label: 'Redis Port', required: false, placeholder: '6379', inputType: 'text', group: 'infrastructure', order: 2 },
  { key: 'REDIS_DB', label: 'Redis DB', required: false, placeholder: '8', inputType: 'text', group: 'infrastructure', order: 3 },
  { key: 'MONGODB_HOST', label: 'MongoDB Host', required: false, placeholder: 'localhost', inputType: 'text', group: 'infrastructure', order: 4 },
  { key: 'MONGODB_PORT', label: 'MongoDB Port', required: false, placeholder: '27017', inputType: 'text', group: 'infrastructure', order: 5 },
  { key: 'MONGODB_USERNAME', label: 'MongoDB User', required: false, placeholder: 'admin', inputType: 'text', group: 'infrastructure', order: 6 },
  { key: 'MONGODB_PASSWORD', label: 'MongoDB Password', required: false, placeholder: 'memsys123', inputType: 'password', group: 'infrastructure', order: 7 },
  { key: 'MONGODB_DATABASE', label: 'MongoDB Database', required: false, placeholder: 'memsys', inputType: 'text', group: 'infrastructure', order: 8 },
  { key: 'ES_HOSTS', label: 'Elasticsearch Hosts', required: false, placeholder: 'http://localhost:19200', inputType: 'text', group: 'infrastructure', order: 9 },
  { key: 'MILVUS_HOST', label: 'Milvus Host', required: false, placeholder: 'localhost', inputType: 'text', group: 'infrastructure', order: 10 },
  { key: 'MILVUS_PORT', label: 'Milvus Port', required: false, placeholder: '19530', inputType: 'text', group: 'infrastructure', order: 11 },

  // ── Other ──
  { key: 'MEMORY_LANGUAGE', label: 'Memory Language', required: false, placeholder: 'en', inputType: 'select', options: ['en', 'zh', 'ja', 'ko', 'es', 'fr', 'de'], group: 'other', order: 1 },
  { key: 'LOG_LEVEL', label: 'Log Level', required: false, placeholder: 'INFO', inputType: 'select', options: ['DEBUG', 'INFO', 'WARNING', 'ERROR'], group: 'other', order: 2 }
]

/** 模板占位值，视为"未填写" */
const PLACEHOLDER_VALUES = ['sk-or-v1-xxxx', 'xxxxx']

// ─── 内存暂存区 ─────────────────────────────────────────
// 目录尚未 clone 时，用户填写的值暂存于此，clone 完成后 flush 到磁盘

let pendingEnv: EnvConfig = {}

// ─── 公开 API ─────────────────────────────────────────

/**
 * UI 可用性：始终返回 true，SetupWizard 始终显示 EverMemOS 配置区域。
 * 实际目录是否已 clone 请使用 isCloned()。
 */
export function isAvailable(): boolean {
  return true
}

/** 检测 .evermemos/ 目录是否已 clone 到本地 */
export function isCloned(): boolean {
  return existsSync(EVERMEMOS_DIR)
}

/** 确保 .env 文件存在（从 env.template 初始化） */
export function ensureEnvFile(): void {
  if (existsSync(ENV_PATH)) return

  if (existsSync(TEMPLATE_PATH)) {
    copyFileSync(TEMPLATE_PATH, ENV_PATH)
  } else {
    writeFileSync(ENV_PATH, '', 'utf-8')
  }
}

/** 读取 .evermemos/.env，目录不存在时返回内存暂存值 */
export function readEnv(): EnvConfig {
  if (!isCloned()) return { ...pendingEnv }
  ensureEnvFile()
  const content = readFileSync(ENV_PATH, 'utf-8')
  return parseEnvContent(content)
}

/** 写入键值对，目录不存在时写入内存暂存区 */
export function writeEnv(updates: EnvConfig): void {
  if (!isCloned()) {
    pendingEnv = { ...pendingEnv, ...updates }
    return
  }
  ensureEnvFile()
  const current = readEnv()
  const merged = { ...current, ...updates }

  let template: string | undefined
  if (existsSync(TEMPLATE_PATH)) {
    template = readFileSync(TEMPLATE_PATH, 'utf-8')
  }

  const content = serializeEnv(merged, template)
  writeFileSync(ENV_PATH, content, 'utf-8')
}

/**
 * 将内存暂存值合并到磁盘 .env 文件。
 * 在 git clone 完成后调用，确保用户在 UI 填写的值不丢失。
 */
export function flushPendingEnv(): void {
  if (Object.keys(pendingEnv).length === 0) return
  if (!isCloned()) return

  ensureEnvFile()
  const current = (() => {
    const content = readFileSync(ENV_PATH, 'utf-8')
    return parseEnvContent(content)
  })()
  const merged = { ...current, ...pendingEnv }

  let template: string | undefined
  if (existsSync(TEMPLATE_PATH)) {
    template = readFileSync(TEMPLATE_PATH, 'utf-8')
  }

  const content = serializeEnv(merged, template)
  writeFileSync(ENV_PATH, content, 'utf-8')
  pendingEnv = {}
}

/**
 * 检查用户是否已配置 LLM_API_KEY（判断是否需要启动 EverMemOS）。
 * 同时检查内存暂存和磁盘值。
 */
export function isConfigured(): boolean {
  const config = readEnv()
  const key = config['LLM_API_KEY']?.trim() ?? ''
  return !!key && !PLACEHOLDER_VALUES.includes(key)
}

/** 验证必填字段 */
export function validateEnv(): EnvValidation {
  const config = readEnv()
  const missing: string[] = []
  const warnings: string[] = []

  for (const field of FIELDS) {
    const value = config[field.key]?.trim() ?? ''
    if (field.required) {
      if (!value || PLACEHOLDER_VALUES.includes(value)) {
        missing.push(field.key)
      }
    }
  }

  return { valid: missing.length === 0, missing, warnings }
}

/** 返回字段元数据列表 */
export function getFields(): EverMemOSEnvField[] {
  return FIELDS
}
