"""
@file_name: agents.py
@author: NetMind.AI
@date: 2025-11-28
@description: Agent 路由聚合器

将各功能域的子路由整合到统一的 /api/agents 前缀下：
- Awareness（自我意识）
- Social Network（社交网络）
- Chat History（聊天历史）
- Files（工作空间文件）
- MCPs（MCP 管理）
- RAG（RAG 文件管理）
"""

from fastapi import APIRouter

from backend.routes.agents_awareness import router as awareness_router
from backend.routes.agents_social_network import router as social_network_router
from backend.routes.agents_chat_history import router as chat_history_router
from backend.routes.agents_files import router as files_router
from backend.routes.agents_mcps import router as mcps_router
from backend.routes.agents_rag import router as rag_router


router = APIRouter()

router.include_router(awareness_router)
router.include_router(social_network_router)
router.include_router(chat_history_router)
router.include_router(files_router)
router.include_router(mcps_router)
router.include_router(rag_router)
