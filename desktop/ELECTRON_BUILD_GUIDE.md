# NarraNexus Electron 桌面应用 -- 从零到打包的完全指南

> **目标读者**：完全没有 Electron 经验的前端/后端开发者。
> **阅读收益**：看完本文，你将能独立从零搭建一个 Electron + React + TypeScript 桌面应用，并打包成 macOS `.dmg` 或 Linux `.AppImage`。
> **项目参考**：本文全部示例代码均取自 NarraNexus Desktop 真实源码。

---

## 目录

- [第 1 章 Electron 是什么](#第-1-章-electron-是什么)
- [第 2 章 三进程模型详解](#第-2-章-三进程模型详解)
- [第 3 章 IPC 通信机制](#第-3-章-ipc-通信机制)
- [第 4 章 项目目录结构设计](#第-4-章-项目目录结构设计)
- [第 5 章 electron-vite 编译系统](#第-5-章-electron-vite-编译系统)
- [第 6 章 TypeScript 配置策略](#第-6-章-typescript-配置策略)
- [第 7 章 Renderer 的类型安全](#第-7-章-renderer-的类型安全)
- [第 8 章 开发模式 vs 生产模式](#第-8-章-开发模式-vs-生产模式)
- [第 9 章 electron-builder 打包配置详解](#第-9-章-electron-builder-打包配置详解)
- [第 10 章 完整打包流水线](#第-10-章-完整打包流水线)
- [第 11 章 生产环境的特殊处理](#第-11-章-生产环境的特殊处理)
- [第 12 章 如何从零创建一个类似项目](#第-12-章-如何从零创建一个类似项目)
- [第 13 章 常见坑与排错](#第-13-章-常见坑与排错)

---

## 第 1 章 Electron 是什么

### 1.1 一句话解释

Electron 就是 **"把一个 Chrome 浏览器和一个 Node.js 运行时打包成一个桌面应用"**。

你写的 HTML/CSS/JS 页面跑在内置的 Chrome（Chromium）里显示界面，你写的 Node.js 代码在后台跑，负责操作文件系统、启动子进程、调用系统 API 这些"浏览器做不到的事"。

### 1.2 为什么能做桌面应用

普通网页跑在浏览器的沙盒里，不能碰文件系统，不能启动子进程。但 Electron 做了一件事：它把 Chromium 和 Node.js **编译到了同一个进程空间里**。这意味着：

```
┌─────────────────────────────────────────────────┐
│              Electron 应用                       │
│                                                 │
│   ┌──────────────┐     ┌──────────────────────┐ │
│   │  Chromium     │     │  Node.js             │ │
│   │  (渲染 UI)    │ ←→  │  (文件/进程/网络)     │ │
│   │  HTML/CSS/JS  │     │  fs/child_process    │ │
│   └──────────────┘     └──────────────────────┘ │
│                                                 │
│   这两个东西被焊在了一起！                         │
└─────────────────────────────────────────────────┘
```

- **界面部分**：就是一个网页，用 React、Vue、甚至纯 HTML 都行
- **后台部分**：就是一个 Node.js 程序，能做任何 Node.js 能做的事
- 它们之间通过一套叫 **IPC（进程间通信）** 的机制互相说话

### 1.3 在 NarraNexus 中的角色

NarraNexus 是一个 AI Agent 平台，本身有 Python 后端 + React 前端 + Docker 数据库。桌面应用的职责是：

1. **一键安装**：检测/安装 uv、Docker、Claude CLI 等依赖
2. **进程管理**：启动/停止/监控 4 个后台 Python 服务
3. **Docker 管理**：启动/停止 MySQL 容器
4. **环境配置**：管理 `.env` 文件中的 API Key
5. **健康监控**：定期检查各服务是否正常运行

所有这些"操作系统级别"的操作（启动子进程、检测端口、管理 Docker），都由 Electron 的 Main Process（Node.js 侧）完成。用户看到的漂亮界面，由 Renderer Process（Chromium 侧）的 React 页面渲染。

---

## 第 2 章 三进程模型详解

这是理解 Electron 最重要的一章。很多 Electron 新手的困惑都来自于搞不清"我这段代码到底跑在哪里"。

### 2.1 三个进程分别是什么

```
┌──────────────────────────────────────────────────────────┐
│                     Electron 应用                         │
│                                                          │
│  ┌───────────────────────────────────────────────────┐   │
│  │                 Main Process                       │   │
│  │          （src/main/index.ts）                      │   │
│  │                                                   │   │
│  │  - 运行环境：Node.js                               │   │
│  │  - 能力：文件读写、启动子进程、管理窗口              │   │
│  │  - 数量：只有 1 个                                  │   │
│  │  - 类比：后端服务器                                 │   │
│  └────────────────────┬──────────────────────────────┘   │
│                       │                                  │
│              Preload 脚本在这里执行                        │
│                       │                                  │
│  ┌────────────────────▼──────────────────────────────┐   │
│  │               Preload Script                       │   │
│  │          （src/preload/index.ts）                    │   │
│  │                                                   │   │
│  │  - 运行环境：特殊的 Node.js 沙盒                    │   │
│  │  - 能力：有限的 Node.js API + contextBridge         │   │
│  │  - 职责：做安全中间人，暴露白名单 API                │   │
│  │  - 类比：API 网关                                   │   │
│  └────────────────────┬──────────────────────────────┘   │
│                       │                                  │
│              通过 contextBridge 暴露 API                   │
│                       │                                  │
│  ┌────────────────────▼──────────────────────────────┐   │
│  │              Renderer Process                      │   │
│  │          （src/renderer/App.tsx）                    │   │
│  │                                                   │   │
│  │  - 运行环境：Chromium（就是个浏览器）               │   │
│  │  - 能力：HTML/CSS/JS，React 渲染                    │   │
│  │  - 限制：不能直接访问 Node.js API                   │   │
│  │  - 类比：前端网页                                   │   │
│  └───────────────────────────────────────────────────┘   │
│                                                          │
└──────────────────────────────────────────────────────────┘
```

### 2.2 为什么需要三个进程

你可能会问：两个不就够了吗？Main 负责后台，Renderer 负责界面，为什么要多一个 Preload？

答案是**安全**。

想象一下：如果 Renderer（网页）能直接调用 Node.js 的 `fs.rmSync('/')` 或者 `child_process.exec('rm -rf /')`，那任何 XSS 漏洞都会变成系统级灾难。早期的 Electron 应用（比如老版 VS Code）确实允许 Renderer 直接访问 Node.js，这带来了巨大的安全风险。

现代 Electron 的安全模型：

| 设置 | 值 | 含义 |
|------|---|------|
| `contextIsolation` | `true` | Preload 和 Renderer 的 JS 上下文完全隔离 |
| `nodeIntegration` | `false` | Renderer 里不能用 `require('fs')` |
| `sandbox` | `false`/`true` | 控制 Preload 能否使用 Node.js API |

在 NarraNexus 的 `main/index.ts` 中：

```typescript
// desktop/src/main/index.ts
const win = new BrowserWindow({
  webPreferences: {
    preload: join(__dirname, '../preload/index.js'),
    sandbox: false,         // Preload 可以用 Node.js API
    contextIsolation: true, // Preload 和 Renderer 的 JS 上下文隔离
    nodeIntegration: false  // Renderer 里不能用 Node.js
  }
})
```

这三个设置的组合效果：

- Renderer（React 页面）只能用 `window.nexus.xxx()` 调用 Preload 暴露的白名单 API
- Preload 可以用 `ipcRenderer` 和 `contextBridge`，但不直接做业务逻辑
- Main Process 才是真正干活的地方

**类比**：

```
Renderer  =  手机 App（只能看到界面，点按钮）
Preload   =  API 网关（只暴露安全的接口，做转发）
Main      =  后端服务器（真正执行操作，文件读写、启动进程）
```

### 2.3 每个进程在 NarraNexus 中的职责

**Main Process（`src/main/`）**：
- `index.ts` -- 创建窗口、管理应用生命周期
- `process-manager.ts` -- 用 `child_process.spawn` 启动 Python 后台服务
- `docker-manager.ts` -- 调用 `docker compose` 管理容器
- `dependency-checker.ts` -- 检测 uv、Node.js、Docker 是否安装
- `health-monitor.ts` -- 定期检查端口和 HTTP 健康状态
- `env-manager.ts` -- 读写 `.env` 配置文件
- `shell-env.ts` -- 解析 macOS 登录 Shell 的环境变量
- `store.ts` -- JSON 文件持久化存储
- `tray-manager.ts` -- 系统托盘图标和菜单
- `ipc-handlers.ts` -- IPC 请求处理注册中心

**Preload Script（`src/preload/`）**：
- `index.ts` -- 把 Main Process 的能力以安全 API 的形式暴露给 Renderer

**Renderer Process（`src/renderer/`）**：
- `App.tsx` -- React 根组件
- `pages/SetupWizard.tsx` -- 初始安装向导页面
- `pages/Dashboard.tsx` -- 主控制面板页面
- `components/` -- UI 组件（ServiceCard、LogViewer 等）

---

## 第 3 章 IPC 通信机制

### 3.1 什么是 IPC

IPC = Inter-Process Communication（进程间通信）。因为 Main 和 Renderer 是两个不同的进程，它们不能直接调用对方的函数，必须通过 Electron 提供的 IPC 通道来"传消息"。

这就像两个人隔着一堵墙说话，必须通过对讲机。

### 3.2 两种通信模式

```
模式 1: invoke / handle（请求-响应，类似 HTTP）
═══════════════════════════════════════════════

  Renderer                    Main
  ┌──────┐    invoke          ┌──────┐
  │      │ ──────────────→    │      │
  │      │    "请启动服务"     │      │  处理请求...
  │      │                    │      │
  │      │    返回 Promise    │      │
  │      │ ←──────────────    │      │
  └──────┘  { success: true } └──────┘


模式 2: send / on（单向推送，类似 WebSocket）
═══════════════════════════════════════════════

  Main                        Renderer
  ┌──────┐    send            ┌──────┐
  │      │ ──────────────→    │      │
  │      │  "有新日志来了"     │      │  更新 UI...
  │      │                    │      │
  │      │    send            │      │
  │      │ ──────────────→    │      │
  │      │  "又有新日志了"     │      │  更新 UI...
  └──────┘                    └──────┘
```

### 3.3 完整链路详解：`window.nexus.startAllServices()`

下面我们用 NarraNexus 真实的"启动所有服务"功能，从头到尾走一遍完整的 IPC 链路。

#### 第 1 步：定义通道名（`shared/ipc-channels.ts`）

首先，Main 和 Preload 需要约定一个"频道名"。就像对讲机要调到同一个频率。

```typescript
// desktop/src/shared/ipc-channels.ts
export const IPC = {
  SERVICE_START_ALL: 'service-start-all',  // ← 这就是频道名
  ON_LOG: 'on-log',
  // ... 其他频道
} as const
```

这个文件放在 `shared/` 目录下，Main 和 Preload 都可以导入。之所以用常量而不是写死字符串，是为了避免拼写错误（一个地方写成 `'service-start-al'` 少了个 l，你可能调半天 Bug）。

#### 第 2 步：Main 侧注册 handler（`main/ipc-handlers.ts`）

Main Process 需要"监听"这个频道，收到消息时执行对应的操作：

```typescript
// desktop/src/main/ipc-handlers.ts
import { ipcMain } from 'electron'
import { IPC } from './constants'

export function registerIpcHandlers(processManager, healthMonitor, mainWindow) {
  // 注册 handler：当 Renderer 调用 invoke('service-start-all') 时，
  // 执行 processManager.startAll() 并返回结果
  ipcMain.handle(IPC.SERVICE_START_ALL, async () => {
    await processManager.startAll()
    return { success: true }
  })

  // 注册事件转发：Main → Renderer 的单向推送
  processManager.on('log', (entry) => {
    mainWindow.webContents.send(IPC.ON_LOG, entry)
  })
}
```

`ipcMain.handle()` 就像 Express 里的 `app.get('/api/start', handler)` -- 注册一个请求处理器。

#### 第 3 步：Preload 做桥接（`preload/index.ts`）

Preload 把 `ipcRenderer.invoke()` 包装成一个"看起来像普通函数"的 API，通过 `contextBridge` 暴露给 Renderer：

```typescript
// desktop/src/preload/index.ts
import { contextBridge, ipcRenderer } from 'electron'
import { IPC } from '../shared/ipc-channels'

const nexusAPI = {
  // 请求-响应模式：调用后等待 Main 返回结果
  startAllServices: () => ipcRenderer.invoke(IPC.SERVICE_START_ALL),

  // 事件监听模式：注册回调，收到 Main 的推送时执行
  onLog: (callback) => {
    const handler = (_event, entry) => callback(entry)
    ipcRenderer.on(IPC.ON_LOG, handler)
    // 返回取消订阅函数（防止内存泄漏）
    return () => ipcRenderer.removeListener(IPC.ON_LOG, handler)
  }
}

// 关键！把 nexusAPI 对象挂到 Renderer 的 window.nexus 上
contextBridge.exposeInMainWorld('nexus', nexusAPI)
```

`contextBridge.exposeInMainWorld('nexus', nexusAPI)` 做了一件很巧妙的事：它在 Renderer 的 `window` 对象上挂了一个 `nexus` 属性，但 Renderer 只能调用这些函数，不能访问 `ipcRenderer` 对象本身。这就是"白名单 API"的含义。

#### 第 4 步：Renderer 调用 API（React 组件中）

现在 Renderer 里的 React 组件可以像调用普通函数一样使用了：

```typescript
// desktop/src/renderer/pages/Dashboard.tsx
const handleStartAll = async () => {
  // 就像调用一个普通的异步函数！
  const result = await window.nexus.startAllServices()
  console.log('启动结果:', result)  // { success: true }
}

// 监听日志推送
useEffect(() => {
  const unsubscribe = window.nexus.onLog((entry) => {
    console.log('新日志:', entry.message)
  })
  return () => unsubscribe()  // 组件卸载时取消监听
}, [])
```

#### 完整链路图

```
  Renderer (React)            Preload                Main (Node.js)
  ═══════════════          ═══════════════         ═══════════════════
        │                        │                        │
        │  window.nexus          │                        │
        │  .startAllServices()   │                        │
        │ ─────────────────→     │                        │
        │                        │  ipcRenderer.invoke    │
        │                        │  ('service-start-all') │
        │                        │ ─────────────────→     │
        │                        │                        │
        │                        │              ipcMain.handle(...)
        │                        │              processManager.startAll()
        │                        │              spawn('uv', ['run', ...])
        │                        │                        │
        │                        │    返回 Promise        │
        │                        │ ←─────────────────     │
        │  { success: true }     │                        │
        │ ←─────────────────     │                        │
        │                        │                        │
        │                        │                   (日志产生...)
        │                        │                   mainWindow.webContents
        │                        │   ipcRenderer.on      .send('on-log', entry)
        │                        │ ←─────────────────     │
        │  onLog callback(entry) │                        │
        │ ←─────────────────     │                        │
        │                        │                        │
```

### 3.4 通信模式对照表

| 方向 | API | Preload 侧 | Main 侧 | 用途 |
|------|-----|------------|---------|------|
| Renderer -> Main（请求-响应） | `ipcRenderer.invoke(channel, ...args)` | 包装成 `nexusAPI.xxx()` | `ipcMain.handle(channel, handler)` | 启动服务、检查依赖、读写配置 |
| Main -> Renderer（单向推送） | `ipcRenderer.on(channel, handler)` | 包装成 `nexusAPI.onXxx(callback)` | `mainWindow.webContents.send(channel, data)` | 日志推送、健康状态更新、安装进度 |

---

## 第 4 章 项目目录结构设计

### 4.1 完整目录结构

```
desktop/
├── src/
│   ├── main/                    # Main Process（Node.js 侧）
│   │   ├── index.ts             # 应用入口、窗口创建、生命周期
│   │   ├── constants.ts         # 路径常量、端口常量、服务定义
│   │   ├── ipc-handlers.ts      # IPC 请求处理注册中心
│   │   ├── process-manager.ts   # 后台服务进程管理（spawn/kill）
│   │   ├── docker-manager.ts    # Docker 容器管理
│   │   ├── dependency-checker.ts# 系统依赖检测（uv/Node/Docker）
│   │   ├── health-monitor.ts    # 服务健康状态轮询
│   │   ├── env-manager.ts       # .env 文件读写
│   │   ├── shell-env.ts         # macOS Shell 环境变量解析
│   │   ├── store.ts             # JSON 持久化存储
│   │   └── tray-manager.ts      # 系统托盘图标 + 菜单
│   │
│   ├── preload/                 # Preload Script（安全桥梁）
│   │   └── index.ts             # contextBridge 暴露 API
│   │
│   ├── shared/                  # Main 和 Preload 共享的代码
│   │   └── ipc-channels.ts      # IPC 通道名常量
│   │
│   └── renderer/                # Renderer Process（React 界面）
│       ├── index.html           # HTML 入口
│       ├── main.tsx             # React 挂载点
│       ├── App.tsx              # 根组件（路由/状态判断）
│       ├── env.d.ts             # 全局类型声明（window.nexus）
│       ├── pages/               # 页面组件
│       │   ├── SetupWizard.tsx  # 初始安装向导
│       │   └── Dashboard.tsx    # 主控面板
│       ├── components/          # UI 组件
│       │   ├── StepIndicator.tsx
│       │   ├── ServiceCard.tsx
│       │   └── LogViewer.tsx
│       └── styles/
│           └── index.css        # Tailwind CSS 入口
│
├── resources/                   # 构建资源（图标等）
│   ├── icon.icns                # macOS 图标
│   └── icon.png                 # 通用图标
│
├── electron.vite.config.ts      # electron-vite 编译配置
├── electron-builder.yml         # electron-builder 打包配置
├── package.json                 # 依赖和脚本
├── tsconfig.json                # TypeScript 根配置（引用）
├── tsconfig.node.json           # Main + Preload 的 TS 配置
├── tsconfig.web.json            # Renderer 的 TS 配置
├── tailwind.config.js           # Tailwind CSS 配置
└── postcss.config.js            # PostCSS 配置
```

### 4.2 为什么这样组织

核心原则：**按进程边界划分目录**。

```
src/
├── main/      ← 跑在 Node.js 里，能用 fs、child_process 等
├── preload/   ← 跑在特殊沙盒里，能用 ipcRenderer、contextBridge
├── shared/    ← Main 和 Preload 共用，不能依赖任何特定进程的 API
└── renderer/  ← 跑在 Chromium 里，只能用浏览器 API + window.nexus
```

**为什么不把所有代码放在一起？**

因为这三个目录的代码跑在**完全不同的运行环境**中：
- `main/` 里能写 `import { app } from 'electron'`，但写不了 `document.getElementById`
- `renderer/` 里能写 `document.getElementById`，但写不了 `import fs from 'fs'`
- `shared/` 里两边都不能写，只能放纯粹的数据定义（常量、类型）

如果你搞混了，TypeScript 编译器会报错，electron-vite 构建会失败。目录结构本身就是一种"编译期强制隔离"。

### 4.3 `shared/` 的妙用

`shared/ipc-channels.ts` 只导出纯数据常量，不依赖任何 Electron API：

```typescript
// desktop/src/shared/ipc-channels.ts
export const IPC = {
  CHECK_DEPENDENCIES: 'check-dependencies',
  SERVICE_START_ALL: 'service-start-all',
  ON_LOG: 'on-log',
  // ...
} as const
```

它被两个地方导入：
- `main/ipc-handlers.ts` 用它注册 `ipcMain.handle(IPC.SERVICE_START_ALL, ...)`
- `preload/index.ts` 用它发送 `ipcRenderer.invoke(IPC.SERVICE_START_ALL)`

这样保证了两边用的是**完全相同的字符串常量**，改一处两边自动同步。

> 注意：`shared/` 里的代码**不能**被 `renderer/` 直接导入。因为 Renderer 是纯浏览器环境，由单独的 Vite 编译配置处理。如果 Renderer 需要 IPC 通道名，它根本不需要知道 -- Preload 已经把 IPC 细节封装好了，Renderer 只需要调用 `window.nexus.xxx()`。

---

## 第 5 章 electron-vite 编译系统

### 5.1 为什么需要 electron-vite

原生的 Electron 开发体验很差：

- Main Process 的 TypeScript 需要你手动用 `tsc` 编译
- Renderer 需要你自己配 Webpack 或 Vite
- 开发时改了 Main 的代码要手动重启
- 三个"进程"的编译配置完全独立，维护成本高

**electron-vite** 把这三件事统一了：

```
electron-vite = Vite(Main) + Vite(Preload) + Vite(Renderer)
```

一个配置文件，三段编译配置，一个命令搞定开发和构建。

### 5.2 配置详解

```typescript
// desktop/electron.vite.config.ts

import { resolve } from 'path'
import { defineConfig, externalizeDepsPlugin } from 'electron-vite'
import react from '@vitejs/plugin-react'
import tailwindcss from 'tailwindcss'
import autoprefixer from 'autoprefixer'

export default defineConfig({
  // ════════════════════════════════════════════
  // 第一段：Main Process 编译配置
  // ════════════════════════════════════════════
  main: {
    plugins: [externalizeDepsPlugin()],
    build: {
      rollupOptions: {
        input: {
          index: resolve(__dirname, 'src/main/index.ts')
        }
      }
    }
  },

  // ════════════════════════════════════════════
  // 第二段：Preload Script 编译配置
  // ════════════════════════════════════════════
  preload: {
    plugins: [externalizeDepsPlugin()],
    build: {
      rollupOptions: {
        input: {
          index: resolve(__dirname, 'src/preload/index.ts')
        }
      }
    }
  },

  // ════════════════════════════════════════════
  // 第三段：Renderer Process 编译配置
  // ════════════════════════════════════════════
  renderer: {
    root: resolve(__dirname, 'src/renderer'),
    build: {
      rollupOptions: {
        input: {
          index: resolve(__dirname, 'src/renderer/index.html')
        }
      }
    },
    plugins: [react()],
    css: {
      postcss: {
        plugins: [
          tailwindcss(),
          autoprefixer()
        ]
      }
    }
  }
})
```

### 5.3 每一段在干什么

| 段 | 输入 | 输出 | 运行环境 | 特殊处理 |
|----|------|------|---------|---------|
| `main` | `src/main/index.ts` | `out/main/index.js` | Node.js | `externalizeDepsPlugin` |
| `preload` | `src/preload/index.ts` | `out/preload/index.js` | Node.js (沙盒) | `externalizeDepsPlugin` |
| `renderer` | `src/renderer/index.html` | `out/renderer/index.html` + JS | Chromium | React 插件 + Tailwind |

编译后的产物结构：

```
out/
├── main/
│   └── index.js          # Main Process 编译产物
├── preload/
│   └── index.js          # Preload 编译产物
└── renderer/
    ├── index.html         # Renderer HTML
    ├── assets/
    │   ├── index-xxxxx.js # Renderer JS（React 打包后）
    │   └── index-xxxxx.css# 样式
    └── ...
```

### 5.4 `externalizeDepsPlugin` 是什么

这个插件做了一件非常重要的事：**告诉 Vite 不要把 Node.js 原生模块和 electron 打包进 bundle**。

为什么需要它？Vite 默认会把所有 `import` 的东西打包成一个大 JS 文件。但有些模块不能被打包：

- `electron` -- 运行时由 Electron 框架提供，不在 `node_modules` 里
- `fs`、`path`、`child_process` -- Node.js 内置模块，不能被打包
- 原生 C++ 模块（`.node` 文件）-- 不是 JS，Vite 处理不了

`externalizeDepsPlugin` 会自动识别这些模块，把它们标记为"外部依赖"：

```javascript
// 编译前（你的源码）
import { app } from 'electron'
import { spawn } from 'child_process'

// 编译后（externalize 处理后）
const { app } = require('electron')      // 保持 require，运行时解析
const { spawn } = require('child_process')
```

**只有 Main 和 Preload 需要这个插件**，因为只有它们跑在 Node.js 环境中。Renderer 跑在浏览器里，不会也不应该 import 这些模块。

### 5.5 Renderer 段的特殊配置

Renderer 段和普通的 Vite 项目配置几乎一样：

- `root: resolve(__dirname, 'src/renderer')` -- 告诉 Vite "你的根目录在这，HTML 入口在这"
- `plugins: [react()]` -- 支持 JSX/TSX
- `css.postcss.plugins` -- Tailwind CSS 处理

这就是为什么在 Renderer 里写 React 的体验和普通 Vite + React 项目完全一样。

### 5.6 开发模式下的魔法

运行 `npm run dev`（即 `electron-vite dev`）时：

1. Vite 为 Renderer 启动一个 dev server（比如 `http://localhost:5173`）
2. electron-vite 把这个 URL 注入到环境变量 `ELECTRON_RENDERER_URL`
3. Main Process 读取这个变量，用 `win.loadURL(process.env.ELECTRON_RENDERER_URL)` 加载

```typescript
// desktop/src/main/index.ts -- 加载页面的逻辑
if (process.env.ELECTRON_RENDERER_URL) {
  win.loadURL(process.env.ELECTRON_RENDERER_URL)  // 开发模式：加载 dev server
} else {
  win.loadFile(join(__dirname, '../renderer/index.html'))  // 生产模式：加载本地文件
}
```

这样在开发时，你修改 React 代码能享受 Vite 的 HMR（热模块替换），不用重启整个 Electron。

---

## 第 6 章 TypeScript 配置策略

### 6.1 为什么需要 3 个 tsconfig

一句话：**因为 Main 和 Renderer 跑在完全不同的运行环境中，TypeScript 需要知道"这段代码能用什么 API"**。

- Main Process 跑在 Node.js 里，能用 `fs.readFileSync()`，但用不了 `document.getElementById()`
- Renderer Process 跑在浏览器里，能用 `document.getElementById()`，但用不了 `fs.readFileSync()`

如果只用一个 tsconfig，TypeScript 不知道该提示哪些 API，要么全部提示（不安全），要么全部不提示（不好用）。

### 6.2 三个配置文件的关系

```
tsconfig.json                    # 根配置（仅做引用，不编译）
├── tsconfig.node.json           # Main + Preload（Node.js 环境）
└── tsconfig.web.json            # Renderer（浏览器环境）
```

根配置文件非常简单，只是把另外两个"链接"起来：

```json
// desktop/tsconfig.json
{
  "files": [],
  "references": [
    { "path": "./tsconfig.node.json" },
    { "path": "./tsconfig.web.json" }
  ]
}
```

`"files": []` 表示根配置自己不编译任何文件，只是一个"总指挥"。

### 6.3 `tsconfig.node.json` -- Main + Preload

```json
// desktop/tsconfig.node.json
{
  "compilerOptions": {
    "composite": true,
    "module": "ESNext",
    "moduleResolution": "bundler",
    "target": "ESNext",
    "lib": ["ESNext"],              // ← 只有 ESNext，没有 DOM！
    "outDir": "./out",
    "strict": true,
    "esModuleInterop": true,
    "skipLibCheck": true,
    "forceConsistentCasingInFileNames": true,
    "resolveJsonModule": true,
    "isolatedModules": true,
    "types": ["node"]               // ← 加载 Node.js 类型定义
  },
  "include": ["src/main/**/*", "src/preload/**/*", "src/shared/**/*"]
}
```

关键配置解释：

| 配置 | 值 | 含义 |
|------|---|------|
| `lib: ["ESNext"]` | 只包含 ECMAScript 标准 API | 写不了 `document.getElementById()`，因为没加载 DOM 类型 |
| `types: ["node"]` | 加载 `@types/node` | 能写 `process.env`、`Buffer`、`require()` 等 Node.js API |
| `include` | `src/main/**/*`, `src/preload/**/*`, `src/shared/**/*` | 只管这三个目录 |

**效果**：在 `src/main/` 里写 `fs.readFileSync()` 有类型提示且不报错；写 `document.getElementById()` 会报错（因为没有 DOM 类型）。

### 6.4 `tsconfig.web.json` -- Renderer

```json
// desktop/tsconfig.web.json
{
  "compilerOptions": {
    "composite": true,
    "module": "ESNext",
    "moduleResolution": "bundler",
    "target": "ESNext",
    "lib": ["ESNext", "DOM", "DOM.Iterable"],  // ← 多了 DOM！
    "outDir": "./out",
    "strict": true,
    "esModuleInterop": true,
    "skipLibCheck": true,
    "forceConsistentCasingInFileNames": true,
    "resolveJsonModule": true,
    "isolatedModules": true,
    "jsx": "react-jsx",                         // ← 支持 JSX
    "types": []                                  // ← 空！不加载 Node 类型
  },
  "include": ["src/renderer/**/*"]
}
```

关键配置解释：

| 配置 | 值 | 含义 |
|------|---|------|
| `lib: ["ESNext", "DOM", "DOM.Iterable"]` | 加载了 DOM 类型 | 能写 `document`、`window`、`HTMLElement` 等 |
| `types: []` | 空数组，不加载任何 `@types/*` | 写 `process.env` 会报错（好！Renderer 不应该碰 Node API） |
| `jsx: "react-jsx"` | React 17+ 的 JSX 转换 | 支持 TSX 文件中写 JSX |
| `include` | `src/renderer/**/*` | 只管 Renderer 目录 |

**效果**：在 `src/renderer/` 里写 `document.getElementById()` 有类型提示；写 `import fs from 'fs'` 会报错。

### 6.5 `lib` 和 `types` 的区别

这两个经常让人搞混：

- **`lib`** -- 控制 TypeScript 内置类型库。`"DOM"` 是 TypeScript 自带的浏览器 API 类型定义，`"ESNext"` 是 ECMAScript 标准 API。不需要安装任何包。
- **`types`** -- 控制从 `node_modules/@types/` 加载哪些第三方类型定义。`"node"` 指的是 `@types/node` 包。设为 `[]` 表示不自动加载任何 `@types/*`。

```
lib: ["ESNext"]           → Promise, Map, Set, Array 等 JS 标准 API
lib: ["DOM"]              → document, window, HTMLElement 等浏览器 API
types: ["node"]           → fs, path, process, Buffer 等 Node.js API
types: []                 → 不加载任何 @types/*
```

### 6.6 `composite: true` 的作用

这个配置启用了 TypeScript 的**项目引用**（Project References）功能。它的好处是：

1. **增量编译**：只重新编译修改过的项目部分
2. **边界强制**：`tsconfig.node.json` 管的代码不能 import `tsconfig.web.json` 管的代码，反之亦然
3. **IDE 支持**：VS Code 能正确理解多项目结构

---

## 第 7 章 Renderer 的类型安全

### 7.1 问题：`window.nexus` 是哪来的？

在 Renderer 的 React 代码里，你可以写：

```typescript
const result = await window.nexus.startAllServices()
```

但 TypeScript 默认不知道 `window` 上有个 `nexus` 属性。如果你直接写，IDE 会画红线报错：

```
Property 'nexus' does not exist on type 'Window & typeof globalThis'
```

### 7.2 解决方案：`env.d.ts`

NarraNexus 在 `src/renderer/env.d.ts` 中做了全局类型声明：

```typescript
// desktop/src/renderer/env.d.ts

/** Preload 暴露的 Nexus API 类型 */
interface NexusAPI {
  checkDependencies: () => Promise<DependencyStatus[]>
  startAllServices: () => Promise<{ success: boolean }>
  stopAllServices: () => Promise<{ success: boolean }>
  onLog: (callback: (entry: LogEntry) => void) => () => void
  // ... 等等
}

interface LogEntry {
  serviceId: string
  timestamp: number
  stream: 'stdout' | 'stderr'
  message: string
}

// 扩展 Window 接口
interface Window {
  nexus: NexusAPI
}
```

### 7.3 这是怎么工作的？

TypeScript 有一个"声明合并"（Declaration Merging）机制。`Window` 是 TypeScript 内置类型（来自 `lib: ["DOM"]`），你可以在任何 `.d.ts` 文件中"追加"属性：

```
TypeScript 内置的 Window:
  - document
  - location
  - localStorage
  - ...

你的 env.d.ts 追加的:
  + nexus: NexusAPI         ← 合并进去！
```

合并后，TypeScript 就知道 `window.nexus` 存在，而且有完整的类型提示：

```typescript
// 现在这样写有完整的 IDE 提示！
window.nexus.startAllServices()  // 返回 Promise<{ success: boolean }>
window.nexus.onLog((entry) => {
  // entry 自动推断为 LogEntry 类型
  console.log(entry.serviceId)  // string
  console.log(entry.message)    // string
})
```

### 7.4 为什么是 `.d.ts` 而不是 `.ts`

- `.d.ts` 文件是纯类型声明文件，不包含运行时代码，不会被编译成 JS
- `.ts` 文件包含运行时代码，会被编译
- 全局类型扩展（如 `interface Window { ... }`）应该放在 `.d.ts` 中

### 7.5 类型如何保持同步

注意一个问题：`env.d.ts` 中的 `NexusAPI` 接口是**手动维护**的，它需要和 `preload/index.ts` 中实际暴露的 API 保持同步。如果 Preload 增加了一个新 API 但忘了更新 `env.d.ts`，Renderer 代码中调用这个 API 时 TypeScript 会报错。

这实际上是一个**优点** -- 它强迫你在增加 API 时同时更新类型声明，相当于一种"合同"。

在 NarraNexus 中，Preload 侧也定义了 `NexusAPI` 接口（用于约束实际实现），和 `env.d.ts` 中的声明形成双重保障：

```typescript
// preload/index.ts -- 实现侧的接口
export interface NexusAPI {
  startAllServices: () => Promise<{ success: boolean }>
  // ...
}
const nexusAPI: NexusAPI = { ... }  // ← TypeScript 检查实现是否匹配
```

```typescript
// renderer/env.d.ts -- 使用侧的接口
interface NexusAPI {
  startAllServices: () => Promise<{ success: boolean }>
  // ...
}
```

---

## 第 8 章 开发模式 vs 生产模式

### 8.1 总览对比

| 方面 | 开发模式 (`npm run dev`) | 生产模式（打包后的 .app） |
|------|------------------------|-------------------------|
| Renderer 加载方式 | `win.loadURL('http://localhost:5173')` | `win.loadFile('out/renderer/index.html')` |
| 项目根目录 | 仓库根目录（直接可写） | `~/Library/Application Support/NarraNexus/project/` |
| 代码热更新 | Vite HMR，改了立刻生效 | 无，需重新打包 |
| DevTools | 自动打开 | 不打开 |
| 环境变量 | 继承终端的完整 `$PATH` | 只有 launchd 的极简环境（macOS） |
| 判断方式 | `app.isPackaged === false` | `app.isPackaged === true` |

### 8.2 路径差异详解

这是最容易踩坑的地方。

```typescript
// desktop/src/main/constants.ts

// 打包后 .app 内的只读项目目录
export const BUNDLED_PROJECT_ROOT = app.isPackaged
  ? join(process.resourcesPath, 'project')  // /path/to/NarraNexus.app/Contents/Resources/project
  : null                                     // 开发模式不存在

// 实际使用的可写项目目录
export const PROJECT_ROOT = app.isPackaged
  ? join(app.getPath('userData'), 'project') // ~/Library/Application Support/NarraNexus/project
  : join(__dirname, '..', '..', '..')        // 开发模式：仓库根目录
```

**为什么有两个路径？**

打包后，项目源码被 `extraResources` 复制到了 `.app/Contents/Resources/project/` 里。但这个位置是**只读的**（macOS 对 `.app` 包的安全限制）。而我们的应用需要写入 `.env` 文件、创建 `.venv` 虚拟环境等。

解决方案：首次启动时，把只读的 `Resources/project` 复制到可写的 `userData/project`。

```typescript
// desktop/src/main/constants.ts
export function ensureWritableProject(): void {
  if (!app.isPackaged || !BUNDLED_PROJECT_ROOT) return  // 开发模式跳过
  if (existsSync(join(PROJECT_ROOT, 'pyproject.toml'))) return  // 已复制过

  cpSync(BUNDLED_PROJECT_ROOT, PROJECT_ROOT, { recursive: true })
}
```

### 8.3 Renderer 加载方式差异

```typescript
// desktop/src/main/index.ts

if (process.env.ELECTRON_RENDERER_URL) {
  // 开发模式：electron-vite 自动注入这个环境变量
  // 值类似 http://localhost:5173
  win.loadURL(process.env.ELECTRON_RENDERER_URL)
} else {
  // 生产模式：加载编译好的本地 HTML 文件
  win.loadFile(join(__dirname, '../renderer/index.html'))
}

// 开发模式下打开 DevTools
if (process.env.ELECTRON_RENDERER_URL) {
  win.webContents.openDevTools({ mode: 'detach' })
}
```

### 8.4 环境变量差异（macOS 特有问题）

在 macOS 上，双击 `.app` 启动的应用**不会继承**你在 `~/.zshrc` 中设置的环境变量（`$PATH`、API Key 等）。这是因为 `.app` 由 `launchd` 启动，只有极简的环境变量。

但 NarraNexus 需要调用 `uv`、`docker`、`node` 等命令，这些通常在 `/usr/local/bin` 或 `~/.local/bin` 里，不在 launchd 的默认 `$PATH` 中。

详细解释见第 11 章。

---

## 第 9 章 electron-builder 打包配置详解

### 9.1 完整配置逐行讲解

```yaml
# desktop/electron-builder.yml

# ═══════════════════════════════════════════════
# 基础信息
# ═══════════════════════════════════════════════

appId: com.narranexus.desktop
# 应用的唯一标识符（反向域名格式）
# macOS 用它来区分不同应用的数据存储
# 影响 app.getPath('userData') 的路径

productName: NarraNexus
# 应用显示名称
# 出现在：macOS 菜单栏、Dock、安装包名等

directories:
  buildResources: resources
  # 构建资源目录，放图标等文件
  # 相对于 desktop/ 目录
  output: dist
  # 打包产物输出目录
  # 最终的 .dmg / .AppImage 文件会出现在 desktop/dist/

# ═══════════════════════════════════════════════
# macOS 配置
# ═══════════════════════════════════════════════

mac:
  category: public.app-category.developer-tools
  # macOS 应用分类，出现在 Finder "获取信息" 中
  # 常用值：
  #   public.app-category.developer-tools  开发工具
  #   public.app-category.productivity     效率工具
  #   public.app-category.utilities        实用工具

  target:
    - target: dmg
      arch:
        - universal
  # 打包格式：DMG（磁盘映像）
  # universal = 同时包含 Intel (x64) 和 Apple Silicon (arm64)
  # 一个安装包兼容所有 Mac

  icon: resources/icon.icns
  # macOS 图标文件（.icns 格式，包含多种尺寸）

  entitlementsInherit: null
  # 应用权限继承设置，null = 不使用沙盒权限
  # 如果要上 App Store 需要配置这个

# ═══════════════════════════════════════════════
# DMG 安装界面配置
# ═══════════════════════════════════════════════

dmg:
  title: "NarraNexus"
  # DMG 打开后窗口标题栏显示的文字

  contents:
    - x: 130
      y: 220
    # 应用图标在 DMG 窗口中的位置
    # (130, 220) = 左侧
    - x: 410
      y: 220
      type: link
      path: /Applications
    # Applications 快捷方式的位置
    # (410, 220) = 右侧
    # type: link + path: /Applications = 创建指向 /Applications 的符号链接

# 用户打开 DMG 后看到的界面：
#
# ┌─────────────────────────────────────┐
# │        NarraNexus                   │
# │                                     │
# │   [NarraNexus]  →  [Applications]  │
# │                                     │
# │   把左边拖到右边就完成安装！          │
# └─────────────────────────────────────┘

# ═══════════════════════════════════════════════
# Linux 配置
# ═══════════════════════════════════════════════

linux:
  target:
    - target: AppImage
    # AppImage = 免安装的单文件可执行程序
    # 双击就能运行，不需要 sudo
    - target: deb
    # deb = Debian/Ubuntu 安装包
    # 可以用 dpkg -i 安装
  category: Development
  # Linux 桌面分类

# ═══════════════════════════════════════════════
# 额外资源（关键！）
# ═══════════════════════════════════════════════

extraResources:
  - from: "../"
    to: "project"
    filter:
      - "**/*"
      - "!**/node_modules/**"
      - "!**/.venv/**"
      - "!**/.git/**"
      - "!**/desktop/**"
      - "!**/__pycache__/**"
      - "!**/*.pyc"
```

### 9.2 `extraResources` -- 把项目源码打进应用

这是最关键也最容易困惑的部分。

**是什么**：`extraResources` 告诉 electron-builder "除了 Electron 应用本身，还要把这些额外的文件打包进去"。

**为什么需要**：NarraNexus Desktop 不只是一个界面，它需要启动 Python 后台服务。这意味着打包后的 `.app` 里必须包含完整的 Python 项目源码（`src/`、`backend/`、`frontend/`、`pyproject.toml` 等）。

**怎么工作的**：

```
打包前的仓库结构：              打包后 .app 内部结构：
NexusAgent/                     NarraNexus.app/Contents/
├── src/            ──→          ├── Resources/
├── backend/        ──→          │   ├── project/        ← extraResources 复制到这里！
├── frontend/       ──→          │   │   ├── src/
├── pyproject.toml  ──→          │   │   ├── backend/
├── desktop/        ✗排除        │   │   ├── frontend/
├── .git/           ✗排除        │   │   └── pyproject.toml
├── .venv/          ✗排除        │   ├── app.asar        ← Electron 应用本体
└── node_modules/   ✗排除        │   └── icon.icns
                                 └── MacOS/
                                     └── NarraNexus      ← 可执行文件
```

配置解读：

```yaml
extraResources:
  - from: "../"           # 从 desktop/ 的上一级（仓库根目录）开始
    to: "project"         # 复制到 Resources/project/
    filter:
      - "**/*"            # 默认包含所有文件
      - "!**/node_modules/**"   # 排除 node_modules（太大了，运行时 npm install）
      - "!**/.venv/**"         # 排除 Python 虚拟环境
      - "!**/.git/**"         # 排除 git 历史
      - "!**/desktop/**"      # 排除 desktop 目录本身（不需要嵌套）
      - "!**/__pycache__/**"  # 排除 Python 缓存
      - "!**/*.pyc"           # 排除编译的 Python 文件
```

### 9.3 macOS `.app` 包的内部结构

macOS 的 `.app` 实际上是一个**文件夹**（在 Finder 中右键 -> 显示包内容）。electron-builder 生成的结构如下：

```
NarraNexus.app/
└── Contents/
    ├── Info.plist              # 应用元信息（名称、版本、图标等）
    ├── PkgInfo                 # 包类型标识
    ├── MacOS/
    │   └── NarraNexus          # 可执行文件（Electron 主程序）
    ├── Frameworks/
    │   ├── Electron Framework.framework/   # Chromium + Node.js
    │   └── ...
    └── Resources/
        ├── app.asar            # 你的 Electron 代码（main + preload + renderer）
        │                       # 被压缩成 asar 归档格式（类似 zip）
        ├── icon.icns           # 应用图标
        └── project/            # ← extraResources 复制进来的！
            ├── src/
            ├── backend/
            ├── frontend/
            ├── pyproject.toml
            └── ...
```

**asar 格式**：electron-builder 默认把你的 `out/` 产物压缩成 `app.asar`。这是 Electron 自创的归档格式，类似 zip 但不需要解压就能读取。好处是：
- 文件数量少，安装更快
- 避免 Windows 上路径过长的问题
- 一定程度上保护源码

---

## 第 10 章 完整打包流水线

### 10.1 从源码到 .dmg 的每一步

NarraNexus 提供了一个打包脚本 `build-desktop.sh`，它自动化了整个流程：

```
bash build-desktop.sh
```

完整流水线如下：

```
┌──────────────────────────────────────────────────────┐
│ Step 1: check_prerequisites                           │
│ 检查 Node.js >= 20 是否已安装                          │
└──────────────┬───────────────────────────────────────┘
               │
┌──────────────▼───────────────────────────────────────┐
│ Step 2: clean                                         │
│ 删除旧的编译产物：rm -rf dist/ out/                    │
└──────────────┬───────────────────────────────────────┘
               │
┌──────────────▼───────────────────────────────────────┐
│ Step 3: install_deps                                  │
│ cd desktop && npm install                             │
│ 安装 Electron、electron-vite、React 等依赖             │
└──────────────┬───────────────────────────────────────┘
               │
┌──────────────▼───────────────────────────────────────┐
│ Step 4: build_frontend                                │
│ cd frontend && npm run build                          │
│ 构建 NarraNexus 的 React 前端（注意：是项目前端，       │
│ 不是 desktop 的 Renderer！）                           │
│ 产物：frontend/dist/                                  │
└──────────────┬───────────────────────────────────────┘
               │
┌──────────────▼───────────────────────────────────────┐
│ Step 5: compile_electron                              │
│ cd desktop && npx electron-vite build                 │
│ 编译 Electron 的三部分代码：                            │
│   src/main/     → out/main/index.js                   │
│   src/preload/  → out/preload/index.js                │
│   src/renderer/ → out/renderer/index.html + assets    │
└──────────────┬───────────────────────────────────────┘
               │
┌──────────────▼───────────────────────────────────────┐
│ Step 6: package_app                                   │
│ cd desktop && npx electron-builder --mac              │
│                                                       │
│ electron-builder 做的事情：                             │
│ 1. 下载对应平台的 Electron 预编译二进制文件              │
│ 2. 把 out/ 编译产物打包成 app.asar                      │
│ 3. 把 app.asar 放入 .app/Contents/Resources/           │
│ 4. 把 extraResources 指定的文件复制进                    │
│    .app/Contents/Resources/project/                    │
│ 5. 生成 .dmg 磁盘映像                                   │
│                                                       │
│ 产物：desktop/dist/NarraNexus-1.0.0-universal.dmg     │
└──────────────────────────────────────────────────────┘
```

### 10.2 build-desktop.sh 关键代码

```bash
# build-desktop.sh 主流程

main() {
  local target="${1:-}"

  # 如果没指定平台，自动检测
  if [ -z "$target" ]; then
    target=$(detect_platform)   # Darwin → mac, Linux → linux
  fi

  check_prerequisites   # 确保 Node.js >= 20
  clean                 # rm -rf desktop/dist desktop/out
  install_deps          # cd desktop && npm install
  build_frontend        # cd frontend && npm run build（项目前端）
  compile_electron      # cd desktop && npx electron-vite build
  package_app "$target" # cd desktop && npx electron-builder --mac/--linux
}
```

### 10.3 两个"前端"不要搞混

NarraNexus 有**两个前端**：

| 前端 | 目录 | 用途 | 什么时候构建 |
|------|------|------|------------|
| 项目前端 | `frontend/` | NarraNexus AI Agent 的 Web UI（React） | Step 4: `build_frontend` |
| 桌面前端 | `desktop/src/renderer/` | Electron 桌面应用的管理界面（React） | Step 5: `compile_electron` |

项目前端是给**最终用户**用的（Agent 管理页面），它被打包进 `.app` 的 `Resources/project/frontend/dist/` 里，由 Python 后端 serve。

桌面前端是给**运维/开发者**用的（服务管理面板），它是 Electron Renderer 的一部分，被编译到 `out/renderer/` 并打包到 `app.asar` 里。

### 10.4 产物清单

打包完成后，`desktop/dist/` 里会有：

macOS:
```
desktop/dist/
├── NarraNexus-1.0.0-universal.dmg     # 安装包（发给用户的）
├── NarraNexus-1.0.0-universal-mac.zip # ZIP 格式（用于自动更新）
├── mac-universal/
│   └── NarraNexus.app/                 # 解压后的应用（调试用）
└── builder-effective-config.yaml       # 实际使用的完整配置（调试用）
```

Linux:
```
desktop/dist/
├── NarraNexus-1.0.0.AppImage          # 免安装单文件
└── narranexus-desktop_1.0.0_amd64.deb # Debian 安装包
```

---

## 第 11 章 生产环境的特殊处理

### 11.1 只读文件系统问题

**问题**：macOS 对 `.app` 包内的文件有只读保护。打包后你的项目源码在 `.app/Contents/Resources/project/` 里，这个目录是只读的。但 NarraNexus 需要：
- 写入 `.env` 文件（存储 API Key）
- 创建 `.venv` 虚拟环境（`uv sync` 会创建）
- 生成 `node_modules`（`npm install` 会创建）

**解决方案**：`ensureWritableProject()`

```typescript
// desktop/src/main/constants.ts

// 只读的打包位置
export const BUNDLED_PROJECT_ROOT = app.isPackaged
  ? join(process.resourcesPath, 'project')
  : null

// 可写的工作位置
export const PROJECT_ROOT = app.isPackaged
  ? join(app.getPath('userData'), 'project')
  : join(__dirname, '..', '..', '..')

export function ensureWritableProject(): void {
  if (!app.isPackaged || !BUNDLED_PROJECT_ROOT) return
  if (existsSync(join(PROJECT_ROOT, 'pyproject.toml'))) return  // 已复制过

  console.log(`Copying bundled project to writable location: ${PROJECT_ROOT}`)
  cpSync(BUNDLED_PROJECT_ROOT, PROJECT_ROOT, { recursive: true })
}
```

工作流程：

```
首次启动 .app：
1. 检测到 app.isPackaged === true
2. 检测到 ~/Library/Application Support/NarraNexus/project/ 不存在
3. 把 .app/Contents/Resources/project/ 整个复制到上面的路径
4. 之后所有操作（写 .env、创建 .venv）都在可写目录进行

后续启动：
1. 检测到 pyproject.toml 已存在
2. 跳过复制，直接使用
```

`app.getPath('userData')` 在各平台的位置：

| 平台 | 路径 |
|------|------|
| macOS | `~/Library/Application Support/NarraNexus/` |
| Linux | `~/.config/NarraNexus/` |
| Windows | `%APPDATA%/NarraNexus/` |

### 11.2 macOS Shell 环境变量问题

**问题**：macOS 双击 `.app` 启动时，应用继承的是 `launchd` 的极简环境变量。用户在 `~/.zshrc` 里设置的 `$PATH`、API Key 等全部不可见。

具体影响：

```
终端启动时的 PATH:
  /usr/local/bin:/opt/homebrew/bin:/Users/xxx/.local/bin:...

.app 启动时的 PATH:
  /usr/bin:/bin:/usr/sbin:/sbin
  （找不到 uv、docker、node！）
```

**解决方案**：`shell-env.ts`

这个模块在应用启动时执行一次用户的登录 Shell，获取完整的环境变量：

```typescript
// desktop/src/main/shell-env.ts

let cachedEnv: Record<string, string> | null = null

export async function initShellEnv(): Promise<void> {
  if (process.platform !== 'darwin') {
    // Linux 从终端启动，已继承完整环境
    cachedEnv = { ...process.env } as Record<string, string>
    return
  }

  try {
    const shell = process.env.SHELL || '/bin/zsh'
    // 执行登录 Shell，用 env -0 打印所有环境变量（NUL 分隔）
    const { stdout } = await execFileAsync(shell, ['-ilc', 'env -0'], {
      timeout: 10000,
      maxBuffer: 10 * 1024 * 1024
    })

    // 解析 KEY=VALUE\0KEY=VALUE\0... 格式
    const parsed: Record<string, string> = {}
    for (const entry of stdout.split('\0')) {
      if (!entry) continue
      const eqIndex = entry.indexOf('=')
      if (eqIndex === -1) continue
      parsed[entry.substring(0, eqIndex)] = entry.substring(eqIndex + 1)
    }

    cachedEnv = parsed
  } catch {
    // 失败时使用 fallback：手动补充常见路径
    cachedEnv = buildFallbackEnv()
  }
}

// 所有子进程启动时都使用这个环境
export function getShellEnv(): Record<string, string> {
  return cachedEnv || buildFallbackEnv()
}
```

**原理**：`shell -ilc 'env -0'` 的含义：
- `-i` = interactive（交互模式，会加载 `~/.zshrc`）
- `-l` = login（登录模式，会加载 `~/.zprofile`）
- `-c` = command（执行后面的命令）
- `env -0` = 打印所有环境变量，用 NUL 字符分隔（比换行更安全，因为值里可能有换行）

**使用方式**：所有 `child_process.spawn()` 和 `execFile()` 都传入 `getShellEnv()` 作为 `env` 参数：

```typescript
// desktop/src/main/process-manager.ts
const proc = spawn(svc.command, svc.args, {
  cwd,
  env: getShellEnv(),  // ← 使用解析后的完整环境
  detached: true
})
```

### 11.3 进程管理的特殊处理

打包后的应用需要管理多个子进程（Python 后端、MCP 服务器等），有一些生产环境特有的问题：

**1. 进程组管理**

NarraNexus 使用 `detached: true` 创建新进程组，这样退出时可以用负 PID 杀掉整个进程组（包括子进程的子进程）：

```typescript
// desktop/src/main/process-manager.ts

const proc = spawn(svc.command, svc.args, {
  detached: true  // 创建新进程组
})

// 停止时杀掉整个进程组
private killProcessGroup(proc: ChildProcess, signal: NodeJS.Signals): void {
  if (!proc.pid) return
  try {
    process.kill(-proc.pid, signal)  // 负 PID = 杀掉整个进程组
  } catch {
    try { proc.kill(signal) } catch {}
  }
}
```

**2. 崩溃自动重启**

后台服务崩溃后会自动重启，使用指数退避策略（避免快速循环重启）：

```typescript
private async tryAutoRestart(svc: ServiceDef): Promise<void> {
  const count = (this.restartCounts.get(svc.id) ?? 0) + 1
  if (count > MAX_RESTART_ATTEMPTS) return  // 最多重启 3 次

  const waitMs = RESTART_BACKOFF_BASE * Math.pow(2, count - 1)
  // 第 1 次等 1 秒，第 2 次等 2 秒，第 3 次等 4 秒
  await this.delay(waitMs)
  this.spawnProcess(svc)
}
```

**3. 端口冲突处理**

启动前检测端口是否被占用，弹窗让用户确认是否终止占用进程：

```typescript
private async killStalePorts(): Promise<void> {
  for (const { port, label } of portsToCheck) {
    const { stdout } = await execFileAsync('lsof', ['-ti', `:${port}`])
    // 如果有进程占用端口，弹窗提示
    const { response } = await dialog.showMessageBox({
      type: 'warning',
      title: '端口冲突',
      message: '以下端口被其他进程占用，是否终止这些进程？',
      buttons: ['终止并继续', '跳过']
    })
  }
}
```

### 11.4 持久化存储

应用需要记住一些状态（比如"是否已完成初始设置"），NarraNexus 使用了一个简单的 JSON 文件存储：

```typescript
// desktop/src/main/store.ts

class SimpleStore {
  private filePath: string
  private data: StoreData

  constructor() {
    this.filePath = join(app.getPath('userData'), 'config.json')
    // 存储在 ~/Library/Application Support/NarraNexus/config.json
    this.data = this.load()
  }

  get<K extends keyof StoreData>(key: K): StoreData[K] {
    return this.data[key]
  }

  set<K extends keyof StoreData>(key: K, value: StoreData[K]): void {
    this.data[key] = value
    this.save()  // 立即写入磁盘
  }
}

export const store = new SimpleStore()
```

为什么不用 `electron-store`（一个流行的 Electron 存储库）？因为 `electron-store` 是 ESM-only 的，和 electron-vite 的 CJS 输出存在兼容性问题。自己写一个只需要 50 行，还没有依赖。

---

## 第 12 章 如何从零创建一个类似项目

下面是从空目录到能打包出 `.dmg` 的完整 step-by-step 教程。

### Step 1: 初始化项目

```bash
mkdir my-desktop-app && cd my-desktop-app
npm init -y
```

### Step 2: 安装核心依赖

```bash
# Electron 运行时
npm install -D electron

# electron-vite（编译工具链）
npm install -D electron-vite

# electron-builder（打包工具）
npm install -D electron-builder

# React 全家桶
npm install -D react react-dom @types/react @types/react-dom

# TypeScript
npm install -D typescript

# Vite 插件
npm install -D @vitejs/plugin-react

# Electron toolkit（可选，提供一些实用工具）
npm install -D @electron-toolkit/preload @electron-toolkit/utils

# Tailwind CSS（可选）
npm install -D tailwindcss postcss autoprefixer
```

### Step 3: 创建目录结构

```bash
mkdir -p src/main src/preload src/shared src/renderer/pages src/renderer/styles resources
```

### Step 4: 配置 package.json

```json
{
  "name": "my-desktop-app",
  "version": "1.0.0",
  "main": "./out/main/index.js",
  "scripts": {
    "dev": "electron-vite dev",
    "build": "electron-vite build",
    "build:mac": "electron-vite build && electron-builder --mac",
    "build:linux": "electron-vite build && electron-builder --linux",
    "postinstall": "electron-builder install-app-deps"
  }
}
```

关键说明：
- `"main": "./out/main/index.js"` -- 告诉 Electron 去哪找 Main Process 的入口
- `"postinstall"` -- 安装依赖后自动重编译原生模块（如果有的话）

### Step 5: 创建 electron-vite 配置

```typescript
// electron.vite.config.ts

import { resolve } from 'path'
import { defineConfig, externalizeDepsPlugin } from 'electron-vite'
import react from '@vitejs/plugin-react'

export default defineConfig({
  main: {
    plugins: [externalizeDepsPlugin()],
    build: {
      rollupOptions: {
        input: {
          index: resolve(__dirname, 'src/main/index.ts')
        }
      }
    }
  },
  preload: {
    plugins: [externalizeDepsPlugin()],
    build: {
      rollupOptions: {
        input: {
          index: resolve(__dirname, 'src/preload/index.ts')
        }
      }
    }
  },
  renderer: {
    root: resolve(__dirname, 'src/renderer'),
    build: {
      rollupOptions: {
        input: {
          index: resolve(__dirname, 'src/renderer/index.html')
        }
      }
    },
    plugins: [react()]
  }
})
```

### Step 6: 创建 TypeScript 配置

```json
// tsconfig.json
{
  "files": [],
  "references": [
    { "path": "./tsconfig.node.json" },
    { "path": "./tsconfig.web.json" }
  ]
}
```

```json
// tsconfig.node.json
{
  "compilerOptions": {
    "composite": true,
    "module": "ESNext",
    "moduleResolution": "bundler",
    "target": "ESNext",
    "lib": ["ESNext"],
    "outDir": "./out",
    "strict": true,
    "esModuleInterop": true,
    "skipLibCheck": true,
    "resolveJsonModule": true,
    "isolatedModules": true,
    "types": ["node"]
  },
  "include": ["src/main/**/*", "src/preload/**/*", "src/shared/**/*"]
}
```

```json
// tsconfig.web.json
{
  "compilerOptions": {
    "composite": true,
    "module": "ESNext",
    "moduleResolution": "bundler",
    "target": "ESNext",
    "lib": ["ESNext", "DOM", "DOM.Iterable"],
    "outDir": "./out",
    "strict": true,
    "esModuleInterop": true,
    "skipLibCheck": true,
    "resolveJsonModule": true,
    "isolatedModules": true,
    "jsx": "react-jsx",
    "types": []
  },
  "include": ["src/renderer/**/*"]
}
```

### Step 7: 写 IPC 通道定义

```typescript
// src/shared/ipc-channels.ts
export const IPC = {
  GREET: 'greet',
  ON_MESSAGE: 'on-message'
} as const
```

### Step 8: 写 Main Process

```typescript
// src/main/index.ts
import { app, BrowserWindow, ipcMain } from 'electron'
import { join } from 'path'
import { IPC } from '../shared/ipc-channels'

let mainWindow: BrowserWindow | null = null

function createWindow(): BrowserWindow {
  const win = new BrowserWindow({
    width: 800,
    height: 600,
    webPreferences: {
      preload: join(__dirname, '../preload/index.js'),
      sandbox: false,
      contextIsolation: true,
      nodeIntegration: false
    },
    show: false
  })

  win.once('ready-to-show', () => win.show())

  if (process.env.ELECTRON_RENDERER_URL) {
    win.loadURL(process.env.ELECTRON_RENDERER_URL)
  } else {
    win.loadFile(join(__dirname, '../renderer/index.html'))
  }

  return win
}

// 注册 IPC handler
ipcMain.handle(IPC.GREET, async (_event, name: string) => {
  return `Hello, ${name}! 来自 Main Process`
})

app.whenReady().then(() => {
  mainWindow = createWindow()

  app.on('activate', () => {
    if (BrowserWindow.getAllWindows().length === 0) {
      mainWindow = createWindow()
    }
  })
})

app.on('window-all-closed', () => {
  if (process.platform !== 'darwin') {
    app.quit()
  }
})
```

### Step 9: 写 Preload

```typescript
// src/preload/index.ts
import { contextBridge, ipcRenderer } from 'electron'
import { IPC } from '../shared/ipc-channels'

const api = {
  greet: (name: string) => ipcRenderer.invoke(IPC.GREET, name)
}

contextBridge.exposeInMainWorld('api', api)
```

### Step 10: 写 Renderer

```html
<!-- src/renderer/index.html -->
<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="UTF-8" />
  <title>My Desktop App</title>
</head>
<body>
  <div id="root"></div>
  <script type="module" src="./main.tsx"></script>
</body>
</html>
```

```typescript
// src/renderer/env.d.ts
interface MyAPI {
  greet: (name: string) => Promise<string>
}
interface Window {
  api: MyAPI
}
```

```typescript
// src/renderer/main.tsx
import React from 'react'
import ReactDOM from 'react-dom/client'

function App() {
  const [message, setMessage] = React.useState('')

  const handleClick = async () => {
    const result = await window.api.greet('World')
    setMessage(result)
  }

  return (
    <div style={{ padding: 20 }}>
      <h1>My Desktop App</h1>
      <button onClick={handleClick}>Say Hello</button>
      <p>{message}</p>
    </div>
  )
}

ReactDOM.createRoot(document.getElementById('root')!).render(<App />)
```

### Step 11: 创建 electron-builder 配置

```yaml
# electron-builder.yml
appId: com.example.my-desktop-app
productName: MyDesktopApp
directories:
  buildResources: resources
  output: dist

mac:
  category: public.app-category.developer-tools
  target:
    - target: dmg
      arch:
        - universal

linux:
  target:
    - target: AppImage
  category: Development
```

### Step 12: 开发和打包

```bash
# 开发模式（热更新）
npm run dev

# 打包 macOS
npm run build:mac

# 打包 Linux
npm run build:linux
```

### Step 13: 验证

开发模式下应该看到一个窗口，点击按钮后显示 "Hello, World! 来自 Main Process"。

打包后在 `dist/` 目录下找到 `.dmg`（macOS）或 `.AppImage`（Linux）。

---

## 第 13 章 常见坑与排错

### 坑 1: macOS 环境变量丢失

**现象**：打包后的 `.app` 执行 `uv`、`docker` 等命令时报 "command not found"。开发模式下完全正常。

**原因**：`.app` 双击启动时继承的是 `launchd` 的极简 `$PATH`，只有 `/usr/bin:/bin:/usr/sbin:/sbin`。

**解决方案**：参考 NarraNexus 的 `shell-env.ts`，在应用启动时执行一次登录 Shell 获取完整环境。

```typescript
// 核心代码
const { stdout } = await execFileAsync(shell, ['-ilc', 'env -0'], { timeout: 10000 })
```

### 坑 2: asar 只读导致写入失败

**现象**：打包后写文件报错 "ENOENT" 或 "EROFS"。

**原因**：`app.asar` 是只读归档，`process.resourcesPath` 下的文件也是只读的（macOS `.app` 签名保护）。

**解决方案**：任何需要写入的文件都放在 `app.getPath('userData')` 下：

```typescript
// 永远用这个路径存放可写数据
const writablePath = join(app.getPath('userData'), 'my-config.json')
```

### 坑 3: 路径在开发/生产模式下不同

**现象**：开发模式下资源路径正确，打包后找不到文件。

**原因**：开发模式下 `__dirname` 指向源码目录，打包后指向 `app.asar` 内部。

**解决方案**：始终用 `app.isPackaged` 判断：

```typescript
const iconPath = app.isPackaged
  ? join(process.resourcesPath, 'icon.png')     // 打包后
  : join(__dirname, '..', '..', 'resources', 'icon.png')  // 开发模式
```

### 坑 4: ESM/CJS 兼容问题

**现象**：某些 npm 包（如 `electron-store`、`got` 等）在 electron-vite 中导入报错。

**原因**：这些包只提供 ESM 格式（`export default`），但 electron-vite 的 Main Process 默认输出 CJS（`module.exports`）。混用会导致 `require()` 无法加载 ESM 模块。

**解决方案**：

方案 A：自己实现简单替代品（NarraNexus 的做法）
```typescript
// 自己写一个简单的 JSON 存储，替代 electron-store
class SimpleStore {
  private filePath: string
  private data: StoreData
  // ... 50 行搞定
}
```

方案 B：在 electron-vite 配置中标记该包不做 externalize
```typescript
main: {
  plugins: [externalizeDepsPlugin({ exclude: ['electron-store'] })],
}
```

### 坑 5: Preload 里不能 import Renderer 的代码

**现象**：在 Preload 里 import React 组件或 Renderer 的工具函数，构建报错。

**原因**：Preload 由 `tsconfig.node.json` 编译，Renderer 由 `tsconfig.web.json` 编译。它们是完全隔离的编译上下文。

**解决方案**：共享代码放在 `src/shared/` 目录，且只包含纯数据（常量、类型），不依赖任何特定运行环境的 API。

### 坑 6: `contextBridge.exposeInMainWorld` 的序列化限制

**现象**：通过 IPC 传递的对象丢失了方法（函数）、`Date` 变成了字符串、`Map`/`Set` 变成了空对象。

**原因**：IPC 通信使用结构化克隆算法（Structured Clone），不能传递函数、Symbol、DOM 节点等。

**解决方案**：只传递纯数据（JSON 可序列化的对象）：

```typescript
// 不要传函数
ipcMain.handle('bad', () => {
  return { doSomething: () => {} }  // 函数会丢失！
})

// 只传纯数据
ipcMain.handle('good', () => {
  return { status: 'ok', count: 42, items: ['a', 'b'] }
})
```

### 坑 7: 子进程残留

**现象**：关闭 Electron 应用后，Python 后台服务还在跑，占用端口。

**原因**：`uv run python xxx.py` 实际上启动了两个进程：`uv` 和 `python`。只杀 `uv` 进程，`python` 子进程可能变成孤儿进程。

**解决方案**：使用 `detached: true` + 进程组管理（NarraNexus 的做法）：

```typescript
// 启动时创建新进程组
const proc = spawn(cmd, args, { detached: true })

// 停止时杀掉整个进程组
process.kill(-proc.pid, 'SIGTERM')  // 负 PID = 整个进程组
```

### 坑 8: `ready-to-show` 事件避免白屏闪烁

**现象**：应用启动时先显示一个白色窗口，然后内容才出现。

**原因**：`BrowserWindow` 创建后立刻显示，但 HTML 还没加载完。

**解决方案**：创建时隐藏，加载完后再显示：

```typescript
const win = new BrowserWindow({
  show: false  // 创建时不显示
})

win.once('ready-to-show', () => {
  win.show()   // HTML 加载完后显示
})
```

### 坑 9: macOS 关闭窗口 != 退出应用

**现象**：点击红色关闭按钮后应用退出，macOS 的习惯是关闭窗口但不退出。

**解决方案**：拦截 `close` 事件，改为隐藏窗口：

```typescript
// desktop/src/main/index.ts

// 关闭窗口时最小化到托盘，而不是退出
win.on('close', (event) => {
  if (!app.isQuitting) {
    event.preventDefault()
    win.hide()
  }
})

// macOS：点击 Dock 图标时重新显示
app.on('activate', () => {
  if (mainWindow) mainWindow.show()
})

// 真正退出时才允许关闭
app.on('before-quit', () => {
  app.isQuitting = true
})
```

### 坑 10: 外部链接在 Electron 内部打开

**现象**：点击一个 `<a href="https://..." target="_blank">` 链接，在 Electron 窗口内打开了（而不是系统浏览器）。

**解决方案**：拦截新窗口请求，用系统浏览器打开：

```typescript
// desktop/src/main/index.ts
win.webContents.setWindowOpenHandler(({ url }) => {
  shell.openExternal(url)  // 用系统默认浏览器打开
  return { action: 'deny' }  // 阻止 Electron 打开新窗口
})
```

---

## 附录 A: 核心文件速查表

| 文件 | 进程 | 职责 |
|------|------|------|
| `src/main/index.ts` | Main | 应用入口，窗口创建，生命周期管理 |
| `src/main/constants.ts` | Main | 路径/端口/服务定义常量 |
| `src/main/ipc-handlers.ts` | Main | IPC 请求处理注册中心 |
| `src/main/process-manager.ts` | Main | 后台服务进程管理（spawn/kill/restart） |
| `src/main/docker-manager.ts` | Main | Docker 容器管理 |
| `src/main/dependency-checker.ts` | Main | 系统依赖检测 |
| `src/main/health-monitor.ts` | Main | 服务健康状态轮询 |
| `src/main/env-manager.ts` | Main | .env 文件读写与验证 |
| `src/main/shell-env.ts` | Main | macOS Shell 环境变量解析 |
| `src/main/store.ts` | Main | JSON 持久化存储 |
| `src/main/tray-manager.ts` | Main | 系统托盘图标 + 菜单 |
| `src/preload/index.ts` | Preload | contextBridge 暴露安全 API |
| `src/shared/ipc-channels.ts` | 共享 | IPC 通道名常量 |
| `src/renderer/index.html` | Renderer | HTML 入口 |
| `src/renderer/main.tsx` | Renderer | React 挂载点 |
| `src/renderer/App.tsx` | Renderer | 根组件，路由/状态控制 |
| `src/renderer/env.d.ts` | Renderer | window.nexus 类型声明 |
| `electron.vite.config.ts` | 构建 | electron-vite 三段编译配置 |
| `electron-builder.yml` | 构建 | electron-builder 打包配置 |
| `tsconfig.node.json` | 构建 | Main + Preload 的 TypeScript 配置 |
| `tsconfig.web.json` | 构建 | Renderer 的 TypeScript 配置 |

## 附录 B: 命令速查

```bash
# 开发模式（热更新，自动打开 DevTools）
npm run dev

# 编译（不打包，产物在 out/ 目录）
npm run build

# 打包 macOS DMG
npm run build:mac

# 打包 Linux AppImage + deb
npm run build:linux

# 一键打包（使用项目根目录的脚本）
bash build-desktop.sh         # 自动检测平台
bash build-desktop.sh mac     # 指定 macOS
bash build-desktop.sh linux   # 指定 Linux
```

## 附录 C: 进一步学习资源

- [Electron 官方文档](https://www.electronjs.org/docs)
- [electron-vite 文档](https://electron-vite.org/)
- [electron-builder 文档](https://www.electron.build/)
- [Electron 安全最佳实践](https://www.electronjs.org/docs/latest/tutorial/security)
