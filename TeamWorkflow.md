# NarraNexus 团队开发流程

---

## 一、仓库与协作模型

我们采用 **双仓库** 结构：

| 仓库 | 地址 | 用途 |
|------|------|------|
| **upstream**（开源） | `github.com/protagolabs/NarraNexus` (public) | 对外发布、接受社区 PR |
| **origin**（内部 fork） | 同 org 下的内部 fork (private) | 日常开发，所有 feature 分支在此推送 |

PS：内部 fork 也是 public 的仓库的。所以也要注意一下安全什么的。

### 配置 Remote

```bash
# Clone internal repo
git clone git@github.com:protagolabs/<internal-repo-name>.git
cd <internal-repo-name>

# Add upstream (public) remote
git remote add upstream git@github.com:protagolabs/NarraNexus.git

# Verify
git remote -v
# origin   git@github.com:protagolabs/<internal-repo-name>.git (fetch/push)
# upstream git@github.com:protagolabs/NarraNexus.git (fetch/push)
```

### 个人开发工作同步流程

```
Developer → clone origin (internal fork) → periodic PR → upstream (public repo)
```

PS：一般来说，我们创建 branch 的时候是基于 main 去创建。

- 日常开发：在 **origin** 上创建分支、推送、发起 PR （工作完成后，就自行发起向 main branch 的 PR）
- origin/main 定期 merge：暂定每周四，会统一完成本周发起的 PR
- 定期合并：由内部讨论确认后，定期统一从 origin/main 向 upstream/main 发起 PR
- 同步上游：`git fetch upstream && git merge upstream/main`

---

## 二、环境搭建

### 前置要求

- Python 3.13+
- [uv](https://docs.astral.sh/uv/)（Python 包管理）
- Node.js 18+（前端开发）
- Docker（MySQL 容器）
- Git

### Step-by-Step

```bash
# 1. Clone repo
git clone git@github.com:protagolabs/<internal-repo-name>.git
cd <internal-repo-name>

# 2. Install Python dependencies
uv sync

# 3. Configure environment variables
cp .env.example .env
# Edit .env — fill in API keys, DB config, etc.

# 4. Start MySQL (Docker)
docker run -d \
  --name nexus-mysql \
  -e MYSQL_ROOT_PASSWORD=your_password \
  -e MYSQL_DATABASE=nexus_agent \
  -p 3306:3306 \
  mysql:8.0

# 5. Create database tables
uv run python src/xyz_agent_context/utils/database_table_management/create_all_tables.py

# 6. Start backend services (4 terminals)
# Terminal 1: MCP servers
uv run python src/xyz_agent_context/module/module_runner.py mcp

# Terminal 2: FastAPI server
uv run uvicorn backend.main:app --reload --port 8000

# Terminal 3: ModulePoller (instance polling)
uv run python -m xyz_agent_context.services.module_poller

# Terminal 4: Job scheduler
uv run python -m xyz_agent_context.module.job_module.job_trigger --interval 60

# 7. Start frontend
cd frontend && npm install && npm run dev

# 8. Verify
# Backend: visit http://localhost:8000/docs — you should see Swagger UI
# Frontend: visit http://localhost:5173 — you should see the page
```

---

## 三、版本号规范

采用 [Semantic Versioning](https://semver.org/)：**MAJOR.MINOR.PATCH**

| 级别 | 何时递增 | 示例 |
|------|---------|------|
| **MAJOR** | 架构级重构或里程碑发布（`0→1` 代表首个生产可用版本） | `1.0.0` |
| **MINOR** | 新 Module / 新功能 / Schema 变更 | `0.2.0` |
| **PATCH** | Bug 修复 / 性能优化 / 文档完善 | `0.1.1` |

### 规则

- **`0.x.x` 阶段**：MINOR 递增允许 breaking change（我们当前处于此阶段）
- **单一真相源**：版本号定义在 `pyproject.toml` 的 `version` 字段
- **前端同步**：`frontend/package.json` 的 `version` 手动保持一致
- **何时 bump**：版本号在合入 `main` 时更新，**不在 feature 分支上改**
- **发版 commit**：单独一个 `[release] v0.x.x` 的 commit

---

## 四、Branch 命名规范

格式：`<type>/<YYYYMMDD>_<short_description>`

| 类型 | 场景 | 示例 |
|------|------|------|
| `feat/` | 新功能 | `feat/20260301_calendar_module` |
| `fix/` | Bug 修复 | `fix/20260301_narrative_duplicate` |
| `hotfix/` | 生产紧急修复 | `hotfix/20260301_db_connection_leak` |
| `refactor/` | 重构 | `refactor/20260301_repository_pattern` |
| `docs/` | 仅文档 | `docs/20260301_api_reference` |
| `chore/` | 构建/依赖/CI | `chore/20260301_upgrade_pydantic` |
| `release/` | 发版准备 | `release/v0.2.0` |

- feat, fix 和 refactor 分支，必须有新建的 branch。

### 注意

- 日期使用创建分支的日期，格式 `YYYYMMDD`
- 描述部分使用 `snake_case`，简洁明了
- `release/` 分支直接用版本号，不加日期

---

## 五、Commit 规范

格式：`[tag] 描述`

- **描述语言**：英文
- **语气**：祈使语气（imperative mood）
- **长度**：≤ 72 字符
- **每个 commit 只做一件事**

### Tag 列表

| Tag | 用途 | 示例 |
|-----|------|------|
| `[feat]` | 新功能 | `[feat] Add calendar module hook_data_gathering` |
| `[fix]` | Bug 修复 | `[fix] Prevent duplicate narrative creation` |
| `[refactor]` | 重构（不改变行为） | `[refactor] Extract instance lifecycle to service` |
| `[docs]` | 文档 | `[docs] Add Google-style docstrings to repository` |
| `[style]` | 格式（不改变逻辑） | `[style] Fix import ordering in module package` |
| `[test]` | 测试 | `[test] Add narrative vector search test` |
| `[perf]` | 性能优化 | `[perf] Batch DB queries in hook_data_gathering` |
| `[chore]` | 杂务（依赖、CI 等） | `[chore] Bump pydantic to 2.13` |
| `[release]` | 发版 | `[release] v0.2.0` |

### Breaking Change

如果 commit 包含破坏性变更，在描述末尾加 `(BREAKING)`：

```
[feat] Rename agent_message.content to agent_message.body (BREAKING)
```

---

## 六、日常开发流程

### 6.1 开始一个任务

```bash
# 1. Make sure main is up to date
git checkout main
git pull origin main

# 2. Create feature branch
git checkout -b feat/20260225_calendar_module

# 3. Develop... commit...
git add <files>
git commit -m "[feat] Add calendar module with hook_data_gathering"

# 4. Push to origin
git push origin feat/20260225_calendar_module
```

### 6.2 PR 前检查清单

在发起 PR 之前，确保以下检查通过：

```bash
# Import check (catch circular imports, missing modules)
uv run python -c "import xyz_agent_context.module; import xyz_agent_context.narrative; import xyz_agent_context.services; print('OK')"

# Frontend build (if frontend changed)
cd frontend && npm run build

# Table schema sync (if schema changed)
cd src/xyz_agent_context/utils/database_table_management
uv run python sync_all_tables.py --dry-run

# Verify no secrets are staged
git diff --cached --name-only | grep -E '\.(env|key|pem|credentials)' && echo "WARNING: Possible sensitive files!" || echo "OK"
```

### 6.3 PR 标题和描述

- **标题格式**：`[tag] 简要描述`，与 commit 格式一致
- **描述**：使用 PR 模板（`.github/pull_request_template.md`）
- 所有 PR 目标分支为 `main`

### 6.4 Code Review 流程

1. 提交 PR 后，在算法 only 群，通知进展，等待 review。
2. 至少 **1 位 maintainer** approve 后方可合并
3. Review 中的 conversation 必须全部 resolved 才能合并
4. 使用 **Squash and Merge** 合并（保持 main 历史清晰）

### 6.5 合并节奏

- **内部仓库**：PR 通过 review 后随时合并
- **开源仓库**：每周四由 maintainer 统一从内部仓库向上游发起 PR

### 6.6 分支清理

- 合并后的分支保留 **7 天**，之后删除
- GitHub 设置中开启 "Automatically delete head branches"

---

## 七、代码规范

> 完整的架构规范、目录结构、设计模式详见 [`CLAUDE.md`](./CLAUDE.md)。
> 本节仅列出日常编码中最常用的规范。

### 7.1 文件头

每个新建的 Python 文件必须包含：

```python
"""
@file_name: example_service.py
@author: Your Name
@date: 2026-02-25
@description: Example service for handling XXX logic

Extended description if needed...
"""
```

### 7.2 Docstring（Google-style）

**类的 Docstring：**

```python
class NarrativeSelector:
    """Narrative selector that picks the best Narrative by semantic matching.

    Computes embedding similarity to find the most relevant candidate Narrative.
    Supports topic-continuity detection and automatic Narrative creation.

    Attributes:
        db_client: Async database client.
        embedding_util: Embedding vector utility.
    """
```

**方法的 Docstring：**

```python
async def select(self, agent_id: str) -> Tuple[List[Narrative], Optional[List[float]]]:
    """Select the most suitable Narratives.

    Workflow:
    1. Detect topic continuity
    2. Match by vector similarity or create a new Narrative

    Args:
        agent_id: Unique identifier of the Agent.

    Returns:
        Tuple of (list of Narratives, query_embedding).
        The Narrative list is empty when no match is found.

    Raises:
        DatabaseError: Raised when the database query fails.
    """
```

### 7.3 命名规范

| 类型 | 规范 | 示例 |
|------|------|------|
| 类名 | PascalCase | `AgentRuntime`, `ChatModule` |
| 函数/方法 | snake_case | `hook_data_gathering`, `get_by_id` |
| 变量 | snake_case | `agent_id`, `ctx_data` |
| 常量 | UPPER_SNAKE_CASE | `MODULE_MAP`, `MAX_RETRIES` |
| 私有包 | `_` 前缀 | `_module_impl/`, `_narrative_impl/` |
| ID 生成 | 前缀 + 8 位随机 | `evt_a1b2c3d4`, `nar_e5f6g7h8` |

### 7.4 Import 顺序

```python
# 1. Standard library
import os
import json
from typing import Optional, List

# 2. Third-party packages
from pydantic import BaseModel
from loguru import logger

# 3. Project top-level packages
from xyz_agent_context.schema.narrative import Narrative
from xyz_agent_context.repository.base import BaseRepository

# 4. Relative imports (same package)
from .models import EventData
from ._event_impl.processor import EventProcessor
```

各组之间空一行。同组内按字母排序。

### 7.5 语言规则

- **代码文件中的一切**（注释、Docstring、变量名）：**英文**
- **Commit message**：英文
- **内部文档**（如本文档）：中文
- **开源文档**（README、CONTRIBUTING 等）：英文

---

## 八、常见陷阱

| Don't | Do |
|-------|-----|
| 从 `_module_impl/` 外部导入其私有内容 | 从 `module/` 的公开 API 导入 |
| 直接 `os.getenv()` 读环境变量 | `from xyz_agent_context.settings import settings` |
| Module A 导入 Module B | 共享逻辑放 `schema/` 或 `utils/` |
| 在 Prompt 里写死具体场景（如销售话术） | 场景逻辑放在 Awareness 中定义 |
| 在 Narrative 里存储记忆内容 | Narrative 只做路由元数据；记忆存在 Module 自己的 DB 表 |
| 添加向后兼容 shim / deprecated 代码 | 直接修改，不做兼容 |
| `git push --force` 到 main | 永远不要 |
| 在 feature 分支上 bump 版本号 | 版本号只在合入 main 时修改 |
| 一个 commit 混合多个不相关的改动 | 一个 commit 做一件事 |
| 提交 `.env`、API Key 等敏感信息 | 使用 `.env.example` 作为模板，`.env` 在 `.gitignore` 中 |
| 表管理脚本被应用代码 import | 表管理脚本（`modify_*_table.py`）保持独立 |

---

## 九、快速参考卡片

```
分支命名    <type>/<YYYYMMDD>_<description>
Commit     [tag] English description in imperative mood (≤72 chars)
PR 标题     [tag] 简要描述
版本号      MAJOR.MINOR.PATCH（定义在 pyproject.toml）
合并方式    Squash and Merge
注释语言    中文
Commit 语言 英文
```
