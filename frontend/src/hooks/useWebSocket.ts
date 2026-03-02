/**
 * WebSocket hook for agent runtime streaming
 *
 * Agent runtime 是一次性流式执行，不支持自动重连（重连会重发请求触发新的 agent run）。
 * 连接意外断开时直接报错，由用户手动重试。
 */

import { useState, useCallback, useRef } from 'react';
import type { RuntimeMessage } from '@/types';

export type WebSocketStatus = 'idle' | 'connecting' | 'connected' | 'error' | 'closed';

interface UseAgentWebSocketOptions {
  onMessage?: (message: RuntimeMessage) => void;
  onError?: (error: Event) => void;
  onComplete?: () => void;
  onClose?: () => void;
}

export function useAgentWebSocket(options: UseAgentWebSocketOptions = {}) {
  const [status, setStatus] = useState<WebSocketStatus>('idle');
  const wsRef = useRef<WebSocket | null>(null);
  // 标记用户主动关闭（跳过报错）
  const intentionalCloseRef = useRef(false);
  // 标记收到 complete 消息（正常结束）
  const completedRef = useRef(false);

  const { onMessage, onError, onComplete, onClose } = options;

  /** 创建 WebSocket 连接 */
  const connect = useCallback(
    (agentId: string, userId: string, inputContent: string): Promise<void> => {
      return new Promise<void>((resolve, reject) => {
        const wsProtocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
        const wsUrl = `${wsProtocol}//${window.location.host}/ws/agent/run`;

        const ws = new WebSocket(wsUrl);
        wsRef.current = ws;
        setStatus('connecting');

        ws.onopen = () => {
          setStatus('connected');
          ws.send(JSON.stringify({
            agent_id: agentId,
            user_id: userId,
            input_content: inputContent,
            working_source: 'chat',
          }));
        };

        ws.onmessage = (event) => {
          try {
            const message = JSON.parse(event.data) as RuntimeMessage;

            // 忽略心跳消息，仅用于保持连接活跃
            if (message.type === 'heartbeat') return;

            onMessage?.(message);

            if (message.type === 'complete') {
              completedRef.current = true;
              onComplete?.();
              resolve();
            }
          } catch (e) {
            console.error('Failed to parse WebSocket message:', e);
          }
        };

        ws.onerror = (error) => {
          setStatus('error');
          onError?.(error);
          reject(error);
        };

        ws.onclose = () => {
          wsRef.current = null;

          // 正常完成或用户主动关闭
          if (completedRef.current || intentionalCloseRef.current) {
            setStatus('closed');
            onClose?.();
            return;
          }

          // 意外断开 → 直接报错，不自动重连
          console.warn('WebSocket closed unexpectedly during agent execution.');
          setStatus('error');
          onClose?.();
        };
      });
    },
    [onMessage, onError, onComplete, onClose]
  );

  /** 发起新的 Agent 请求 */
  const run = useCallback(
    async (agentId: string, userId: string, inputContent: string) => {
      // 关闭已有连接
      if (wsRef.current) {
        intentionalCloseRef.current = true;
        wsRef.current.close();
        intentionalCloseRef.current = false;
      }

      // 重置状态
      completedRef.current = false;
      intentionalCloseRef.current = false;

      return connect(agentId, userId, inputContent);
    },
    [connect]
  );

  /** 手动关闭连接 */
  const close = useCallback(() => {
    intentionalCloseRef.current = true;
    if (wsRef.current) {
      wsRef.current.close();
      wsRef.current = null;
    }
    setStatus('idle');
  }, []);

  return {
    run,
    close,
    status,
    isConnected: status === 'connected',
    isLoading: status === 'connecting' || status === 'connected',
  };
}
