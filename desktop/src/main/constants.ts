/**
 * @file constants.ts
 * @description 桌面应用常量定义：端口、路径、服务配置
 */

import { app } from 'electron'
import { join } from 'path'
import { cpSync, existsSync } from 'fs'

// ─── 路径 ───────────────────────────────────────────

/**
 * 打包后的只读项目源码（Resources/project）
 * 开发模式下为 null
 */
export const BUNDLED_PROJECT_ROOT = app.isPackaged
  ? join(process.resourcesPath, 'project')
  : null

/**
 * 可写的项目根目录：
 * - 开发模式：仓库根目录（本身可写）
 * - 打包模式：~/Library/Application Support/NarraNexus/project/
 *
 * 打包后 .app 内是只读文件系统，无法写入 .env 或 .venv，
 * 因此首次启动时将项目复制到 userData 目录。
 */
export const PROJECT_ROOT = app.isPackaged
  ? join(app.getPath('userData'), 'project')
  : join(__dirname, '..', '..', '..')  // out/main/ -> desktop/ -> NexusAgent/

/**
 * 确保可写的项目目录与打包资源保持同步
 *
 * 每次启动都用 cpSync 覆盖式合并（overlay），确保源码和依赖声明始终最新。
 * 用户数据（.env、.venv）只存在于目标目录，不会被覆盖或删除。
 */
export function ensureWritableProject(): void {
  if (!app.isPackaged || !BUNDLED_PROJECT_ROOT) return

  console.log(`[constants] Syncing bundled project to: ${PROJECT_ROOT}`)
  cpSync(BUNDLED_PROJECT_ROOT, PROJECT_ROOT, { recursive: true })
  console.log('[constants] Project synced successfully')
}

/** 前端目录 */
export const FRONTEND_DIR = join(PROJECT_ROOT, 'frontend')

/** .env 文件路径 */
export const ENV_FILE_PATH = join(PROJECT_ROOT, '.env')

/** .env.example 文件路径 */
export const ENV_EXAMPLE_PATH = join(PROJECT_ROOT, '.env.example')

/** Docker Compose 文件路径（MySQL） */
export const DOCKER_COMPOSE_PATH = join(PROJECT_ROOT, 'docker-compose.yaml')

/** EverMemOS Docker Compose 文件路径 */
export const EVERMEMOS_COMPOSE_PATH = join(PROJECT_ROOT, '.evermemos', 'docker-compose.yaml')

/** 表管理脚本目录 */
export const TABLE_MGMT_DIR = join(
  PROJECT_ROOT,
  'src',
  'xyz_agent_context',
  'utils',
  'database_table_management'
)

// ─── 端口 ───────────────────────────────────────────

export const PORTS = {
  MYSQL: 3306,
  BACKEND: 8000,
  MCP_START: 7801,
  MCP_END: 7805,
  EVERMEMOS: 1995
} as const

/** EverMemOS 项目目录 */
export const EVERMEMOS_DIR = join(PROJECT_ROOT, '.evermemos')

/** EverMemOS Git 仓库地址 */
export const EVERMEMOS_GIT_URL = 'https://github.com/NetMindAI-Open/EverMemOS.git'

/** 所有 MCP 模块端口列表（7801-7805） */
export const MCP_PORTS = Array.from(
  { length: PORTS.MCP_END - PORTS.MCP_START + 1 },
  (_, i) => PORTS.MCP_START + i
)

// ─── 基础设施服务（Docker 容器） ─────────────────────

/** 基础设施服务定义（Docker 容器，HealthMonitor 检查端口健康） */
export const INFRA_SERVICES = [
  { id: 'mysql',         label: 'MySQL',         port: 3306,  required: true },
  { id: 'mongodb',       label: 'MongoDB',       port: 27017, required: false },
  { id: 'elasticsearch', label: 'Elasticsearch', port: 19200, required: false },
  { id: 'milvus',        label: 'Milvus',        port: 19530, required: false },
  { id: 'redis',         label: 'Redis',         port: 6379,  required: false }
] as const

// ─── 服务定义 ───────────────────────────────────────

export interface ServiceDef {
  /** 服务唯一标识 */
  id: string
  /** 显示名称 */
  label: string
  /** 启动命令 */
  command: string
  /** 命令参数 */
  args: string[]
  /** 工作目录（相对于 PROJECT_ROOT） */
  cwd?: string
  /** 健康检查端口（null 表示无端口检查） */
  port: number | null
  /** 健康检查 URL（null 表示仅检查端口） */
  healthUrl: string | null
  /** 启动顺序（越小越先启动） */
  order: number
  /** 可选服务：cwd 不存在时静默跳过（不阻塞启动） */
  optional?: boolean
}

/** 所有后台服务定义 */
export const SERVICES: ServiceDef[] = [
  {
    id: 'backend',
    label: 'Backend API',
    command: 'uv',
    args: ['run', 'uvicorn', 'backend.main:app', '--port', '8000'],
    port: PORTS.BACKEND,
    healthUrl: 'http://localhost:8000/docs',
    order: 1
  },
  {
    id: 'mcp',
    label: 'MCP Server',
    command: 'uv',
    args: ['run', 'python', 'src/xyz_agent_context/module/module_runner.py', 'mcp'],
    port: PORTS.MCP_START,
    healthUrl: null,
    order: 2
  },
  {
    id: 'poller',
    label: 'Module Poller',
    command: 'uv',
    args: ['run', 'python', '-m', 'xyz_agent_context.services.module_poller'],
    port: null,
    healthUrl: null,
    order: 3
  },
  {
    id: 'job-trigger',
    label: 'Job Trigger',
    command: 'uv',
    args: [
      'run',
      'python',
      'src/xyz_agent_context/module/job_module/job_trigger.py'
    ],
    port: null,
    healthUrl: null,
    order: 4
  },
  {
    id: 'evermemos',
    label: 'EverMemOS',
    command: 'uv',
    args: ['run', 'web'],
    cwd: '.evermemos',
    port: PORTS.EVERMEMOS,
    healthUrl: null,
    order: 5,
    optional: true
  }
]

// ─── 健康检查 ───────────────────────────────────────

/** 健康检查轮询间隔（毫秒） */
export const HEALTH_CHECK_INTERVAL = 5000

/** 进程崩溃后最大自动重启次数 */
export const MAX_RESTART_ATTEMPTS = 3

/** 重启退避基数（毫秒），实际等待 = BASE * 2^attempt */
export const RESTART_BACKOFF_BASE = 1000

// ─── IPC 通道名（从 shared 模块重导出，main 侧可直接用） ───
export { IPC } from '../shared/ipc-channels'
