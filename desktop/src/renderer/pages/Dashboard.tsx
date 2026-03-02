/**
 * @file Dashboard.tsx
 * @description 主控制面板 — 服务状态展示、启动/停止、日志查看
 *
 * 日志区域支持按服务 tab 切换，前端状态由 Backend 健康状态派生。
 */

import React, { useCallback, useEffect, useState } from 'react'
import ServiceCard from '../components/ServiceCard'
import LogViewer from '../components/LogViewer'
import AsciiBanner from '../components/AsciiBanner'

interface DashboardProps {
  onOpenSettings: () => void
}

/** 固定的日志 tab 前缀（All 始终在最前） */
const LOG_TAB_ALL = { id: null as string | null, label: 'All' }

const Dashboard: React.FC<DashboardProps> = ({ onOpenSettings }) => {
  const [services, setServices] = useState<ProcessInfo[]>([])
  const [health, setHealth] = useState<OverallHealth | null>(null)
  const [starting, setStarting] = useState(false)
  const [stopping, setStopping] = useState(false)
  const [logs, setLogs] = useState<LogEntry[]>([])
  const [logFilter, setLogFilter] = useState<string | null>(null)

  // 加载初始状态
  const refreshStatus = useCallback(async () => {
    const [svcStatus, healthStatus, initialLogs] = await Promise.all([
      window.nexus.getServiceStatus(),
      window.nexus.getHealthStatus(),
      window.nexus.getLogs()
    ])
    setServices(svcStatus)
    setHealth(healthStatus)
    setLogs(initialLogs)
  }, [])

  useEffect(() => {
    refreshStatus()
  }, [refreshStatus])

  // 订阅实时健康状态
  useEffect(() => {
    const unsubscribe = window.nexus.onHealthUpdate((status: OverallHealth) => {
      setHealth(status)
    })
    return unsubscribe
  }, [])

  // 定期刷新服务状态
  useEffect(() => {
    const interval = setInterval(async () => {
      const svcStatus = await window.nexus.getServiceStatus()
      setServices(svcStatus)
    }, 3000)
    return () => clearInterval(interval)
  }, [])

  // 监听托盘操作
  useEffect(() => {
    const unsubscribe = window.nexus.onTrayAction(async (action: string) => {
      if (action === 'start-all') await handleStartAll()
      if (action === 'stop-all') await handleStopAll()
      if (action === 'quit') {
        await handleStopAll()
      }
    })
    return unsubscribe
  }, [])

  const handleStartAll = async () => {
    setStarting(true)
    await window.nexus.startDocker()
    await new Promise((r) => setTimeout(r, 3000))
    await window.nexus.startAllServices()
    setStarting(false)
    refreshStatus()
  }

  const handleStopAll = async () => {
    setStopping(true)
    await window.nexus.stopAllServices()
    await window.nexus.stopDocker()
    setStopping(false)
    refreshStatus()
  }

  const handleRestart = async (serviceId: string) => {
    await window.nexus.restartService(serviceId)
    refreshStatus()
  }

  const handleOpenApp = () => {
    window.nexus.openExternal('http://localhost:8000')
  }

  // 合并健康检查和进程状态，确定每个卡片的显示状态
  const getCardStatus = (serviceId: string) => {
    const proc = services.find((s) => s.serviceId === serviceId)
    const svcHealth = health?.services.find((s) => s.serviceId === serviceId)

    if (svcHealth?.state === 'healthy') return 'healthy'
    if (proc?.status === 'running') return 'running'
    if (proc?.status === 'starting') return 'starting'
    if (proc?.status === 'crashed') return 'crashed'
    return 'stopped'
  }

  const getCardPort = (serviceId: string) => {
    return health?.services.find((s) => s.serviceId === serviceId)?.port ?? null
  }

  // Frontend 状态：由 Backend 健康状态派生（Backend serve 前端静态文件）
  const frontendStatus = getCardStatus('backend')

  // 检查是否有任何服务在运行
  const anyRunning = services.some(
    (s) => s.status === 'running' || s.status === 'starting'
  )

  // 日志 tab 从 services 动态派生
  const logTabs = [LOG_TAB_ALL, ...services.map((s) => ({ id: s.serviceId, label: s.label }))]

  return (
    <div className="flex flex-col h-screen bg-gray-50">
      {/* 标题栏 */}
      <div className="titlebar-drag shrink-0 flex items-center justify-center pt-8 pb-2 bg-white border-b border-gray-100">
        <AsciiBanner size="small" />
      </div>

      {/* 主内容 */}
      <div className="flex-1 flex flex-col overflow-hidden">
        {/* 基础设施状态 */}
        <div className="px-5 pt-4 pb-1">
          <h2 className="text-xs font-semibold text-gray-500 uppercase tracking-wider mb-2">
            Infrastructure
          </h2>
          <div className="grid grid-cols-5 gap-2">
            {(health?.infrastructure ?? []).map((infra) => (
              <ServiceCard
                key={infra.serviceId}
                label={infra.label}
                status={infra.state === 'healthy' ? 'healthy' : 'stopped'}
                port={infra.port}
              />
            ))}
          </div>
        </div>

        {/* 应用服务状态 */}
        <div className="px-5 pt-2 pb-2">
          <h2 className="text-xs font-semibold text-gray-500 uppercase tracking-wider mb-2">
            Services
          </h2>
          <div className="grid grid-cols-4 gap-2">
            {/* 应用服务（从 getServiceStatus 动态渲染） */}
            {services.map((svc) => (
              <ServiceCard
                key={svc.serviceId}
                label={svc.label}
                status={getCardStatus(svc.serviceId)}
                port={getCardPort(svc.serviceId)}
                message={svc.lastError ?? undefined}
                onRestart={() => handleRestart(svc.serviceId)}
              />
            ))}

            {/* Frontend（由 Backend :8000 提供，状态跟随 Backend） */}
            <ServiceCard
              label="Frontend"
              status={frontendStatus}
              port={frontendStatus === 'stopped' ? null : 8000}
              message="Served by Backend"
            />
          </div>
        </div>

        {/* 操作按钮 */}
        <div className="flex items-center gap-2 px-5 py-3">
          <button
            onClick={handleOpenApp}
            disabled={!anyRunning}
            className="titlebar-no-drag flex-1 px-4 py-2.5 text-sm font-medium text-white bg-blue-500 rounded-lg hover:bg-blue-600 disabled:opacity-40 disabled:cursor-not-allowed transition-colors"
          >
            Open NarraNexus
          </button>

          {!anyRunning ? (
            <button
              onClick={handleStartAll}
              disabled={starting}
              className="titlebar-no-drag px-4 py-2.5 text-sm font-medium text-green-700 bg-green-50 rounded-lg hover:bg-green-100 disabled:opacity-50 transition-colors"
            >
              {starting ? 'Starting...' : 'Start All'}
            </button>
          ) : (
            <button
              onClick={handleStopAll}
              disabled={stopping}
              className="titlebar-no-drag px-4 py-2.5 text-sm font-medium text-red-700 bg-red-50 rounded-lg hover:bg-red-100 disabled:opacity-50 transition-colors"
            >
              {stopping ? 'Stopping...' : 'Stop All'}
            </button>
          )}

          <button
            onClick={onOpenSettings}
            className="titlebar-no-drag p-2.5 text-gray-400 hover:text-gray-600 hover:bg-gray-200 rounded-lg transition-colors"
            title="Settings"
          >
            <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor">
              <path
                strokeLinecap="round"
                strokeLinejoin="round"
                strokeWidth={2}
                d="M10.325 4.317c.426-1.756 2.924-1.756 3.35 0a1.724 1.724 0 002.573 1.066c1.543-.94 3.31.826 2.37 2.37a1.724 1.724 0 001.066 2.573c1.756.426 1.756 2.924 0 3.35a1.724 1.724 0 00-1.066 2.573c.94 1.543-.826 3.31-2.37 2.37a1.724 1.724 0 00-2.573 1.066c-.426 1.756-2.924 1.756-3.35 0a1.724 1.724 0 00-2.573-1.066c-1.543.94-3.31-.826-2.37-2.37a1.724 1.724 0 00-1.066-2.573c-1.756-.426-1.756-2.924 0-3.35a1.724 1.724 0 001.066-2.573c-.94-1.543.826-3.31 2.37-2.37.996.608 2.296.07 2.572-1.065z"
              />
              <path
                strokeLinecap="round"
                strokeLinejoin="round"
                strokeWidth={2}
                d="M15 12a3 3 0 11-6 0 3 3 0 016 0z"
              />
            </svg>
          </button>
        </div>

        {/* 日志区域：tab 切换 + 日志内容 */}
        <div className="flex-1 mx-5 mb-4 border border-gray-200 rounded-lg overflow-hidden bg-white flex flex-col">
          {/* 服务 tab 栏 */}
          <div className="flex items-center gap-1 px-2 pt-1.5 pb-0 bg-gray-50 border-b border-gray-200">
            {logTabs.map((tab) => (
              <button
                key={tab.id ?? '__all'}
                onClick={() => setLogFilter(tab.id)}
                className={`titlebar-no-drag text-[11px] px-2.5 py-1 rounded-t transition-colors ${
                  logFilter === tab.id
                    ? 'bg-white text-gray-800 font-medium border border-gray-200 border-b-white -mb-px'
                    : 'text-gray-500 hover:text-gray-700 hover:bg-gray-100'
                }`}
              >
                {tab.label}
              </button>
            ))}
          </div>

          {/* 日志内容 */}
          <div className="flex-1 overflow-hidden">
            <LogViewer initialLogs={logs} serviceFilter={logFilter} />
          </div>
        </div>
      </div>
    </div>
  )
}

export default Dashboard
