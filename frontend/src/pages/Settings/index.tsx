/* Hallmark · pre-emit critique: P5 H5 E5 S5 R5 V4 */
import { useEffect, useMemo, useRef, useState } from 'react'
import { AudioLines, CheckCircle2, KeyRound, RefreshCw, Search, ShieldAlert } from 'lucide-react'
import { accountConnectApi, nicheResearchApi } from '@/lib/api'
import { cn } from '@/lib/utils'
import { Button } from '@/components/ui/button'

type VerifyState = 'idle' | 'typing' | 'verifying' | 'connected' | 'error'

function getErrorMessage(error: unknown, fallback: string) {
  return error instanceof Error ? error.message : fallback
}

function StatusPill({ state, label }: { state: VerifyState; label: string }) {
  return (
    <span className={cn(
      'inline-flex shrink-0 items-center gap-1.5 whitespace-nowrap rounded-full border px-2.5 py-1 text-[11px] font-semibold',
      state === 'connected' && 'border-emerald-500/20 bg-emerald-500/10 text-emerald-300',
      state === 'verifying' && 'border-sky-500/20 bg-sky-500/10 text-sky-300',
      state === 'typing' && 'border-amber-500/20 bg-amber-500/10 text-amber-300',
      state === 'error' && 'border-rose-500/20 bg-rose-500/10 text-rose-300',
      state === 'idle' && 'border-surface-200 bg-surface-100 text-surface-600',
    )}>
      {state === 'verifying'
        ? <RefreshCw size={12} className="animate-spin" />
        : state === 'connected'
          ? <CheckCircle2 size={12} />
          : <ShieldAlert size={12} />}
      {label}
    </span>
  )
}

export default function SettingsPage() {
  const [loading, setLoading] = useState(true)
  const [clientId, setClientId] = useState('')
  const [clientSecret, setClientSecret] = useState('')
  const [verifyState, setVerifyState] = useState<VerifyState>('idle')
  const [message, setMessage] = useState('Nhập YouTube OAuth Client ID và Client Secret.')
  const [youtubeDataLoading, setYoutubeDataLoading] = useState(true)
  const [youtubeDataKey, setYoutubeDataKey] = useState('')
  const [youtubeDataKeyHint, setYoutubeDataKeyHint] = useState('')
  const [youtubeDataState, setYoutubeDataState] = useState<VerifyState>('idle')
  const [youtubeDataMessage, setYoutubeDataMessage] = useState(
    'Enter a YouTube Data API key to enable niche research.',
  )
  const [ai33Loading, setAi33Loading] = useState(true)
  const [ai33Key, setAi33Key] = useState('')
  const [ai33Configured, setAi33Configured] = useState(false)
  const [ai33KeyHint, setAi33KeyHint] = useState('')
  const [ai33Dirty, setAi33Dirty] = useState(false)
  const [ai33State, setAi33State] = useState<VerifyState>('idle')
  const [ai33Message, setAi33Message] = useState('Enter an AI33.pro API key to connect.')
  const ai33RequestId = useRef(0)

  const ready = useMemo(
    () => Boolean(clientId.trim() && clientSecret.trim()),
    [clientId, clientSecret],
  )

  useEffect(() => {
    let cancelled = false

    void (async () => {
      const [oauthResult, youtubeDataResult, ai33Result] = await Promise.allSettled([
        accountConnectApi.oauthSettings(),
        nicheResearchApi.settings(),
        accountConnectApi.ai33Settings(),
      ])
      if (cancelled) return

      if (oauthResult.status === 'fulfilled') {
        const response = oauthResult.value
        setClientId(response.settings.client_id ?? '')
        setClientSecret(response.settings.client_secret ?? '')
        setVerifyState(response.ready ? 'connected' : 'idle')
        setMessage(response.message)
      } else {
        setVerifyState('error')
        setMessage(getErrorMessage(oauthResult.reason, 'Không tải được YouTube OAuth settings.'))
      }

      if (youtubeDataResult.status === 'fulfilled') {
        const response = youtubeDataResult.value
        setYoutubeDataKeyHint(response.settings.key_hint)
        setYoutubeDataState(response.settings.connected ? 'connected' : 'idle')
        setYoutubeDataMessage(response.message)
      } else {
        setYoutubeDataState('error')
        setYoutubeDataMessage(getErrorMessage(
          youtubeDataResult.reason,
          'Could not load YouTube Data API settings.',
        ))
      }

      if (ai33Result.status === 'fulfilled') {
        const response = ai33Result.value
        setAi33Configured(response.settings.configured)
        setAi33KeyHint(response.settings.key_hint)
        setAi33State(response.settings.connected ? 'connected' : 'idle')
        setAi33Message(response.message)
      } else {
        setAi33State('error')
        setAi33Message(getErrorMessage(ai33Result.reason, 'Could not load AI33.pro settings.'))
      }

      setLoading(false)
      setYoutubeDataLoading(false)
      setAi33Loading(false)
    })()

    return () => {
      cancelled = true
    }
  }, [])

  useEffect(() => {
    if (!ai33Dirty) return

    const normalizedKey = ai33Key.trim()
    if (normalizedKey.length < 8) return

    const requestId = ai33RequestId.current

    const timer = window.setTimeout(() => {
      setAi33State('verifying')
      setAi33Message('Connecting to AI33.pro...')

      void accountConnectApi.verifyAi33({ api_key: normalizedKey })
        .then((response) => {
          if (ai33RequestId.current !== requestId) return
          setAi33Configured(response.settings.configured)
          setAi33KeyHint(response.settings.key_hint)
          setAi33State(response.settings.connected ? 'connected' : 'idle')
          setAi33Message(response.message)
          setAi33Dirty(false)
          setAi33Key('')
        })
        .catch((error: unknown) => {
          if (ai33RequestId.current !== requestId) return
          setAi33State('error')
          setAi33Message(getErrorMessage(error, 'Could not verify this AI33.pro API key.'))
        })
    }, 900)

    return () => {
      window.clearTimeout(timer)
      if (ai33RequestId.current === requestId) ai33RequestId.current += 1
    }
  }, [ai33Dirty, ai33Key])

  const verify = async () => {
    if (!ready || verifyState === 'verifying') return

    setVerifyState('verifying')
    setMessage('Đang xác minh YouTube OAuth keys...')

    try {
      const response = await accountConnectApi.verifyOauth({
        client_id: clientId.trim(),
        client_secret: clientSecret.trim(),
      })
      setClientId(response.settings.client_id ?? '')
      setClientSecret(response.settings.client_secret ?? '')
      setVerifyState(response.ready ? 'connected' : 'idle')
      setMessage(response.message)
    } catch (error: unknown) {
      setVerifyState('error')
      setMessage(getErrorMessage(error, 'Xác minh YouTube OAuth thất bại.'))
    }
  }

  const verifyYoutubeDataKey = async () => {
    const apiKey = youtubeDataKey.trim()
    if (!apiKey || youtubeDataState === 'verifying') return

    setYoutubeDataState('verifying')
    setYoutubeDataMessage('Connecting to YouTube Data API...')

    try {
      const response = await nicheResearchApi.verifyKey({ api_key: apiKey })
      setYoutubeDataKeyHint(response.settings.key_hint)
      setYoutubeDataState(response.settings.connected ? 'connected' : 'idle')
      setYoutubeDataMessage(response.message)
      setYoutubeDataKey('')
    } catch (error: unknown) {
      setYoutubeDataState('error')
      setYoutubeDataMessage(getErrorMessage(error, 'Could not verify this YouTube Data API key.'))
    }
  }

  const statusLabel = verifyState === 'verifying'
    ? 'Verifying'
    : verifyState === 'connected'
      ? 'Verified'
      : verifyState === 'error'
        ? 'Verify failed'
        : 'Not verified'

  const ai33StatusLabel = ai33State === 'verifying'
    ? 'Verifying'
    : ai33State === 'connected'
      ? 'Connected'
      : ai33State === 'error'
        ? 'Connection failed'
        : ai33State === 'typing'
          ? 'Waiting'
          : 'Not connected'

  const youtubeDataStatusLabel = youtubeDataState === 'verifying'
    ? 'Verifying'
    : youtubeDataState === 'connected'
      ? 'Connected'
      : youtubeDataState === 'error'
        ? 'Connection failed'
        : youtubeDataState === 'typing'
          ? 'Waiting'
          : 'Not connected'

  const connectedCount = [verifyState, youtubeDataState, ai33State]
    .filter((state) => state === 'connected').length

  return (
    <div className="flex h-full flex-col bg-background">
      <div className="flex-1 overflow-y-auto p-4 md:p-6">
        <div className="mx-auto max-w-6xl">
          <div className="mb-4 flex flex-col gap-3 sm:flex-row sm:items-end sm:justify-between">
            <div>
              <p className="text-[10px] font-semibold uppercase tracking-[0.16em] text-primary-300">Connections</p>
              <h1 className="mt-1 font-heading text-xl font-semibold tracking-[-0.025em] text-surface-900">Credential Center</h1>
              <p className="mt-1 text-sm text-surface-500">Manage the services that power channels, research, and voice.</p>
            </div>
            <div className="flex items-center gap-2 rounded-lg border border-surface-200 bg-surface-50 px-3 py-2">
              <span className="size-2 rounded-full bg-primary-500" />
              <span className="text-xs font-medium text-surface-700">{connectedCount} of 3 connected</span>
            </div>
          </div>

          <div className="mb-4 grid gap-px overflow-hidden rounded-xl border border-surface-200 bg-surface-200 sm:grid-cols-3">
            {[
              { label: 'Channel access', value: statusLabel, state: verifyState },
              { label: 'Niche research', value: youtubeDataStatusLabel, state: youtubeDataState },
              { label: 'Voice engine', value: ai33StatusLabel, state: ai33State },
            ].map((item) => (
              <div key={item.label} className="flex items-center justify-between gap-3 bg-surface-50 px-4 py-3">
                <span>
                  <span className="block text-[11px] font-medium uppercase tracking-[0.08em] text-surface-500">{item.label}</span>
                  <span className="mt-0.5 block text-sm font-semibold text-surface-900">{item.value}</span>
                </span>
                <span className={cn(
                  'size-2 rounded-full',
                  item.state === 'connected' && 'bg-emerald-500',
                  item.state === 'verifying' && 'animate-pulse bg-sky-500',
                  item.state === 'typing' && 'bg-amber-500',
                  item.state === 'error' && 'bg-rose-500',
                  item.state === 'idle' && 'bg-surface-300',
                )} />
              </div>
            ))}
          </div>

          <div className="grid items-start gap-4 xl:grid-cols-[minmax(0,1.25fr)_minmax(22rem,0.75fr)]">
            <section className="card overflow-hidden">
              <div className="flex items-start justify-between gap-4 border-b border-surface-200 bg-surface-0 px-5 py-4">
                <div className="flex min-w-0 items-start gap-3">
                  <div className="flex size-9 shrink-0 items-center justify-center rounded-lg border border-primary-500/20 bg-primary-50 text-primary-300">
                    <KeyRound size={17} />
                  </div>
                  <div className="min-w-0">
                    <p className="text-[10px] font-semibold uppercase tracking-[0.12em] text-surface-500">Primary connection</p>
                    <h2 className="mt-0.5 font-heading text-base font-semibold text-surface-900">YouTube OAuth</h2>
                    <p className="mt-1 text-xs text-surface-500">Connect Gmail accounts and pair YouTube channels with MasterOS.</p>
                  </div>
                </div>
                <StatusPill state={verifyState} label={statusLabel} />
              </div>

              <div className="space-y-4 p-5">
                <div>
                  <label className="label" htmlFor="youtube-client-id">OAuth Client ID</label>
                  <input
                    id="youtube-client-id"
                    className="input h-10 font-mono text-xs"
                    value={clientId}
                    onChange={(event) => {
                      setClientId(event.target.value)
                      setVerifyState('idle')
                    }}
                    placeholder="xxxx.apps.googleusercontent.com"
                    autoComplete="off"
                    disabled={loading}
                  />
                </div>

                <div>
                  <label className="label" htmlFor="youtube-client-secret">OAuth Client Secret</label>
                  <input
                    id="youtube-client-secret"
                    className="input h-10 font-mono text-xs"
                    type="password"
                    value={clientSecret}
                    onChange={(event) => {
                      setClientSecret(event.target.value)
                      setVerifyState('idle')
                    }}
                    placeholder="GOCSPX-..."
                    autoComplete="off"
                    disabled={loading}
                  />
                </div>

                <div className="flex flex-col gap-3 border-t border-surface-200 pt-4 sm:flex-row sm:items-center sm:justify-between">
                  <p className={cn('min-w-0 text-xs leading-relaxed', verifyState === 'error' ? 'text-rose-300' : 'text-surface-500')}>
                    {loading ? 'Loading YouTube OAuth settings...' : message}
                  </p>
                  <Button className="shrink-0" onClick={() => void verify()} disabled={loading || !ready || verifyState === 'verifying'}>
                    {verifyState === 'verifying' ? <RefreshCw className="animate-spin" /> : <CheckCircle2 />}
                    Verify OAuth
                  </Button>
                </div>
              </div>
            </section>

            <div className="grid gap-4">
              <section className="card overflow-hidden">
                <div className="flex items-start justify-between gap-3 border-b border-surface-200 bg-surface-0 px-4 py-3.5">
                  <div className="flex min-w-0 items-start gap-3">
                    <div className="flex size-8 shrink-0 items-center justify-center rounded-lg border border-violet-500/20 bg-violet-500/10 text-violet-300">
                      <Search size={15} />
                    </div>
                    <div className="min-w-0">
                      <p className="text-[10px] font-semibold uppercase tracking-[0.12em] text-surface-500">Research</p>
                      <h2 className="font-heading text-sm font-semibold text-surface-900">YouTube Data API</h2>
                    </div>
                  </div>
                  <StatusPill state={youtubeDataState} label={youtubeDataStatusLabel} />
                </div>

                <div className="space-y-3 p-4">
                  <div>
                    <label className="label" htmlFor="youtube-data-api-key">API Key</label>
                    <input
                      id="youtube-data-api-key"
                      className="input h-10 font-mono text-xs"
                      type="password"
                      value={youtubeDataKey}
                      onChange={(event) => {
                        setYoutubeDataKey(event.target.value)
                        setYoutubeDataState(event.target.value.trim() ? 'typing' : (youtubeDataKeyHint ? 'connected' : 'idle'))
                        setYoutubeDataMessage(event.target.value.trim()
                          ? 'Verify the key before starting niche research.'
                          : youtubeDataKeyHint
                            ? 'YouTube Data API key is connected.'
                            : 'Enter a YouTube Data API key to enable niche research.')
                      }}
                      placeholder={youtubeDataKeyHint
                        ? `Stored key ${youtubeDataKeyHint} — enter a new key to replace it`
                        : 'Paste a Google Cloud API key'}
                      autoComplete="new-password"
                      spellCheck={false}
                      disabled={youtubeDataLoading || youtubeDataState === 'verifying'}
                    />
                  </div>

                  <div className="flex flex-col gap-3 border-t border-surface-200 pt-3 sm:flex-row sm:items-center sm:justify-between xl:flex-col xl:items-stretch 2xl:flex-row 2xl:items-center">
                    <p className={cn('min-w-0 flex-1 text-xs leading-relaxed', youtubeDataState === 'error' ? 'text-rose-300' : 'text-surface-500')}>
                      {youtubeDataLoading ? 'Loading settings...' : youtubeDataMessage}
                    </p>
                    <Button size="sm" className="shrink-0" onClick={() => void verifyYoutubeDataKey()} disabled={youtubeDataLoading || !youtubeDataKey.trim() || youtubeDataState === 'verifying'}>
                      {youtubeDataState === 'verifying' ? <RefreshCw className="animate-spin" /> : <CheckCircle2 />}
                      Verify key
                    </Button>
                  </div>
                </div>
              </section>

              <section className="card overflow-hidden">
                <div className="flex items-start justify-between gap-3 border-b border-surface-200 bg-surface-0 px-4 py-3.5">
                  <div className="flex min-w-0 items-start gap-3">
                    <div className="flex size-8 shrink-0 items-center justify-center rounded-lg border border-sky-500/20 bg-sky-500/10 text-sky-300">
                      <AudioLines size={15} />
                    </div>
                    <div className="min-w-0">
                      <p className="text-[10px] font-semibold uppercase tracking-[0.12em] text-surface-500">Voice engine</p>
                      <h2 className="font-heading text-sm font-semibold text-surface-900">AI33.pro API</h2>
                    </div>
                  </div>
                  <StatusPill state={ai33State} label={ai33StatusLabel} />
                </div>

                <div className="space-y-3 p-4">
                  <div>
                    <label className="label" htmlFor="ai33-api-key">API Key</label>
                    <input
                      id="ai33-api-key"
                      className="input h-10 font-mono text-xs"
                      type="password"
                      value={ai33Key}
                      onChange={(event) => {
                        const nextKey = event.target.value
                        const normalizedKey = nextKey.trim()
                        ai33RequestId.current += 1
                        setAi33Key(nextKey)
                        setAi33Dirty(true)
                        if (!normalizedKey) {
                          setAi33State(ai33Configured ? 'connected' : 'idle')
                          setAi33Message(ai33Configured ? 'AI33.pro API key is connected.' : 'Enter an AI33.pro API key to connect.')
                        } else if (normalizedKey.length < 8) {
                          setAi33State('typing')
                          setAi33Message('Continue entering the AI33.pro API key.')
                        } else {
                          setAi33State('typing')
                          setAi33Message('AI33.pro will verify automatically when you stop typing.')
                        }
                      }}
                      placeholder={ai33KeyHint ? `Stored key ${ai33KeyHint} — enter a new key` : 'Paste your AI33.pro API key'}
                      autoComplete="new-password"
                      spellCheck={false}
                      disabled={ai33Loading || ai33State === 'verifying'}
                    />
                  </div>

                  <div className="flex items-start gap-2 border-t border-surface-200 pt-3">
                    {ai33State === 'verifying'
                      ? <RefreshCw size={13} className="mt-0.5 shrink-0 animate-spin text-sky-300" />
                      : ai33State === 'connected'
                        ? <CheckCircle2 size={13} className="mt-0.5 shrink-0 text-emerald-300" />
                        : <ShieldAlert size={13} className={cn('mt-0.5 shrink-0', ai33State === 'error' ? 'text-rose-300' : 'text-surface-500')} />}
                    <p className={cn('text-xs leading-relaxed', ai33State === 'error' ? 'text-rose-300' : 'text-surface-500')}>
                      {ai33Loading ? 'Loading settings...' : ai33Message}
                    </p>
                  </div>
                </div>
              </section>
            </div>
          </div>
        </div>
      </div>
    </div>
  )
}
