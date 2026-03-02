/**
 * @file ipc-handlers.ts
 * @description IPC communication registration center
 *
 * Exposes all Main Process capabilities to Renderer via ipcMain.handle,
 * Renderer calls them securely through preload's contextBridge.
 */

import { ipcMain, shell, BrowserWindow } from 'electron'
import { store } from './store'
import { IPC, PROJECT_ROOT, TABLE_MGMT_DIR } from './constants'
import { checkAllDependencies, installDependency } from './dependency-checker'
import { readEnv, writeEnv, validateEnv, getEnvFields } from './env-manager'
import { getClaudeAuthInfo, startClaudeLogin, validateSetupToken } from './claude-auth-manager'
import type { LoginProcessStatus } from './claude-auth-manager'
import * as everMemOSEnv from './evermemos-env-manager'
import * as dockerManager from './docker-manager'
import { ProcessManager } from './process-manager'
import { HealthMonitor } from './health-monitor'
import { execFile } from 'child_process'
import { promisify } from 'util'
import { join } from 'path'
import { getShellEnv } from './shell-env'

const execFileAsync = promisify(execFile)

// ─── Register All IPC Handlers ───────────────────────────

export function registerIpcHandlers(
  processManager: ProcessManager,
  healthMonitor: HealthMonitor,
  mainWindow: BrowserWindow
): void {
  // ─── Dependency Detection ─────────────────────────────────────

  ipcMain.handle(IPC.CHECK_DEPENDENCIES, async () => {
    return checkAllDependencies()
  })

  ipcMain.handle(IPC.INSTALL_DEPENDENCY, async (_event, depId: string) => {
    return installDependency(depId)
  })

  // ─── Environment Variables ─────────────────────────────────────

  ipcMain.handle(IPC.GET_ENV, () => {
    return { config: readEnv(), fields: getEnvFields() }
  })

  ipcMain.handle(IPC.SET_ENV, (_event, updates: Record<string, string>) => {
    writeEnv(updates)
    return { success: true }
  })

  ipcMain.handle(IPC.VALIDATE_ENV, () => {
    return validateEnv()
  })

  // ─── EverMemOS Environment Variables ─────────────────────────────

  ipcMain.handle(IPC.GET_EVERMEMOS_ENV, () => {
    return {
      available: true,
      config: everMemOSEnv.readEnv(),
      fields: everMemOSEnv.getFields()
    }
  })

  ipcMain.handle(IPC.SET_EVERMEMOS_ENV, (_event, updates: Record<string, string>) => {
    everMemOSEnv.writeEnv(updates)
    return { success: true }
  })

  ipcMain.handle(IPC.VALIDATE_EVERMEMOS_ENV, () => {
    return everMemOSEnv.validateEnv()
  })

  // ─── Docker ───────────────────────────────────────

  ipcMain.handle(IPC.DOCKER_STATUS, async () => {
    const ready = await dockerManager.isDockerReady()
    const groups = ready ? await dockerManager.getAllStatus() : []
    return { dockerReady: ready, groups }
  })

  ipcMain.handle(IPC.DOCKER_START, async () => {
    return dockerManager.startAll()
  })

  ipcMain.handle(IPC.DOCKER_STOP, async () => {
    await dockerManager.stopAll()
    return { success: true }
  })

  // ─── Service Processes ─────────────────────────────────────

  ipcMain.handle(IPC.SERVICE_START_ALL, async () => {
    await processManager.startAll()
    return { success: true }
  })

  ipcMain.handle(IPC.SERVICE_STOP_ALL, async () => {
    await processManager.stopAll()
    return { success: true }
  })

  ipcMain.handle(IPC.SERVICE_RESTART, async (_event, serviceId: string) => {
    const success = await processManager.restartService(serviceId)
    return { success }
  })

  ipcMain.handle(IPC.SERVICE_STATUS, () => {
    return processManager.getAllStatus()
  })

  // ─── Health Check ─────────────────────────────────────

  ipcMain.handle(IPC.HEALTH_STATUS, () => {
    return healthMonitor.getStatus()
  })

  // ─── One-Click Auto Setup ─────────────────────────────────

  ipcMain.handle(IPC.AUTO_SETUP, async (_event, options?: { skipEverMemOS?: boolean }) => {
    return processManager.runAutoSetup(options)
  })

  ipcMain.handle(IPC.QUICK_START, async (_event, options?: { skipEverMemOS?: boolean }) => {
    return processManager.runQuickStart(options)
  })

  // Push setup progress in real-time
  processManager.on('setup-progress', (progress) => {
    mainWindow.webContents.send(IPC.ON_SETUP_PROGRESS, progress)
  })

  // ─── Claude Code Authentication ─────────────────────────────

  // Active login process handle (prevent concurrent logins)
  let activeLogin: { cancel: () => void; sendInput: (text: string) => void } | null = null

  ipcMain.handle(IPC.CLAUDE_AUTH_INFO, async () => {
    return getClaudeAuthInfo(readEnv)
  })

  ipcMain.handle(IPC.CLAUDE_LOGIN_START, async () => {
    if (activeLogin) {
      return { state: 'running', message: 'Login already in progress' } as LoginProcessStatus
    }

    const onStatusChange = (status: LoginProcessStatus) => {
      mainWindow.webContents.send(IPC.ON_CLAUDE_LOGIN_STATUS, status)
      // Clean up handle after login ends
      if (status.state !== 'running') {
        activeLogin = null
      }
    }

    const handle = startClaudeLogin(
      onStatusChange,
      (url) => {
        // Electron natively opens browser, more reliable than CLI's xdg-open
        shell.openExternal(url)
      }
    )
    activeLogin = handle
    return handle.promise
  })

  ipcMain.handle(IPC.CLAUDE_LOGIN_CANCEL, async () => {
    if (activeLogin) {
      activeLogin.cancel()
      activeLogin = null
      return { success: true }
    }
    return { success: false }
  })

  ipcMain.handle(IPC.CLAUDE_LOGIN_INPUT, async (_event, text: string) => {
    if (activeLogin) {
      activeLogin.sendInput(text)
      return { success: true }
    }
    return { success: false }
  })

  ipcMain.handle(IPC.CLAUDE_SAVE_SETUP_TOKEN, async (_event, token: string) => {
    const validation = validateSetupToken(token)
    if (!validation.valid) {
      return validation
    }
    // Token format valid, write to .env as ANTHROPIC_API_KEY
    writeEnv({ ANTHROPIC_API_KEY: token.trim() })
    return { valid: true, message: 'Setup token saved successfully' }
  })

  // ─── Database Initialization ─────────────────────────────────

  ipcMain.handle(IPC.INIT_DATABASE, async () => {
    try {
      const scriptPath = join(TABLE_MGMT_DIR, 'create_all_tables.py')
      const { stdout, stderr } = await execFileAsync(
        'uv',
        ['run', 'python', scriptPath],
        {
          cwd: PROJECT_ROOT,
          timeout: 60000,
          env: getShellEnv()
        }
      )
      return { success: true, output: stdout + stderr }
    } catch (err: unknown) {
      const message = err instanceof Error ? err.message : String(err)
      return { success: false, output: message }
    }
  })

  // ─── Miscellaneous ─────────────────────────────────────────

  ipcMain.handle(IPC.OPEN_EXTERNAL, async (_event, url: string) => {
    await shell.openExternal(url)
  })

  ipcMain.handle(IPC.GET_SETUP_STATE, () => {
    return { setupComplete: store.get('setupComplete') as boolean }
  })

  ipcMain.handle(IPC.SET_SETUP_COMPLETE, () => {
    store.set('setupComplete', true)
    return { success: true }
  })

  ipcMain.handle(IPC.GET_LOGS, (_event, serviceId?: string) => {
    return processManager.getLogs(serviceId)
  })

  // ─── Event Forwarding (Main → Renderer) ──────────────────

  // Real-time log push
  processManager.on('log', (entry) => {
    mainWindow.webContents.send(IPC.ON_LOG, entry)
  })

  // Real-time health status push
  healthMonitor.on('health-update', (status) => {
    mainWindow.webContents.send(IPC.ON_HEALTH_UPDATE, status)
  })
}
