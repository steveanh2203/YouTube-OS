import { useState, useEffect, useRef, useCallback } from 'react'
import { usePanelContext } from '@/contexts/PanelContext'
import { useAppStore } from '@/store/app.store'
import { toast } from '@/store/toast.store'
import * as Tooltip from '@radix-ui/react-tooltip'
import { motion, AnimatePresence } from 'framer-motion'
import { open as openDialog } from '@tauri-apps/plugin-dialog'
import {
  Wifi, WifiOff, Copy, Check,
  FolderOpen, Play, CheckCircle2, XCircle,
  Loader2, FolderSearch, Clapperboard,
  Settings2, Clock, Download, RefreshCw,
  AlertCircle, Trash2, Square, CheckSquare, CircleHelp,
  X,
} from 'lucide-react'
import { cn } from '@/lib/utils'
import SoraJobLogPopup from './SoraJobLogPopup'

const API = 'http://127.0.0.1:8765'
const POLL_INTERVAL = 3000
const FALLBACK_CONNECTION_POLL_MS = 15000
const SORA_WS_URL = 'ws://127.0.0.1:8765/api/sora/ws?client=desktop'
const WS_RECONNECT_DELAY_MS = 1500
const WS_PING_MS = 12000

// ── Types ──────────────────────────────────────────────────────────────────────
type SoraStatus = 'pending' | 'generating' | 'done' | 'failed'
type OutputConflictMode = 'keep_both' | 'replace_all'

interface SoraJobRow {
  id: number
  job_index: number
  prompt: string
  ratio: string
  duration: number
  status: SoraStatus
  progress: number
  video_path: string | null
  generation_id: string | null
  permalink: string | null
  error_msg: string | null
}

interface OutputFolderInspectResult {
  folder: string
  exists: boolean
  video_count: number
  video_sample_names: string[]
}

interface OutputConflictDialogState {
  folder: string
  videoCount: number
  videoSampleNames: string[]
}

type SoraRealtimeMessage =
  | {
    type: 'sora_presence'
    online: boolean
    worker_count: number
    last_seen: number | null
  }
  | {
    type: 'sora_jobs'
    child_project_id: number
    jobs: SoraJobRow[]
  }
  | {
    type: 'sora_job_patch'
    child_project_id: number
    job: SoraJobRow
  }
  | {
    type: 'sora_config'
    config: {
      max_threads?: number
      thread_start_delay?: number
    }
  }
  | {
    type: 'sora_pong'
  }

// ── Prompt helpers (same pattern as AIGen) ─────────────────────────────────────
/** Strip leading number prefix: "1  " / "2. " / "3) " */
function stripNum(line: string) {
  return line.replace(/^\d+[.)\s]\s*/, '').trim()
}

/** Parse text block → clean prompts array */
function parsePrompts(text: string): string[] {
  return text
    .replace(/\r\n/g, '\n').replace(/\r/g, '\n')
    .split('\n')
    .map(l => stripNum(l))
    .filter(l => l.length > 0)
}

/** Format prompts as numbered text: "1  …\n2  …" */
function formatPrompts(prompts: string[]): string {
  return prompts.map((p, i) => `${i + 1}  ${p}`).join('\n')
}

// ── Status badge ───────────────────────────────────────────────────────────────
function StatusBadge({ status }: { status: SoraStatus }) {
  if (status === 'pending')    return (
    <span className="inline-flex items-center gap-1 text-[11px] font-medium text-surface-500 bg-surface-100 border border-surface-200 px-2 py-0.5 rounded-full">
      <Clock size={10} /> Pending
    </span>
  )
  if (status === 'generating') return (
    <span className="inline-flex items-center gap-1 text-[11px] font-medium text-teal-700 bg-teal-50 border border-teal-200 px-2 py-0.5 rounded-full">
      <Loader2 size={10} className="animate-spin" /> Generating…
    </span>
  )
  if (status === 'done') return (
    <span className="inline-flex items-center gap-1 text-[11px] font-medium text-green-700 bg-green-50 border border-green-200 px-2 py-0.5 rounded-full">
      <CheckCircle2 size={10} /> Done
    </span>
  )
  return (
    <span className="inline-flex items-center gap-1 text-[11px] font-medium text-red-600 bg-red-50 border border-red-200 px-2 py-0.5 rounded-full">
      <XCircle size={10} /> Failed
    </span>
  )
}

function StatHelp({
  label,
  help,
  tone = 'default',
}: {
  label: string
  help: string
  tone?: 'default' | 'success' | 'error'
}) {
  const toneClass = tone === 'success'
    ? 'text-green-500 hover:text-green-600'
    : tone === 'error'
      ? 'text-red-400 hover:text-red-500'
      : 'text-surface-400 hover:text-surface-500'

  return (
    <Tooltip.Root delayDuration={120}>
      <Tooltip.Trigger asChild>
        <button
          type="button"
          aria-label={`Guide for ${label}`}
          className={cn(
            'inline-flex h-4 w-4 items-center justify-center rounded-full transition-colors focus:outline-none focus:ring-2 focus:ring-teal-300 focus:ring-offset-1',
            toneClass,
          )}
        >
          <CircleHelp size={12} />
        </button>
      </Tooltip.Trigger>
      <Tooltip.Portal>
        <Tooltip.Content
          side="top"
          sideOffset={8}
          className="z-[70] max-w-[220px] rounded-lg border border-surface-200 bg-white px-2.5 py-2 text-[11px] leading-relaxed text-surface-600 shadow-xl"
        >
          {help}
          <Tooltip.Arrow className="fill-white" />
        </Tooltip.Content>
      </Tooltip.Portal>
    </Tooltip.Root>
  )
}

// ── Mini pipeline stepper ─────────────────────────────────────────────────────
const STAGE_DOTS = [
  { label: 'Generate', active: 'bg-blue-500', done: 'bg-blue-400' },
  { label: 'Publish', active: 'bg-violet-500', done: 'bg-violet-400' },
  { label: 'Remove Watermark', active: 'bg-orange-500', done: 'bg-orange-400' },
  { label: 'Denoise', active: 'bg-cyan-500', done: 'bg-cyan-400' },
  { label: 'Upscale 1080p', active: 'bg-amber-500', done: 'bg-amber-400' },
  { label: 'Save to Folder', active: 'bg-green-500', done: 'bg-green-400' },
] as const

function getPipelineStage(status: SoraStatus, progress: number) {
  if (status === 'done') return STAGE_DOTS.length
  if (progress >= 95) return 6
  if (progress >= 83) return 5
  if (progress >= 71) return 4
  if (progress >= 56) return 3
  if (progress >= 41) return 2
  if (progress > 0) return 1
  return 0
}

function humanizeSoraError(error?: string | null) {
  const value = String(error || '').trim()
  if (!value) return ''
  if (/unable[_\s-]*to[_\s-]*generate/i.test(value)) {
    return 'Unable to generate. Sora could not create this video for the current prompt.'
  }
  if (/create request was not observed/i.test(value)) {
    return 'Generate request did not start. The worker could not confirm Sora accepted the prompt.'
  }
  return value
}

function PipelineDots({ status, progress }: { status: SoraStatus; progress: number }) {
  const doneAll = status === 'done'
  const active = status === 'generating'
  const failed = status === 'failed'
  const stageIndex = getPipelineStage(status, progress)

  return (
    <div className="flex items-center gap-0.5 mt-1">
      {STAGE_DOTS.map((s, i) => {
        const step = i + 1
        const isDone = doneAll || step < stageIndex
        const isActive = active && step === stageIndex
        const isFailedStage = failed && step === Math.max(stageIndex, 1)
        const dotCls = doneAll
          ? 'bg-green-500'
          : isDone
            ? s.done
            : isActive
              ? s.active
              : isFailedStage
                ? 'bg-red-200'
                : 'bg-surface-200'
        return (
          <Tooltip.Root key={s.label} delayDuration={100}>
            <Tooltip.Trigger asChild>
              <span className={cn('w-2 h-2 rounded-full shrink-0 transition-colors', dotCls)} />
            </Tooltip.Trigger>
            <Tooltip.Portal>
              <Tooltip.Content side="top" sideOffset={4}
                className="z-[80] px-1.5 py-0.5 rounded text-[10px] bg-surface-800 text-white">
                {s.label}
                <Tooltip.Arrow className="fill-surface-800" />
              </Tooltip.Content>
            </Tooltip.Portal>
          </Tooltip.Root>
        )
      })}
    </div>
  )
}

// ── Main component ─────────────────────────────────────────────────────────────
export default function SoraGen() {
  const { selectedChildId } = usePanelContext()
  const { childProjects, updateChild } = useAppStore()
  const child = childProjects.find(c => c.id === selectedChildId)

  const [extensionOnline, setExtensionOnline] = useState(false)
  const [workerCount, setWorkerCount]         = useState(0)
  const [apiKey, setApiKey]                   = useState<string | null>(null)
  const [keyCopied, setKeyCopied]             = useState(false)
  const [folderPath, setFolderPath]           = useState('')
  const [rawText, setRawText]                 = useState('')
  const [jobs, setJobs]                       = useState<SoraJobRow[]>([])
  const [submitting, setSubmitting]           = useState(false)
  const [showSettings, setShowSettings]       = useState(true)
  const [selected, setSelected]               = useState<Set<number>>(new Set())
  const [logPopupJobId, setLogPopupJobId]     = useState<number | null>(null)
  const [realtimeConnected, setRealtimeConnected] = useState(false)
  const [outputConflictDialog, setOutputConflictDialog] = useState<OutputConflictDialogState | null>(null)

  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null)
  const wsRef = useRef<WebSocket | null>(null)
  const wsReconnectRef = useRef<ReturnType<typeof setTimeout> | null>(null)
  const wsPingRef = useRef<ReturnType<typeof setInterval> | null>(null)
  const outputConflictResolverRef = useRef<((mode: OutputConflictMode | null) => void) | null>(null)

  // ── Sync folder from child ───────────────────────────────────────────────
  useEffect(() => { setFolderPath(child?.base_folder_path ?? '') }, [child?.base_folder_path])

  // ── Load API key ─────────────────────────────────────────────────────────
  useEffect(() => {
    fetch(`${API}/api/sora/key`)
      .then(r => r.json())
      .then(d => setApiKey(d.key))
      .catch(() => {})
  }, [])

  useEffect(() => () => {
    outputConflictResolverRef.current?.(null)
    outputConflictResolverRef.current = null
  }, [])

  const inspectOutputFolder = useCallback(async (folder: string): Promise<OutputFolderInspectResult> => {
    const res = await fetch(`${API}/api/sora/output-folder/inspect`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ folder }),
    })
    const data = await res.json()
    if (!res.ok) throw new Error(data.detail ?? 'Cannot inspect output folder')
    return data as OutputFolderInspectResult
  }, [])

  const initOutputFolder = useCallback(async (folder: string, conflictMode: OutputConflictMode) => {
    const res = await fetch(`${API}/api/sora/output-folder/init`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ folder, conflict_mode: conflictMode }),
    })
    const data = await res.json()
    if (!res.ok) throw new Error(data.detail ?? 'Cannot prepare output folder')
    return data
  }, [])

  const askOutputConflictMode = useCallback((info: OutputConflictDialogState) => (
    new Promise<OutputConflictMode | null>((resolve) => {
      outputConflictResolverRef.current = resolve
      setOutputConflictDialog(info)
    })
  ), [])

  const resolveOutputConflictMode = useCallback((mode: OutputConflictMode | null) => {
    const resolver = outputConflictResolverRef.current
    outputConflictResolverRef.current = null
    setOutputConflictDialog(null)
    resolver?.(mode)
  }, [])

  const ensureOutputFolderMode = useCallback(async (folder: string): Promise<OutputConflictMode | null> => {
    const target = folder.trim()
    if (!target) return 'keep_both'
    const info = await inspectOutputFolder(target)
    if ((info.video_count || 0) === 0) return 'keep_both'
    return askOutputConflictMode({
      folder: target,
      videoCount: info.video_count || 0,
      videoSampleNames: info.video_sample_names || [],
    })
  }, [askOutputConflictMode, inspectOutputFolder])

  // ── Connection status poll ───────────────────────────────────────────────
  const checkConnection = useCallback(async () => {
    try {
      const res  = await fetch(`${API}/api/sora/connection-status`)
      const data = await res.json()
      setExtensionOnline(data.online === true)
      setWorkerCount(data.worker_count ?? 0)
    } catch {
      setExtensionOnline(false)
      setWorkerCount(0)
    }
  }, [])

  useEffect(() => {
    if (realtimeConnected) return
    checkConnection()
    const id = setInterval(checkConnection, FALLBACK_CONNECTION_POLL_MS)
    return () => clearInterval(id)
  }, [checkConnection, realtimeConnected])

  const patchJob = useCallback((job: SoraJobRow) => {
    setJobs(prev => {
      const next = [...prev]
      const idx = next.findIndex(item => item.id === job.id)
      if (idx >= 0) next[idx] = job
      else next.push(job)
      next.sort((a, b) => a.job_index - b.job_index)
      return next
    })
  }, [])

  const fetchJobs = useCallback(async (childId: number) => {
    try {
      const res  = await fetch(`${API}/api/sora/jobs/${childId}`)
      const data = await res.json()
      setJobs(data.jobs ?? [])
      return data.jobs as SoraJobRow[]
    } catch {
      return []
    }
  }, [])

  const startPolling = useCallback((childId: number) => {
    if (realtimeConnected) return
    if (pollRef.current) clearInterval(pollRef.current)
    pollRef.current = setInterval(async () => {
      const fetched = await fetchJobs(childId)
      const anyActive = fetched.some(j => j.status === 'pending' || j.status === 'generating')
      if (!anyActive) {
        clearInterval(pollRef.current!)
        pollRef.current = null
      }
    }, POLL_INTERVAL)
  }, [fetchJobs, realtimeConnected])

  useEffect(() => {
    let mounted = true

    const clearWsTimers = () => {
      if (wsReconnectRef.current) {
        clearTimeout(wsReconnectRef.current)
        wsReconnectRef.current = null
      }
      if (wsPingRef.current) {
        clearInterval(wsPingRef.current)
        wsPingRef.current = null
      }
    }

    const connectRealtime = () => {
      if (!mounted) return

      try {
        const ws = new WebSocket(SORA_WS_URL)
        wsRef.current = ws

        ws.onopen = () => {
          setRealtimeConnected(true)
          clearWsTimers()
          wsPingRef.current = setInterval(() => {
            if (ws.readyState === WebSocket.OPEN) ws.send('ping')
          }, WS_PING_MS)
          checkConnection()
          if (selectedChildId) void fetchJobs(Number(selectedChildId))
        }

        ws.onmessage = (event: MessageEvent) => {
          try {
            const msg = JSON.parse(event.data as string) as SoraRealtimeMessage
            if (msg.type === 'sora_presence') {
              setExtensionOnline(msg.online === true)
              setWorkerCount(msg.worker_count ?? 0)
              return
            }
            if (msg.type === 'sora_config') {
              return
            }
            if (!selectedChildId) return
            const childId = Number(selectedChildId)
            if (msg.type === 'sora_jobs' && msg.child_project_id === childId) {
              setJobs(msg.jobs ?? [])
              return
            }
            if (msg.type === 'sora_job_patch' && msg.child_project_id === childId) {
              patchJob(msg.job)
            }
          } catch {
            // ignore malformed socket payloads
          }
        }

        ws.onclose = () => {
          if (wsRef.current === ws) wsRef.current = null
          clearWsTimers()
          setRealtimeConnected(false)
          if (mounted) {
            wsReconnectRef.current = setTimeout(connectRealtime, WS_RECONNECT_DELAY_MS)
          }
        }

        ws.onerror = () => {
          ws.close()
        }
      } catch {
        setRealtimeConnected(false)
        wsReconnectRef.current = setTimeout(connectRealtime, WS_RECONNECT_DELAY_MS)
      }
    }

    connectRealtime()

    return () => {
      mounted = false
      clearWsTimers()
      if (wsRef.current) {
        wsRef.current.close()
        wsRef.current = null
      }
    }
  }, [checkConnection, fetchJobs, patchJob, selectedChildId])

  // Initial load + poll if active
  useEffect(() => {
    if (!selectedChildId) { setJobs([]); return }
    fetchJobs(Number(selectedChildId)).then(fetched => {
      const anyActive = fetched.some(j => j.status === 'pending' || j.status === 'generating')
      if (anyActive) startPolling(Number(selectedChildId))
    })
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [selectedChildId, realtimeConnected])

  useEffect(() => () => { if (pollRef.current) clearInterval(pollRef.current) }, [])

  // ── Folder picker ────────────────────────────────────────────────────────
  const handlePickFolder = async () => {
    try {
      const result = await openDialog({ directory: true, multiple: false })
      if (!result || typeof result !== 'string') return
      setFolderPath(result)
      await fetch(`${API}/api/child-projects/${selectedChildId}`, {
        method: 'PATCH',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ base_folder_path: result }),
      })
      updateChild(selectedChildId!, { base_folder_path: result, folderPath: result })
    } catch (e) { console.error('Folder pick failed:', e) }
  }

  const handleOpenFolder = useCallback(async () => {
    const target = folderPath.trim()
    if (!target) {
      toast.warning('No folder selected', 'Chọn folder lưu video trước đã.')
      return
    }
    try {
      const res = await fetch(`${API}/api/sora/output-folder/open`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ folder: target }),
      })
      const data = await res.json().catch(() => ({}))
      if (!res.ok) throw new Error(data.detail ?? 'Cannot open output folder')
    } catch (error) {
      const message = error instanceof Error ? error.message : 'Cannot open output folder'
      toast.error('Open folder failed', message)
    }
  }, [folderPath])

  // ── Auto-number on blur ──────────────────────────────────────────────────
  const handleBlur = () => {
    const prompts = parsePrompts(rawText)
    if (prompts.length > 0) setRawText(formatPrompts(prompts))
  }

  // ── Paste handler: auto-number multiline paste ───────────────────────────
  const handlePaste = (e: React.ClipboardEvent<HTMLTextAreaElement>) => {
    const pasted = e.clipboardData.getData('text')
    const lines  = pasted.split(/\r?\n/).map(stripNum).filter(Boolean)
    if (lines.length <= 1) return  // single line → normal paste
    e.preventDefault()
    const existing = parsePrompts(rawText)
    const deduped  = [...new Map([...existing, ...lines].map(l => [l, l])).values()]
    setRawText(formatPrompts(deduped))
  }

  // ── Stop ──────────────────────────────────────────────────────────────────
  const handleStop = async () => {
    if (!selectedChildId) return
    try {
      await fetch(`${API}/api/sora/stop-batch/${selectedChildId}`, { method: 'POST' })
      await fetchJobs(Number(selectedChildId))
      if (pollRef.current) { clearInterval(pollRef.current); pollRef.current = null }
    } catch (e) { console.error('Stop failed:', e) }
  }

  const handleDownloadVideo = useCallback(async (job: SoraJobRow) => {
    if (!job.video_path) return
    try {
      const res = await fetch(`${API}/api/sora/download/${job.id}`)
      if (!res.ok) throw new Error(`Download failed (${res.status})`)
      const blob = await res.blob()
      const url = URL.createObjectURL(blob)
      const a = document.createElement('a')
      a.href = url
      a.download = job.video_path.split('/').pop() || `sora-job-${job.id}.mp4`
      a.click()
      setTimeout(() => URL.revokeObjectURL(url), 1000)
    } catch (e) {
      console.error('Sora video download failed:', e)
    }
  }, [])

  // ── Generate ─────────────────────────────────────────────────────────────
  const handleGenerate = async () => {
    if (!selectedChildId) return
    const prompts = parsePrompts(rawText)
    if (prompts.length === 0) return
    setSubmitting(true)
    try {
      const outputMode = await ensureOutputFolderMode(folderPath)
      if (!outputMode) return
      if (folderPath.trim()) {
        await initOutputFolder(folderPath, outputMode)
      }

      const res = await fetch(`${API}/api/sora/queue-batch/${selectedChildId}`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ prompts }),
      })
      if (!res.ok) {
        const err = await res.json()
        throw new Error(err.detail || 'Unknown error')
      }
      await fetchJobs(Number(selectedChildId))
      startPolling(Number(selectedChildId))
    } catch (e: unknown) {
      alert(e instanceof Error ? e.message : 'Unknown error')
    } finally {
      setSubmitting(false)
    }
  }

  // ⌘+Enter to generate
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if ((e.metaKey || e.ctrlKey) && e.key === 'Enter') {
        e.preventDefault()
        if (canGenerate) handleGenerate()
      }
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [rawText, extensionOnline, submitting, jobs])

  const copyKey = () => {
    if (!apiKey) return
    navigator.clipboard.writeText(apiKey)
    setKeyCopied(true)
    setTimeout(() => setKeyCopied(false), 2000)
  }

  const toggleSelect = (id: number) => {
    setSelected(prev => {
      const next = new Set(prev)
      if (next.has(id)) next.delete(id)
      else next.add(id)
      return next
    })
  }

  const toggleSelectAll = () => {
    if (selected.size === jobs.length && jobs.length > 0) {
      setSelected(new Set())
    } else {
      setSelected(new Set(jobs.map(j => j.id)))
    }
  }

  const handleRequeueSelected = async () => {
    if (!selectedChildId || selected.size === 0) return
    await fetch(`${API}/api/sora/requeue-jobs`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ job_ids: [...selected] }),
    })
    setSelected(new Set())
    await fetchJobs(Number(selectedChildId))
    startPolling(Number(selectedChildId))
  }

  const handleRequeueFailed = async () => {
    if (!selectedChildId) return
    await fetch(`${API}/api/sora/requeue-failed/${selectedChildId}`, { method: 'POST' })
    await fetchJobs(Number(selectedChildId))
    startPolling(Number(selectedChildId))
  }

  const handleDeleteJob = useCallback(async (jobId: number) => {
    await fetch(`${API}/api/sora/job/${jobId}`, { method: 'DELETE' })
    setJobs(prev => prev.filter(j => j.id !== jobId))
    setSelected(prev => { const n = new Set(prev); n.delete(jobId); return n })
  }, [])

  const handleClear = async () => {
    if (!selectedChildId) return
    await fetch(`${API}/api/sora/jobs/${selectedChildId}`, { method: 'DELETE' })
    setJobs([])
    setSelected(new Set())
    setRawText('')
  }

  if (!child) {
    return (
        <div className="flex flex-col items-center justify-center h-full gap-3 text-surface-300">
        <Clapperboard size={28} strokeWidth={1.5} />
        <p className="text-sm text-surface-400">Select a video project to use Sora Gen</p>
      </div>
    )
  }

  const parsedCount  = parsePrompts(rawText).length
  const anyActive    = jobs.some(j => j.status === 'pending' || j.status === 'generating')
  const canGenerate  = !anyActive && !submitting && parsedCount > 0 && extensionOnline
  const doneCount    = jobs.filter(j => j.status === 'done').length
  const failedCount  = jobs.filter(j => j.status === 'failed').length
  const pendingCount = jobs.filter(j => j.status === 'pending' || j.status === 'generating').length
  const allSelected  = jobs.length > 0 && selected.size === jobs.length

  return (
    <Tooltip.Provider>
      <div className="flex flex-col h-full overflow-hidden">

      {/* ── Header ──────────────────────────────────────────────── */}
      <div className="flex items-center justify-between px-5 py-3.5 border-b border-surface-200 shrink-0">
        <div className="flex items-center gap-3">
          <div className="w-8 h-8 rounded-lg bg-teal-100 flex items-center justify-center shrink-0">
            <Clapperboard size={15} className="text-teal-700" strokeWidth={2} />
          </div>
          <div>
            <h1 className="page-title">Sora Video Generator</h1>
            <p className="page-sub">OpenAI Sora · Video {child.video_number}</p>
          </div>
        </div>
        <div className="flex items-center gap-2">
          {extensionOnline && (
            <span className="flex items-center gap-1.5 px-2 py-1 rounded-full bg-green-50 border border-green-200 text-[11px] font-medium text-green-700">
              <span className="w-1.5 h-1.5 rounded-full bg-green-500 animate-pulse" />
              {workerCount > 1 ? `${workerCount} profiles online` : '1 profile online'}
            </span>
          )}
          <button
            className={cn('btn-icon', showSettings && 'bg-teal-50 text-teal-700')}
            onClick={() => setShowSettings(s => !s)}
            title="Settings"
          >
            <Settings2 size={16} />
          </button>
        </div>
      </div>

      <div className="flex flex-1 overflow-hidden">

        {/* ── Settings Panel ──────────────────────────────────────── */}
        <AnimatePresence initial={false}>
          {showSettings && (
            <motion.aside
              initial={{ width: 0, opacity: 0 }}
              animate={{ width: 256, opacity: 1 }}
              exit={{ width: 0, opacity: 0 }}
              transition={{ duration: 0.2 }}
              className="shrink-0 border-r border-surface-200 bg-white overflow-y-auto overflow-x-hidden"
            >
              <div className="p-4 space-y-5 w-[256px]">

                {/* Bridge status */}
                <section>
                  <label className="label mb-2 block">Bridge</label>
                  <div className={cn(
                    'flex items-center gap-2 px-3 py-2.5 rounded-lg border text-xs font-medium',
                    extensionOnline
                      ? 'border-green-200 bg-green-50 text-green-700'
                      : 'border-orange-200 bg-orange-50 text-orange-700'
                  )}>
                    {extensionOnline
                      ? <><Wifi size={12} className="shrink-0" /> {workerCount} profile{workerCount !== 1 ? 's' : ''} connected</>
                      : <><WifiOff size={12} className="shrink-0" /> Profile offline</>
                    }
                  </div>
                </section>

                {/* Bridge Key */}
                {apiKey && (
                  <section>
                    <label className="label mb-2 block">Bridge Key</label>
                    <div className="flex gap-1.5">
                      <input
                        className="input flex-1 text-xs font-mono min-w-0"
                        value={apiKey}
                        readOnly
                      />
                      <button
                        onClick={copyKey}
                        className="shrink-0 px-2.5 py-2 rounded-lg border border-surface-200 bg-white text-surface-500 hover:border-teal-400 hover:text-teal-700 hover:bg-teal-50 transition-all cursor-pointer"
                        title="Copy bridge key"
                      >
                        {keyCopied ? <Check size={14} /> : <Copy size={14} />}
                      </button>
                    </div>
                    <p className="text-[10px] text-surface-400 mt-1">Paste this into the Sora Worker panel to connect the extension.</p>
                  </section>
                )}

                {/* Output Folder */}
                <section>
                  <label className="label flex items-center gap-1.5 mb-2">
                    <FolderOpen size={11} /> Output Folder
                  </label>
                  <div className="flex gap-1.5">
                    <input
                      className="input flex-1 text-xs font-mono min-w-0"
                      placeholder="/path/to/save/videos…"
                      value={folderPath}
                      readOnly
                    />
                      <button
                      onClick={handlePickFolder}
                      className="shrink-0 px-2.5 py-2 rounded-lg border border-surface-200 bg-white text-surface-500 hover:border-teal-400 hover:text-teal-700 hover:bg-teal-50 transition-all cursor-pointer"
                      title="Browse folder"
                    >
                      <FolderSearch size={14} />
                    </button>
                  </div>
                  <div className="mt-2">
                    <button
                      onClick={handleOpenFolder}
                      disabled={!folderPath.trim()}
                      className={cn(
                        'w-full inline-flex items-center justify-center gap-1.5 px-3 py-2 rounded-lg border text-[11px] font-semibold transition-all cursor-pointer',
                        folderPath.trim()
                          ? 'border-teal-200 bg-teal-50 text-teal-700 hover:border-teal-300 hover:bg-teal-100'
                          : 'border-surface-200 bg-surface-50 text-surface-400 cursor-not-allowed opacity-60'
                      )}
                      title="Open output folder"
                    >
                      <FolderOpen size={12} />
                      Open Folder
                    </button>
                  </div>
                  {folderPath ? (
                    <p className="text-[10px] text-green-600 mt-1.5 flex items-center gap-1">
                      <CheckCircle2 size={9} /> Videos will be saved here
                    </p>
                  ) : (
                    <p className="text-[10px] text-surface-400 mt-1.5">Choose a folder to save generated videos automatically.</p>
                  )}
                </section>

              </div>
            </motion.aside>
          )}
        </AnimatePresence>

        {/* ── Main area ───────────────────────────────────────────── */}
        <div className="flex-1 flex flex-col overflow-hidden min-w-0">

          {/* Prompt area */}
          <div className="p-4 flex flex-col gap-0 shrink-0">

            <div className="flex items-start gap-3">

              {/* Textarea */}
              <div className="flex-1 relative">
                <textarea
                  value={rawText}
                  onChange={e => setRawText(e.target.value)}
                  onPaste={handlePaste}
                  onBlur={handleBlur}
                  disabled={anyActive}
                  rows={5}
                  placeholder={'Paste prompts here — one per line, auto-numbered:\n\n1  A cinematic drone shot over a mountain...\n2  Slow motion ocean waves at sunset...\n3  A neon city time lapse after rain...'}
                  className={cn(
                    'input w-full resize-none text-sm leading-relaxed font-mono',
                    anyActive && 'opacity-60 cursor-not-allowed'
                  )}
                />
                {/* Row count badge */}
                {parsedCount > 0 && (
                  <div className="absolute top-2 right-2 px-1.5 py-0.5 rounded text-[10px] font-semibold bg-teal-100 text-teal-700 pointer-events-none select-none">
                    {parsedCount}
                  </div>
                )}
              </div>

              {/* Quick action panel */}
              <div className="flex flex-col gap-2 shrink-0 pt-0.5">
                <button
                  className="btn-primary flex items-center gap-2 px-4 py-2.5 disabled:opacity-40 disabled:cursor-not-allowed whitespace-nowrap cursor-pointer"
                  onClick={handleGenerate}
                  disabled={!canGenerate}
                >
                  {anyActive || submitting
                    ? <Loader2 size={14} className="animate-spin" />
                    : <Play size={14} className={cn(canGenerate && 'fill-white')} />
                  }
                  {submitting ? 'Queuing…' : anyActive ? `Generating…` : parsedCount > 0 ? `Generate (${parsedCount})` : 'Generate'}
                </button>
                <div className="text-[10px] text-surface-400 text-center">⌘+Enter</div>
                {anyActive && (
                  <button
                    onClick={handleStop}
                    className="flex items-center justify-center gap-1.5 px-3 py-1.5 rounded-lg border border-red-200 bg-red-50 text-red-600 text-[11px] font-semibold hover:bg-red-100 hover:border-red-300 transition-all cursor-pointer"
                  >
                    <Square size={10} className="fill-red-500 text-red-500" /> Stop
                  </button>
                )}
              </div>

            </div>
          </div>

          {/* ── Jobs table ────────────────────────────────────────── */}
          <div className="border-t border-surface-200 flex flex-col overflow-hidden flex-1 min-h-0">

            {/* Toolbar */}
            <div className="bg-white border-b border-surface-200 px-4 py-2.5 flex items-center justify-between shrink-0">
              <div className="flex items-center gap-2">
                <span className="text-xs font-semibold text-surface-700">Generation Jobs</span>
                {jobs.length > 0 && (
                  <span className="text-[11px] bg-surface-100 text-surface-500 border border-surface-200 px-2 py-0.5 rounded-full font-medium">
                    {jobs.length}
                  </span>
                )}
                {anyActive && (
                  <span className="flex items-center gap-1 text-[11px] text-teal-700">
                    <Loader2 size={10} className="animate-spin" /> Running…
                  </span>
                )}
              </div>
              <div className="flex items-center gap-1.5">
                <button
                  onClick={handleRequeueSelected}
                  disabled={selected.size === 0 || anyActive}
                  className={cn(
                    'flex items-center gap-1.5 px-3 py-1.5 rounded-lg border text-[11px] font-medium transition-all',
                    selected.size > 0 && !anyActive
                      ? 'border-teal-300 bg-teal-50 text-teal-700 hover:bg-teal-100 cursor-pointer'
                      : 'border-surface-200 text-surface-400 cursor-not-allowed opacity-50',
                  )}
                >
                  <RefreshCw size={11} /> Regenerate Selected
                </button>
                <button
                  onClick={handleRequeueFailed}
                  disabled={failedCount === 0 || anyActive}
                  className={cn(
                    'flex items-center gap-1.5 px-3 py-1.5 rounded-lg border text-[11px] font-medium transition-all',
                    failedCount > 0 && !anyActive
                      ? 'border-red-300 bg-red-50 text-red-700 hover:bg-red-100 cursor-pointer'
                      : 'border-surface-200 text-surface-400 cursor-not-allowed opacity-50',
                  )}
                >
                  <AlertCircle size={11} /> Regenerate Failed
                </button>
                <button
                  onClick={handleClear}
                  disabled={jobs.length === 0}
                  className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg border border-surface-200 text-[11px] font-medium text-surface-500 hover:border-surface-300 hover:bg-surface-50 transition-all disabled:opacity-40 disabled:cursor-not-allowed cursor-pointer"
                >
                  <Trash2 size={11} /> Clear
                </button>
              </div>
            </div>

            {/* Table */}
            <div className="overflow-y-auto flex-1">
              {jobs.length > 0 ? (
                <table className="w-full border-collapse text-sm">
                  <thead className="sticky top-0 z-10">
                    <tr className="bg-surface-50 border-b border-surface-200 text-[11px] font-semibold text-surface-500 uppercase tracking-wide">
                      <th className="w-10 px-3 py-2 text-left">
                        <button onClick={toggleSelectAll} className="text-surface-400 hover:text-teal-700 transition-colors">
                          {allSelected ? <CheckSquare size={14} className="text-teal-700" /> : <Square size={14} />}
                        </button>
                      </th>
                      <th className="text-left px-2 py-2 w-8">#</th>
                      <th className="text-left px-3 py-2 w-32">Status</th>
                      <th className="text-left px-3 py-2">Prompt</th>
                      <th className="text-left px-3 py-2 w-14">Actions</th>
                    </tr>
                  </thead>
                  <tbody>
                    {jobs.map(job => (
                      <tr
                        key={job.id}
                        className={cn(
                          'border-b border-surface-100 text-sm transition-colors',
                          selected.has(job.id)        ? 'bg-teal-50' :
                          job.status === 'done'       ? 'bg-green-50/40' :
                          job.status === 'failed'     ? 'bg-red-50/40' :
                          job.status === 'generating' ? 'bg-teal-50/40' : 'bg-white hover:bg-surface-50',
                        )}
                      >
                        {/* Checkbox */}
                        <td className="px-3 py-2.5">
                          <button onClick={() => toggleSelect(job.id)} className="text-surface-400 hover:text-teal-700 transition-colors">
                            {selected.has(job.id) ? <CheckSquare size={14} className="text-teal-700" /> : <Square size={14} />}
                          </button>
                        </td>

                        {/* Index */}
                        <td className="px-2 py-2.5">
                          <span className={cn(
                            'inline-flex items-center justify-center w-5 h-5 rounded text-[10px] font-bold',
                            job.status === 'done'       ? 'bg-green-100 text-green-700' :
                            job.status === 'failed'     ? 'bg-red-100 text-red-600' :
                            job.status === 'generating' ? 'bg-teal-100 text-teal-700' :
                            'bg-surface-100 text-surface-500'
                          )}>
                            {job.job_index}
                          </span>
                        </td>

                        {/* Status + progress */}
                        <td className="px-3 py-2.5">
                          <div className="flex flex-col gap-1">
                            <StatusBadge status={job.status} />
                            {job.status === 'generating' && (
                              <div className="flex items-center gap-2">
                                <div className="h-1 flex-1 rounded-full bg-surface-200 overflow-hidden">
                                  <motion.div
                                    className="h-full rounded-full bg-teal-600"
                                    animate={{ width: `${job.progress}%` }}
                                    transition={{ duration: 0.5, ease: 'easeOut' }}
                                  />
                                </div>
                                <span className="text-[10px] font-mono text-surface-400 tabular-nums shrink-0">
                                  {job.progress}%
                                </span>
                              </div>
                            )}
                            <PipelineDots status={job.status} progress={job.progress} />
                          </div>
                        </td>

                        {/* Prompt */}
                        <td className="px-3 py-2.5">
                          <p className="text-xs text-surface-700 line-clamp-2 leading-relaxed">{job.prompt}</p>
                          {job.status === 'done' && job.video_path && (
                            <p className="text-[10px] text-green-600 font-mono mt-1 flex items-center gap-1">
                              <FolderOpen size={9} />
                              {job.video_path.split('/').pop()}
                            </p>
                          )}
                          {job.status === 'failed' && job.error_msg && (
                            <p className="text-[10px] font-medium text-red-600 mt-1 line-clamp-2">
                              {humanizeSoraError(job.error_msg)}
                            </p>
                          )}
                        </td>

                        {/* Actions */}
                        <td className="px-3 py-2.5">
                          <div className="flex items-center gap-1">
                            {job.status === 'done' && job.video_path && (
                              <button
                                type="button"
                                onClick={() => handleDownloadVideo(job)}
                                className="p-1.5 rounded-lg text-green-500 hover:text-green-600 hover:bg-green-50 transition-all cursor-pointer"
                                title="Tải video"
                              >
                                <Download size={13} />
                              </button>
                            )}
                            {(job.status === 'done' || job.status === 'failed') && (
                              <button
                                type="button"
                                onClick={() => handleDeleteJob(job.id)}
                                className="p-1.5 rounded-lg text-surface-300 hover:text-red-500 hover:bg-red-50 transition-all cursor-pointer"
                                title="Xoá job này"
                              >
                                <X size={13} />
                              </button>
                            )}
                          </div>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              ) : (
                <div className="flex flex-col items-center justify-center py-8 gap-2 text-surface-300">
                  <Clapperboard size={22} strokeWidth={1.5} />
                  <p className="text-xs text-surface-400">Paste prompts above to start generating videos</p>
                </div>
              )}
            </div>

            {/* Stats footer */}
            {jobs.length > 0 && (
              <div className="shrink-0 border-t border-surface-200 bg-white px-4 py-2.5 flex items-center gap-6">
                <div className="flex items-center gap-1.5 text-[11px] font-medium text-green-700">
                  <CheckCircle2 size={12} />
                  <span>{doneCount} Completed</span>
                  <StatHelp label="Completed" help="Finished videos that were published, cleaned, denoised, upscaled to 1080p, and saved back to your folder." tone="success" />
                </div>
                <div className="flex items-center gap-1.5 text-[11px] font-medium text-red-600">
                  <XCircle size={12} />
                  <span>{failedCount} Failed</span>
                  <StatHelp label="Failed" help="Jobs that the Sora worker could not finish. You can regenerate them after fixing the issue." tone="error" />
                </div>
                <div className="flex items-center gap-1.5 text-[11px] font-medium text-surface-500">
                  <Clock size={12} />
                  <span>{pendingCount} Pending</span>
                  <StatHelp label="Pending" help="Jobs still waiting in the queue or currently being processed by the connected Sora worker." />
                </div>
                {selected.size > 0 && (
                  <span className="ml-auto text-[11px] text-teal-700 font-medium">{selected.size} selected</span>
                )}
              </div>
            )}
          </div>

        </div>
      </div>
      </div>

      {/* ── Output Folder Conflict Modal ────────────────────────── */}
      <AnimatePresence>
        {outputConflictDialog && (
          <motion.div
            initial={{ opacity: 0 }} animate={{ opacity: 1 }} exit={{ opacity: 0 }}
            transition={{ duration: 0.18 }}
            className="fixed inset-0 z-50 bg-black/35 backdrop-blur-[2px] flex items-center justify-center p-6"
            onClick={() => resolveOutputConflictMode(null)}
          >
            <motion.div
              initial={{ opacity: 0, scale: 0.96, y: 8 }}
              animate={{ opacity: 1, scale: 1, y: 0 }}
              exit={{ opacity: 0, scale: 0.96, y: 8 }}
              transition={{ duration: 0.18, ease: 'easeOut' }}
              className="w-full max-w-[460px] rounded-2xl border border-surface-200 bg-white px-7 py-6 shadow-xl"
              onClick={e => e.stopPropagation()}
            >
              <h3 className="text-xl font-semibold text-surface-900">
                Existing video found
              </h3>
              <p className="mt-3 text-sm leading-6 text-surface-500">
                This folder already has {outputConflictDialog.videoCount} video(s).
                Choose how to save this new Sora batch.
              </p>
              {outputConflictDialog.videoSampleNames.length > 0 && (
                <div className="mt-4 rounded-xl border border-amber-200 bg-amber-50 px-4 py-3 text-xs leading-5 text-amber-800">
                  Videos: {outputConflictDialog.videoSampleNames.join(', ')}
                </div>
              )}

              <div className="mt-6 flex items-center justify-end gap-2.5">
                <button
                  className="inline-flex h-10 items-center justify-center rounded-lg px-3.5 text-sm font-medium text-surface-500 transition-colors hover:bg-surface-50 hover:text-surface-700"
                  onClick={() => resolveOutputConflictMode(null)}
                >
                  Close
                </button>
                <button
                  className="inline-flex h-10 items-center justify-center rounded-lg border border-[#0D9488]/20 px-4 text-sm font-semibold text-[#0D9488] transition-colors hover:bg-[#0D9488]/5"
                  onClick={() => resolveOutputConflictMode('keep_both')}
                >
                  Keep
                </button>
                <button
                  className="inline-flex h-10 items-center justify-center rounded-lg bg-[#F97316] px-4 text-sm font-semibold text-white transition-colors hover:bg-[#EA580C]"
                  onClick={() => resolveOutputConflictMode('replace_all')}
                >
                  Override
                </button>
              </div>
            </motion.div>
          </motion.div>
        )}
      </AnimatePresence>

      {/* ── Job Log Popup ────────────────────────────────────────── */}
      {logPopupJobId !== null && (() => {
        const logJob = jobs.find(j => j.id === logPopupJobId)
        if (!logJob) return null
        return (
          <SoraJobLogPopup
            jobId={logJob.id}
            jobIndex={logJob.job_index}
            prompt={logJob.prompt}
            status={logJob.status}
            open={true}
            onClose={() => setLogPopupJobId(null)}
          />
        )
      })()}
    </Tooltip.Provider>
  )
}
