import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { accountConnectApi, youtubeReplyApi, type AccountConnectSettingsResponse, type YouTubeQuotaSnapshot } from '@/lib/api'
import { cn } from '@/lib/utils'
import {
  DEFAULT_MODEL,
  OPENROUTER_FREE_MODEL_GROUPS,
  fromApiYouTubeAIReplySettings,
  sanitizeYouTubeAIReplySettings,
  toApiYouTubeAIReplySettings,
} from '@/lib/youtubeReply'
import { CheckCircle2, ChevronsUpDown, KeyRound, Link2, Plus, RefreshCw, ShieldAlert, ShieldCheck, X } from 'lucide-react'

function formatTime(value?: string) {
  if (!value) return 'Chưa verify'
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return value
  return date.toLocaleString()
}

function getErrorMessage(error: unknown, fallback: string) {
  return error instanceof Error ? error.message : fallback
}

function formatQuotaUpdated(value?: string) {
  if (!value) return 'No usage yet'
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return value
  return `Updated ${date.toLocaleTimeString()}`
}

type StatusVerifyState = 'idle' | 'verifying' | 'connected' | 'error'

export default function SettingsPage() {
  const [loading, setLoading] = useState(true)
  const [regenerating, setRegenerating] = useState(false)
  const [bridge, setBridge] = useState<AccountConnectSettingsResponse | null>(null)
  const [clientId, setClientId] = useState('')
  const [clientSecret, setClientSecret] = useState('')
  const [verifiedAt, setVerifiedAt] = useState('')
  const [oauthVerifyState, setOauthVerifyState] = useState<StatusVerifyState>('idle')
  const [lastVerifiedOauthSignature, setLastVerifiedOauthSignature] = useState('')
  const [pairCount, setPairCount] = useState(0)
  const [quota, setQuota] = useState<YouTubeQuotaSnapshot | null>(null)
  const [, setAiSaving] = useState(false)
  const [aiSettings, setAiSettings] = useState(sanitizeYouTubeAIReplySettings())
  const [aiVerifyState, setAiVerifyState] = useState<StatusVerifyState>('idle')
  const [lastVerifiedKey, setLastVerifiedKey] = useState('')
  const latestOauthSignatureRef = useRef('')
  const latestAiKeyRef = useRef('')
  const [aiStatus, setAiStatus] = useState('Nhập OpenRouter key và fallback models cho auto reply.')
  const [status, setStatus] = useState('Nhập OAuth Client ID và Client Secret rồi bấm Verify.')
  const [statusTone, setStatusTone] = useState<'neutral' | 'success' | 'error'>('neutral')

  const load = async () => {
    setLoading(true)
    try {
      const [bridgeRes, oauthRes, configsRes, aiRes, quotaRes] = await Promise.all([
        accountConnectApi.settings(),
        accountConnectApi.oauthSettings(),
        youtubeReplyApi.configs(),
        youtubeReplyApi.aiSettings(),
        youtubeReplyApi.quota(),
      ])
      setBridge(bridgeRes)
      setClientId(oauthRes.settings.client_id ?? '')
      setClientSecret(oauthRes.settings.client_secret ?? '')
      setVerifiedAt(oauthRes.settings.verified_at ?? '')
      const oauthSignature = `${oauthRes.settings.client_id ?? ''}:::${oauthRes.settings.client_secret ?? ''}`.trim()
      setLastVerifiedOauthSignature(oauthRes.ready && oauthSignature ? oauthSignature : '')
      setOauthVerifyState(oauthRes.ready && oauthSignature ? 'connected' : 'idle')
      const loadedAiSettings = fromApiYouTubeAIReplySettings(aiRes.settings)
      const loadedKey = loadedAiSettings.openRouterKey.trim()
      setAiSettings(loadedAiSettings)
      setLastVerifiedKey(loadedAiSettings.verifiedAt && loadedKey ? loadedKey : '')
      setAiVerifyState(loadedAiSettings.verifiedAt && loadedKey ? 'connected' : 'idle')
      const connectedCount = Object.values(configsRes.items).filter((item) => item.connected && item.channel_id).length
      setPairCount(connectedCount)
      setQuota(quotaRes.quota)
      setStatus(oauthRes.message)
      setStatusTone(oauthRes.ready ? 'success' : 'neutral')
      setAiStatus('AI reply settings loaded.')
    } catch (error: unknown) {
      setStatus(getErrorMessage(error, 'Khong load duoc Settings.'))
      setStatusTone('error')
      setAiStatus(getErrorMessage(error, 'Khong load duoc AI settings.'))
      setQuota(null)
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => {
    void load()
  }, [])

  const oauthReady = useMemo(
    () => Boolean(clientId.trim() && clientSecret.trim()),
    [clientId, clientSecret],
  )

  const aiReady = useMemo(
    () => Boolean(aiSettings.openRouterKey.trim()),
    [aiSettings.openRouterKey],
  )

  const oauthStatusMeta = useMemo(() => {
    if (!oauthReady) {
      return {
        label: 'Ready to verify',
        detail: 'Add client id and secret to start.',
        tone: 'text-surface-500',
      }
    }

    if (oauthVerifyState === 'verifying') {
      return {
        label: 'Verifying access',
        detail: 'Checking OAuth credentials now...',
        tone: 'text-sky-700',
      }
    }

    if (oauthVerifyState === 'connected') {
      return {
        label: 'Verified',
        detail: verifiedAt ? `Verified ${formatTime(verifiedAt)}` : 'OAuth credentials verified.',
        tone: 'text-emerald-700',
      }
    }

    if (oauthVerifyState === 'error') {
      return {
        label: 'Verify failed',
        detail: 'Check client id and secret.',
        tone: 'text-rose-700',
      }
    }

    return {
      label: 'Waiting',
      detail: 'Pause after typing and app will verify it.',
      tone: 'text-amber-700',
    }
  }, [oauthReady, oauthVerifyState, verifiedAt])

  const aiStatusCard = useMemo(() => {
    if (!aiReady) {
      return {
        title: 'Not verified',
        detail: aiStatus || 'Add OpenRouter key to start.',
        iconTone: 'border-surface-200 bg-surface-50 text-surface-500',
        titleTone: 'text-surface-700',
        detailTone: 'text-surface-500',
      }
    }

    if (aiVerifyState === 'verifying') {
      return {
        title: 'Verifying',
        detail: 'Checking OpenRouter key now...',
        iconTone: 'border-sky-200 bg-sky-50 text-sky-600',
        titleTone: 'text-sky-700',
        detailTone: 'text-surface-500',
      }
    }

    if (aiVerifyState === 'connected') {
      return {
        title: 'Connected',
        detail: aiStatus || (aiSettings.verifiedAt ? `Verified ${formatTime(aiSettings.verifiedAt)}` : 'Key verified and saved.'),
        iconTone: 'border-emerald-200 bg-emerald-50 text-emerald-600',
        titleTone: 'text-emerald-700',
        detailTone: 'text-surface-500',
      }
    }

    if (aiVerifyState === 'error') {
      return {
        title: 'Verify failed',
        detail: aiStatus || 'Check key and try again.',
        iconTone: 'border-rose-200 bg-rose-50 text-rose-600',
        titleTone: 'text-rose-700',
        detailTone: 'text-surface-500',
      }
    }

    return {
      title: 'Ready to verify',
      detail: aiStatus || 'Pause after typing and app will verify it.',
      iconTone: 'border-amber-200 bg-amber-50 text-amber-600',
      titleTone: 'text-amber-700',
      detailTone: 'text-surface-500',
    }
  }, [aiReady, aiSettings.verifiedAt, aiStatus, aiVerifyState])

  const flatModelOptions = useMemo(
    () => OPENROUTER_FREE_MODEL_GROUPS.flatMap((group) => group.options),
    [],
  )

  const quotaUsagePct = Math.min(100, Math.round((quota?.usage_ratio ?? 0) * 100))

  useEffect(() => {
    latestOauthSignatureRef.current = `${clientId.trim()}:::${clientSecret.trim()}`
  }, [clientId, clientSecret])

  useEffect(() => {
    latestAiKeyRef.current = aiSettings.openRouterKey.trim()
  }, [aiSettings.openRouterKey])

  const verifyOauth = useCallback(async (
    credentialsOverride?: { clientId: string; clientSecret: string },
    options?: { applyState?: boolean },
  ) => {
    const applyState = options?.applyState ?? true
    try {
      const nextClientId = credentialsOverride?.clientId ?? clientId.trim()
      const nextClientSecret = credentialsOverride?.clientSecret ?? clientSecret.trim()
      const signature = `${nextClientId}:::${nextClientSecret}`
      const response = await accountConnectApi.verifyOauth({
        client_id: nextClientId,
        client_secret: nextClientSecret,
      })
      if (!applyState && latestOauthSignatureRef.current !== signature) {
        return null
      }
      const bridgeRes = await accountConnectApi.settings()
      if (!applyState && latestOauthSignatureRef.current !== signature) {
        return null
      }
      if (applyState) {
        setClientId(response.settings.client_id)
        setClientSecret(response.settings.client_secret)
        setVerifiedAt(response.settings.verified_at)
        setStatus(response.message)
        setStatusTone(response.ready ? 'success' : 'neutral')
        setBridge(bridgeRes)
        setOauthVerifyState(response.ready ? 'connected' : 'idle')
        setLastVerifiedOauthSignature(response.ready ? signature : '')
      }
      return { response, bridgeRes, signature }
    } catch (error: unknown) {
      const fallbackSignature = `${credentialsOverride?.clientId ?? clientId.trim()}:::${credentialsOverride?.clientSecret ?? clientSecret.trim()}`
      if (applyState || latestOauthSignatureRef.current === fallbackSignature) {
        setStatus(getErrorMessage(error, 'Verify OAuth that bai.'))
        setStatusTone('error')
        setOauthVerifyState('error')
      }
      return null
    }
  }, [clientId, clientSecret])

  const regenerateBridgeKey = async () => {
    setRegenerating(true)
    try {
      const response = await accountConnectApi.regenerate()
      setBridge(response)
      setStatus('Da tao bridge key moi cho extension.')
      setStatusTone('success')
    } catch (error: unknown) {
      setStatus(getErrorMessage(error, 'Khong tao lai duoc bridge key.'))
      setStatusTone('error')
    } finally {
      setRegenerating(false)
    }
  }

  const updateFallbackModel = (index: number, value: string) => {
    const next = [...aiSettings.fallbackModels]
    next[index] = value
    setAiSettings(sanitizeYouTubeAIReplySettings({
      ...aiSettings,
      fallbackModels: next,
    }))
  }

  const addFallbackModel = () => {
    const current = aiSettings.fallbackModels
    if (current.length >= 4) return
    const used = new Set([aiSettings.primaryModel, ...current].filter(Boolean))
    const nextModel = flatModelOptions.find((item) => !used.has(item.value))?.value ?? DEFAULT_MODEL
    setAiSettings(sanitizeYouTubeAIReplySettings({
      ...aiSettings,
      fallbackModels: [...current, nextModel],
    }))
  }

  const removeFallbackModel = (index: number) => {
    setAiSettings(sanitizeYouTubeAIReplySettings({
      ...aiSettings,
      fallbackModels: aiSettings.fallbackModels.filter((_, itemIndex) => itemIndex !== index),
    }))
  }

  const verifyAndSaveAiSettings = useCallback(async (
    keyOverride?: string,
    options?: { applyState?: boolean },
  ) => {
    const applyState = options?.applyState ?? true
    setAiSaving(true)
    try {
      const key = (keyOverride ?? aiSettings.openRouterKey).trim()
      if (!key) {
        throw new Error('OpenRouter key is required.')
      }
      const verifyRes = await youtubeReplyApi.verifyOpenRouter({ api_key: key })
      if (!verifyRes.ok) {
        throw new Error(verifyRes.message)
      }
      if (!applyState && latestAiKeyRef.current !== key) {
        return null
      }

      const saved = await youtubeReplyApi.saveAiSettings(toApiYouTubeAIReplySettings({
        ...aiSettings,
        openRouterKey: key,
        verifiedAt: new Date().toISOString(),
      }))
      const nextSettings = fromApiYouTubeAIReplySettings(saved.settings)
      if (!applyState && latestAiKeyRef.current !== key) {
        return null
      }
      if (applyState) {
        setAiSettings(nextSettings)
        setLastVerifiedKey(key)
        setAiVerifyState('connected')
        setAiStatus(saved.message)
      }
      return nextSettings
    } catch (error: unknown) {
      if (applyState || latestAiKeyRef.current === (keyOverride ?? aiSettings.openRouterKey).trim()) {
        setAiVerifyState('error')
        setAiStatus(getErrorMessage(error, 'Khong luu duoc AI settings.'))
      }
      return null
    } finally {
      setAiSaving(false)
    }
  }, [aiSettings])

  useEffect(() => {
    if (loading) return

    const nextClientId = clientId.trim()
    const nextClientSecret = clientSecret.trim()
    const signature = `${nextClientId}:::${nextClientSecret}`

    if (!nextClientId || !nextClientSecret) {
      setOauthVerifyState('idle')
      setStatus('Nhập OAuth Client ID và Client Secret để app tự verify.')
      setStatusTone('neutral')
      return
    }

    if (signature === lastVerifiedOauthSignature && verifiedAt) {
      setOauthVerifyState('connected')
      return
    }

    setOauthVerifyState('verifying')
    setStatus('Dang verify OAuth config...')
    setStatusTone('neutral')

    let cancelled = false
    const timer = window.setTimeout(() => {
      void (async () => {
        const result = await verifyOauth(
          { clientId: nextClientId, clientSecret: nextClientSecret },
          { applyState: false },
        )
        if (cancelled || !result) return
        if (latestOauthSignatureRef.current !== signature) return
        setClientId(result.response.settings.client_id)
        setClientSecret(result.response.settings.client_secret)
        setVerifiedAt(result.response.settings.verified_at)
        setBridge(result.bridgeRes)
        setStatus(result.response.message)
        setStatusTone(result.response.ready ? 'success' : 'neutral')
        setOauthVerifyState(result.response.ready ? 'connected' : 'idle')
        setLastVerifiedOauthSignature(result.response.ready ? signature : '')
      })()
    }, 700)

    return () => {
      cancelled = true
      window.clearTimeout(timer)
    }
  }, [clientId, clientSecret, lastVerifiedOauthSignature, loading, verifiedAt, verifyOauth])

  useEffect(() => {
    if (loading) return

    const key = aiSettings.openRouterKey.trim()
    if (!key) {
      setAiVerifyState('idle')
      setAiStatus('Nhập OpenRouter key và fallback models cho auto reply.')
      return
    }

    if (key === lastVerifiedKey && aiSettings.verifiedAt) {
      setAiVerifyState('connected')
      return
    }

    setAiVerifyState('verifying')
    setAiStatus('Dang verify OpenRouter key...')

    let cancelled = false
    const timer = window.setTimeout(() => {
      void (async () => {
        const saved = await verifyAndSaveAiSettings(key, { applyState: false })
        if (cancelled || !saved) return
        if (saved.openRouterKey.trim() !== key) return
        if (latestAiKeyRef.current !== key) return
        setAiSettings(saved)
        setLastVerifiedKey(key)
        setAiVerifyState('connected')
        setAiStatus('OpenRouter key verified and saved.')
      })()
    }, 700)

    return () => {
      cancelled = true
      window.clearTimeout(timer)
    }
  }, [aiSettings.openRouterKey, aiSettings.verifiedAt, lastVerifiedKey, loading, verifyAndSaveAiSettings])

  return (
    <div className="flex h-full flex-col bg-[radial-gradient(circle_at_top_right,_rgba(34,197,94,0.08),_transparent_30%),linear-gradient(180deg,#f8fafc_0%,#eef2ff_100%)]">
      <div className="border-b border-surface-200 bg-white/85 px-6 py-4 backdrop-blur">
        <h1 className="page-title">Settings</h1>
        <p className="page-sub mt-0.5">Nhập OAuth config trong app, rồi qua extension login Gmail để pair kênh vào project.</p>
      </div>

      <div className="flex-1 overflow-y-auto p-4">
        <div className="mx-auto grid max-w-[1440px] gap-4 xl:grid-cols-[minmax(0,1.06fr)_minmax(380px,0.94fr)]">
          <div className="grid gap-4">
            <section className="space-y-4">
              <section className="card overflow-hidden">
                <div className="border-b border-surface-200 bg-white px-5 py-4">
                  <div className="flex items-center justify-between gap-3">
                    <div>
                      <div className="flex items-center gap-2 text-sm font-semibold text-surface-900">
                        {oauthReady ? <ShieldCheck size={16} className="text-emerald-500" /> : <ShieldAlert size={16} className="text-amber-500" />}
                        OAuth / API Access
                      </div>
                      <p className="mt-1 text-sm text-surface-500">
                        Verify app credentials ở đây. Login kênh thật vẫn đi qua extension.
                      </p>
                    </div>
                    <span className={cn(
                      'inline-flex shrink-0 items-center gap-1.5 whitespace-nowrap rounded-full border px-3 py-1.5 text-xs font-semibold shadow-sm',
                      oauthVerifyState === 'connected' && 'border-emerald-200 bg-emerald-50 text-emerald-700',
                      oauthVerifyState === 'verifying' && 'border-sky-200 bg-sky-50 text-sky-700',
                      oauthVerifyState === 'error' && 'border-rose-200 bg-rose-50 text-rose-700',
                      (oauthVerifyState === 'idle' || (!oauthReady && oauthVerifyState !== 'error')) && 'border-amber-200 bg-amber-50 text-amber-700',
                    )}>
                      {oauthVerifyState === 'verifying' ? <RefreshCw size={13} className="animate-spin" /> : oauthVerifyState === 'connected' ? <CheckCircle2 size={13} /> : <ShieldAlert size={13} />}
                      {oauthVerifyState === 'verifying' ? 'Verifying' : oauthVerifyState === 'connected' ? 'Verified' : oauthVerifyState === 'error' ? 'Retry needed' : 'Verify needed'}
                    </span>
                  </div>
                </div>

                <div className="space-y-5 p-5">
                  <div>
                    <label className="label">OAuth Client ID</label>
                    <input
                      className="input mt-2 h-11 font-mono text-xs"
                      value={clientId}
                      onChange={(e) => setClientId(e.target.value)}
                      placeholder="xxxx.apps.googleusercontent.com"
                    />
                  </div>

                  <div>
                    <label className="label">OAuth Client Secret</label>
                    <input
                      className="input mt-2 h-11 font-mono text-xs"
                      value={clientSecret}
                      onChange={(e) => setClientSecret(e.target.value)}
                      placeholder="GOCSPX-..."
                    />
                  </div>

                  <div className="flex flex-wrap items-center gap-2">
                    <div className="inline-flex items-center gap-1.5 rounded-full bg-surface-100 px-3 py-1.5 text-sm text-surface-700">
                      <span className="text-surface-500">Last verify:</span>
                      <span className="font-medium text-surface-900">{formatTime(verifiedAt)}</span>
                    </div>

                    <div className="inline-flex items-center gap-1.5 rounded-full bg-surface-100 px-3 py-1.5 text-sm text-surface-700">
                      <span className="text-surface-500">Pairing mode:</span>
                      <span className="font-medium text-surface-900">OAuth + extension</span>
                    </div>
                  </div>

                  <div className="rounded-2xl border border-surface-200 bg-surface-50 px-4 py-3">
                    <div className="flex items-center justify-between gap-3">
                      <div>
                        <p className="text-sm font-semibold text-surface-900">YouTube quota today</p>
                        <p className="mt-1 text-xs text-surface-500">
                          {quota ? `${quota.estimated_units_used.toLocaleString()} / ${quota.daily_limit.toLocaleString()} units` : 'Loading usage...'}
                        </p>
                      </div>
                      <div className="text-right">
                        <p className="text-sm font-semibold text-surface-900">
                          {quota ? `${quotaUsagePct}%` : '--'}
                        </p>
                        <p className="mt-1 text-[11px] text-surface-500">
                          {quota ? `${quota.remaining_units.toLocaleString()} left` : ''}
                        </p>
                      </div>
                    </div>

                    <div className="mt-3 h-2 overflow-hidden rounded-full bg-white">
                      <div
                        className={cn(
                          'h-full rounded-full transition-all',
                          quotaUsagePct >= 80 ? 'bg-amber-500' : 'bg-emerald-500',
                        )}
                        style={{ width: `${quotaUsagePct}%` }}
                      />
                    </div>

                    <div className="mt-3 flex flex-wrap gap-2 text-[11px] text-surface-500">
                      <span className="rounded-full bg-white px-2.5 py-1">Verify: {quota?.breakdown.channel_verify ?? 0}</span>
                      <span className="rounded-full bg-white px-2.5 py-1">Inbox: {quota?.breakdown.load_inbox ?? 0}</span>
                      <span className="rounded-full bg-white px-2.5 py-1">Reply: {quota?.breakdown.post_reply ?? 0}</span>
                      <span className="rounded-full bg-white px-2.5 py-1">{formatQuotaUpdated(quota?.updated_at)}</span>
                    </div>
                  </div>

                  <div className="space-y-1 pt-1">
                    {oauthVerifyState !== 'connected' && (
                      <div className="flex items-center justify-end gap-2">
                        {oauthVerifyState === 'verifying' ? (
                          <RefreshCw size={15} className="animate-spin text-sky-600" />
                        ) : (
                          <ShieldAlert size={15} className={cn(
                            oauthVerifyState === 'error' ? 'text-rose-600' : 'text-surface-400',
                          )} />
                        )}
                        <span className={cn('text-sm font-semibold', oauthStatusMeta.tone)}>{oauthStatusMeta.label}</span>
                      </div>
                    )}

                    {statusTone !== 'success' && (
                      <p className={cn(
                        'text-right text-xs',
                        statusTone === 'error' && 'text-rose-700',
                        statusTone === 'neutral' && 'text-surface-500',
                      )}>
                        {status}
                      </p>
                    )}
                  </div>
                </div>
              </section>

              <section className="card overflow-hidden">
                <div className="border-b border-surface-200 bg-white px-5 py-4">
                  <div className="flex items-center justify-between gap-3">
                    <div>
                      <div className="flex items-center gap-2 text-sm font-semibold text-surface-900">
                        <KeyRound size={16} className="text-primary-500" />
                        Extension Bridge
                      </div>
                      <p className="mt-1 text-sm text-surface-500">Giữ bridge ổn định để extension pair đúng project, đúng profile.</p>
                    </div>
                    <div className="flex shrink-0 items-center gap-2">
                      <span className={cn(
                        'inline-flex items-center gap-1.5 whitespace-nowrap rounded-full border px-3 py-1.5 text-xs font-semibold shadow-sm',
                        bridge?.extension_connected
                          ? 'border-emerald-200 bg-emerald-50 text-emerald-700'
                          : 'border-amber-200 bg-amber-50 text-amber-700',
                      )}>
                        {bridge?.extension_connected ? <CheckCircle2 size={13} /> : <ShieldAlert size={13} />}
                        {bridge?.extension_connected ? 'Extension connected' : 'Extension offline'}
                      </span>

                      <span className="inline-flex items-center gap-1.5 whitespace-nowrap rounded-full border border-sky-200 bg-sky-50 px-3 py-1.5 text-xs font-semibold text-sky-700 shadow-sm">
                        <Link2 size={13} />
                        {bridge?.extension_name ?? 'YTB Connect'}
                      </span>
                    </div>
                  </div>
                </div>

                <div className="space-y-5 p-5">
                  <div>
                    <label className="label">Bridge URL</label>
                    <input className="input mt-2 h-11 font-mono text-xs" readOnly value={bridge?.bridge_url ?? 'http://127.0.0.1:8765'} />
                  </div>

                  <div>
                    <label className="label">Bridge Key</label>
                    <input className="input mt-2 h-11 font-mono text-xs" readOnly value={bridge?.bridge_key ?? ''} />
                  </div>

                  <div className="flex flex-wrap items-center gap-2">
                    <div className="inline-flex items-center gap-1.5 rounded-full bg-surface-100 px-3 py-1.5 text-sm text-surface-700">
                      <span className="text-surface-500">Extension:</span>
                      <span className={cn(
                        'font-medium',
                        bridge?.extension_connected ? 'text-emerald-700' : 'text-amber-700',
                      )}>
                        {bridge?.extension_connected ? 'Connected' : 'Offline'}
                      </span>
                    </div>

                    <div className="inline-flex items-center gap-1.5 rounded-full bg-surface-100 px-3 py-1.5 text-sm text-surface-700">
                      <span className="text-surface-500">OAuth state:</span>
                      <span className={cn(
                        'font-medium',
                        bridge?.oauth_ready ? 'text-emerald-700' : 'text-amber-700',
                      )}>
                        {bridge?.oauth_ready ? 'Ready' : 'Not ready'}
                      </span>
                    </div>

                    <div className="inline-flex items-center gap-1.5 rounded-full bg-surface-100 px-3 py-1.5 text-sm text-surface-700">
                      <span className="text-surface-500">Projects paired:</span>
                      <span className="font-medium text-surface-900">{pairCount}</span>
                    </div>
                  </div>

                  <div className="flex justify-center pt-1">
                    <button
                      className="inline-flex h-11 w-full items-center justify-center gap-2 rounded-xl border border-amber-200 bg-amber-50 px-4 text-sm font-semibold text-amber-700 shadow-sm transition hover:bg-amber-100 active:bg-amber-200 disabled:cursor-not-allowed disabled:opacity-60"
                      onClick={() => void regenerateBridgeKey()}
                      disabled={regenerating || loading}
                    >
                      {regenerating ? <RefreshCw size={14} className="animate-spin" /> : <RefreshCw size={14} />}
                      Regenerate key
                    </button>
                  </div>
                </div>
              </section>
            </section>

          </div>

          <section className="card overflow-hidden">
              <div className="border-b border-surface-200 bg-white px-5 py-4">
                <div className="flex items-center justify-between gap-3">
                    <div>
                      <div className="flex items-center gap-2 text-sm font-semibold text-surface-900">
                      {aiVerifyState === 'connected' ? <ShieldCheck size={16} className="text-emerald-500" /> : <ShieldAlert size={16} className="text-amber-500" />}
                      AI Reply Engine
                    </div>
                    <p className="mt-1 text-sm text-surface-500">
                      Một key dùng chung, còn model sẽ chạy theo thứ tự ưu tiên rồi fallback xuống nếu model trên fail.
                    </p>
                  </div>
                  <span className={cn(
                    'inline-flex shrink-0 items-center gap-1.5 whitespace-nowrap rounded-full border px-3 py-1.5 text-xs font-semibold shadow-sm',
                    aiVerifyState === 'connected' && 'border-emerald-200 bg-emerald-50 text-emerald-700',
                    aiVerifyState === 'verifying' && 'border-sky-200 bg-sky-50 text-sky-700',
                    aiVerifyState === 'error' && 'border-rose-200 bg-rose-50 text-rose-700',
                    (aiVerifyState === 'idle' || (!aiReady && aiVerifyState !== 'error')) && 'border-amber-200 bg-amber-50 text-amber-700',
                  )}>
                    {aiVerifyState === 'verifying' ? <RefreshCw size={13} className="animate-spin" /> : aiVerifyState === 'connected' ? <CheckCircle2 size={13} /> : <ShieldAlert size={13} />}
                    {aiVerifyState === 'verifying' ? 'Verifying' : aiVerifyState === 'connected' ? 'Connected' : aiVerifyState === 'error' ? 'Retry needed' : 'Setup needed'}
                  </span>
                </div>
              </div>

              <div className="space-y-5 p-5">
                <div className="grid gap-3 md:grid-cols-[minmax(0,1fr)_220px]">
                  <div>
                    <label className="label">OpenRouter API Key</label>
                    <input
                      className="input mt-2 font-mono text-xs"
                      type="password"
                      value={aiSettings.openRouterKey}
                      onChange={(e) => setAiSettings((prev) => ({ ...prev, openRouterKey: e.target.value }))}
                      placeholder="sk-or-v1-..."
                    />
                  </div>

                  <div className="flex min-h-[52px] items-start gap-3 pt-6">
                    <div className={cn(
                      'inline-flex h-10 w-10 shrink-0 items-center justify-center rounded-full border',
                      aiStatusCard.iconTone,
                    )}>
                      {aiVerifyState === 'verifying' ? (
                        <RefreshCw size={15} className="animate-spin" />
                      ) : aiVerifyState === 'connected' ? (
                        <CheckCircle2 size={15} />
                      ) : (
                        <ShieldAlert size={15} />
                      )}
                    </div>

                    <div className="min-w-0">
                      <p className={cn('text-sm font-semibold', aiStatusCard.titleTone)}>{aiStatusCard.title}</p>
                      <p className={cn('mt-1 text-xs', aiStatusCard.detailTone)}>{aiStatusCard.detail}</p>
                    </div>
                  </div>
                </div>

                <div className="grid gap-4 rounded-2xl border border-surface-200 bg-surface-50/70 p-4">
                  <div className="flex items-start justify-between gap-3">
                    <div>
                      <p className="text-sm font-semibold text-surface-900">Model routing</p>
                      <p className="mt-1 text-xs text-surface-500">Model chính chạy trước. Chỉ thêm fallback khi thật sự cần.</p>
                    </div>

                    <button
                      className="inline-flex h-9 shrink-0 items-center gap-1.5 rounded-xl border border-surface-200 bg-white px-3 text-xs font-semibold text-surface-700 shadow-sm transition hover:bg-surface-50 disabled:cursor-not-allowed disabled:opacity-50"
                      onClick={addFallbackModel}
                      disabled={aiSettings.fallbackModels.length >= 4}
                    >
                      <Plus size={14} />
                      Add fallback
                    </button>
                  </div>

                  <div className="grid gap-3">
                    <div>
                      <label className="label">Primary model</label>
                      <div className="relative mt-2">
                        <select
                          className="h-11 w-full appearance-none rounded-2xl border border-surface-200 bg-white pl-3 pr-10 text-sm font-semibold text-surface-900 shadow-sm outline-none transition focus:border-primary-300 focus:ring-2 focus:ring-primary-100"
                          value={aiSettings.primaryModel || DEFAULT_MODEL}
                          onChange={(e) => setAiSettings((prev) => sanitizeYouTubeAIReplySettings({ ...prev, primaryModel: e.target.value }))}
                        >
                          {OPENROUTER_FREE_MODEL_GROUPS.map((group) => (
                            <optgroup key={group.label} label={group.label}>
                              {group.options.map((model) => (
                                <option key={model.value} value={model.value}>{model.label}</option>
                              ))}
                            </optgroup>
                          ))}
                        </select>
                        <ChevronsUpDown size={15} className="pointer-events-none absolute right-3 top-1/2 -translate-y-1/2 text-surface-400" />
                      </div>
                    </div>

                    {aiSettings.fallbackModels.length > 0 ? (
                      <div className="space-y-3">
                        {aiSettings.fallbackModels.map((value, index) => (
                          <div key={`${index}-${value}`} className="rounded-2xl border border-surface-200 bg-white px-3 py-3 shadow-sm">
                            <div className="flex items-center justify-between gap-3">
                              <label className="text-xs font-medium text-surface-500">Fallback {index + 1}</label>
                              <button
                                className="inline-flex h-7 w-7 items-center justify-center rounded-lg text-surface-400 transition hover:bg-surface-100 hover:text-surface-700"
                                onClick={() => removeFallbackModel(index)}
                                aria-label={`Remove fallback model ${index + 1}`}
                              >
                                <X size={14} />
                              </button>
                            </div>

                            <div className="relative mt-2">
                              <select
                                className="h-10 w-full appearance-none rounded-xl border border-surface-200 bg-white pl-3 pr-10 text-sm font-semibold text-surface-900 outline-none transition focus:border-primary-300 focus:ring-2 focus:ring-primary-100"
                                value={value}
                                onChange={(e) => updateFallbackModel(index, e.target.value)}
                              >
                                {OPENROUTER_FREE_MODEL_GROUPS.map((group) => (
                                  <optgroup key={group.label} label={group.label}>
                                    {group.options.map((model) => (
                                      <option key={model.value} value={model.value}>{model.label}</option>
                                    ))}
                                  </optgroup>
                                ))}
                              </select>
                              <ChevronsUpDown size={15} className="pointer-events-none absolute right-3 top-1/2 -translate-y-1/2 text-surface-400" />
                            </div>
                          </div>
                        ))}
                      </div>
                    ) : (
                      <div className="rounded-2xl border border-dashed border-surface-200 bg-white px-4 py-4 text-sm text-surface-500">
                        Chưa có fallback model. Khi cần thì bấm <span className="font-semibold text-surface-700">+ Add fallback</span>.
                      </div>
                    )}
                  </div>
                </div>

              </div>
          </section>
        </div>
      </div>
    </div>
  )
}
