/**
 * @file preload/index.ts
 * @description Preload 脚本 — 通过 contextBridge 暴露安全 API 给 Renderer
 *
 * Renderer 进程无法直接访问 Node.js API，
 * 所有对 Main Process 的调用都通过这里桥接。
 */

import { contextBridge, ipcRenderer } from 'electron'
import { IPC } from '../shared/ipc-channels'

// ─── 类型定义（Renderer 侧使用） ────────────────────

export interface NexusAPI {
  // 依赖检测
  checkDependencies: () => Promise<unknown[]>
  installDependency: (depId: string) => Promise<{ success: boolean; output: string }>

  // 环境变量
  getEnv: () => Promise<{ config: Record<string, string>; fields: unknown[] }>
  setEnv: (updates: Record<string, string>) => Promise<{ success: boolean }>
  validateEnv: () => Promise<{ valid: boolean; missing: string[]; warnings: string[] }>

  // EverMemOS 环境变量
  getEverMemOSEnv: () => Promise<{ available: boolean; config: Record<string, string>; fields: unknown[] }>
  setEverMemOSEnv: (updates: Record<string, string>) => Promise<{ success: boolean }>
  validateEverMemOSEnv: () => Promise<{ valid: boolean; missing: string[]; warnings: string[] }>

  // Docker
  getDockerStatus: () => Promise<{ dockerReady: boolean; groups: unknown[] }>
  startDocker: () => Promise<{ mysql: boolean; evermemos: boolean }>
  stopDocker: () => Promise<{ success: boolean }>

  // 服务进程
  startAllServices: () => Promise<{ success: boolean }>
  stopAllServices: () => Promise<{ success: boolean }>
  restartService: (serviceId: string) => Promise<{ success: boolean }>
  getServiceStatus: () => Promise<unknown[]>

  // 健康检查
  getHealthStatus: () => Promise<unknown>

  // 数据库
  initDatabase: () => Promise<{ success: boolean; output: string }>

  // 一键自动安装
  autoSetup: (options?: { skipEverMemOS?: boolean }) => Promise<{ success: boolean; error?: string }>
  quickStart: (options?: { skipEverMemOS?: boolean }) => Promise<{ success: boolean; error?: string }>
  onSetupProgress: (callback: (progress: unknown) => void) => () => void

  // Claude Code 认证
  getClaudeAuthInfo: () => Promise<unknown>
  startClaudeLogin: () => Promise<unknown>
  cancelClaudeLogin: () => Promise<{ success: boolean }>
  sendClaudeLoginInput: (text: string) => Promise<{ success: boolean }>
  saveSetupToken: (token: string) => Promise<{ valid: boolean; message: string }>
  onClaudeLoginStatus: (callback: (status: unknown) => void) => () => void

  // 杂项
  openExternal: (url: string) => Promise<void>
  getSetupState: () => Promise<{ setupComplete: boolean }>
  setSetupComplete: () => Promise<{ success: boolean }>
  getLogs: (serviceId?: string) => Promise<unknown[]>

  // 事件监听
  onLog: (callback: (entry: unknown) => void) => () => void
  onHealthUpdate: (callback: (status: unknown) => void) => () => void
  onTrayAction: (callback: (action: string) => void) => () => void
  onNavigate: (callback: (page: string) => void) => () => void
}

// ─── 暴露 API ───────────────────────────────────────

const nexusAPI: NexusAPI = {
  // ─── 依赖检测 ─────────────────────────────────────
  checkDependencies: () => ipcRenderer.invoke(IPC.CHECK_DEPENDENCIES),
  installDependency: (depId) => ipcRenderer.invoke(IPC.INSTALL_DEPENDENCY, depId),

  // ─── 环境变量 ─────────────────────────────────────
  getEnv: () => ipcRenderer.invoke(IPC.GET_ENV),
  setEnv: (updates) => ipcRenderer.invoke(IPC.SET_ENV, updates),
  validateEnv: () => ipcRenderer.invoke(IPC.VALIDATE_ENV),

  // ─── EverMemOS 环境变量 ─────────────────────────────
  getEverMemOSEnv: () => ipcRenderer.invoke(IPC.GET_EVERMEMOS_ENV),
  setEverMemOSEnv: (updates) => ipcRenderer.invoke(IPC.SET_EVERMEMOS_ENV, updates),
  validateEverMemOSEnv: () => ipcRenderer.invoke(IPC.VALIDATE_EVERMEMOS_ENV),

  // ─── Docker ───────────────────────────────────────
  getDockerStatus: () => ipcRenderer.invoke(IPC.DOCKER_STATUS),
  startDocker: () => ipcRenderer.invoke(IPC.DOCKER_START),
  stopDocker: () => ipcRenderer.invoke(IPC.DOCKER_STOP),

  // ─── 服务进程 ─────────────────────────────────────
  startAllServices: () => ipcRenderer.invoke(IPC.SERVICE_START_ALL),
  stopAllServices: () => ipcRenderer.invoke(IPC.SERVICE_STOP_ALL),
  restartService: (serviceId) => ipcRenderer.invoke(IPC.SERVICE_RESTART, serviceId),
  getServiceStatus: () => ipcRenderer.invoke(IPC.SERVICE_STATUS),

  // ─── 健康检查 ─────────────────────────────────────
  getHealthStatus: () => ipcRenderer.invoke(IPC.HEALTH_STATUS),

  // ─── 数据库 ───────────────────────────────────────
  initDatabase: () => ipcRenderer.invoke(IPC.INIT_DATABASE),

  // ─── 一键自动安装 ─────────────────────────────────
  autoSetup: (options?) => ipcRenderer.invoke(IPC.AUTO_SETUP, options),
  quickStart: (options?) => ipcRenderer.invoke(IPC.QUICK_START, options),
  onSetupProgress: (callback) => {
    const handler = (_event: unknown, progress: unknown) => callback(progress)
    ipcRenderer.on(IPC.ON_SETUP_PROGRESS, handler)
    return () => ipcRenderer.removeListener(IPC.ON_SETUP_PROGRESS, handler)
  },

  // ─── Claude Code 认证 ─────────────────────────────
  getClaudeAuthInfo: () => ipcRenderer.invoke(IPC.CLAUDE_AUTH_INFO),
  startClaudeLogin: () => ipcRenderer.invoke(IPC.CLAUDE_LOGIN_START),
  cancelClaudeLogin: () => ipcRenderer.invoke(IPC.CLAUDE_LOGIN_CANCEL),
  sendClaudeLoginInput: (text: string) => ipcRenderer.invoke(IPC.CLAUDE_LOGIN_INPUT, text),
  saveSetupToken: (token) => ipcRenderer.invoke(IPC.CLAUDE_SAVE_SETUP_TOKEN, token),
  onClaudeLoginStatus: (callback) => {
    const handler = (_event: unknown, status: unknown) => callback(status)
    ipcRenderer.on(IPC.ON_CLAUDE_LOGIN_STATUS, handler)
    return () => ipcRenderer.removeListener(IPC.ON_CLAUDE_LOGIN_STATUS, handler)
  },

  // ─── 杂项 ─────────────────────────────────────────
  openExternal: (url) => ipcRenderer.invoke(IPC.OPEN_EXTERNAL, url),
  getSetupState: () => ipcRenderer.invoke(IPC.GET_SETUP_STATE),
  setSetupComplete: () => ipcRenderer.invoke(IPC.SET_SETUP_COMPLETE),
  getLogs: (serviceId?) => ipcRenderer.invoke(IPC.GET_LOGS, serviceId),

  // ─── 事件监听（返回取消订阅函数） ─────────────────
  onLog: (callback) => {
    const handler = (_event: unknown, entry: unknown) => callback(entry)
    ipcRenderer.on(IPC.ON_LOG, handler)
    return () => ipcRenderer.removeListener(IPC.ON_LOG, handler)
  },

  onHealthUpdate: (callback) => {
    const handler = (_event: unknown, status: unknown) => callback(status)
    ipcRenderer.on(IPC.ON_HEALTH_UPDATE, handler)
    return () => ipcRenderer.removeListener(IPC.ON_HEALTH_UPDATE, handler)
  },

  onTrayAction: (callback) => {
    const handler = (_event: unknown, action: string) => callback(action)
    ipcRenderer.on('tray-action', handler)
    return () => ipcRenderer.removeListener('tray-action', handler)
  },

  onNavigate: (callback) => {
    const handler = (_event: unknown, page: string) => callback(page)
    ipcRenderer.on('navigate', handler)
    return () => ipcRenderer.removeListener('navigate', handler)
  }
}

contextBridge.exposeInMainWorld('nexus', nexusAPI)
