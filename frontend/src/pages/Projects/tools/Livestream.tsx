import { useState, useEffect, useRef, useCallback } from 'react'
import { open } from '@tauri-apps/plugin-dialog'
import {
  Radio, Play, Square, Plus, ChevronDown, ChevronUp, Trash2,
  FolderOpen, RefreshCw, X, CheckCircle, AlertTriangle, Loader,
  Wifi, WifiOff, Terminal, Settings,
} from 'lucide-react'
import { cn } from '@/lib/utils'
import { taskStore } from '@/store/task.store'

const API = 'http://127.0.0.1:8765'

// ─── Types ────────────────────────────────────────────────────────────────────

interface Channel {
  id: string
  name: string
  stream_key: string
  video_dirs: string[]
  play_mode: 'loop' | 'play_once'
  rtmp_url: string
  recursive: boolean
  shuffle: boolean
  bitrate: string
  fps: number
  status: 'idle' | 'streaming' | 'error'
}

interface ChannelStats {
  status: 'idle' | 'streaming' | 'error'
  fps: number
  bitrate_kbps: number
  current_video: string
  started_at?: number
  total: number
  ok: number
  fail: number
}

// ─── Helpers ──────────────────────────────────────────────────────────────────

async function apiFetch(path: string, init?: RequestInit) {
  const res = await fetch(`${API}${path}`, {
    headers: { 'Content-Type': 'application/json' },
    ...init,
  })
  if (!res.ok) throw new Error(`${res.status} ${res.statusText}`)
  return res.json()
}

function StatusBadge({ status }: { status: string }) {
  return (
    <span className={cn(
      'inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-xs font-medium',
      status === 'streaming' ? 'bg-green-100 text-green-700' :
      status === 'error'     ? 'bg-red-100 text-red-700'     :
                               'bg-surface-100 text-surface-500',
    )}>
      {status === 'streaming' && <span className="w-1.5 h-1.5 rounded-full bg-green-500 animate-pulse" />}
      {status === 'error'     && <span className="w-1.5 h-1.5 rounded-full bg-red-500" />}
      {status === 'idle'      && <span className="w-1.5 h-1.5 rounded-full bg-surface-300" />}
      {status}
    </span>
  )
}

// ─── Channel Card ─────────────────────────────────────────────────────────────

function ChannelCard({
  ch,
  stats,
  logs: _logs,
  onUpdate,
  onDelete,
  onStart,
  onStop,
  selectedId,
  onSelect,
}: {
  ch: Channel
  stats: ChannelStats | null
  logs: string[]
  onUpdate: (id: string, patch: Partial<Channel>) => void
  onDelete: (id: string) => void
  onStart: (id: string) => void
  onStop: (id: string) => void
  selectedId: string | null
  onSelect: (id: string) => void
}) {
  const [isExpanded, setIsExpanded] = useState(true)
  const status = stats?.status ?? ch.status ?? 'idle'
  const isStreaming = status === 'streaming'
  const isSelected = selectedId === ch.id

  const handleAddFolder = async () => {
    try {
      const selected = await open({ directory: true, multiple: false })
      if (selected && typeof selected === 'string') {
        onUpdate(ch.id, { video_dirs: [...ch.video_dirs, selected] })
      }
    } catch { /* cancelled */ }
  }

  const removeFolder = (dir: string) => {
    onUpdate(ch.id, { video_dirs: ch.video_dirs.filter(d => d !== dir) })
  }

  return (
    <div
      className={cn(
        'rounded-xl border bg-white overflow-hidden transition-all',
        isStreaming ? 'border-green-300/60' :
        status === 'error' ? 'border-red-200' :
        isSelected ? 'border-primary-300' : 'border-surface-200',
      )}
    >
      {/* Header */}
      <div
        className="flex items-center gap-3 px-4 py-3 cursor-pointer hover:bg-surface-50 transition-colors"
        onClick={() => { setIsExpanded(p => !p); onSelect(ch.id) }}
      >
        <div className={cn(
          'w-8 h-8 rounded-full flex items-center justify-center shrink-0',
          isStreaming ? 'bg-green-100' : 'bg-surface-100',
        )}>
          {isStreaming
            ? <Radio size={14} className="text-green-600 animate-pulse" />
            : <Radio size={14} className="text-surface-400" />
          }
        </div>

        <div className="flex-1 min-w-0">
          <p className="text-sm font-semibold text-surface-900 truncate">{ch.name}</p>
          <div className="flex items-center gap-2 mt-0.5">
            <StatusBadge status={status} />
            {isStreaming && stats?.current_video && (
              <span className="text-xs text-surface-400 truncate max-w-[140px]">{stats.current_video}</span>
            )}
          </div>
        </div>

        {/* Stats chips */}
        {isStreaming && stats && (
          <div className="hidden sm:flex items-center gap-2 shrink-0">
            {stats.fps > 0 && (
              <span className="text-xs font-mono bg-green-50 text-green-700 px-1.5 py-0.5 rounded">
                {stats.fps.toFixed(0)} fps
              </span>
            )}
            {stats.bitrate_kbps > 0 && (
              <span className="text-xs font-mono bg-blue-50 text-blue-700 px-1.5 py-0.5 rounded">
                {(stats.bitrate_kbps / 1000).toFixed(1)} Mb/s
              </span>
            )}
          </div>
        )}

        {/* Start/Stop button */}
        <button
          className={cn(
            'shrink-0 flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs font-medium transition-all',
            isStreaming
              ? 'bg-red-500 text-white hover:bg-red-600'
              : 'bg-green-500 text-white hover:bg-green-600',
          )}
          onClick={e => { e.stopPropagation(); isStreaming ? onStop(ch.id) : onStart(ch.id) }}
        >
          {isStreaming ? <><Square size={11} /> Stop</> : <><Play size={11} /> Go Live</>}
        </button>

        {/* Expand toggle */}
        {isExpanded
          ? <ChevronUp size={14} className="shrink-0 text-surface-400" />
          : <ChevronDown size={14} className="shrink-0 text-surface-400" />
        }
      </div>

      {/* Expanded config */}
      {isExpanded && (
        <div className="px-4 pb-4 space-y-3 border-t border-surface-100">
          <div className="pt-3 grid grid-cols-2 gap-3">

            {/* Name */}
            <div className="col-span-2">
              <label className="label mb-1">Channel name</label>
              <input
                className="input text-sm w-full"
                value={ch.name}
                onChange={e => onUpdate(ch.id, { name: e.target.value })}
              />
            </div>

            {/* Stream key */}
            <div className="col-span-2">
              <label className="label mb-1">Stream key</label>
              <input
                type="password"
                className="input text-sm w-full font-mono"
                value={ch.stream_key}
                onChange={e => onUpdate(ch.id, { stream_key: e.target.value })}
                placeholder="xxxx-xxxx-xxxx-xxxx-xxxx"
              />
            </div>

            {/* RTMP URL */}
            <div className="col-span-2">
              <label className="label mb-1">RTMP URL</label>
              <input
                className="input text-sm w-full font-mono"
                value={ch.rtmp_url}
                onChange={e => onUpdate(ch.id, { rtmp_url: e.target.value })}
              />
            </div>

            {/* Video folders */}
            <div className="col-span-2">
              <label className="label mb-1">Video folders</label>
              <div className="space-y-1.5">
                {ch.video_dirs.map(dir => (
                  <div key={dir} className="flex items-center gap-2 bg-surface-50 rounded-lg px-3 py-1.5">
                    <FolderOpen size={12} className="text-surface-400 shrink-0" />
                    <span className="text-xs text-surface-600 font-mono flex-1 truncate" title={dir}>
                      {dir.split('/').slice(-2).join('/')}
                    </span>
                    <button
                      className="text-surface-300 hover:text-red-400"
                      onClick={() => removeFolder(dir)}
                    >
                      <X size={12} />
                    </button>
                  </div>
                ))}
                <button
                  className="w-full flex items-center justify-center gap-1.5 px-3 py-1.5 rounded-lg border border-dashed border-surface-300 text-xs text-surface-500 hover:border-primary-400 hover:text-primary-600 transition-colors"
                  onClick={handleAddFolder}
                >
                  <FolderOpen size={12} /> Add folder
                </button>
              </div>
            </div>

            {/* Play mode */}
            <div>
              <label className="label mb-1">Play mode</label>
              <div className="flex rounded-xl overflow-hidden border border-surface-200">
                {(['loop', 'play_once'] as const).map(m => (
                  <button
                    key={m}
                    className={cn(
                      'flex-1 py-1.5 text-xs font-medium transition-colors',
                      ch.play_mode === m
                        ? 'bg-primary-600 text-white'
                        : 'bg-white text-surface-600 hover:bg-surface-50',
                    )}
                    onClick={() => onUpdate(ch.id, { play_mode: m })}
                  >
                    {m === 'loop' ? 'Loop' : 'Play once'}
                  </button>
                ))}
              </div>
            </div>

            {/* Bitrate */}
            <div>
              <label className="label mb-1">Bitrate</label>
              <input
                className="input text-sm w-full font-mono"
                value={ch.bitrate}
                onChange={e => onUpdate(ch.id, { bitrate: e.target.value })}
                placeholder="6000k"
              />
            </div>

            {/* FPS */}
            <div>
              <label className="label mb-1">FPS</label>
              <input
                type="number"
                className="input text-sm w-full"
                value={ch.fps}
                onChange={e => onUpdate(ch.id, { fps: parseInt(e.target.value) || 30 })}
                min={15} max={60}
              />
            </div>

            {/* Toggles */}
            <div className="col-span-1 space-y-2">
              {([
                { key: 'shuffle' as const,   label: 'Shuffle' },
                { key: 'recursive' as const, label: 'Recursive scan' },
              ] as const).map(({ key, label }) => (
                <button
                  key={key}
                  className="w-full flex items-center justify-between px-3 py-2 rounded-lg border border-surface-200 hover:bg-surface-50 transition-colors"
                  onClick={() => onUpdate(ch.id, { [key]: !ch[key] })}
                >
                  <span className="text-xs font-medium text-surface-700">{label}</span>
                  <div className={cn(
                    'w-8 h-4 rounded-full relative transition-colors',
                    ch[key] ? 'bg-primary-500' : 'bg-surface-200',
                  )}>
                    <div className={cn(
                      'absolute top-0.5 w-3 h-3 bg-white rounded-full shadow transition-transform',
                      ch[key] ? 'translate-x-4' : 'translate-x-0.5',
                    )} />
                  </div>
                </button>
              ))}
            </div>
          </div>

          {/* Delete */}
          <div className="flex justify-end pt-1">
            <button
              className="flex items-center gap-1.5 px-3 py-1.5 text-xs text-red-500 hover:bg-red-50 rounded-lg transition-colors"
              onClick={() => onDelete(ch.id)}
            >
              <Trash2 size={12} /> Delete channel
            </button>
          </div>
        </div>
      )}
    </div>
  )
}

// ─── Main Component ───────────────────────────────────────────────────────────

type FfmpegStatus = 'checking' | 'installed' | 'missing'

export default function Livestream() {
  const [channels, setChannels] = useState<Channel[]>([])
  const [statusMap, setStatusMap]   = useState<Record<string, ChannelStats>>({})
  const [logsMap, setLogsMap]       = useState<Record<string, string[]>>({})
  const [selectedId, setSelectedId] = useState<string | null>(null)
  const [loading, setLoading]       = useState(false)
  const [ffmpeg, setFfmpeg]         = useState<{ status: FfmpegStatus; version?: string }>({ status: 'checking' })
  const logsRef = useRef<HTMLDivElement>(null)

  // ── Fetch channels ──────────────────────────────────────────────────────────
  const fetchChannels = useCallback(async () => {
    try {
      const data = await apiFetch('/api/livestream')
      setChannels(data)
      if (data.length > 0 && !selectedId) setSelectedId(data[0].id)
    } catch { /* backend not running yet */ }
  }, [selectedId])

  // ── Poll status for all channels ───────────────────────────────────────────
  const pollStatus = useCallback(async () => {
    for (const ch of channels) {
      try {
        const data = await apiFetch(`/api/livestream/${ch.id}/status`)
        setStatusMap(prev => ({ ...prev, [ch.id]: data.stats }))
        setLogsMap(prev => ({ ...prev, [ch.id]: data.logs }))
      } catch { /* ignore */ }
    }
  }, [channels])

  useEffect(() => {
    fetchChannels()
    apiFetch('/api/livestream/ffmpeg-check')
      .then(d => setFfmpeg({ status: d.installed ? 'installed' : 'missing', version: d.version ?? undefined }))
      .catch(() => setFfmpeg({ status: 'missing' }))
  }, [])

  useEffect(() => {
    if (channels.length === 0) return
    const t = setInterval(pollStatus, 2000)
    return () => clearInterval(t)
  }, [channels, pollStatus])

  // Auto-scroll logs
  useEffect(() => {
    if (logsRef.current) {
      logsRef.current.scrollTop = logsRef.current.scrollHeight
    }
  }, [logsMap, selectedId])

  // ── CRUD + controls ────────────────────────────────────────────────────────
  const createChannel = async () => {
    setLoading(true)
    const taskId = taskStore.add({ toolId: 'livestream', toolLabel: 'Livestream', label: 'Create livestream channel' })
    try {
      const data = await apiFetch('/api/livestream', {
        method: 'POST',
        body: JSON.stringify({
          name: `Channel ${channels.length + 1}`,
          stream_key: '',
          video_dirs: [],
        }),
      })
      setChannels(prev => [...prev, data])
      setSelectedId(data.id)
      taskStore.complete(taskId, 'done', `Created "${data.name}"`)
    } catch (e) {
      alert(`Failed to create channel: ${e}`)
      taskStore.complete(taskId, 'error', String(e))
    } finally {
      setLoading(false)
    }
  }

  const updateChannel = async (id: string, patch: Partial<Channel>) => {
    setChannels(prev => prev.map(c => c.id === id ? { ...c, ...patch } : c))
    try {
      await apiFetch(`/api/livestream/${id}`, {
        method: 'PUT',
        body: JSON.stringify(patch),
      })
    } catch { /* retry on next edit */ }
  }

  const deleteChannel = async (id: string) => {
    try {
      await apiFetch(`/api/livestream/${id}`, { method: 'DELETE' })
      setChannels(prev => prev.filter(c => c.id !== id))
      if (selectedId === id) setSelectedId(channels.find(c => c.id !== id)?.id ?? null)
    } catch (e) {
      alert(`Failed to delete channel: ${e}`)
    }
  }

  const startChannel = async (id: string) => {
    try {
      await apiFetch(`/api/livestream/${id}/start`, { method: 'POST' })
      setStatusMap(prev => ({ ...prev, [id]: { ...(prev[id] ?? {}), status: 'streaming' } as ChannelStats }))
    } catch (e) {
      alert(`Failed to start: ${e}`)
    }
  }

  const stopChannel = async (id: string) => {
    try {
      await apiFetch(`/api/livestream/${id}/stop`, { method: 'POST' })
      setStatusMap(prev => ({ ...prev, [id]: { ...(prev[id] ?? {}), status: 'idle' } as ChannelStats }))
    } catch (e) {
      alert(`Failed to stop: ${e}`)
    }
  }

  const clearLogs = async (id: string) => {
    try {
      await apiFetch(`/api/livestream/${id}/logs`, { method: 'DELETE' })
      setLogsMap(prev => ({ ...prev, [id]: [] }))
    } catch { /* ignore */ }
  }

  const streamingCount = channels.filter(c => (statusMap[c.id]?.status ?? c.status) === 'streaming').length
  const selectedLogs   = selectedId ? (logsMap[selectedId] ?? []) : []

  return (
    <div className="flex flex-col h-full">
      {/* ── Header ──────────────────────────────────────────────────────────── */}
      <div className="flex items-center justify-between px-6 py-4 border-b border-surface-200 bg-white">
        <div className="flex items-center gap-3">
          <div className={cn(
            'w-8 h-8 rounded-lg flex items-center justify-center',
            streamingCount > 0 ? 'bg-green-100' : 'bg-rose-50',
          )}>
            <Radio size={16} className={streamingCount > 0 ? 'text-green-600 animate-pulse' : 'text-rose-500'} />
          </div>
          <div>
            <h1 className="page-title">Livestream Studio</h1>
            <div className="flex items-center gap-2 mt-0.5">
              <p className="page-sub">24/7 YouTube RTMP · FFmpeg</p>
              {ffmpeg.status === 'checking' && (
                <span className="inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-xs bg-surface-100 text-surface-400">
                  <Loader size={10} className="animate-spin" /> checking…
                </span>
              )}
              {ffmpeg.status === 'installed' && (
                <span className="inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-xs bg-green-100 text-green-700 font-medium">
                  <CheckCircle size={10} />
                  installed{ffmpeg.version ? ` · ${ffmpeg.version}` : ''}
                </span>
              )}
              {ffmpeg.status === 'missing' && (
                <span className="inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-xs bg-red-100 text-red-600 font-medium">
                  <AlertTriangle size={10} />
                  not installed — <a href="https://ffmpeg.org/download.html" target="_blank" rel="noreferrer" className="underline">download</a>
                </span>
              )}
            </div>
          </div>
        </div>

        <div className="flex items-center gap-2">
          {streamingCount > 0 && (
            <span className="flex items-center gap-1.5 px-2.5 py-1 rounded-full bg-green-100 text-xs font-medium text-green-700">
              <span className="w-1.5 h-1.5 rounded-full bg-green-500 animate-pulse" />
              {streamingCount} live
            </span>
          )}
          <button className="btn-secondary text-xs" onClick={fetchChannels}>
            <RefreshCw size={12} /> Refresh
          </button>
          <button
            className="btn-primary"
            onClick={createChannel}
            disabled={loading}
          >
            {loading ? <Loader size={14} className="animate-spin" /> : <Plus size={14} />}
            Add Channel
          </button>
        </div>
      </div>

      {/* ── Body ────────────────────────────────────────────────────────────── */}
      <div className="flex-1 overflow-hidden flex">

        {/* Left: channel list */}
        <div className="w-[420px] shrink-0 border-r border-surface-200 flex flex-col overflow-hidden">
          <div className="flex-1 overflow-y-auto p-4 space-y-3">
            {channels.length === 0 ? (
              <div className="flex flex-col items-center justify-center h-48 text-surface-400">
                <WifiOff size={28} className="mb-3 opacity-30" />
                <p className="text-sm font-medium">No channels yet</p>
                <p className="text-xs mt-1">Click "Add Channel" to get started</p>
              </div>
            ) : (
              channels.map(ch => (
                <ChannelCard
                  key={ch.id}
                  ch={ch}
                  stats={statusMap[ch.id] ?? null}
                  logs={logsMap[ch.id] ?? []}
                  onUpdate={updateChannel}
                  onDelete={deleteChannel}
                  onStart={startChannel}
                  onStop={stopChannel}
                  selectedId={selectedId}
                  onSelect={setSelectedId}
                />
              ))
            )}
          </div>
        </div>

        {/* Right: logs + stats */}
        <div className="flex-1 flex flex-col overflow-hidden">
          {selectedId ? (
            <>
              {/* Stats bar */}
              {statusMap[selectedId] && (
                <div className="flex items-center gap-4 px-5 py-2.5 border-b border-surface-200 bg-surface-50">
                  <div className="flex items-center gap-1.5 text-xs text-surface-600">
                    <Settings size={11} className="text-surface-400" />
                    <span className="font-mono">
                      {channels.find(c => c.id === selectedId)?.name}
                    </span>
                  </div>
                  {statusMap[selectedId].fps > 0 && (
                    <span className="text-xs font-mono bg-white border border-surface-200 px-2 py-0.5 rounded text-surface-700">
                      {statusMap[selectedId].fps.toFixed(0)} fps
                    </span>
                  )}
                  {statusMap[selectedId].bitrate_kbps > 0 && (
                    <span className="text-xs font-mono bg-white border border-surface-200 px-2 py-0.5 rounded text-surface-700">
                      {(statusMap[selectedId].bitrate_kbps / 1000).toFixed(1)} Mb/s
                    </span>
                  )}
                  {statusMap[selectedId].current_video && (
                    <span className="text-xs text-surface-500 truncate flex-1">
                      ▶ {statusMap[selectedId].current_video}
                    </span>
                  )}
                  {(statusMap[selectedId].total ?? 0) > 0 && (
                    <span className="text-xs text-surface-400 ml-auto shrink-0">
                      {statusMap[selectedId].ok}/{statusMap[selectedId].total} ok
                    </span>
                  )}
                </div>
              )}

              {/* Log header */}
              <div className="flex items-center justify-between px-5 py-2 border-b border-surface-100">
                <div className="flex items-center gap-2 text-xs text-surface-500">
                  <Terminal size={12} />
                  Activity log
                  <span className="text-surface-300">({selectedLogs.length} lines)</span>
                </div>
                <button
                  className="text-xs text-surface-400 hover:text-surface-600"
                  onClick={() => clearLogs(selectedId)}
                >
                  Clear
                </button>
              </div>

              {/* Logs */}
              <div
                ref={logsRef}
                className="flex-1 overflow-y-auto p-4 bg-surface-900 font-mono"
              >
                {selectedLogs.length === 0 ? (
                  <p className="text-xs text-surface-500 italic">No logs yet. Start the stream to see activity.</p>
                ) : (
                  selectedLogs.map((line, i) => (
                    <div
                      key={i}
                      className={cn(
                        'text-xs leading-5',
                        line.includes('✗') || line.includes('error') || line.includes('Error')
                          ? 'text-red-400'
                          : line.includes('✓') || line.includes('success')
                          ? 'text-green-400'
                          : line.includes('▶') || line.includes('→')
                          ? 'text-blue-300'
                          : 'text-surface-300',
                      )}
                    >
                      {line}
                    </div>
                  ))
                )}
              </div>
            </>
          ) : (
            <div className="flex-1 flex flex-col items-center justify-center text-surface-400">
              <Wifi size={32} className="mb-3 opacity-20" />
              <p className="text-sm">Select a channel to view logs</p>
            </div>
          )}
        </div>
      </div>
    </div>
  )
}
