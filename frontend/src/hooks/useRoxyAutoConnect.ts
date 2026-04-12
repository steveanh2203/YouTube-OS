import { useCallback, useEffect, useMemo, useRef, useState } from 'react'

import { roxyApi, type RoxyProfileInfo, type RoxyWorkspaceInfo } from '@/lib/api'
import {
  isRoxyOfflineMessage,
  loadRoxyConfig,
  normalizeRoxyHost,
  saveRoxyConfig,
} from '@/lib/roxy'

type ConnectOptions = {
  preferredWorkspaceId?: number | null
  preferredProfileId?: string
  silent?: boolean
}

type UseRoxyAutoConnectOptions = {
  autoStart?: boolean
  enabled?: boolean
  preferredWorkspaceId?: number | null
  preferredProfileId?: string
  retryMs?: number
}

function getErrorMessage(error: unknown, fallback: string): string {
  return error instanceof Error ? error.message : fallback
}

export function useRoxyAutoConnect(options: UseRoxyAutoConnectOptions = {}) {
  const {
    autoStart = true,
    enabled = true,
    preferredWorkspaceId,
    preferredProfileId,
    retryMs = 3000,
  } = options

  const initialConfig = useMemo(() => loadRoxyConfig(), [])
  const [apiHost, setApiHost] = useState(initialConfig.apiHost)
  const [apiToken, setApiToken] = useState(initialConfig.apiToken)
  const [workspaces, setWorkspaces] = useState<RoxyWorkspaceInfo[]>([])
  const [profiles, setProfiles] = useState<RoxyProfileInfo[]>([])
  const [workspaceId, setWorkspaceId] = useState<number | null>(preferredWorkspaceId ?? initialConfig.workspaceId)
  const [profileId, setProfileId] = useState(preferredProfileId ?? initialConfig.profileId)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [showReminder, setShowReminder] = useState(false)
  const retryTimerRef = useRef<number | null>(null)

  const normalizedHost = normalizeRoxyHost(apiHost || '')
  const hasConfig = !!apiHost.trim() && !!apiToken.trim()
  const activeWorkspace = workspaces.find(item => item.workspace_id === workspaceId)
  const activeProfile = profiles.find(item => item.dir_id === profileId)

  const clearRetry = useCallback(() => {
    if (retryTimerRef.current !== null) {
      window.clearTimeout(retryTimerRef.current)
      retryTimerRef.current = null
    }
  }, [])

  const loadProfilesForWorkspace = useCallback(async (
    targetWorkspaceId: number,
    nextPreferredProfileId?: string,
  ): Promise<RoxyProfileInfo[]> => {
    const res = await roxyApi.profiles(normalizedHost, apiToken.trim(), targetWorkspaceId)
    if (!res.ok) throw new Error(res.message || 'Could not load Roxy profiles')

    setProfiles(res.profiles)
    if (res.profiles.length === 0) {
      setProfileId('')
      return []
    }

    const resolvedProfileId = nextPreferredProfileId && res.profiles.some(item => item.dir_id === nextPreferredProfileId)
      ? nextPreferredProfileId
      : res.profiles[0].dir_id

    setProfileId(resolvedProfileId)
    return res.profiles
  }, [apiToken, normalizedHost])

  const connect = useCallback(async (connectOptions: ConnectOptions = {}) => {
    if (!enabled || !hasConfig) return false

    setLoading(true)
    if (!connectOptions.silent) setError(null)

    try {
      const res = await roxyApi.workspaces(normalizedHost, apiToken.trim())
      if (!res.ok || res.workspaces.length === 0) {
        throw new Error(res.message || 'No Roxy workspace found')
      }

      const requestedWorkspaceId = connectOptions.preferredWorkspaceId
        ?? preferredWorkspaceId
        ?? workspaceId

      const resolvedWorkspaceId = requestedWorkspaceId && res.workspaces.some(item => item.workspace_id === requestedWorkspaceId)
        ? requestedWorkspaceId
        : res.workspaces[0].workspace_id

      const requestedProfileId = connectOptions.preferredProfileId
        ?? preferredProfileId
        ?? profileId

      setApiHost(normalizedHost)
      setWorkspaces(res.workspaces)
      setWorkspaceId(resolvedWorkspaceId)
      await loadProfilesForWorkspace(resolvedWorkspaceId, requestedProfileId)
      setError(null)
      setShowReminder(false)
      clearRetry()
      return true
    } catch (err: unknown) {
      const message = getErrorMessage(err, 'Could not connect to the Roxy API')
      setError(message)
      setShowReminder(isRoxyOfflineMessage(message))
      return false
    } finally {
      setLoading(false)
    }
  }, [
    apiToken,
    clearRetry,
    enabled,
    hasConfig,
    loadProfilesForWorkspace,
    normalizedHost,
    preferredProfileId,
    preferredWorkspaceId,
    profileId,
    workspaceId,
  ])

  const handleWorkspaceChange = useCallback(async (nextWorkspaceId: number) => {
    setWorkspaceId(nextWorkspaceId)
    setLoading(true)
    setError(null)

    try {
      await loadProfilesForWorkspace(nextWorkspaceId)
      setShowReminder(false)
      clearRetry()
    } catch (err: unknown) {
      const message = getErrorMessage(err, 'Could not load Roxy profiles')
      setError(message)
      setShowReminder(isRoxyOfflineMessage(message))
    } finally {
      setLoading(false)
    }
  }, [clearRetry, loadProfilesForWorkspace])

  useEffect(() => {
    saveRoxyConfig({ apiHost, apiToken, workspaceId, profileId })
  }, [apiHost, apiToken, workspaceId, profileId])

  useEffect(() => {
    if (preferredWorkspaceId === undefined) return
    setWorkspaceId(preferredWorkspaceId ?? null)
  }, [preferredWorkspaceId])

  useEffect(() => {
    if (preferredProfileId === undefined) return
    setProfileId(preferredProfileId)
  }, [preferredProfileId])

  useEffect(() => {
    clearRetry()
    if (!enabled || !autoStart || !hasConfig) return

    const timer = window.setTimeout(() => {
      void connect({ silent: true })
    }, 80)

    return () => {
      window.clearTimeout(timer)
      clearRetry()
    }
  }, [autoStart, clearRetry, connect, enabled, hasConfig])

  useEffect(() => {
    clearRetry()
    if (!enabled || !hasConfig || !showReminder) return

    retryTimerRef.current = window.setTimeout(() => {
      void connect({ silent: true })
    }, retryMs)

    return clearRetry
  }, [clearRetry, connect, enabled, hasConfig, retryMs, showReminder])

  return {
    activeProfile,
    activeWorkspace,
    apiHost,
    apiToken,
    connect,
    error,
    handleWorkspaceChange,
    hasConfig,
    loading,
    normalizedHost,
    profileId,
    profiles,
    setApiHost,
    setApiToken,
    setProfileId,
    setShowReminder,
    showReminder,
    workspaceId,
    workspaces,
  }
}
