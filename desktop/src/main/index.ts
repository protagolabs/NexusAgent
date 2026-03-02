/**
 * @file index.ts
 * @description Electron 主进程入口
 *
 * 初始化 BrowserWindow、各 Manager 模块，
 * 管理应用生命周期（启动、关闭、托盘最小化）。
 */

import { app, BrowserWindow, shell } from 'electron'
import { join } from 'path'
import { ensureWritableProject } from './constants'
import { initShellEnv } from './shell-env'
import { ProcessManager } from './process-manager'
import { HealthMonitor } from './health-monitor'
import { TrayManager } from './tray-manager'
import { registerIpcHandlers } from './ipc-handlers'

// ─── 全局实例 ───────────────────────────────────────

let mainWindow: BrowserWindow | null = null
let processManager: ProcessManager | null = null
const healthMonitor = new HealthMonitor()
let trayManager: TrayManager | null = null

// ─── 窗口创建 ───────────────────────────────────────

function createMainWindow(): BrowserWindow {
  const win = new BrowserWindow({
    width: 800,
    height: 620,
    minWidth: 640,
    minHeight: 480,
    title: 'NarraNexus',
    titleBarStyle: 'hiddenInset',
    trafficLightPosition: { x: 15, y: 15 },
    webPreferences: {
      preload: join(__dirname, '../preload/index.js'),
      sandbox: false,
      contextIsolation: true,
      nodeIntegration: false
    },
    show: false // 等待 ready-to-show 再显示，避免白屏闪烁
  })

  // 窗口准备好后显示
  win.once('ready-to-show', () => {
    win.show()
  })

  // 拦截外部链接，用系统浏览器打开
  win.webContents.setWindowOpenHandler(({ url }) => {
    shell.openExternal(url)
    return { action: 'deny' }
  })

  // 关闭窗口时直接退出应用
  win.on('close', () => {
    app.isQuitting = true
    app.quit()
  })

  // 加载页面
  if (process.env.ELECTRON_RENDERER_URL) {
    // 开发模式：electron-vite 注入的 dev server URL
    win.loadURL(process.env.ELECTRON_RENDERER_URL)
  } else {
    // 生产模式：加载构建产物
    const indexPath = join(__dirname, '../renderer/index.html')
    console.log('[main] Loading renderer from:', indexPath)
    win.loadFile(indexPath)
  }

  // 捕获渲染进程加载错误
  win.webContents.on('did-fail-load', (_event, errorCode, errorDescription) => {
    console.error('[main] Renderer failed to load:', errorCode, errorDescription)
  })

  // 开发模式下打开 DevTools
  if (process.env.ELECTRON_RENDERER_URL) {
    win.webContents.openDevTools({ mode: 'detach' })
  }

  return win
}

// ─── 应用生命周期 ───────────────────────────────────

// 标记是否正在退出（用于区分关闭窗口和退出应用）
declare module 'electron' {
  interface App {
    isQuitting: boolean
  }
}
app.isQuitting = false

app.whenReady().then(async () => {
  // 确保可写的项目目录存在（打包后首次运行时复制）
  ensureWritableProject()

  // 解析用户登录 Shell 的完整环境变量（macOS .app 启动时必要）
  await initShellEnv()

  // 初始化进程管理器（依赖 shell-env 已初始化）
  processManager = new ProcessManager()

  // 创建主窗口
  mainWindow = createMainWindow()

  // 注册 IPC 处理器
  registerIpcHandlers(processManager, healthMonitor, mainWindow)

  // 创建系统托盘
  trayManager = new TrayManager(healthMonitor, processManager)
  trayManager.create(mainWindow)

  // 启动健康检查
  healthMonitor.start()

  // macOS: 点击 Dock 图标时重新显示窗口
  app.on('activate', () => {
    if (mainWindow) {
      mainWindow.show()
    }
  })
})

// 所有窗口关闭时（非 macOS）
app.on('window-all-closed', () => {
  if (process.platform !== 'darwin') {
    app.quit()
  }
})

// 应用退出前：清理所有后台进程
// before-quit 是同步事件，async 不会让 Electron 等待。
// 必须用 preventDefault() 阻止退出，等清理完毕后再手动 quit。
let cleanupDone = false

app.on('before-quit', (e) => {
  app.isQuitting = true

  if (cleanupDone) return // 清理已完成，允许退出

  e.preventDefault() // 阻止立即退出

  // 停止健康检查
  healthMonitor.stop()

  // 销毁托盘
  trayManager?.destroy()

  // 停止所有服务进程，完成后再退出
  const cleanup = processManager?.stopAll() ?? Promise.resolve()
  cleanup.finally(() => {
    cleanupDone = true
    app.quit()
  })
})
