/**
 * @file health-monitor.ts
 * @description 服务健康状态轮询检查
 *
 * 定期检查各服务的端口连通性和 HTTP 响应，
 * 通过事件通知 Renderer 进程更新 Dashboard 状态。
 */

import * as net from 'net'
import * as http from 'http'
import { EventEmitter } from 'events'
import { SERVICES, INFRA_SERVICES, HEALTH_CHECK_INTERVAL } from './constants'

// ─── 类型定义 ───────────────────────────────────────

export type HealthState = 'healthy' | 'unhealthy' | 'unknown'

export interface ServiceHealth {
  serviceId: string
  label: string
  state: HealthState
  port: number | null
  lastChecked: number
  message: string
}

export interface OverallHealth {
  services: ServiceHealth[]
  infrastructure: ServiceHealth[]
  mysql: HealthState
  allHealthy: boolean
}

// ─── HealthMonitor ──────────────────────────────────

export class HealthMonitor extends EventEmitter {
  private intervalId: ReturnType<typeof setInterval> | null = null
  private healthStates = new Map<string, ServiceHealth>()
  private infraStates = new Map<string, ServiceHealth>()

  constructor() {
    super()
    // 初始化所有应用服务为 unknown 状态
    for (const svc of SERVICES) {
      this.healthStates.set(svc.id, {
        serviceId: svc.id,
        label: svc.label,
        state: 'unknown',
        port: svc.port,
        lastChecked: 0,
        message: 'Waiting...'
      })
    }
    // 初始化所有基础设施服务为 unknown 状态
    for (const infra of INFRA_SERVICES) {
      this.infraStates.set(infra.id, {
        serviceId: infra.id,
        label: infra.label,
        state: 'unknown',
        port: infra.port,
        lastChecked: 0,
        message: 'Waiting...'
      })
    }
  }

  /** 开始定时健康检查 */
  start(): void {
    if (this.intervalId) return
    // 立即执行一次
    this.checkAll()
    this.intervalId = setInterval(() => this.checkAll(), HEALTH_CHECK_INTERVAL)
  }

  /** 停止定时健康检查 */
  stop(): void {
    if (this.intervalId) {
      clearInterval(this.intervalId)
      this.intervalId = null
    }
  }

  /** 获取当前所有健康状态 */
  getStatus(): OverallHealth {
    const services = Array.from(this.healthStates.values())
    const infrastructure = Array.from(this.infraStates.values())
    const mysql = this.infraStates.get('mysql')?.state ?? 'unknown'
    // allHealthy 只考虑应用服务和必需基础设施（MySQL），EverMemOS 相关的可选服务不参与
    const requiredInfraIds = new Set(INFRA_SERVICES.filter((i) => i.required).map((i) => i.id))
    const allHealthy =
      services.every((s) => s.state === 'healthy') &&
      infrastructure.filter((s) => requiredInfraIds.has(s.serviceId)).every((s) => s.state === 'healthy')
    return { services, infrastructure, mysql, allHealthy }
  }

  /** 手动触发一次全量检查 */
  async checkAll(): Promise<OverallHealth> {
    const checks: Promise<void>[] = []

    // 检查基础设施端口（MySQL、MongoDB、Elasticsearch 等）
    for (const infra of INFRA_SERVICES) {
      checks.push(this.checkInfraPortHealth(infra.id, infra.port))
    }

    // 检查各应用服务
    for (const svc of SERVICES) {
      if (svc.healthUrl) {
        checks.push(this.checkHttpHealth(svc.id, svc.healthUrl))
      } else if (svc.port) {
        checks.push(this.checkPortHealth(svc.id, svc.port))
      }
      // 无端口的服务（poller、job-trigger）由 ProcessManager 状态判断
    }

    await Promise.all(checks)
    const status = this.getStatus()
    this.emit('health-update', status)
    return status
  }

  // ─── 内部方法 ─────────────────────────────────────

  /** TCP 端口连通性检查（应用服务） */
  private async checkPortHealth(serviceId: string, port: number): Promise<void> {
    const reachable = await this.isPortReachable(port)
    this.updateHealth(this.healthStates, serviceId, reachable ? 'healthy' : 'unhealthy', port, reachable ? 'Port reachable' : 'Port unreachable')
  }

  /** TCP 端口连通性检查（基础设施） */
  private async checkInfraPortHealth(serviceId: string, port: number): Promise<void> {
    const reachable = await this.isPortReachable(port)
    this.updateHealth(this.infraStates, serviceId, reachable ? 'healthy' : 'unhealthy', port, reachable ? 'Port reachable' : 'Port unreachable')
  }

  /** HTTP 健康检查 */
  private async checkHttpHealth(serviceId: string, url: string): Promise<void> {
    const healthy = await this.isHttpHealthy(url)
    const svc = SERVICES.find((s) => s.id === serviceId)
    this.updateHealth(
      this.healthStates,
      serviceId,
      healthy ? 'healthy' : 'unhealthy',
      svc?.port ?? null,
      healthy ? 'HTTP 200' : 'HTTP unreachable'
    )
  }

  /** 更新某个服务的健康状态 */
  private updateHealth(
    states: Map<string, ServiceHealth>,
    serviceId: string,
    state: HealthState,
    port: number | null,
    message: string
  ): void {
    const current = states.get(serviceId)
    if (!current) return

    const prev = current.state
    current.state = state
    current.port = port
    current.lastChecked = Date.now()
    current.message = message

    // 仅在状态变化时触发事件
    if (prev !== state) {
      this.emit('service-health-change', serviceId, state, prev)
    }
  }

  /** 检查 TCP 端口是否可连接 */
  private isPortReachable(
    port: number,
    host = '127.0.0.1',
    timeout = 2000
  ): Promise<boolean> {
    return new Promise((resolve) => {
      const socket = new net.Socket()
      socket.setTimeout(timeout)
      socket.on('connect', () => {
        socket.destroy()
        resolve(true)
      })
      socket.on('error', () => resolve(false))
      socket.on('timeout', () => {
        socket.destroy()
        resolve(false)
      })
      socket.connect(port, host)
    })
  }

  /** 检查 HTTP URL 是否返回 2xx */
  private isHttpHealthy(url: string, timeout = 3000): Promise<boolean> {
    return new Promise((resolve) => {
      const req = http.get(url, { timeout }, (res) => {
        const statusCode = res.statusCode ?? 0
        // 消费响应数据，防止内存泄漏
        res.resume()
        resolve(statusCode >= 200 && statusCode < 400)
      })
      req.on('error', () => resolve(false))
      req.on('timeout', () => {
        req.destroy()
        resolve(false)
      })
    })
  }
}
