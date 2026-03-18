import { useState, useCallback, useEffect, useRef } from 'react'
import { motion, AnimatePresence } from 'framer-motion'
import { useAppStore, type CapcutProject } from '@/store/app.store'
import {
  RefreshCw, Play, Loader, CheckCircle, XCircle, Clock,
  Film, Video, Zap, Layers, ServerCrash, Inbox,
} from 'lucide-react'
import { cn } from '@/lib/utils'
import { projectsApi, syncApi, type ApiCapcutProject, type SyncResponse } from '@/lib/api'
import AutomateV2 from './AutomateV2'

const STATUS_CONFIG = {
  pending:    { label: 'Pending',    cls: 'badge-neutral', icon: Clock },
  processing: { label: 'Processing', cls: 'badge-info',    icon: Loader },
  done:       { label: 'Done',       cls: 'badge-success', icon: CheckCircle },
  failed:     { label: 'Failed',     cls: 'badge-error',   icon: XCircle },
}

function apiToStore(a: ApiCapcutProject): CapcutProject {
  return {
    id: a.id,
    name: a.name,
    path: a.path,
    source: a.source,
    status: a.status,
    notes: a.notes,
    assignedProjectId: a.assigned_project_id,
    isSelected: a.is_selected,
    selectionOrder: a.selection_order,
    metadata: {
      durationS: a.metadata.duration_s,
      fps: a.metadata.fps,
      width: a.metadata.width ?? 1920,
      height: a.metadata.height ?? 1080,
      videoSegments: a.metadata.video_segments,
      trackCount: a.metadata.track_count,
      ffmpegReady: a.metadata.ffmpeg_ready,
      modifiedAt: a.metadata.modified_at ?? '',
    },
  }
}

type FetchStatus = 'idle' | 'loading' | 'ok' | 'error'

const MAX_RETRIES = 6
const RETRY_DELAY = 1500

export default function RenderDashboard() {
  const { capcutProjects } = useAppStore()

  const [view, setView]                 = useState<'projects' | 'automate-v2'>('projects')
  const [selected, setSelected]         = useState<Set<string>>(new Set())
  const [fetchStatus, setFetchStatus]   = useState<FetchStatus>('idle')
  const [fetchError, setFetchError]     = useState<string | null>(null)
  const [retryCount, setRetryCount]     = useState(0)
  const [bgRefreshing, setBgRefreshing] = useState(false)
  const retryTimer = useRef<ReturnType<typeof setTimeout> | null>(null)
  const [syncingOp, setSyncingOp]       = useState<string | null>(null)
  const [toast, setToast]               = useState<{ type: 'ok' | 'err'; msg: string } | null>(null)
  const toastTimer = useRef<ReturnType<typeof setTimeout> | null>(null)

  const totalSelected = selected.size
  const totalDone     = capcutProjects.filter(p => p.status === 'done').length

  const toggle = (id: string) => setSelected(prev => {
    const next = new Set(prev)
    next.has(id) ? next.delete(id) : next.add(id)
    return next
  })

  const handleDiscover = useCallback(async (isRetry = false) => {
    if (!isRetry) setRetryCount(0)
    setFetchStatus('loading')
    setFetchError(null)
    try {
      const raw = await projectsApi.discover()
      const mapped = raw.map(apiToStore)
      useAppStore.setState({ capcutProjects: mapped })
      setFetchStatus('ok')
    } catch (err: any) {
      setFetchStatus('error')
      setFetchError(err?.message ?? 'Could not connect to the API.')
    }
  }, [])

  // Auto-discover on mount
  // - If cache exists → silent background refresh (no loading spinner)
  // - If no cache → show full loading state
  useEffect(() => {
    let cancelled = false
    const hasCached = capcutProjects.length > 0

    const tryConnect = async (attempt: number) => {
      if (cancelled) return
      if (!hasCached) { setFetchStatus('loading'); setFetchError(null) }
      else setBgRefreshing(true)

      try {
        const raw = await projectsApi.discover()
        if (cancelled) return
        useAppStore.setState({ capcutProjects: raw.map(apiToStore) })
        setFetchStatus('ok')
        setBgRefreshing(false)
      } catch {
        if (cancelled) return
        setBgRefreshing(false)
        if (!hasCached) {
          const next = attempt + 1
          setRetryCount(next)
          if (next < MAX_RETRIES) {
            retryTimer.current = setTimeout(() => tryConnect(next), RETRY_DELAY)
          } else {
            setFetchStatus('error')
            setFetchError('The API is not responding. Make sure `start_api.py` is running.')
          }
        }
        // If we have cached data and API fails → silently keep cache, no error shown
      }
    }

    tryConnect(0)
    return () => {
      cancelled = true
      if (retryTimer.current) clearTimeout(retryTimer.current)
    }
  }, []) // eslint-disable-line react-hooks/exhaustive-deps

  const showToast = (type: 'ok' | 'err', msg: string) => {
    setToast({ type, msg })
    if (toastTimer.current) clearTimeout(toastTimer.current)
    toastTimer.current = setTimeout(() => setToast(null), 4000)
  }

  const handleSync = async (op: 'Audio' | 'Images' | 'Captions') => {
    const paths = capcutProjects
      .filter(p => selected.size === 0 || selected.has(p.id))
      .map(p => p.path)
    if (paths.length === 0) { showToast('err', 'There are no projects to sync'); return }

    setSyncingOp(op)
    try {
      let res: SyncResponse
      if (op === 'Audio')    res = await syncApi.audio(paths)
      else if (op === 'Images')  res = await syncApi.images(paths)
      else                       res = await syncApi.captions(paths)

      if (res.failed === 0) {
        showToast('ok', `${op}: ${res.success} projects succeeded`)
      } else {
        showToast('err', `${op}: ${res.success} OK · ${res.failed} failed`)
      }
    } catch (err: any) {
      showToast('err', err?.message ?? `${op} sync failed`)
    } finally {
      setSyncingOp(null)
    }
  }

  return (
    <div className="flex flex-col h-full">
      {/* Header */}
      <div className="flex items-center justify-between px-6 py-4 border-b border-surface-200 bg-white">
        <div>
          <h1 className="page-title">Render Dashboard</h1>
          <p className="page-sub mt-0.5">Manage and render CapCut projects</p>
        </div>
        <div className="flex items-center gap-2">
          {bgRefreshing && (
            <span className="flex items-center gap-1 text-xs text-surface-400 mr-1">
              <RefreshCw size={11} className="animate-spin" />
              Syncing...
            </span>
          )}
          <button
            className="btn-secondary"
            onClick={() => handleDiscover(false)}
            disabled={fetchStatus === 'loading'}
          >
            <RefreshCw size={13} className={fetchStatus === 'loading' ? 'animate-spin' : ''} />
            {fetchStatus === 'loading'
              ? retryCount > 0 ? `Connecting... (${retryCount}/${MAX_RETRIES})` : 'Scanning...'
              : 'Discover Projects'}
          </button>
          <button
            className="btn-primary"
            onClick={() => setView('automate-v2')}
            disabled={selected.size === 0}
          >
            <Zap size={13} />
            Auto Render {selected.size > 0 ? `(${selected.size})` : ''}
          </button>
        </div>
      </div>

      {/* Stats */}
      <div className="grid grid-cols-3 gap-4 px-6 py-4 border-b border-surface-200 bg-surface-50">
        <div className="stat-card bg-gradient-to-br from-primary-500 to-primary-600 text-white">
          <div className="flex items-center gap-2">
            <Film size={14} className="opacity-80" />
            <span className="stat-label text-white">Loaded Projects</span>
          </div>
          <span className="stat-value text-white">{capcutProjects.length}</span>
        </div>
        <div className="stat-card bg-gradient-to-br from-amber-400 to-orange-500 text-white">
          <div className="flex items-center gap-2">
            <Video size={14} className="opacity-80" />
            <span className="stat-label text-white">Selected</span>
          </div>
          <span className="stat-value text-white">{totalSelected}</span>
        </div>
        <div className="stat-card bg-gradient-to-br from-emerald-400 to-green-600 text-white">
          <div className="flex items-center gap-2">
            <CheckCircle size={14} className="opacity-80" />
            <span className="stat-label text-white">Done</span>
          </div>
          <span className="stat-value text-white">{totalDone}</span>
        </div>
      </div>

      {/* Sync / View Actions */}
      <div className="flex items-center gap-2 px-6 py-3 border-b border-surface-200 bg-surface-50">
        <span className="text-xs font-medium text-surface-400 mr-1">Sync:</span>
        {(['Audio', 'Images', 'Captions'] as const).map(op => (
          <button
            key={op}
            className={cn(
              'inline-flex items-center gap-1.5 text-xs font-medium px-3 py-1.5 rounded-md border transition-colors shadow-sm',
              syncingOp === op
                ? 'border-primary-300 bg-primary-50 text-primary-600 cursor-wait'
                : 'border-surface-200 bg-white text-surface-600 hover:border-primary-400 hover:text-primary-600 hover:bg-primary-50',
            )}
            onClick={() => { setView('projects'); handleSync(op) }}
            disabled={!!syncingOp}
          >
            <Layers size={11} className={syncingOp === op ? 'animate-spin' : ''} />
            {syncingOp === op ? `${op}...` : op}
          </button>
        ))}

        <div className="w-px h-4 bg-surface-200 mx-1" />

        <button
          className={cn(
            'inline-flex items-center gap-1.5 text-xs font-medium px-3 py-1.5 rounded-md border transition-colors shadow-sm',
            view === 'automate-v2'
              ? 'border-violet-300 bg-violet-50 text-violet-700'
              : 'border-surface-200 bg-white text-surface-600 hover:border-violet-400 hover:text-violet-600 hover:bg-violet-50',
          )}
          onClick={() => setView(v => v === 'automate-v2' ? 'projects' : 'automate-v2')}
        >
          <Zap size={11} />
          Automate V2
        </button>
      </div>

      {/* Automate V2 view */}
      {view === 'automate-v2' && (
        <div className="flex-1 overflow-hidden">
          <AutomateV2 />
        </div>
      )}

      {/* Content */}
      {view === 'projects' && <div className="flex-1 overflow-y-auto p-6">

        {/* Sync toast */}
        <AnimatePresence>
          {toast && (
            <motion.div
              initial={{ opacity: 0, y: -8 }} animate={{ opacity: 1, y: 0 }} exit={{ opacity: 0, y: -8 }}
              className={cn(
                'flex items-center gap-2 rounded-lg px-4 py-2.5 mb-4 text-sm font-medium',
                toast.type === 'ok'
                  ? 'bg-green-50 border border-green-200 text-green-700'
                  : 'bg-red-50 border border-red-200 text-red-700',
              )}
            >
              {toast.type === 'ok' ? <CheckCircle size={14} /> : <XCircle size={14} />}
              {toast.msg}
            </motion.div>
          )}
        </AnimatePresence>

        {/* Error banner */}
        <AnimatePresence>
          {fetchStatus === 'error' && fetchError && (
            <motion.div
              initial={{ opacity: 0, y: -8 }} animate={{ opacity: 1, y: 0 }} exit={{ opacity: 0, y: -8 }}
              className="flex items-start gap-3 bg-red-50 border border-red-200 rounded-lg px-4 py-3 mb-4"
            >
              <ServerCrash size={16} className="text-red-400 mt-0.5 shrink-0" />
              <div>
                <p className="text-sm font-medium text-red-700">Could not connect to the API</p>
                <p className="text-xs text-red-500 mt-0.5">{fetchError}</p>
                <code className="text-xs text-red-600 mt-1 block bg-red-100 px-2 py-1 rounded">
                  python start_api.py --port 8765
                </code>
              </div>
            </motion.div>
          )}
        </AnimatePresence>

        {/* Empty state */}
        {capcutProjects.length === 0 && fetchStatus !== 'loading' && (
          <div className="flex flex-col items-center justify-center h-48 text-surface-400">
            <Inbox size={36} className="mb-3 opacity-40" />
            <p className="text-sm font-medium">No projects yet</p>
            <p className="text-xs mt-1">Click <strong>Discover Projects</strong> to scan the CapCut folder</p>
          </div>
        )}

        {/* Loading skeleton */}
        {fetchStatus === 'loading' && (
          <div className="space-y-2">
            {[1, 2, 3, 4, 5].map(i => (
              <div key={i} className="h-12 bg-surface-100 rounded-lg animate-pulse" />
            ))}
          </div>
        )}

        {/* Table */}
        {capcutProjects.length > 0 && fetchStatus !== 'loading' && (
          <div className="card overflow-hidden">
            <table className="w-full text-sm">
              <thead>
                <tr className="border-b border-surface-200 bg-surface-50">
                  <th className="w-8 px-4 py-2.5">
                    <input type="checkbox" className="rounded"
                      checked={selected.size === capcutProjects.length}
                      onChange={e => setSelected(e.target.checked ? new Set(capcutProjects.map(p => p.id)) : new Set())} />
                  </th>
                  <th className="px-4 py-2.5 text-left text-xs font-semibold text-surface-500">Project</th>
                  <th className="px-4 py-2.5 text-left text-xs font-semibold text-surface-500">Status</th>
                  <th className="px-4 py-2.5 text-center text-xs font-semibold text-surface-500">Segments</th>
                  <th className="px-4 py-2.5 text-center text-xs font-semibold text-surface-500">Duration</th>
                  <th className="px-4 py-2.5 text-center text-xs font-semibold text-surface-500">FFmpeg</th>
                  <th className="px-4 py-2.5 text-left text-xs font-semibold text-surface-500">Modified</th>
                  <th className="px-4 py-2.5" />
                </tr>
              </thead>
              <tbody>
                <AnimatePresence>
                  {capcutProjects.map((p, i) => {
                    const { label, cls, icon: StatusIcon } = STATUS_CONFIG[p.status]
                    const durationStr = p.metadata.durationS
                      ? `${Math.floor(p.metadata.durationS / 60)}:${String(Math.round(p.metadata.durationS % 60)).padStart(2, '0')}`
                      : '—'

                    return (
                      <motion.tr
                        key={p.id}
                        initial={{ opacity: 0 }}
                        animate={{ opacity: 1 }}
                        exit={{ opacity: 0 }}
                        transition={{ delay: i * 0.03 }}
                        className={cn(
                          'border-b border-surface-100 hover:bg-surface-50 transition-colors',
                          selected.has(p.id) && 'bg-primary-50/40',
                        )}
                      >
                        <td className="px-4 py-3">
                          <input type="checkbox" className="rounded"
                            checked={selected.has(p.id)} onChange={() => toggle(p.id)} />
                        </td>
                        <td className="px-4 py-3">
                          <p className="font-medium text-surface-900 truncate max-w-[200px]">{p.name}</p>
                          <p className="text-xs text-surface-400 truncate max-w-[200px]">{p.source}</p>
                        </td>
                        <td className="px-4 py-3">
                          <span className={cn('badge flex items-center gap-1 w-fit', cls)}>
                            <StatusIcon size={10} className={p.status === 'processing' ? 'animate-spin' : ''} />
                            {label}
                          </span>
                        </td>
                        <td className="px-4 py-3 text-center text-surface-700">{p.metadata.videoSegments || '—'}</td>
                        <td className="px-4 py-3 text-center text-surface-700 font-mono text-xs">{durationStr}</td>
                        <td className="px-4 py-3 text-center">
                          {p.metadata.ffmpegReady
                            ? <CheckCircle size={14} className="text-green-500 mx-auto" />
                            : <XCircle    size={14} className="text-surface-300 mx-auto" />}
                        </td>
                        <td className="px-4 py-3 text-xs text-surface-400">{p.metadata.modifiedAt || '—'}</td>
                        <td className="px-4 py-3">
                          <button className="btn-icon text-surface-400 hover:text-primary-600 hover:bg-primary-50" title="Run">
                            <Play size={13} />
                          </button>
                        </td>
                      </motion.tr>
                    )
                  })}
                </AnimatePresence>
              </tbody>
            </table>
          </div>
        )}
      </div>}
    </div>
  )
}
