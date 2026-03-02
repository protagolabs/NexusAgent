# NarraNexus Desktop

Electron 桌面应用 — 将 NarraNexus 打包为一键安装的 macOS DMG / Linux AppImage。

用户安装后打开 app → 填写 API Key → 点击 Apply → 自动完成所有环境配置和服务启动。

---

## 目录

- [快速开始](#快速开始)
- [使用指南](#使用指南)
- [打包分发](#打包分发)
- [架构设计](#架构设计)
- [源码更新与打包的关系](#源码更新与打包的关系)
- [需要改动打包配置的场景](#需要改动打包配置的场景)
- [目录结构](#目录结构)
- [常见问题](#常见问题)

---

## 快速开始

### 开发模式

```bash
cd desktop
npm install
npm run dev
```

会启动 Electron + Vite dev server，支持热重载。此模式下 `PROJECT_ROOT` 指向仓库根目录，直接读写本地文件。

### 打包

```bash
# 在项目根目录执行
bash build-desktop.sh          # 自动检测平台
bash build-desktop.sh mac      # macOS DMG
bash build-desktop.sh linux    # Linux AppImage/deb
```

产物位于 `desktop/dist/`。

---

## 使用指南

### 安装（macOS）

1. 双击 `.dmg` 文件，将 NarraNexus 拖入 Applications
2. 首次打开会被 Gatekeeper 拦截，解决方式：
   - **方式 A**：右键 NarraNexus.app → 打开 → 确认打开
   - **方式 B**：终端执行 `xattr -cr /Applications/NarraNexus.app`
   - **方式 C**：系统设置 → 隐私与安全性 → 允许任何来源（需先执行 `sudo spctl --master-disable`）

### 前置要求

用户机器上只需要预装一样东西：

- **Docker Desktop** — 用于运行 MySQL 数据库容器

其他所有依赖（uv、Python、Claude Code）由 app 自动安装。

### 首次使用流程

```
打开 NarraNexus.app
  │
  ├─ 显示 SetupWizard 配置页
  │   ├─ 填写 API Keys（OPENAI_API_KEY 必填）
  │   ├─ 数据库配置使用默认值即可
  │   └─ 点击 "Apply & 启动"
  │
  ├─ 自动执行 10 步安装（进度条实时展示）：
  │   ├─  1. 检测/安装 uv（Python 包管理器）
  │   ├─  2. 检测/安装 Claude Code + 验证登录
  │   ├─  3. uv sync（安装 Python 依赖）
  │   ├─  4. 检测 Docker（未安装则给出下载链接）
  │   ├─  5. docker compose up -d（启动 MySQL 容器）
  │   ├─  6. 等待 MySQL 就绪（最长 60 秒）
  │   ├─  7. 创建数据表（失败自动重试 5 次）
  │   ├─  8. 同步表结构
  │   ├─  9. 构建前端（如果 dist/ 不存在）
  │   └─ 10. 启动 4 个后台服务
  │
  └─ 自动切换到 Dashboard
      └─ 点击 "打开 NarraNexus" → 浏览器打开 http://localhost:8000
```

### Dashboard 功能

- **服务状态**：6 张卡片（MySQL、Backend、MCP、Poller、Job Trigger、Frontend）
- **日志查看**：底部日志区域，支持按服务 tab 切换（全部 / Backend / MCP / Poller / Job Trigger）
- **启动/停止**：一键启动或停止所有服务
- **端口冲突**：启动时如果检测到端口被占用，弹窗显示占用进程的名称和 PID，用户确认后终止
- **系统托盘**：关闭窗口时最小化到托盘，不退出 app
- **设置**：点击齿轮图标可重新修改 .env 配置

### 再次打开

首次配置完成后，后续打开 app 直接进入 Dashboard，不再显示 SetupWizard。

---

## 打包分发

### 打包流程

`build-desktop.sh` 按顺序执行 4 步：

```
1. npm install          → 安装 Electron 依赖
2. frontend: npm build  → 构建前端静态文件到 frontend/dist/
3. electron-vite build  → 编译 Electron 源码到 desktop/out/
4. electron-builder     → 打包为 DMG/AppImage，产物在 desktop/dist/
```

### 打包内容

`electron-builder.yml` 中 `extraResources` 将项目根目录打包到 app 的 `Resources/project/`：

```yaml
extraResources:
  - from: "../"              # 项目根目录
    to: "project"            # → Resources/project/
    filter:
      - "**/*"               # 包含一切
      - "!**/node_modules/**"  # 排除（运行时由 uv sync 安装 Python 依赖）
      - "!**/.venv/**"
      - "!**/.git/**"
      - "!**/desktop/**"       # 排除 desktop 自身
      - "!**/__pycache__/**"
      - "!**/*.pyc"
      # frontend/dist/ 不排除 → 预构建的前端会包含在内
```

### 分发给用户

将 `desktop/dist/NarraNexus-*.dmg` 发给用户即可。

---

## 架构设计

### 整体思路

```
┌─────────────────────────────────────────────────┐
│                 Electron App                     │
│                                                  │
│  ┌─────────┐  IPC   ┌──────────────────────┐    │
│  │Renderer │◄──────►│    Main Process       │    │
│  │(React)  │        │                        │    │
│  │         │        │  ┌──────────────────┐  │    │
│  │ Setup   │        │  │ ProcessManager   │  │    │
│  │ Wizard  │        │  │  spawn 4 服务     │  │    │
│  │         │        │  └───────┬──────────┘  │    │
│  │ Dash-   │        │  ┌──────┴──────────┐   │    │
│  │ board   │        │  │ HealthMonitor   │   │    │
│  │         │        │  │ DockerManager   │   │    │
│  │ Log     │        │  │ TrayManager     │   │    │
│  │ Viewer  │        │  │ EnvManager      │   │    │
│  └─────────┘        │  └────────────────┘    │    │
│                      └──────────────────────┘    │
└──────────────────────┬──────────────────────────┘
                       │ child_process.spawn
            ┌──────────┼──────────────┐
            ▼          ▼              ▼
     ┌──────────┐ ┌────────┐  ┌───────────┐
     │ Backend  │ │  MCP   │  │  Poller   │ ...
     │ :8000    │ │ :7801  │  │ (no port) │
     │          │ └────────┘  └───────────┘
     │ serves   │
     │ frontend │
     │ static   │
     └──────────┘
```

### 核心设计决策

#### 1. 后端 serve 前端静态文件

不再启动独立的前端 dev server（需要 Node.js 运行时）。改为：
- 打包时预构建 `frontend/dist/`
- `backend/main.py` 挂载 StaticFiles，所有非 API 请求回退到 `index.html`（SPA 路由）
- 用户访问 `http://localhost:8000` 即可看到前端

**好处**：用户机器上不需要安装 Node.js。

#### 2. 只读 → 可写项目目录

macOS `.app` 内部是只读文件系统，无法写入 `.env` 或 `.venv`。解决方案：

```
打包时：项目源码 → Resources/project/（只读）
首次启动：复制到 ~/Library/Application Support/NarraNexus/project/（可写）
后续启动：检测到 pyproject.toml 存在则跳过复制
```

关键代码（`constants.ts`）：
```typescript
export const BUNDLED_PROJECT_ROOT = app.isPackaged
  ? join(process.resourcesPath, 'project') : null

export const PROJECT_ROOT = app.isPackaged
  ? join(app.getPath('userData'), 'project')    // 可写位置
  : join(__dirname, '..', '..', '..')           // 开发模式：仓库根目录
```

#### 3. IPC 通道隔离

Preload 脚本不能引用 `electron.app`（会导致白屏崩溃）。因此 IPC 通道名定义在 `src/shared/ipc-channels.ts`（纯字符串常量，零依赖），main 和 preload 都从这里导入。

#### 4. 进程组管理

后台服务通过 `child_process.spawn` 启动，设置 `detached: true` 创建独立进程组。停止时用 `process.kill(-pid, signal)` 杀掉整个进程组，确保 `uv → python` 子进程链被完整清理。

#### 5. 端口冲突检测

启动服务前扫描所有服务端口（8000、7801），如果被占用：
- 用 `lsof` 获取占用进程的 PID 和名称
- 弹出系统原生对话框让用户确认是否终止
- 不检测 MySQL 3306（用户可能本地一直运行着数据库）

#### 6. 崩溃自动重启

`ProcessManager` 监听进程 `exit` 事件，非正常退出时自动重启：
- 指数退避：等待时间 = 1s × 2^(次数-1)
- 最多重启 3 次
- 手动重启时重置计数

### 进程间通信

```
Renderer (React)
    │  contextBridge.exposeInMainWorld('nexus', {...})
    ▼
Preload (ipc-channels.ts)
    │  ipcRenderer.invoke / ipcRenderer.on
    ▼
Main Process (ipc-handlers.ts)
    │  ipcMain.handle / mainWindow.webContents.send
    ▼
ProcessManager / DockerManager / HealthMonitor / EnvManager
```

| 方向 | 用途 | 示例 |
|------|------|------|
| Renderer → Main | 调用操作 | `startAllServices()`, `setEnv()`, `autoSetup()` |
| Main → Renderer | 推送事件 | `onLog()`, `onHealthUpdate()`, `onSetupProgress()` |

### 页面结构

| 页面 | 文件 | 何时显示 |
|------|------|---------|
| Loading | App.tsx | 检查 setupComplete 标志时 |
| SetupWizard | SetupWizard.tsx | 首次使用 / 点击设置 |
| Dashboard | Dashboard.tsx | 配置完成后 |

### Main Process 模块

| 模块 | 职责 |
|------|------|
| `index.ts` | 应用生命周期、窗口创建 |
| `constants.ts` | 路径、端口、服务定义、IPC 通道 |
| `process-manager.ts` | 服务进程启停、自动重启、一键安装 |
| `health-monitor.ts` | TCP/HTTP 健康轮询 |
| `docker-manager.ts` | Docker Compose 容器管理 |
| `tray-manager.ts` | 系统托盘菜单 |
| `ipc-handlers.ts` | IPC 注册中心 |
| `env-manager.ts` | .env 文件读写 |
| `dependency-checker.ts` | 系统依赖检测 |
| `store.ts` | 持久化存储（setupComplete 等） |

---

## 源码更新与打包的关系

### 核心结论

> **外部 `src/`、`backend/`、`frontend/` 代码更新后，只需重新执行 `bash build-desktop.sh`，新代码会自动包含在 DMG 中。无需修改任何打包配置。**

### 原理

```
build-desktop.sh 执行时：
  1. npm run build (frontend/)     → 重新构建前端 → frontend/dist/ 更新
  2. electron-vite build           → 编译 Electron 源码 → desktop/out/ 更新
  3. electron-builder              → 打包时读取 extraResources 规则：
     from: "../" → to: "project"
     把项目根目录（含最新的 src/、backend/、frontend/dist/）
     全部复制到 app 的 Resources/project/ 中
```

所以：

| 你改了什么 | 需要做什么 | 需要改打包配置吗 |
|-----------|-----------|:---------------:|
| `src/` 下的 Python 代码 | 重新 `bash build-desktop.sh` | 否 |
| `backend/` 路由/逻辑 | 重新 `bash build-desktop.sh` | 否 |
| `frontend/` 组件/页面 | 重新 `bash build-desktop.sh` | 否 |
| `pyproject.toml` 依赖 | 重新 `bash build-desktop.sh` | 否 |
| `.env.example` 字段 | 重新 `bash build-desktop.sh` | 否 |
| `docker-compose.yaml` | 重新 `bash build-desktop.sh` | 否 |

### 用户侧更新

**注意**：用户安装新版 DMG 后，`~/Library/Application Support/NarraNexus/project/` 不会自动更新（因为 `ensureWritableProject()` 检测到 `pyproject.toml` 已存在就跳过复制）。

如需强制更新，用户需要删除该目录后重新打开 app，或者我们后续实现增量更新机制。

---

## 需要改动打包配置的场景

以下情况需要修改 `desktop/` 中的代码：

### 1. 新增后台服务进程

修改 `constants.ts` 的 `SERVICES` 数组：

```typescript
// 例如新增一个 scheduler 服务
{
  id: 'scheduler',
  label: 'Scheduler',
  command: 'uv',
  args: ['run', 'python', '-m', 'xyz_agent_context.services.scheduler'],
  port: null,           // 无端口则填 null
  healthUrl: null,
  order: 5              // 启动顺序
}
```

同时更新 `Dashboard.tsx` 中的服务卡片列表和 `LOG_TABS`。

### 2. 新增需要排除的大文件目录

修改 `electron-builder.yml` 的 `filter`：

```yaml
filter:
  - "!**/new_large_dir/**"
```

### 3. 修改 Electron UI

修改 `desktop/src/renderer/` 或 `desktop/src/main/` 下的文件，然后重新打包。

### 4. 新增 IPC 通道

1. `src/shared/ipc-channels.ts` — 添加通道名常量
2. `src/main/ipc-handlers.ts` — 注册 handler
3. `src/preload/index.ts` — 暴露给 renderer
4. `src/renderer/env.d.ts` — 添加类型声明

---

## 目录结构

```
desktop/
├── src/
│   ├── main/                    # Electron 主进程
│   │   ├── index.ts             # 应用入口、窗口创建、生命周期
│   │   ├── constants.ts         # 路径、端口、服务定义
│   │   ├── process-manager.ts   # 服务进程管理 + 一键安装
│   │   ├── health-monitor.ts    # 健康状态轮询
│   │   ├── docker-manager.ts    # Docker Compose 管理
│   │   ├── tray-manager.ts      # 系统托盘
│   │   ├── ipc-handlers.ts      # IPC 注册中心
│   │   ├── env-manager.ts       # .env 读写
│   │   ├── dependency-checker.ts # 依赖检测
│   │   └── store.ts             # 持久化存储
│   ├── preload/
│   │   └── index.ts             # contextBridge，暴露 nexus API
│   ├── shared/
│   │   └── ipc-channels.ts      # IPC 通道名（纯常量，无 electron 依赖）
│   └── renderer/                # React 前端
│       ├── App.tsx              # 路由：loading → setup → dashboard
│       ├── env.d.ts             # 全局类型声明
│       ├── pages/
│       │   ├── SetupWizard.tsx  # 配置页：.env 表单 + 安装进度
│       │   └── Dashboard.tsx    # 主面板：服务状态 + 日志 + 操作
│       ├── components/
│       │   ├── ServiceCard.tsx  # 服务状态卡片
│       │   ├── LogViewer.tsx    # 实时日志查看器
│       │   └── StepIndicator.tsx
│       └── styles/
│           └── index.css        # Tailwind + 自定义样式
├── resources/                   # 图标等静态资源
├── electron-builder.yml         # 打包配置
├── electron.vite.config.ts      # electron-vite 配置
├── package.json
├── tsconfig.json                # 总配置
├── tsconfig.node.json           # Main/Preload 编译配置
├── tsconfig.web.json            # Renderer 编译配置
├── tailwind.config.js
└── postcss.config.js
```

---

## 常见问题

### Q: 用户安装后打开白屏？
检查 preload 是否引用了 `electron.app`。Preload 只能从 `src/shared/` 导入，不能从 `src/main/` 导入。

### Q: 用户报 EROFS 错误（只读文件系统）？
确保所有文件写入操作使用 `PROJECT_ROOT`（可写目录），不要写入 `BUNDLED_PROJECT_ROOT`（只读）。

### Q: MySQL 建表失败？
首次启动时 MySQL 端口通了但可能还在初始化。当前已有重试机制（5 次，间隔 5 秒）。

### Q: 服务停不掉？
确保使用 `detached: true` 创建进程组，停止时用 `process.kill(-pid)` 杀掉整组。

### Q: 用户需要更新到新版本？
目前需要删除 `~/Library/Application Support/NarraNexus/project/` 后重新安装。后续可实现版本检测 + 增量更新。

### Q: 打包后 Docker 命令找不到？
Electron 打包后 PATH 很短，确保 `docker-manager.ts` 和 `process-manager.ts` 都使用增强 PATH（包含 `/usr/local/bin`、`/opt/homebrew/bin`）。
