/**
 * @file LogViewer.tsx
 * @description 实时日志查看器 — 展示后台服务的 stdout/stderr 输出
 *
 * 所有日志全量存储，通过 serviceFilter 在渲染时过滤。
 * 切换 tab 时无需重新订阅，即时显示对应服务的历史日志。
 */

import React, { useEffect, useMemo, useRef, useState } from 'react'

interface LogViewerProps {
  /** 初始日志列表 */
  initialLogs?: LogEntry[]
  /** 过滤指定服务（null 显示全部） */
  serviceFilter?: string | null
}

/** 服务颜色映射 */
const SERVICE_COLORS: Record<string, string> = {
  backend: 'text-blue-500',
  mcp: 'text-purple-500',
  poller: 'text-green-500',
  'job-trigger': 'text-orange-500'
}

const LogViewer: React.FC<LogViewerProps> = ({
  initialLogs = [],
  serviceFilter = null
}) => {
  const [allLogs, setAllLogs] = useState<LogEntry[]>(initialLogs)
  const [autoScroll, setAutoScroll] = useState(true)
  const containerRef = useRef<HTMLDivElement>(null)

  // 全量订阅实时日志（不做服务过滤）
  useEffect(() => {
    const unsubscribe = window.nexus.onLog((entry: LogEntry) => {
      setAllLogs((prev) => {
        const next = [...prev, entry]
        return next.length > 500 ? next.slice(-500) : next
      })
    })
    return unsubscribe
  }, [])

  // 渲染时按服务过滤
  const visibleLogs = useMemo(() => {
    if (!serviceFilter) return allLogs
    return allLogs.filter((e) => e.serviceId === serviceFilter)
  }, [allLogs, serviceFilter])

  // 自动滚动到底部
  useEffect(() => {
    if (autoScroll && containerRef.current) {
      containerRef.current.scrollTop = containerRef.current.scrollHeight
    }
  }, [visibleLogs, autoScroll])

  // 检测用户是否手动滚动
  const handleScroll = () => {
    if (!containerRef.current) return
    const { scrollTop, scrollHeight, clientHeight } = containerRef.current
    setAutoScroll(scrollHeight - scrollTop - clientHeight < 40)
  }

  const formatTime = (ts: number): string => {
    const d = new Date(ts)
    return d.toLocaleTimeString('en-US', { hour12: false })
  }

  const clearLogs = () => setAllLogs([])

  return (
    <div className="flex flex-col h-full">
      {/* 工具栏 */}
      <div className="flex items-center justify-between px-3 py-1.5 border-b border-gray-200 bg-gray-50">
        <span className="text-xs text-gray-500 font-medium">
          Logs ({visibleLogs.length})
        </span>
        <div className="flex items-center gap-2 titlebar-no-drag">
          <button
            onClick={clearLogs}
            className="text-[10px] text-gray-400 hover:text-gray-600 px-1.5 py-0.5 rounded hover:bg-gray-200 transition-colors"
          >
            Clear
          </button>
          <button
            onClick={() => {
              setAutoScroll(true)
              if (containerRef.current) {
                containerRef.current.scrollTop = containerRef.current.scrollHeight
              }
            }}
            className={`text-[10px] px-1.5 py-0.5 rounded transition-colors ${
              autoScroll
                ? 'text-blue-500 bg-blue-50'
                : 'text-gray-400 hover:text-gray-600 hover:bg-gray-200'
            }`}
          >
            Auto-scroll
          </button>
        </div>
      </div>

      {/* 日志内容 */}
      <div
        ref={containerRef}
        onScroll={handleScroll}
        className="flex-1 overflow-y-auto p-2 log-container bg-white"
      >
        {visibleLogs.length === 0 ? (
          <p className="text-xs text-gray-400 text-center py-4">No logs yet</p>
        ) : (
          visibleLogs.map((entry, i) => (
            <div
              key={i}
              className={`flex gap-2 py-0.5 ${
                entry.stream === 'stderr' ? 'text-red-600' : 'text-gray-700'
              }`}
            >
              <span className="text-gray-400 shrink-0">{formatTime(entry.timestamp)}</span>
              <span className={`shrink-0 ${SERVICE_COLORS[entry.serviceId] || 'text-gray-500'}`}>
                [{entry.serviceId}]
              </span>
              <span className="break-all">{entry.message}</span>
            </div>
          ))
        )}
      </div>
    </div>
  )
}

export default LogViewer
