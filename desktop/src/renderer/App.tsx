/**
 * @file App.tsx
 * @description 应用根组件 — 根据安装状态显示 SetupWizard 或 Dashboard
 */

import React, { useEffect, useState } from 'react'
import SetupWizard from './pages/SetupWizard'
import Dashboard from './pages/Dashboard'

type Page = 'loading' | 'setup' | 'dashboard'

const App: React.FC = () => {
  const [page, setPage] = useState<Page>('loading')

  useEffect(() => {
    // 检查是否已完成初始设置
    window.nexus.getSetupState().then(({ setupComplete }) => {
      setPage(setupComplete ? 'dashboard' : 'setup')
    })
  }, [])

  // 设置完成后切换到 Dashboard
  const handleSetupComplete = () => {
    setPage('dashboard')
  }

  // 从 Dashboard 返回设置页
  const handleOpenSettings = () => {
    setPage('setup')
  }

  if (page === 'loading') {
    return (
      <div className="flex items-center justify-center h-screen bg-gray-50">
        <div className="flex flex-col items-center gap-3">
          <div className="w-8 h-8 border-2 border-blue-500 border-t-transparent rounded-full animate-spin" />
          <p className="text-sm text-gray-500">Loading...</p>
        </div>
      </div>
    )
  }

  if (page === 'setup') {
    return <SetupWizard onComplete={handleSetupComplete} />
  }

  return <Dashboard onOpenSettings={handleOpenSettings} />
}

export default App
