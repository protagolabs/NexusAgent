/**
 * @file AsciiBanner.tsx
 * @description 渐变 ASCII Banner 组件 — 从 run.sh 提取的品牌标识
 *
 * 支持两种尺寸：
 * - normal: SetupWizard 使用，完整大小 + 副标题
 * - small: Dashboard 标题栏使用，缩小紧凑版 + 副标题
 */

import React from 'react'

interface AsciiBannerProps {
  size?: 'normal' | 'small'
}

const ASCII_ART = `    ███╗   ██╗ █████╗ ██████╗ ██████╗  █████╗ ███╗   ██╗███████╗██╗  ██╗██╗   ██╗███████╗
    ████╗  ██║██╔══██╗██╔══██╗██╔══██╗██╔══██╗████╗  ██║██╔════╝╚██╗██╔╝██║   ██║██╔════╝
    ██╔██╗ ██║███████║██████╔╝██████╔╝███████║██╔██╗ ██║█████╗   ╚███╔╝ ██║   ██║███████╗
    ██║╚██╗██║██╔══██║██╔══██╗██╔══██╗██╔══██║██║╚██╗██║██╔══╝   ██╔██╗ ██║   ██║╚════██║
    ██║ ╚████║██║  ██║██║  ██║██║  ██║██║  ██║██║ ╚████║███████╗██╔╝ ██╗╚██████╔╝███████║
    ╚═╝  ╚═══╝╚═╝  ╚═╝╚═╝  ╚═╝╚═╝  ╚═╝╚═╝  ╚═╝╚═╝  ╚═══╝╚══════╝╚═╝  ╚═╝ ╚═════╝ ╚══════╝`

const SUBTITLE = 'Modular Agent Framework with Long-term Memory'

const AsciiBanner: React.FC<AsciiBannerProps> = ({ size = 'normal' }) => {
  const isSmall = size === 'small'

  return (
    <div className="flex flex-col items-center select-none">
      <div
        className={`ascii-banner ${isSmall ? 'text-[5px] leading-[1.15]' : 'text-[8px] leading-[1.15]'}`}
      >
        {ASCII_ART}
      </div>
      <p className={`text-gray-400 ${isSmall ? 'text-[9px] mt-0.5' : 'text-xs mt-1'}`}>
        {SUBTITLE}
      </p>
    </div>
  )
}

export default AsciiBanner
