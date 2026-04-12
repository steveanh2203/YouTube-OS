/**
 * useExtensionSocket — lắng nghe real-time push từ Chrome extension.
 *
 * Mỗi tool component (AIGen, AIAudio, SRTGen...) gọi hook này với feature ID
 * tương ứng. Khi extension gửi data, chỉ component đúng feature mới nhận được.
 *
 * URL WebSocket: ws://127.0.0.1:8765/api/extension/ws
 */

import { useCallback, useEffect, useRef } from 'react'

// ── Types ──────────────────────────────────────────────────────────────────────

export type ExtensionFeature =
  | 'ai_gen'
  | 'ai_audio'
  | 'srt_gen'
  | 'raw_seo'
  | 'resource_prep'
  | 'account_connect'

export interface ExtensionPushMessage {
  type: 'extension_push'
  feature: ExtensionFeature
  content: string
  parent_project_id: number | null
  child_project_id: number | null
}

export type ExtensionMessageHandler = (msg: ExtensionPushMessage) => void

// ── Constants ──────────────────────────────────────────────────────────────────

const WS_URL = 'ws://127.0.0.1:8765/api/extension/ws'
const RECONNECT_DELAY_MS = 3000

// ── Hook ───────────────────────────────────────────────────────────────────────

/**
 * @param feature   Feature ID để filter message — chỉ nhận đúng feature của mình
 * @param onMessage Callback được gọi khi có push phù hợp từ extension
 *
 * @example
 * useExtensionSocket('ai_gen', (msg) => {
 *   setRawText(prev => prev ? prev + '\n' + msg.content : msg.content)
 *   toast.success('Nhận prompt từ Claude!')
 * })
 */
export function useExtensionSocket(
  feature: ExtensionFeature,
  onMessage: ExtensionMessageHandler,
): void {
  const wsRef      = useRef<WebSocket | null>(null)
  const connectRef = useRef<() => void>(() => {})
  const handlerRef = useRef<ExtensionMessageHandler>(onMessage)
  const featureRef = useRef<ExtensionFeature>(feature)
  const mountedRef = useRef(true)

  // Luôn giữ refs mới nhất để không cần recreate WebSocket khi prop thay đổi
  useEffect(() => { handlerRef.current = onMessage }, [onMessage])
  useEffect(() => { featureRef.current = feature },   [feature])

  const connect = useCallback(() => {
    if (!mountedRef.current) return

    try {
      const ws = new WebSocket(WS_URL)
      wsRef.current = ws

      ws.onopen = () => {
        // Connection established — không cần log ở frontend
      }

      ws.onmessage = (event: MessageEvent) => {
        try {
          const msg = JSON.parse(event.data as string) as ExtensionPushMessage
          // Chỉ xử lý đúng loại message và đúng feature
          if (msg.type === 'extension_push' && msg.feature === featureRef.current) {
            handlerRef.current(msg)
          }
        } catch {
          // Bỏ qua message không hợp lệ
        }
      }

      ws.onclose = () => {
        wsRef.current = null
        if (mountedRef.current) {
          // Tự reconnect sau khi mất kết nối (backend restart, v.v.)
          setTimeout(() => connectRef.current(), RECONNECT_DELAY_MS)
        }
      }

      ws.onerror = () => {
        ws.close()
      }
    } catch {
      // WebSocket không khả dụng (backend chưa start) — thử lại sau
      if (mountedRef.current) {
        setTimeout(() => connectRef.current(), RECONNECT_DELAY_MS)
      }
    }
  }, [])

  useEffect(() => {
    connectRef.current = connect
  }, [connect])

  useEffect(() => {
    mountedRef.current = true
    connect()

    return () => {
      mountedRef.current = false
      wsRef.current?.close()
      wsRef.current = null
    }
  }, [connect])
}
