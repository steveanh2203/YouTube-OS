import { apiFetch } from './api.ts'

export type MediaKind = 'audio' | 'image' | 'subtitle' | 'text' | 'video'

export interface MediaAsset {
  id: string
  reference: string
  original_name: string
  kind: MediaKind
  content_type: string
  size_bytes: number
  sha256: string
  created_at: string
  content_url: string
  download_url: string
}

async function mediaJson<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await apiFetch(path, init)
  const body = await response.json().catch(() => null) as { detail?: unknown } | T | null
  if (!response.ok) {
    const detail = body && typeof body === 'object' && 'detail' in body ? body.detail : null
    throw new Error(typeof detail === 'string' ? detail : `Media request failed (${response.status})`)
  }
  return body as T
}

export const mediaApi = {
  list: (kind?: MediaKind): Promise<MediaAsset[]> => {
    const query = kind ? `?kind=${encodeURIComponent(kind)}` : ''
    return mediaJson(`/api/media/assets${query}`)
  },

  upload: (file: File): Promise<MediaAsset> => {
    const body = new FormData()
    body.append('file', file)
    return mediaJson('/api/media/assets', { method: 'POST', body })
  },

  remove: async (assetId: string): Promise<void> => {
    const response = await apiFetch(`/api/media/assets/${encodeURIComponent(assetId)}`, { method: 'DELETE' })
    if (!response.ok) throw new Error(`Could not delete media (${response.status})`)
  },
}

export function mediaContentUrl(reference: string): string {
  const assetId = reference.startsWith('media:') ? reference.slice('media:'.length) : reference
  return `/api/media/assets/${encodeURIComponent(assetId)}/content`
}
