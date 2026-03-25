import { useState, useEffect, useRef, useCallback } from 'react'
import { usePanelContext } from '@/contexts/PanelContext'
import { useAppStore } from '@/store/app.store'
import { motion, AnimatePresence } from 'framer-motion'
import { open as openDialog } from '@tauri-apps/plugin-dialog'
import {
  Wifi, WifiOff, Copy, Check, ExternalLink,
  FolderOpen, Play, CheckCircle2, XCircle,
  Loader2, FolderSearch, RefreshCw, Clapperboard,
  Settings2, Clock, Download,
} from 'lucide-react'
import { cn } from '@/lib/utils'

const API = 'http://127.0.0.1:8765'
const POLL_INTERVAL = 3000

type SoraStatus = 'pending' | 'generating' | 'done' | 'failed' | null

interface JobStatus {
  sora_status: SoraStatus
  sora_progress: number
  sora_video_path: string | null
  sora_permalink: string | null
  sora_generation_id: string | null
}

const RATIOS = [
  { value: '16:9' as const, label: '16:9', w: 20, h: 12 },
  { value: '9:16' as const, label: '9:16', w: 12, h: 20 },
  { value: '1:1'  as const, label: '1:1',  w: 16, h: 16 },
]

const DURATIONS = [5, 10, 20] as const

// ── Status badge ──────────────────────────────────────────────────────────────
function StatusBadge({ status }: { status: SoraStatus }) {
  if (!status) return null
  if (status === 'pending')    return (
    <span className="inline-flex items-center gap-1 text-[11px] font-medium text-surface-500 bg-surface-100 border border-surface-200 px-2 py-0.5 rounded-full">
      <Clock size={10} /> Pending
    </span>
  )
  if (status === 'generating') return (
    <span className="inline-flex items-center gap-1 text-[11px] font-medium text-violet-600 bg-violet-50 border border-violet-200 px-2 py-0.5 rounded-full">
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

// ── Main component ────────────────────────────────────────────────────────────
export default function SoraGen() {
  const { selectedChildId } = usePanelContext()
  const { childProjects, updateChild } = useAppStore()
  const child = childProjects.find(c => c.id === selectedChildId)

  const [extensionOnline, setExtensionOnline] = useState(false)
  const [workerCount, setWorkerCount]         = useState(0)
  const [apiKey, setApiKey]                   = useState<string | null>(null)
  const [keyCopied, setKeyCopied]             = useState(false)
  const [folderPath, setFolderPath]           = useState('')
  const [prompt, setPrompt]                   = useState('')
  const [ratio, setRatio]                     = useState<'16:9' | '9:16' | '1:1'>('16:9')
  const [duration, setDuration]               = useState<5 | 10 | 20>(5)
  const [jobStatus, setJobStatus]             = useState<JobStatus | null>(null)
  const [submitting, setSubmitting]           = useState(false)
  const [showSettings, setShowSettings]       = useState(true)

  const pollRef    = useRef<ReturnType<typeof setInterval> | null>(null)
  const textareaRef = useRef<HTMLTextAreaElement>(null)

  useEffect(() => { setFolderPath(child?.base_folder_path ?? '') }, [child?.id])

  useEffect(() => {
    fetch(`${API}/api/sora/key`)
      .then(r => r.json())
      .then(d => setApiKey(d.key))
      .catch(() => {})
  }, [])

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
    checkConnection()
    const id = setInterval(checkConnection, 5000)
    return () => clearInterval(id)
  }, [checkConnection])

  useEffect(() => {
    if (!selectedChildId) return
    fetch(`${API}/api/sora/job-status/${selectedChildId}`)
      .then(r => r.json())
      .then(d => {
        setJobStatus(d)
        if (d.sora_status === 'pending' || d.sora_status === 'generating') {
          startPolling(Number(selectedChildId))
        }
      })
      .catch(() => setJobStatus(null))
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [selectedChildId])

  const startPolling = useCallback((childId: number) => {
    if (pollRef.current) clearInterval(pollRef.current)
    pollRef.current = setInterval(async () => {
      try {
        const res  = await fetch(`${API}/api/sora/job-status/${childId}`)
        const data = await res.json()
        setJobStatus(data)
        if (data.sora_status === 'done' || data.sora_status === 'failed' || !data.sora_status) {
          clearInterval(pollRef.current!)
          pollRef.current = null
        }
      } catch { /* ignore */ }
    }, POLL_INTERVAL)
  }, [])

  useEffect(() => () => { if (pollRef.current) clearInterval(pollRef.current) }, [])

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
      updateChild(selectedChildId!, { base_folder_path: result } as any)
    } catch (e) { console.error('Folder pick failed:', e) }
  }

  const handleGenerate = async () => {
    if (!selectedChildId || !prompt.trim()) return
    setSubmitting(true)
    try {
      const res = await fetch(`${API}/api/sora/queue/${selectedChildId}`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ prompt: prompt.trim(), ratio, duration }),
      })
      if (!res.ok) {
        const err = await res.json()
        throw new Error(err.detail || 'Lỗi không xác định')
      }
      startPolling(Number(selectedChildId))
    } catch (e: any) {
      alert(e.message)
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
  }, [prompt, extensionOnline, submitting, jobStatus])

  const copyKey = () => {
    if (!apiKey) return
    navigator.clipboard.writeText(apiKey)
    setKeyCopied(true)
    setTimeout(() => setKeyCopied(false), 2000)
  }

  if (!child) {
    return (
      <div className="flex flex-col items-center justify-center h-full gap-3 text-surface-300">
        <Clapperboard size={28} strokeWidth={1.5} />
        <p className="text-sm text-surface-400">Chọn một video project để dùng Sora Gen</p>
      </div>
    )
  }

  const isRunning   = jobStatus?.sora_status === 'pending' || jobStatus?.sora_status === 'generating'
  const progress    = jobStatus?.sora_progress ?? 0
  const canGenerate = !isRunning && !submitting && prompt.trim().length > 0 && extensionOnline

  return (
    <div className="flex flex-col h-full overflow-hidden">

      {/* ── Header ─────────────────────────────────────────────── */}
      <div className="flex items-center justify-between px-5 py-3.5 border-b border-surface-200 shrink-0">
        <div className="flex items-center gap-3">
          <div className="w-8 h-8 rounded-lg bg-violet-100 flex items-center justify-center shrink-0">
            <Clapperboard size={15} className="text-violet-600" strokeWidth={2} />
          </div>
          <div>
            <h1 className="page-title">Sora Video Generator</h1>
            <p className="page-sub">
              OpenAI Sora · Video {child.video_number}
            </p>
          </div>
        </div>
        <div className="flex items-center gap-2">
          {extensionOnline && (
            <span className="flex items-center gap-1.5 px-2 py-1 rounded-full bg-green-50 border border-green-200 text-[11px] font-medium text-green-700">
              <span className="w-1.5 h-1.5 rounded-full bg-green-500 animate-pulse" />
              {workerCount > 1 ? `${workerCount} workers` : '1 worker'}
            </span>
          )}
          <button
            className={cn('btn-icon', showSettings && 'bg-surface-100 text-primary-600')}
            onClick={() => setShowSettings(s => !s)}
            title="Settings"
          >
            <Settings2 size={16} />
          </button>
        </div>
      </div>

      <div className="flex flex-1 overflow-hidden">

        {/* ── Settings Panel ────────────────────────────────────── */}
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

                {/* Extension status */}
                <section>
                  <label className="label mb-2 block">Extension</label>
                  <div className={cn(
                    'flex items-center gap-2 px-3 py-2.5 rounded-lg border text-xs font-medium',
                    extensionOnline
                      ? 'border-green-200 bg-green-50 text-green-700'
                      : 'border-amber-200 bg-amber-50 text-amber-700'
                  )}>
                    {extensionOnline
                      ? <><Wifi size={12} className="shrink-0" /> {workerCount} worker{workerCount !== 1 ? 's' : ''} connected</>
                      : <><WifiOff size={12} className="shrink-0" /> Not connected</>
                    }
                  </div>
                </section>

                {/* API Key (chỉ hiện khi offline) */}
                {apiKey && (
                  <section>
                    <label className="label mb-2 block">API Key</label>
                    <div className="flex gap-1.5">
                      <input
                        className="input flex-1 text-xs font-mono min-w-0"
                        value={apiKey}
                        readOnly
                      />
                      <button
                        onClick={copyKey}
                        className="shrink-0 px-2.5 py-2 rounded-lg border border-surface-200 bg-white text-surface-500 hover:border-violet-400 hover:text-violet-600 hover:bg-violet-50 transition-all"
                        title="Copy API key"
                      >
                        {keyCopied ? <Check size={14} /> : <Copy size={14} />}
                      </button>
                    </div>
                    <p className="text-[10px] text-surface-400 mt-1">Paste vào extension panel để kết nối</p>
                  </section>
                )}

                {/* Aspect Ratio */}
                <section>
                  <label className="label mb-2 block">Aspect Ratio</label>
                  <div className="grid grid-cols-3 gap-1">
                    {RATIOS.map(r => (
                      <button
                        key={r.value}
                        onClick={() => setRatio(r.value)}
                        disabled={isRunning}
                        className={cn(
                          'flex flex-col items-center gap-1.5 py-2.5 px-1 rounded-lg border text-[10px] font-medium transition-all cursor-pointer',
                          ratio === r.value
                            ? 'border-violet-500 bg-violet-50 text-violet-700'
                            : 'border-surface-200 text-surface-500 hover:border-surface-300 hover:bg-surface-50',
                          isRunning && 'opacity-50 cursor-not-allowed'
                        )}
                      >
                        <div
                          className={cn('border-2 rounded-[2px]', ratio === r.value ? 'border-violet-500' : 'border-surface-400')}
                          style={{ width: r.w, height: r.h }}
                        />
                        {r.label}
                      </button>
                    ))}
                  </div>
                </section>

                {/* Duration */}
                <section>
                  <label className="label mb-2 block">Duration</label>
                  <div className="flex rounded-lg border border-surface-200 bg-white overflow-hidden p-0.5 gap-0.5">
                    {DURATIONS.map(sec => (
                      <button
                        key={sec}
                        onClick={() => setDuration(sec)}
                        disabled={isRunning}
                        className={cn(
                          'flex-1 py-1.5 rounded-md text-xs font-semibold transition-all cursor-pointer',
                          duration === sec
                            ? 'bg-violet-500 text-white shadow-sm'
                            : 'text-surface-500 hover:text-surface-700 hover:bg-surface-50',
                          isRunning && 'opacity-50 cursor-not-allowed'
                        )}
                      >
                        {sec}s
                      </button>
                    ))}
                  </div>
                </section>

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
                      className="shrink-0 px-2.5 py-2 rounded-lg border border-surface-200 bg-white text-surface-500 hover:border-violet-400 hover:text-violet-600 hover:bg-violet-50 transition-all"
                      title="Browse folder"
                    >
                      <FolderSearch size={14} />
                    </button>
                  </div>
                  {folderPath ? (
                    <p className="text-[10px] text-green-600 mt-1.5 flex items-center gap-1">
                      <CheckCircle2 size={9} /> Video sẽ được lưu tại đây
                    </p>
                  ) : (
                    <p className="text-[10px] text-surface-400 mt-1.5">Chọn folder để tự động lưu video.</p>
                  )}
                </section>

              </div>
            </motion.aside>
          )}
        </AnimatePresence>

        {/* ── Main area ─────────────────────────────────────────── */}
        <div className="flex-1 flex flex-col overflow-hidden min-w-0">

          {/* Prompt textarea */}
          <div className="flex-1 p-4 flex flex-col min-h-0">
            <textarea
              ref={textareaRef}
              value={prompt}
              onChange={e => setPrompt(e.target.value)}
              disabled={isRunning}
              placeholder={
                extensionOnline
                  ? 'Paste prompt here — mô tả cảnh video bạn muốn tạo...'
                  : 'Kết nối extension trước, sau đó nhập prompt tại đây...'
              }
              className={cn(
                'flex-1 w-full rounded-xl border text-sm text-surface-900 px-4 py-3',
                'placeholder:text-surface-300 resize-none outline-none transition-all leading-relaxed',
                'border-surface-200 bg-surface-50/30 hover:border-surface-300',
                'focus:border-violet-400 focus:ring-2 focus:ring-violet-100 focus:bg-white',
                isRunning && 'opacity-60 cursor-not-allowed'
              )}
            />
          </div>

          {/* Generate button row */}
          <div className="px-4 pb-3 flex items-center justify-between gap-3 shrink-0">
            <span className="text-xs text-surface-400 tabular-nums">
              {prompt.length > 0 ? `${prompt.length} chars` : 'Enter prompt above'}
            </span>
            <button
              onClick={handleGenerate}
              disabled={!canGenerate}
              className={cn(
                'flex items-center gap-2 px-4 py-2 rounded-lg font-semibold text-sm transition-all',
                canGenerate
                  ? 'bg-violet-600 text-white hover:bg-violet-700 shadow-sm shadow-violet-200 cursor-pointer'
                  : 'bg-surface-100 text-surface-400 cursor-not-allowed border border-surface-200'
              )}
            >
              {isRunning || submitting
                ? <Loader2 size={14} className="animate-spin" />
                : <Play size={14} className={cn(canGenerate && 'fill-white')} />
              }
              {submitting ? 'Queuing…' : isRunning ? 'Generating…' : 'Generate'}
              {!isRunning && !submitting && (
                <kbd className="ml-0.5 text-[10px] opacity-50 font-normal">⌘↵</kbd>
              )}
            </button>
          </div>

          {/* ── Results table ─────────────────────────────────── */}
          <div className="border-t border-surface-200 shrink-0">
            <div className="flex items-center justify-between px-4 py-2.5">
              <div className="flex items-center gap-2">
                <span className="text-xs font-semibold text-surface-700">Generation Result</span>
                {jobStatus?.sora_status && (
                  <StatusBadge status={jobStatus.sora_status} />
                )}
              </div>
              {jobStatus?.sora_status === 'failed' && (
                <button
                  onClick={() => setJobStatus(null)}
                  className="flex items-center gap-1 text-xs text-surface-400 hover:text-surface-600 transition-colors cursor-pointer"
                >
                  <RefreshCw size={11} /> Retry
                </button>
              )}
            </div>

            {/* Table */}
            <div className="overflow-x-auto">
              {jobStatus?.sora_status ? (
                <table className="w-full border-collapse text-sm">
                  <thead>
                    <tr className="bg-surface-50 border-t border-b border-surface-200 text-[11px] font-semibold text-surface-500 uppercase tracking-wide">
                      <th className="text-left px-4 py-2 w-8">#</th>
                      <th className="text-left px-3 py-2 w-28">Status</th>
                      <th className="text-left px-3 py-2">Prompt</th>
                      <th className="text-left px-3 py-2 w-16">Actions</th>
                    </tr>
                  </thead>
                  <tbody>
                    <tr className={cn(
                      'border-b border-surface-100 text-sm transition-colors',
                      jobStatus.sora_status === 'done'      && 'bg-green-50/40',
                      jobStatus.sora_status === 'failed'    && 'bg-red-50/40',
                      jobStatus.sora_status === 'generating' && 'bg-violet-50/40',
                    )}>
                      {/* Index */}
                      <td className="px-4 py-3">
                        <span className={cn(
                          'inline-flex items-center justify-center w-5 h-5 rounded text-[10px] font-bold',
                          jobStatus.sora_status === 'done'       ? 'bg-green-100 text-green-700' :
                          jobStatus.sora_status === 'failed'     ? 'bg-red-100 text-red-600' :
                          jobStatus.sora_status === 'generating' ? 'bg-violet-100 text-violet-600' :
                          'bg-surface-100 text-surface-500'
                        )}>
                          {child.video_number ?? 1}
                        </span>
                      </td>

                      {/* Status + progress */}
                      <td className="px-3 py-3">
                        <div className="flex flex-col gap-1.5">
                          <StatusBadge status={jobStatus.sora_status} />
                          {(isRunning || jobStatus.sora_status === 'done') && (
                            <div className="flex items-center gap-2">
                              <div className="h-1 flex-1 rounded-full bg-surface-200 overflow-hidden">
                                <motion.div
                                  className={cn('h-full rounded-full', jobStatus.sora_status === 'done' ? 'bg-green-500' : 'bg-violet-500')}
                                  animate={{ width: `${jobStatus.sora_status === 'done' ? 100 : progress}%` }}
                                  transition={{ duration: 0.5, ease: 'easeOut' }}
                                />
                              </div>
                              <span className="text-[10px] font-mono text-surface-400 tabular-nums shrink-0">
                                {jobStatus.sora_status === 'done' ? '100' : progress}%
                              </span>
                            </div>
                          )}
                        </div>
                      </td>

                      {/* Prompt text */}
                      <td className="px-3 py-3">
                        <p className="text-xs text-surface-700 line-clamp-2 leading-relaxed">{prompt || '—'}</p>
                        {jobStatus.sora_status === 'done' && jobStatus.sora_video_path && (
                          <p className="text-[10px] text-green-600 font-mono mt-1 flex items-center gap-1">
                            <FolderOpen size={9} />
                            {jobStatus.sora_video_path.split('/').pop()}
                          </p>
                        )}
                      </td>

                      {/* Actions */}
                      <td className="px-3 py-3">
                        <div className="flex items-center gap-1">
                          {jobStatus.sora_status === 'done' && jobStatus.sora_permalink && (
                            <a
                              href={jobStatus.sora_permalink}
                              target="_blank"
                              rel="noreferrer"
                              className="p-1.5 rounded-lg text-surface-400 hover:text-violet-600 hover:bg-violet-50 transition-all"
                              title="Xem trên Sora"
                            >
                              <ExternalLink size={13} />
                            </a>
                          )}
                          {jobStatus.sora_status === 'done' && jobStatus.sora_video_path && (
                            <button
                              className="p-1.5 rounded-lg text-surface-400 hover:text-green-600 hover:bg-green-50 transition-all"
                              title="Đã lưu"
                            >
                              <Download size={13} />
                            </button>
                          )}
                        </div>
                      </td>
                    </tr>
                  </tbody>
                </table>
              ) : (
                <div className="flex flex-col items-center justify-center py-8 gap-2 text-surface-300">
                  <Clapperboard size={22} strokeWidth={1.5} />
                  <p className="text-xs text-surface-400">Paste prompt above to generate video</p>
                </div>
              )}
            </div>
          </div>

        </div>
      </div>
    </div>
  )
}
