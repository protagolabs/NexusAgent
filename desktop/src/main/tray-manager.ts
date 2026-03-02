/**
 * @file tray-manager.ts
 * @description 系统托盘图标 + 右键菜单
 *
 * 提供系统托盘图标，展示服务状态概览，
 * 支持快速操作：启动/停止服务、打开设置、退出应用。
 */

import { Tray, Menu, nativeImage, app, BrowserWindow } from 'electron'
import { join } from 'path'
import { HealthMonitor, HealthState } from './health-monitor'
import { ProcessManager } from './process-manager'

// ─── TrayManager ────────────────────────────────────

export class TrayManager {
  private tray: Tray | null = null
  private mainWindow: BrowserWindow | null = null
  private healthMonitor: HealthMonitor
  private processManager: ProcessManager

  constructor(healthMonitor: HealthMonitor, processManager: ProcessManager) {
    this.healthMonitor = healthMonitor
    this.processManager = processManager
  }

  /** 创建系统托盘图标 */
  create(mainWindow: BrowserWindow): void {
    this.mainWindow = mainWindow

    // 创建 16x16 的托盘图标（使用简单的圆形图标）
    const icon = this.createTrayIcon()
    this.tray = new Tray(icon)
    this.tray.setToolTip('NarraNexus')

    // 点击托盘图标显示主窗口
    this.tray.on('click', () => {
      this.showMainWindow()
    })

    // 初始化菜单
    this.updateMenu()

    // 监听健康状态变化，更新菜单
    this.healthMonitor.on('health-update', () => {
      this.updateMenu()
    })
  }

  /** 销毁托盘 */
  destroy(): void {
    this.tray?.destroy()
    this.tray = null
  }

  // ─── 内部方法 ─────────────────────────────────────

  /** 创建托盘图标（16x16 纯色圆形） */
  private createTrayIcon(): nativeImage {
    // 使用项目 Logo 作为托盘图标
    const iconPath = app.isPackaged
      ? join(process.resourcesPath, 'icon.png')
      : join(__dirname, '..', '..', 'resources', 'icon.png')

    try {
      const icon = nativeImage.createFromPath(iconPath)
      return icon.resize({ width: 16, height: 16 })
    } catch {
      // 如果找不到图标文件，创建一个简单的占位图标
      return nativeImage.createEmpty()
    }
  }

  /** 更新托盘右键菜单 */
  private updateMenu(): void {
    if (!this.tray) return

    const health = this.healthMonitor.getStatus()
    const processes = this.processManager.getAllStatus()

    // 构建服务状态子菜单
    const statusItems: Electron.MenuItemConstructorOptions[] = []

    // MySQL
    const mysqlHealth = health.services.find((s) => s.serviceId === 'mysql')
    statusItems.push({
      label: `${stateIcon(mysqlHealth?.state)} MySQL`,
      enabled: false
    })

    // 其他服务
    for (const proc of processes) {
      const svcHealth = health.services.find((s) => s.serviceId === proc.serviceId)
      const state = svcHealth?.state ?? (proc.status === 'running' ? 'healthy' : 'unhealthy')
      statusItems.push({
        label: `${stateIcon(state)} ${proc.label}  ${proc.status === 'running' ? 'Running' : 'Stopped'}`,
        enabled: false
      })
    }

    const contextMenu = Menu.buildFromTemplate([
      {
        label: 'Open NarraNexus',
        click: () => this.showMainWindow()
      },
      { type: 'separator' },
      {
        label: 'Service Status',
        submenu: statusItems
      },
      { type: 'separator' },
      {
        label: 'Start All Services',
        click: () => {
          this.mainWindow?.webContents.send('tray-action', 'start-all')
        }
      },
      {
        label: 'Stop All Services',
        click: () => {
          this.mainWindow?.webContents.send('tray-action', 'stop-all')
        }
      },
      { type: 'separator' },
      {
        label: 'View Logs',
        click: () => {
          this.showMainWindow()
          this.mainWindow?.webContents.send('navigate', 'logs')
        }
      },
      {
        label: 'Settings (API Keys)',
        click: () => {
          this.showMainWindow()
          this.mainWindow?.webContents.send('navigate', 'settings')
        }
      },
      { type: 'separator' },
      {
        label: 'Quit',
        click: () => {
          // 通知主进程停止所有服务后退出
          this.mainWindow?.webContents.send('tray-action', 'quit')
          // 延时退出，给服务停止留时间
          setTimeout(() => app.quit(), 3000)
        }
      }
    ])

    this.tray.setContextMenu(contextMenu)
  }

  /** 显示主窗口 */
  private showMainWindow(): void {
    if (!this.mainWindow) return
    if (this.mainWindow.isMinimized()) {
      this.mainWindow.restore()
    }
    this.mainWindow.show()
    this.mainWindow.focus()
  }
}

// ─── 工具函数 ───────────────────────────────────────

/** 状态图标映射 */
function stateIcon(state?: HealthState): string {
  switch (state) {
    case 'healthy':
      return '●'
    case 'unhealthy':
      return '○'
    default:
      return '◌'
  }
}
