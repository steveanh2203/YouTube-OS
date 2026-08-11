const STORAGE_KEY = 'masteros.roxy.config'

export interface RoxyConfig {
  apiHost: string
  apiToken: string
  workspaceId: number | null
  profileId: string
}

const EMPTY_CONFIG: RoxyConfig = {
  apiHost: '',
  apiToken: '',
  workspaceId: null,
  profileId: '',
}

export function normalizeRoxyHost(value: string): string {
  return value.trim().replace(/\/+$/, '')
}

export function loadRoxyConfig(): RoxyConfig {
  try {
    const raw = localStorage.getItem(STORAGE_KEY)
    if (!raw) return { ...EMPTY_CONFIG }
    const parsed = JSON.parse(raw) as Partial<RoxyConfig>
    return {
      apiHost: normalizeRoxyHost(parsed.apiHost ?? ''),
      apiToken: String(parsed.apiToken ?? '').trim(),
      workspaceId: Number.isInteger(parsed.workspaceId) ? parsed.workspaceId ?? null : null,
      profileId: String(parsed.profileId ?? '').trim(),
    }
  } catch {
    return { ...EMPTY_CONFIG }
  }
}

export function saveRoxyConfig(config: RoxyConfig): void {
  localStorage.setItem(STORAGE_KEY, JSON.stringify({
    apiHost: normalizeRoxyHost(config.apiHost),
    apiToken: config.apiToken.trim(),
    workspaceId: config.workspaceId,
    profileId: config.profileId.trim(),
  }))
}

export function isRoxyOfflineMessage(value: string): boolean {
  const message = value.toLowerCase()
  return message.includes('could not connect')
    || message.includes('connection refused')
    || message.includes('failed to fetch')
    || message.includes('network error')
}
