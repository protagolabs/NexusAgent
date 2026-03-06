"""
@file_name: agents_rag.py
@author: NetMind.AI
@date: 2025-11-28
@description: Agent RAG 文件管理路由

Provides endpoints for:
- GET /{agent_id}/rag-files - 列出 RAG 文件及状态
- POST /{agent_id}/rag-files - 上传文件到 RAG 存储
- DELETE /{agent_id}/rag-files/{filename} - 删除 RAG 文件
"""

import asyncio

from fastapi import APIRouter, Query, UploadFile, File
from loguru import logger

from xyz_agent_context.module.gemini_rag_module.rag_file_service import RAGFileService
from xyz_agent_context.schema import (
    RAGFileInfo,
    RAGFileListResponse,
    RAGFileUploadResponse,
    RAGFileDeleteResponse,
)


router = APIRouter()


@router.get("/{agent_id}/rag-files", response_model=RAGFileListResponse)
async def list_rag_files(
    agent_id: str,
    user_id: str = Query(..., description="User ID"),
):
    """列出 Agent-User 对的全部 RAG 文件及上传状态"""
    logger.info(f"Listing RAG files for agent: {agent_id}, user: {user_id}")

    try:
        files_data = RAGFileService.list_files(agent_id, user_id)
        stats = RAGFileService.get_stats(agent_id, user_id)

        files = [RAGFileInfo(**f) for f in files_data]

        return RAGFileListResponse(
            success=True,
            files=files,
            total_count=stats["total_count"],
            completed_count=stats["completed_count"],
            pending_count=stats["pending_count"],
        )

    except Exception as e:
        logger.error(f"Error listing RAG files: {e}")
        return RAGFileListResponse(success=False, error=str(e))


@router.post("/{agent_id}/rag-files", response_model=RAGFileUploadResponse)
async def upload_rag_file(
    agent_id: str,
    user_id: str = Query(..., description="User ID"),
    file: UploadFile = File(..., description="File to upload to RAG store"),
):
    """上传文件到 RAG 临时目录并触发 Gemini 存储上传"""
    logger.info(f"Uploading RAG file '{file.filename}' for agent: {agent_id}, user: {user_id}")

    # 支持的文件格式
    SUPPORTED_EXTENSIONS = {'.txt', '.md', '.pdf'}

    filename_lower = file.filename.lower() if file.filename else ""
    file_ext = None
    for ext in SUPPORTED_EXTENSIONS:
        if filename_lower.endswith(ext):
            file_ext = ext
            break

    if not file_ext:
        logger.warning(f"Rejected unsupported file format: {file.filename}")
        return RAGFileUploadResponse(
            success=False,
            error=f"Unsupported file format. Only {', '.join(sorted(SUPPORTED_EXTENSIONS))} are supported."
        )

    try:
        content = await file.read()
        logger.info(f"Uploading RAG file content: {content[:100]}...")
        file_size = len(content)

        filepath = RAGFileService.save_file(agent_id, user_id, file.filename, content)

        RAGFileService.update_file_status(
            agent_id, user_id, file.filename, "pending",
            extra={"saved_at": str(filepath.stat().st_mtime)}
        )

        # 触发后台上传任务
        asyncio.create_task(
            RAGFileService.upload_to_gemini_store(agent_id, user_id, str(filepath), file.filename)
        )

        return RAGFileUploadResponse(
            success=True,
            filename=file.filename,
            size=file_size,
            upload_status="pending",
        )

    except Exception as e:
        logger.error(f"Error uploading RAG file: {e}")
        return RAGFileUploadResponse(success=False, error=str(e))


@router.delete("/{agent_id}/rag-files/{filename}", response_model=RAGFileDeleteResponse)
async def delete_rag_file(
    agent_id: str,
    filename: str,
    user_id: str = Query(..., description="User ID"),
):
    """
    删除 RAG 临时目录中的文件

    注意：不会从 Gemini store 中删除文件（Gemini 不支持删除）。
    """
    logger.info(f"Deleting RAG file '{filename}' for agent: {agent_id}, user: {user_id}")

    try:
        deleted = RAGFileService.delete_file(agent_id, user_id, filename)

        if not deleted:
            return RAGFileDeleteResponse(
                success=False,
                error=f"File not found: {filename}"
            )

        return RAGFileDeleteResponse(success=True, filename=filename)

    except Exception as e:
        logger.error(f"Error deleting RAG file: {e}")
        return RAGFileDeleteResponse(success=False, error=str(e))
