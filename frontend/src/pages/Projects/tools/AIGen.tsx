import { useState, useRef, useCallback, useEffect } from 'react'
import { useExtensionSocket } from '@/hooks/useExtensionSocket'
import { toast } from '@/store/toast.store'
import * as Tooltip from '@radix-ui/react-tooltip'
import { motion, AnimatePresence } from 'framer-motion'
import {
  Sparkles, Download, Loader, Settings2, Wand2,
  ChevronDown, ChevronLeft, ChevronRight, ImageIcon,
  Key, Plus, Trash2, Camera, Zap, Upload,
  RefreshCw, XCircle, CheckCircle2, Clock, AlertCircle,
  Square, CheckSquare, FolderOpen, X, Copy, Check, CircleHelp,
} from 'lucide-react'
import { pickWorkspaceDirectory } from '@/lib/browserPickers'
import { cn } from '@/lib/utils'
import { taskStore } from '@/store/task.store'

const API = ''
const LS = (k: string) => `ai_gen_${k}`

const STYLES = [
  'Auto', 'Super Realistic', 'Cartoon', 'Sketch', 'Anime',
  '3D Render', 'Oil Painting', 'Watercolor', 'Cyberpunk',
  'Vintage', 'Minimalist', 'Fantasy', 'Pop Art', 'Isometric',
]
const RATIOS = [
  { label: '16:9', w: 16, h: 9 },
  { label: '9:16', w: 9,  h: 16 },
  { label: '1:1',  w: 1,  h: 1 },
  { label: '4:3',  w: 4,  h: 3 },
  { label: '3:4',  w: 3,  h: 4 },
]
const FLOW_MODELS: { label: string; value: string }[] = [
  { label: '🍌 Nano Banana Pro', value: 'BANANA_PRO_2' },
  { label: '🍌 Nano Banana 2',   value: 'NARWHAL' },
  { label: 'Imagen 4',           value: 'IMAGEN_3_5' },
]

const GEN_STAGES = [
  { key: 'uploading',  label: 'Uploading refs…',   pct: 20  },
  { key: 'workflow',   label: 'Creating workflow…', pct: 40  },
  { key: 'generating', label: 'Generating…',        pct: 88  },
  { key: 'finalizing', label: 'Finalizing…',        pct: 98  },
  { key: 'done',       label: 'Done!',              pct: 100 },
]

// ── Types ─────────────────────────────────────────────────────────────────────
interface RefImage {
  id: string; thumbnail: string; fileName: string; name: string; mediaId: string
}

type RowStatus = 'pending' | 'generating' | 'upscaling' | 'done' | 'failed'
type OutputConflictMode = 'overwrite' | 'keep_both' | 'replace_all'
type BackendMode = 'whisk' | 'google_flow'

interface PromptRow {
  id: string
  prompt: string
  status: RowStatus
  imageSrc?: string
  seed?: number
  errorMsg?: string
  createdAt?: string
}

interface Toast {
  visible: boolean; projectId: string; tokenPreview: string; autoStart: boolean
}

interface OutputFolderInspectResult {
  folder: string
  exists: boolean
  image_count: number
  managed_image_count: number
  sample_names: string[]
  video_count: number
  video_sample_names: string[]
}

interface OutputFolderInitResult {
  folder: string
  conflict_mode: OutputConflictMode
  start_index: number
  image_count_before: number
  removed_image_count: number
}

interface OutputConflictDialogState {
  folder: string
  imageCount: number
  videoCount: number
  sampleNames: string[]
  videoSampleNames: string[]
}

// ── Helpers ───────────────────────────────────────────────────────────────────
function uid() { return Math.random().toString(36).slice(2) }
function readBackendMode(): BackendMode {
  return localStorage.getItem(LS('backend')) === 'google_flow' ? 'google_flow' : 'whisk'
}

/** Strip leading number prefix: "1  " / "2. " / "3) " */
function stripNum(line: string) {
  return line.replace(/^\d+[.)\s]\s*/, '').trim()
}

/**
 * Suggest optimal thread count for a given number of prompts.
 * Mirrors WhiskForge-Pro suggest_threads() logic:
 *   1 prompt  → 1 thread
 *   2-3       → 2
 *   4-6       → 3
 *   7-12      → 4
 *   13-24     → 6
 *   25+       → 8  (cap — Google rate-limits aggressively)
 */
function suggestThreads(numPrompts: number): number {
  if (numPrompts <= 1)  return 1
  if (numPrompts <= 3)  return 2
  if (numPrompts <= 6)  return 3
  if (numPrompts <= 12) return 4
  if (numPrompts <= 24) return 6
  return 8
}

/**
 * Run `tasks` with at most `concurrency` running simultaneously.
 * Each task is a zero-arg async function returning void.
 */
async function runConcurrent(
  tasks: (() => Promise<void>)[],
  concurrency: number,
): Promise<void> {
  let idx = 0
  async function worker() {
    while (idx < tasks.length) {
      const taskIdx = idx++
      await tasks[taskIdx]()
    }
  }
  const workers = Array.from({ length: Math.min(concurrency, tasks.length) }, worker)
  await Promise.all(workers)
}

/** Parse a text block into clean prompts (port of Whisk Forge extract_prompts) */
function parsePrompts(text: string): string[] {
  return text
    .replace(/\r\n/g, '\n').replace(/\r/g, '\n')
    .split('\n')
    .map(l => stripNum(l))
    .filter(l => l.length > 0)
}

/** Format prompts array as numbered text: "1  …\n2  …" */
function formatPrompts(prompts: string[]): string {
  return prompts.map((p, i) => `${i + 1}  ${p}`).join('\n')
}

// ── Status badge component ────────────────────────────────────────────────────
function StatusBadge({ status, errorMsg }: { status: RowStatus; errorMsg?: string }) {
  if (status === 'pending')    return (
    <span className="inline-flex items-center gap-1 text-[11px] font-medium text-surface-500 bg-surface-100 border border-surface-200 px-2 py-0.5 rounded-full">
      <Clock size={10} /> Pending
    </span>
  )
  if (status === 'generating') return (
    <span className="inline-flex items-center gap-1 text-[11px] font-medium text-violet-600 bg-violet-50 border border-violet-200 px-2 py-0.5 rounded-full">
      <Loader size={10} className="animate-spin" /> Generating…
    </span>
  )
  if (status === 'upscaling') return (
    <span className="inline-flex items-center gap-1 text-[11px] font-medium text-sky-600 bg-sky-50 border border-sky-200 px-2 py-0.5 rounded-full">
      <Loader size={10} className="animate-spin" /> Upscaling…
    </span>
  )
  if (status === 'done')       return (
    <span className="inline-flex items-center gap-1 text-[11px] font-medium text-green-700 bg-green-50 border border-green-200 px-2 py-0.5 rounded-full">
      <CheckCircle2 size={10} /> Done
    </span>
  )
  return (
    <span className="inline-flex items-center gap-1 text-[11px] font-medium text-red-600 bg-red-50 border border-red-200 px-2 py-0.5 rounded-full" title={errorMsg}>
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
            'inline-flex h-4 w-4 items-center justify-center rounded-full transition-colors focus:outline-none focus:ring-2 focus:ring-violet-300 focus:ring-offset-1',
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

// ── Upscale resolution map ─────────────────────────────────────────────────
const UPSCALE_RESOLUTIONS: { key: string; label: string }[] = [
  { key: 'FHD', label: 'Full HD' },
  { key: '2K',  label: '2K' },
  { key: '4K',  label: '4K' },
]

/** Return { w, h } for upscale target given current aspect ratio + resolution key */
function resolveUpscaleDimensions(aspectRatio: string, resKey: string): { w: number; h: number } {
  const map: Record<string, Record<string, [number, number]>> = {
    '16:9': { FHD: [1920, 1080], '2K': [2560, 1440], '4K': [3840, 2160] },
    '9:16': { FHD: [1080, 1920], '2K': [1440, 2560], '4K': [2160, 3840] },
    '1:1':  { FHD: [1080, 1080], '2K': [1440, 1440], '4K': [2160, 2160] },
    '4:3':  { FHD: [1440, 1080], '2K': [2560, 1920], '4K': [3840, 2880] },
    '3:4':  { FHD: [1080, 1440], '2K': [1920, 2560], '4K': [2880, 3840] },
  }
  const [w, h] = map[aspectRatio]?.[resKey] ?? map['16:9']['FHD']
  return { w, h }
}

// ── Main component ────────────────────────────────────────────────────────────
export default function AIGen() {
  // Settings
  const [backend, setBackend]                     = useState<BackendMode>(readBackendMode)
  const [cookie, setCookie]                       = useState(() => localStorage.getItem(LS('cookie')) ?? '')
  const [flowSessionCookie, setFlowSessionCookie] = useState(() => localStorage.getItem(LS('flow_session_cookie')) ?? '')
  const [flowProjectId, setFlowProjectId]         = useState(() => localStorage.getItem(LS('flow_project_id')) ?? '')
  const [flowModel, setFlowModel]                 = useState(() => localStorage.getItem(LS('flow_model')) ?? 'BANANA_PRO_2')
  const [ratio, setRatio]                 = useState('16:9')
  const [style, setStyle]                 = useState('Auto')
  const [seedMode]                        = useState<'random' | 'fixed'>('random')
  const [seed]                            = useState(521968)
  const [showSettings, setShowSettings]   = useState(true)
  const [refImages, setRefImages]         = useState<RefImage[]>([])
  const [threadCount, setThreadCount]     = useState<number>(30)

  // Output folder
  const [outputFolder, setOutputFolder] = useState(() => localStorage.getItem(LS('output_folder')) ?? '')
  const [outputConflictDialog, setOutputConflictDialog] = useState<OutputConflictDialogState | null>(null)

  // Upscale
  const [upscaleEnabled, setUpscaleEnabled]       = useState(() => localStorage.getItem(LS('upscale_enabled')) === 'true')
  const [upscaleResolution, setUpscaleResolution] = useState(() => localStorage.getItem(LS('upscale_res')) ?? 'FHD')

  // Prompt input + table rows
  const [rawText, setRawText]   = useState('')
  const [rows, setRows]         = useState<PromptRow[]>([])
  const [selected, setSelected] = useState<Set<string>>(new Set())

  // ── Extension bridge — nhận prompt từ Claude extension ────────────────────
  useExtensionSocket('ai_gen', (msg) => {
    setRawText(prev => {
      const trimmed = prev.trim()
      // Append vào existing prompts, ngăn cách bằng newline
      return trimmed ? `${trimmed}\n${msg.content}` : msg.content
    })
    toast.success('⚡ Prompts received from Claude', `Added ${msg.content.split('\n').filter(Boolean).length} prompt(s) to the list`, 4000)
  })

  // UI state
  const [isRunning, setIsRunning]   = useState(false)
  const [errorMsg, setErrorMsg]     = useState('')
  const [lightbox, setLightbox]     = useState<PromptRow | null>(null)
  const [isDragging, setIsDragging] = useState(false)
  const fileInputRef = useRef<HTMLInputElement>(null)

  // Lightbox navigation helpers
  const lightboxImages = rows.filter(r => r.imageSrc)
  const lightboxIndex  = lightbox ? lightboxImages.findIndex(r => r.id === lightbox.id) : -1
  const lightboxPrev   = lightboxIndex > 0 ? lightboxImages[lightboxIndex - 1] : null
  const lightboxNext   = lightboxIndex >= 0 && lightboxIndex < lightboxImages.length - 1
    ? lightboxImages[lightboxIndex + 1] : null

  // Keyboard navigation for lightbox
  useEffect(() => {
    if (!lightbox) return
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'ArrowLeft'  && lightboxPrev) setLightbox(lightboxPrev)
      if (e.key === 'ArrowRight' && lightboxNext) setLightbox(lightboxNext)
      if (e.key === 'Escape') setLightbox(null)
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [lightbox, lightboxPrev, lightboxNext])

  // Progress
  const [genPct,   setGenPct]   = useState(0)
  const [genStage, setGenStage] = useState('')
  const [genTotal, setGenTotal] = useState(1)
  const [genDone,  setGenDone]  = useState(0)
  const progressTimerRef = useRef<ReturnType<typeof setInterval> | null>(null)

  // Toast
  const [tokenToast, setTokenToast] = useState<Toast>({ visible: false, projectId: '', tokenPreview: '', autoStart: false })
  const toastTimerRef         = useRef<ReturnType<typeof setTimeout> | null>(null)

  // Failed prompts modal
  const [showFailedModal, setShowFailedModal] = useState(false)
  const [copiedRowId, setCopiedRowId]         = useState<string | null>(null)
  const [copiedAll, setCopiedAll]             = useState(false)

  useEffect(() => {
    if (!showFailedModal) return
    const onKey = (e: KeyboardEvent) => { if (e.key === 'Escape') setShowFailedModal(false) }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [showFailedModal])

  useEffect(() => () => {
    outputConflictResolverRef.current?.(null)
    outputConflictResolverRef.current = null
  }, [])

  // Stable refs
  const addInputRef         = useRef<HTMLInputElement>(null)
  const lastExtTokenTs      = useRef<number>(0)
  const isRunningRef        = useRef(false)
  const rawTextRef          = useRef('')
  const cookieRef           = useRef(cookie)
  const rowsRef             = useRef<PromptRow[]>([])
  const outputConflictResolverRef = useRef<((mode: OutputConflictMode | null) => void) | null>(null)
  const handleGenerateRef   = useRef<() => void>(() => {})
  const abortControllerRef  = useRef<AbortController | null>(null)

  useEffect(() => { isRunningRef.current = isRunning }, [isRunning])
  useEffect(() => { rawTextRef.current = rawText }, [rawText])
  useEffect(() => { cookieRef.current = cookie }, [cookie])
  useEffect(() => { rowsRef.current = rows }, [rows])

  const save = (k: string, v: string) => localStorage.setItem(LS(k), v)
  const applyOutputFolder = useCallback((folder: string) => {
    const next = folder.trim()
    setOutputFolder(next)
    save('output_folder', next)
  }, [])

  const inspectOutputFolder = useCallback(async (folder: string): Promise<OutputFolderInspectResult> => {
    const res = await fetch(`${API}/api/ai-gen/output-folder/inspect`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ folder }),
    })
    const data = await res.json()
    if (!res.ok) throw new Error(data.detail ?? 'Cannot inspect output folder')
    return data as OutputFolderInspectResult
  }, [])

  const initOutputFolder = useCallback(async (
    folder: string,
    conflictMode: OutputConflictMode,
    expectedCount: number,
  ): Promise<OutputFolderInitResult> => {
    const res = await fetch(`${API}/api/ai-gen/output-folder/init`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ folder, conflict_mode: conflictMode, expected_count: expectedCount }),
    })
    const data = await res.json()
    if (!res.ok) throw new Error(data.detail ?? 'Cannot prepare output folder')
    return data as OutputFolderInitResult
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
    if ((info.video_count || 0) === 0) {
      return 'keep_both'
    }
    return askOutputConflictMode({
      folder: target,
      imageCount: info.image_count,
      videoCount: info.video_count || 0,
      sampleNames: info.sample_names || [],
      videoSampleNames: info.video_sample_names || [],
    })
  }, [askOutputConflictMode, inspectOutputFolder])

  // ── Sync rows from rawText ────────────────────────────────────────────────
  useEffect(() => {
    const prompts = parsePrompts(rawText)
    setRows(prev => {
      const existingByPrompt = new Map(prev.map(r => [r.prompt, r]))
      const next = prompts.map(p => existingByPrompt.get(p) ?? { id: uid(), prompt: p, status: 'pending' as RowStatus })
      return next
    })
    setSelected(prev => {
      // Remove selected IDs that no longer exist
      const validPrompts = new Set(prompts)
      const next = new Set<string>()
      prev.forEach(id => {
        const row = rowsRef.current.find(r => r.id === id)
        if (row && validPrompts.has(row.prompt)) next.add(id)
      })
      return next
    })
  }, [rawText])

  // ── Paste handler: auto-split multiline paste into numbered prompts ────────
  const handlePaste = (e: React.ClipboardEvent<HTMLTextAreaElement>) => {
    const pasted = e.clipboardData.getData('text')
    const lines = pasted.split(/\r?\n/).map(stripNum).filter(Boolean)
    if (lines.length <= 1) return  // single line → normal paste

    e.preventDefault()
    const existing = parsePrompts(rawText)
    const deduped  = [...new Map([...existing, ...lines].map(l => [l, l])).values()]
    setRawText(formatPrompts(deduped))
  }

  // ── File import: read file content → prompts ─────────────────────────────
  function readFileAsPrompts(file: File): Promise<string[]> {
    return new Promise((resolve, reject) => {
      const reader = new FileReader()
      reader.onload = () => {
        const text = reader.result as string
        const ext = file.name.split('.').pop()?.toLowerCase() ?? ''
        let lines: string[] = []

        if (ext === 'json') {
          try {
            const parsed = JSON.parse(text)
            if (Array.isArray(parsed)) {
              lines = parsed.map(item =>
                typeof item === 'string' ? item : (item?.prompt ?? item?.text ?? JSON.stringify(item))
              )
            } else if (typeof parsed === 'object' && parsed !== null) {
              // Try common keys: prompts, data, items, lines
              const arr = parsed.prompts ?? parsed.data ?? parsed.items ?? parsed.lines
              if (Array.isArray(arr)) {
                lines = arr.map((item: unknown) =>
                  typeof item === 'string' ? item : ((item as Record<string, unknown>)?.prompt ?? (item as Record<string, unknown>)?.text ?? JSON.stringify(item)) as string
                )
              }
            }
          } catch {
            lines = text.split(/\r?\n/)
          }
        } else if (ext === 'csv') {
          // CSV: take first column of each row (skip header if it looks like one)
          const csvLines = text.split(/\r?\n/).filter(Boolean)
          const hasHeader = csvLines.length > 1 && /^(prompt|text|content|description)/i.test(csvLines[0])
          const dataLines = hasHeader ? csvLines.slice(1) : csvLines
          lines = dataLines.map(line => {
            // Basic CSV parsing: handle quoted fields
            const match = line.match(/^"([^"]*)"/) || line.match(/^([^,]*)/)
            return match ? match[1].trim() : line.trim()
          })
        } else {
          // .txt, .md, and anything else: split by newline
          lines = text.split(/\r?\n/)
        }

        resolve(lines.map(stripNum).filter(Boolean))
      }
      reader.onerror = () => reject(new Error(`Failed to read file: ${file.name}`))
      reader.readAsText(file)
    })
  }

  const ACCEPTED_FILE_TYPES = ['.txt', '.csv', '.md', '.json']

  /** Merge imported prompts with existing, dedup */
  const mergePrompts = (imported: string[]) => {
    if (imported.length === 0) return
    const existing = parsePrompts(rawText)
    const deduped = [...new Map([...existing, ...imported].map(l => [l, l])).values()]
    setRawText(formatPrompts(deduped))
  }

  /** Handle dropped or selected files */
  const handleFileImport = async (files: FileList | File[]) => {
    const accepted = Array.from(files).filter(f =>
      ACCEPTED_FILE_TYPES.some(ext => f.name.toLowerCase().endsWith(ext))
    )
    if (accepted.length === 0) {
      setErrorMsg('Unsupported file type. Use .txt, .csv, .md, or .json')
      return
    }
    try {
      const allPrompts: string[] = []
      for (const file of accepted) {
        const prompts = await readFileAsPrompts(file)
        allPrompts.push(...prompts)
      }
      mergePrompts(allPrompts)
      const names = accepted.map(f => f.name).join(', ')
      setErrorMsg('')
      // Brief success feedback via stage text
      setGenStage(`Imported ${allPrompts.length} prompts from ${names}`)
      setTimeout(() => setGenStage(''), 3000)
    } catch (err) {
      setErrorMsg(err instanceof Error ? err.message : 'Failed to read file')
    }
  }

  // ── Drag & drop handlers ──────────────────────────────────────────────────
  const dragCounter = useRef(0)

  const handleDragEnter = (e: React.DragEvent) => {
    e.preventDefault()
    e.stopPropagation()
    dragCounter.current++
    if (e.dataTransfer.types.includes('Files')) {
      setIsDragging(true)
    }
  }

  const handleDragOver = (e: React.DragEvent) => {
    e.preventDefault()
    e.stopPropagation()
  }

  const handleDragLeave = (e: React.DragEvent) => {
    e.preventDefault()
    e.stopPropagation()
    dragCounter.current--
    if (dragCounter.current === 0) {
      setIsDragging(false)
    }
  }

  const handleDrop = (e: React.DragEvent) => {
    e.preventDefault()
    e.stopPropagation()
    setIsDragging(false)
    dragCounter.current = 0
    if (e.dataTransfer.files.length > 0) {
      handleFileImport(e.dataTransfer.files)
    }
  }

  // ── Row helpers ───────────────────────────────────────────────────────────
  const updateRow = (id: string, updates: Partial<PromptRow>) =>
    setRows(prev => prev.map(r => r.id === id ? { ...r, ...updates } : r))

  const toggleSelect = (id: string) =>
    setSelected(prev => {
      const s = new Set(prev)
      if (s.has(id)) s.delete(id)
      else s.add(id)
      return s
    })

  const toggleSelectAll = () => {
    setSelected(prev =>
      prev.size === rows.length
        ? new Set()
        : new Set(rows.map(r => r.id))
    )
  }

  const clearResults = () => {
    setRawText('')
    setRows([])
    setSelected(new Set())
    setErrorMsg('')
    setLightbox(null)
    setShowFailedModal(false)
  }

  // ── Progress helpers ──────────────────────────────────────────────────────
  const startProgress = (hasRefs: boolean, isWhisk: boolean, itemIdx = 0) => {
    if (progressTimerRef.current) clearInterval(progressTimerRef.current)
    setGenPct(0)
    setGenStage(hasRefs && itemIdx === 0 ? 'Uploading reference images…' : isWhisk ? 'Creating workflow…' : 'Sending request…')
    progressTimerRef.current = setInterval(() => {
      setGenPct(prev => {
        if (prev >= 88) return prev
        const inc = prev < 40 ? 3 : prev < 70 ? 1.5 : 0.5
        const next = Math.min(88, prev + inc)
        if (next < 20 && hasRefs && itemIdx === 0) setGenStage('Uploading reference images…')
        else if (next < 45) setGenStage(isWhisk ? 'Creating workflow…' : 'Sending request…')
        else setGenStage('Generating image…')
        return next
      })
    }, 400)
  }

  const finishProgress = (success: boolean) => {
    if (progressTimerRef.current) { clearInterval(progressTimerRef.current); progressTimerRef.current = null }
    setGenPct(success ? 100 : 0)
    setGenStage(success ? 'Done!' : 'Failed')
  }

  // ── Upload reference helper ───────────────────────────────────────────────
  const uploadRef = async (ref: RefImage, currentCookie: string, currentBackend: string) => {
    if (ref.mediaId) return ref.mediaId
    const res = await fetch(`${API}/api/ai-gen/upload-reference`, {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ cookie: currentCookie, image_data: ref.thumbnail, backend: currentBackend, flow_session_cookie: flowSessionCookie.trim() }),
    })
    const data = await res.json()
    if (!res.ok) throw new Error(data.detail ?? 'Upload failed')
    return data.media_id as string
  }

  // ── Core generate loop ────────────────────────────────────────────────────
  const generateRows = useCallback(async (rowsToGen: PromptRow[]) => {
    const pending = rowsToGen.filter(r => r.prompt.trim())
    if (!pending.length) return
    const currentCookie = cookieRef.current.trim()
    if (!currentCookie || isRunningRef.current) return
    const currentOutputFolder = outputFolder.trim()

    let outputPlan: OutputFolderInitResult | null = null
    if (currentOutputFolder) {
      try {
        const conflictMode = await ensureOutputFolderMode(currentOutputFolder)
        if (!conflictMode) return
        outputPlan = await initOutputFolder(currentOutputFolder, conflictMode, pending.length)
        if (outputPlan.removed_image_count > 0) {
          toast.info('Old images cleared', `Removed ${outputPlan.removed_image_count} existing image(s) before saving the new batch`)
        }
      } catch (e: unknown) {
        const msg = e instanceof Error ? e.message : String(e || 'Output folder failed')
        setErrorMsg(msg)
        toast.error('Folder error', msg)
        return
      }
    }

    // Create fresh AbortController for this run
    const controller = new AbortController()
    abortControllerRef.current = controller
    const { signal } = controller

    setIsRunning(true)
    setErrorMsg('')
    setGenDone(0)
    setGenTotal(pending.length)
    const isWhisk = backend !== 'google_flow'

    // Register background task
    const bgTaskId = taskStore.add({
      toolId: 'ai-gen',
      toolLabel: 'AI Gen',
      label: `Generate ${pending.length} images`,
    })

    // Upload refs once
    const mediaIds: string[] = []
    if (refImages.length > 0) {
      startProgress(true, isWhisk, 0)
      for (const ref of refImages) {
        if (signal.aborted) break
        const mid = await uploadRef(ref, currentCookie, backend)
        mediaIds.push(mid)
        setRefImages(prev => prev.map(r => r.id === ref.id ? { ...r, mediaId: mid } : r))
      }
      setGenPct(22)
    }

    let anySuccess = false
    let lastError  = ''

    // Upscale queue: runs out-of-band so generation slots are freed immediately
    let upscaleFailCount = 0
    const upscaleQueue: (() => Promise<void>)[] = []
    const upscaleQueueRunning = { value: false }

    const drainUpscaleQueue = async () => {
      if (upscaleQueueRunning.value) return
      upscaleQueueRunning.value = true
      // Max 4 concurrent upscale tasks (CPU-bound on server)
      const UPSCALE_CONCURRENCY = 4
      await runConcurrent(upscaleQueue.splice(0), UPSCALE_CONCURRENCY)
      upscaleQueueRunning.value = false
    }

    const concurrency = threadCount > 0 ? threadCount : suggestThreads(pending.length)
    const tasks = pending.map((row, rowIndex) => async () => {
      // Skip if already aborted (worker picked up task after stop)
      if (signal.aborted) {
        updateRow(row.id, { status: 'pending', errorMsg: undefined })
        return
      }
      updateRow(row.id, { status: 'generating', errorMsg: undefined })
      const outputIndex = outputPlan ? outputPlan.start_index + rowIndex : null

      try {
        const res = await fetch(`${API}/api/ai-gen/generate`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          signal,
          body: JSON.stringify({
            backend, cookie: currentCookie, prompt: row.prompt,
            aspect_ratio: ratio, style,
            seed: seedMode === 'fixed' ? seed : null,
            reference_media_ids: mediaIds,
            flow_project_id: flowProjectId.trim(),
            flow_model_name: flowModel,
            flow_session_cookie: flowSessionCookie.trim(),
            output_folder: upscaleEnabled ? '' : currentOutputFolder,
            output_index: upscaleEnabled ? null : outputIndex,
          }),
        })
        const data = await res.json()

        if (!res.ok) {
          lastError = data.detail ?? 'Generation failed'
          updateRow(row.id, { status: 'failed', errorMsg: lastError })
          setGenDone(prev => prev + 1)
          return
        }

        // Mark row done with the original image immediately — frees concurrency slot
        updateRow(row.id, { status: 'done', imageSrc: data.image, seed: data.seed, createdAt: new Date().toLocaleTimeString() })
        setGenDone(prev => prev + 1)
        anySuccess = true

        // Push upscale work into out-of-band queue (doesn't block generation)
        if (upscaleEnabled && data.image && !signal.aborted) {
          const capturedImage = data.image
          const capturedRowId = row.id
          const capturedSeed = data.seed
          const capturedPrompt = row.prompt
          const capturedOutputIndex = outputIndex
          upscaleQueue.push(async () => {
            if (signal.aborted) return
            updateRow(capturedRowId, { status: 'upscaling' })
            try {
              const { w, h } = resolveUpscaleDimensions(ratio, upscaleResolution)
              const upRes = await fetch(`${API}/api/ai-gen/upscale`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                  image: capturedImage,
                  target_width: w,
                  target_height: h,
                  engine: 'auto',
                  output_folder: currentOutputFolder,
                  output_index: capturedOutputIndex,
                  prompt: capturedPrompt,
                  seed: capturedSeed,
                }),
              })
              if (upRes.ok) {
                const upData = await upRes.json()
                if (upData.image) {
                  updateRow(capturedRowId, { status: 'done', imageSrc: upData.image })
                  return
                }
              }
              // Upscale failed — revert to done with original image
              upscaleFailCount++
              updateRow(capturedRowId, { status: 'done' })
            } catch {
              upscaleFailCount++
              updateRow(capturedRowId, { status: 'done' })
            }
          })
          // Kick off drain without awaiting (fire-and-forget)
          drainUpscaleQueue()
        }
      } catch (e: unknown) {
        if (e instanceof DOMException && e.name === 'AbortError') {
          // Stopped by user — reset row to pending
          updateRow(row.id, { status: 'pending', errorMsg: undefined })
        } else {
          const msg = e instanceof Error ? e.message : String(e)
          updateRow(row.id, { status: 'failed', errorMsg: msg })
          setGenDone(prev => prev + 1)
          lastError = msg
        }
      }
    })

    await runConcurrent(tasks, concurrency)

    // Wait for any remaining out-of-band upscale tasks to finish
    if (upscaleEnabled && upscaleQueue.length > 0) {
      await runConcurrent(upscaleQueue.splice(0), 4)
    }

    finishProgress(anySuccess)
    setIsRunning(false)
    abortControllerRef.current = null
    if (!signal.aborted && !anySuccess && lastError) setErrorMsg(lastError)

    if (signal.aborted) {
      taskStore.complete(bgTaskId, 'error', 'Cancelled by user')
    } else if (anySuccess) {
      const successRows = rowsRef.current.filter(r => rowsToGen.some(p => p.id === r.id) && r.status === 'done')
      const failRows    = rowsRef.current.filter(r => rowsToGen.some(p => p.id === r.id) && r.status === 'failed')
      const parts: string[] = []
      if (failRows.length > 0) parts.push(`${failRows.length} prompt${failRows.length !== 1 ? 's' : ''} failed`)
      if (upscaleFailCount > 0) parts.push(`${upscaleFailCount} upscale${upscaleFailCount !== 1 ? 's' : ''} failed`)
      if (parts.length > 0) {
        taskStore.complete(bgTaskId, 'done', `${successRows.length} images done, ${parts.join(', ')}`)
      } else {
        const upscaleNote = upscaleEnabled ? ' (upscaled)' : ''
        taskStore.complete(bgTaskId, 'done', `${successRows.length} images completed${upscaleNote}`)
      }
    } else {
      taskStore.complete(bgTaskId, 'error', lastError || 'Unknown error')
    }
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [backend, ensureOutputFolderMode, flowModel, flowProjectId, flowSessionCookie, initOutputFolder, outputFolder, ratio, refImages, seed, seedMode, style, threadCount, upscaleEnabled, upscaleResolution])

  // Expose generate all pending as stable ref for extension polling
  const handleGenerateAll = useCallback(() => {
    generateRows(rowsRef.current.filter(r => r.status === 'pending' || r.status === 'failed'))
  }, [generateRows])
  useEffect(() => { handleGenerateRef.current = handleGenerateAll }, [handleGenerateAll])

  const handleStop = useCallback(() => {
    if (abortControllerRef.current) {
      abortControllerRef.current.abort()
    }
  }, [])

  // ── Extension token polling ───────────────────────────────────────────────
  useEffect(() => {
    let stopped = false
    const poll = async () => {
      try {
        const res = await fetch(`${API}/api/ai-gen/flow-tokens/latest`)
        if (!res.ok) return
        const data = await res.json()
        const ts: number = data.timestamp ?? 0
        if (!ts || ts <= lastExtTokenTs.current) return
        lastExtTokenTs.current = ts

        const token: string  = data.bearerToken ?? ''
        const projId: string = data.projectId ?? ''
        const autoStart: boolean = data.autoStart ?? false
        if (!token) return

        setCookie(token); save('cookie', token)
        if (projId) { setFlowProjectId(projId); save('flow_project_id', projId) }
        const sessionCookie: string = data.sessionCookie ?? ''
        if (sessionCookie) { setFlowSessionCookie(sessionCookie); save('flow_session_cookie', sessionCookie) }
        setBackend('google_flow'); save('backend', 'google_flow')

        const preview = token.length > 20 ? token.slice(0, 16) + '…' : token
        const shouldShowTokenToast = !autoStart && !isRunningRef.current
        if (shouldShowTokenToast) {
          setTokenToast({ visible: true, projectId: projId, tokenPreview: preview, autoStart })
          if (toastTimerRef.current) clearTimeout(toastTimerRef.current)
          toastTimerRef.current = setTimeout(() => setTokenToast(t => ({ ...t, visible: false })), 5000)
        } else if (toastTimerRef.current) {
          clearTimeout(toastTimerRef.current)
          toastTimerRef.current = null
          setTokenToast(t => ({ ...t, visible: false }))
        }

        if (autoStart && parsePrompts(rawTextRef.current).length > 0 && !isRunningRef.current) {
          setTimeout(() => handleGenerateRef.current(), 600)
        }
      } catch { /* API not ready */ }
    }
    const interval = setInterval(() => { if (!stopped) poll() }, 2000)
    return () => { stopped = true; clearInterval(interval) }
  }, [])

  // ── File helpers ──────────────────────────────────────────────────────────
  const fileToRef = (file: File): Promise<RefImage> =>
    new Promise(res => {
      const r = new FileReader()
      r.onload = e => res({ id: uid(), thumbnail: e.target?.result as string, fileName: file.name, name: file.name.replace(/\.[^.]+$/, ''), mediaId: '' })
      r.readAsDataURL(file)
    })

  const handleAddFile = useCallback(async (file: File) => {
    if (refImages.length >= 3) return
    const ref = await fileToRef(file)
    setRefImages(prev => [...prev, ref])
  }, [refImages.length])

  const handleChangeFile = useCallback(async (file: File, id: string) => {
    const ref = await fileToRef(file)
    setRefImages(prev => prev.map(r => r.id === id ? { ...ref, id, name: r.name } : r))
  }, [])

  const handleDownload = (row: PromptRow) => {
    if (!row.imageSrc) return
    const a = document.createElement('a')
    a.href = row.imageSrc
    a.download = `ai_gen_${Date.now()}.jpg`
    a.click()
  }

  // ── Derived counts ────────────────────────────────────────────────────────
  const pendingCount    = rows.filter(r => r.status === 'pending').length
  const generatingCount = rows.filter(r => r.status === 'generating').length
  const doneCount       = rows.filter(r => r.status === 'done').length
  const failedCount     = rows.filter(r => r.status === 'failed').length
  const selectedRows    = rows.filter(r => selected.has(r.id))
  const failedRows      = rows.filter(r => r.status === 'failed')
  const canGenerate     = rows.length > 0 && !!cookie.trim() && !isRunning
  const allSelected     = rows.length > 0 && selected.size === rows.length

  // ──────────────────────────────────────────────────────────────────────────
  return (
    <div className="flex flex-col h-full bg-surface-50 overflow-hidden">

      {/* ── Toast ── */}
      <AnimatePresence>
        {tokenToast.visible && (
          <motion.div
            initial={{ height: 0, opacity: 0 }} animate={{ height: 'auto', opacity: 1 }}
            exit={{ height: 0, opacity: 0 }} transition={{ duration: 0.22 }}
            className="overflow-hidden shrink-0"
          >
            <div className="flex items-center justify-between gap-3 px-5 py-2.5 bg-gradient-to-r from-violet-600 to-indigo-600 text-white text-xs">
              <div className="flex items-center gap-2 min-w-0">
                <Zap size={13} className="shrink-0" />
                <span className="font-medium">Token received from extension</span>
                <span className="opacity-70">·</span>
                <span className="font-mono opacity-80 truncate max-w-[200px]">{tokenToast.tokenPreview}</span>
                {tokenToast.projectId && <><span className="opacity-70">·</span><span className="opacity-80 truncate max-w-[120px]">Project: {tokenToast.projectId.slice(0, 8)}…</span></>}
                {tokenToast.autoStart && <span className="ml-1 px-1.5 py-0.5 rounded-full bg-white/20 font-semibold">⚡ Auto-generating</span>}
              </div>
              <button className="shrink-0 opacity-60 hover:opacity-100 transition-opacity" onClick={() => setTokenToast(t => ({ ...t, visible: false }))}>✕</button>
            </div>
          </motion.div>
        )}
      </AnimatePresence>

      {/* ── Header ── */}
      <div className="flex items-center justify-between px-6 py-3.5 border-b border-surface-200 bg-white shrink-0">
        <div className="flex items-center gap-3">
          <div className="w-8 h-8 rounded-lg bg-violet-100 flex items-center justify-center">
            <Wand2 size={16} className="text-violet-600" />
          </div>
          <div>
            <h1 className="page-title">AI Image Generator</h1>
            <p className="page-sub">
              {backend === 'whisk' ? 'Whisk (Google) · IMAGEN 3.5' : `Google Flow · ${FLOW_MODELS.find(m => m.value === flowModel)?.label ?? flowModel}`}
            </p>
          </div>
        </div>
        <div className="flex items-center gap-2">
          {lastExtTokenTs.current > 0 && (
            <span className="flex items-center gap-1.5 px-2 py-1 rounded-full bg-green-50 border border-green-200 text-[11px] font-medium text-green-700">
              <span className="w-1.5 h-1.5 rounded-full bg-green-500 animate-pulse" />
              Extension connected
            </span>
          )}
          <button className={cn('btn-icon', showSettings && 'bg-surface-100 text-primary-600')} onClick={() => setShowSettings(s => !s)} title="Settings">
            <Settings2 size={16} />
          </button>
        </div>
      </div>

      <div className="flex flex-1 overflow-hidden">

        {/* ── Settings Panel ── */}
        <AnimatePresence initial={false}>
          {showSettings && (
            <motion.aside
              initial={{ width: 0, opacity: 0 }} animate={{ width: 272, opacity: 1 }} exit={{ width: 0, opacity: 0 }}
              transition={{ duration: 0.2 }}
              className="shrink-0 border-r border-surface-200 bg-white overflow-y-auto overflow-x-hidden"
            >
              <div className="p-4 space-y-5 w-[272px]">

                {/* Backend */}
                <section>
                  <label className="label mb-2 block">Backend</label>
                  <div className="flex gap-1">
                    {(['whisk', 'google_flow'] as const).map(b => (
                      <button key={b} onClick={() => { setBackend(b); save('backend', b) }}
                        className={cn('flex-1 py-2 rounded-lg border text-xs font-medium transition-all',
                          backend === b ? 'border-violet-500 bg-violet-50 text-violet-700' : 'border-surface-200 text-surface-500 hover:border-surface-300')}>
                        {b === 'whisk' ? '⚡ Whisk' : '🌊 Google Flow'}
                      </button>
                    ))}
                  </div>
                </section>

                {/* Cookie / Token */}
                <section>
                  <label className="label flex items-center gap-1.5 mb-2"><Key size={11} />{backend === 'whisk' ? 'Session Cookie' : 'Bearer Token'}</label>
                  <textarea className="input text-xs font-mono resize-none leading-relaxed" rows={4}
                    placeholder={backend === 'whisk' ? 'Paste Google session cookie…' : 'Paste ya29.xxx bearer token…'}
                    value={cookie} onChange={e => { setCookie(e.target.value); save('cookie', e.target.value) }}
                  />
                  <p className="text-[10px] text-surface-400 mt-1">
                    {backend === 'whisk' ? 'F12 → Application → Cookies → labs.google' : 'From Google Flow extension or DevTools'}
                  </p>
                </section>

                {/* Google Flow settings */}
                {backend === 'google_flow' && (
                  <>
                    <section>
                      <label className="label mb-2 block">Flow Project ID</label>
                      <input className="input text-xs font-mono" placeholder="xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx"
                        value={flowProjectId} onChange={e => { setFlowProjectId(e.target.value); save('flow_project_id', e.target.value) }} />
                      <p className="text-[10px] text-surface-400 mt-1">From labs.google/fx/tools/flow URL</p>
                    </section>
                    <section>
                      <label className="label flex items-center gap-1.5 mb-2"><Key size={11} /> Flow Session Cookie</label>
                      <textarea className="input text-xs font-mono resize-none leading-relaxed" rows={3}
                        placeholder="__Secure-next-auth.session-token=xxxxxx…"
                        value={flowSessionCookie}
                        onChange={e => { setFlowSessionCookie(e.target.value); save('flow_session_cookie', e.target.value) }}
                      />
                      <p className="text-[10px] text-surface-400 mt-1">F12 → Application → Cookies → labs.google → copy <strong>__Secure-next-auth.session-token</strong></p>
                    </section>
                    <section>
                      <label className="label mb-2 block">Model</label>
                      <div className="relative">
                        <select className="input appearance-none pr-7 text-xs" value={flowModel}
                          onChange={e => { setFlowModel(e.target.value); save('flow_model', e.target.value) }}>
                          {FLOW_MODELS.map(m => <option key={m.value} value={m.value}>{m.label}</option>)}
                        </select>
                        <ChevronDown size={13} className="absolute right-2 top-1/2 -translate-y-1/2 text-surface-400 pointer-events-none" />
                      </div>
                    </section>
                  </>
                )}

                {/* Reference Images */}
                <section>
                  <label className="label mb-3 flex items-center justify-between">
                    <span>Reference Images</span>
                    <span className="text-[10px] text-surface-400 font-normal">{refImages.length}/3</span>
                  </label>
                  <div className="space-y-3">
                    {refImages.map((ref, idx) => (
                      <div key={ref.id} className="rounded-xl border border-surface-200 bg-surface-50 overflow-hidden">
                        <div className="flex items-center gap-3 p-2">
                          <div className="w-14 h-14 shrink-0 rounded-lg overflow-hidden bg-surface-200">
                            <img src={ref.thumbnail} alt="" className="w-full h-full object-cover" />
                          </div>
                          <div className="flex-1 min-w-0">
                            <label htmlFor={`ref-change-${ref.id}`}
                              className="flex items-center gap-1.5 px-2.5 py-1.5 rounded-lg border border-surface-200 bg-white text-[11px] font-medium text-surface-600 hover:border-violet-400 hover:text-violet-600 hover:bg-violet-50 cursor-pointer transition-all w-full justify-center">
                              <Camera size={11} /> Change #{idx + 1}
                            </label>
                            <input id={`ref-change-${ref.id}`} type="file" accept="image/*" className="hidden"
                              onChange={e => { const f = e.target.files?.[0]; if (f) handleChangeFile(f, ref.id); e.target.value = '' }} />
                          </div>
                        </div>
                        <div className="px-2 pb-2">
                          <div className="flex items-center gap-1.5">
                            <input className="input flex-1 text-xs py-1.5" placeholder={`Name #${idx + 1}`}
                              value={ref.name} onChange={e => setRefImages(prev => prev.map(r => r.id === ref.id ? { ...r, name: e.target.value } : r))} />
                            <button className="p-1.5 rounded-lg text-surface-400 hover:text-red-500 hover:bg-red-50 transition-all"
                              onClick={() => setRefImages(prev => prev.filter(r => r.id !== ref.id))}><Trash2 size={14} /></button>
                          </div>
                          <p className="text-[10px] text-surface-400 mt-1 px-0.5 truncate">{ref.fileName}</p>
                        </div>
                      </div>
                    ))}
                  </div>
                  {refImages.length < 3 && (
                    <>
                      <input id="ref-add-input" ref={addInputRef} type="file" accept="image/*" className="hidden"
                        onChange={e => { const f = e.target.files?.[0]; if (f) handleAddFile(f); e.target.value = '' }} />
                      <label htmlFor="ref-add-input"
                        className="mt-3 w-full flex items-center justify-center gap-2 py-2 rounded-lg border border-dashed text-xs transition-all cursor-pointer border-surface-300 text-surface-500 hover:border-violet-400 hover:text-violet-600 hover:bg-violet-50">
                        <Plus size={12} /> Add Reference Image
                      </label>
                    </>
                  )}
                </section>

                {/* Aspect Ratio */}
                <section>
                  <label className="label mb-2 block">Aspect Ratio</label>
                  <div className="grid grid-cols-5 gap-1">
                    {RATIOS.map(r => (
                      <button key={r.label} onClick={() => setRatio(r.label)}
                        className={cn('flex flex-col items-center gap-1 py-2 px-1 rounded-lg border text-[10px] font-medium transition-all',
                          ratio === r.label ? 'border-violet-500 bg-violet-50 text-violet-700' : 'border-surface-200 text-surface-500 hover:border-surface-300')}>
                        <div className={cn('border-2 rounded-sm', ratio === r.label ? 'border-violet-500' : 'border-surface-400')}
                          style={{ width: r.w >= r.h ? 18 : Math.round(18 * r.w / r.h), height: r.h >= r.w ? 18 : Math.round(18 * r.h / r.w) }} />
                        {r.label}
                      </button>
                    ))}
                  </div>
                </section>

                {/* Style */}
                <section>
                  <label className="label mb-2 block">Style Preset</label>
                  <div className="relative">
                    <select className="input appearance-none pr-7 text-xs" value={style} onChange={e => setStyle(e.target.value)}>
                      {STYLES.map(s => <option key={s}>{s}</option>)}
                    </select>
                    <ChevronDown size={13} className="absolute right-2 top-1/2 -translate-y-1/2 text-surface-400 pointer-events-none" />
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
                      placeholder="/path/to/save/images…"
                      value={outputFolder}
                      onChange={e => applyOutputFolder(e.target.value)}
                    />
                    <button
                      onClick={async () => {
                        try {
                          const selected = await pickWorkspaceDirectory('AI images')
                          if (selected && typeof selected === 'string') {
                            applyOutputFolder(selected)
                          }
                        } catch { /* user cancelled */ }
                      }}
                      className="shrink-0 px-2.5 py-2 rounded-lg border border-surface-200 bg-white text-surface-500 hover:border-violet-400 hover:text-violet-600 hover:bg-violet-50 transition-all"
                      title="Browse folder"
                    >
                      <FolderOpen size={14} />
                    </button>
                  </div>
                  {outputFolder.trim() ? (
                    <p className="text-[10px] text-green-600 mt-1.5 flex items-center gap-1">
                      <CheckCircle2 size={9} /> Images will be auto-saved here
                    </p>
                  ) : (
                    <p className="text-[10px] text-surface-400 mt-1.5">
                      Optional. Set a folder to auto-save generated images.
                    </p>
                  )}
                </section>


                {/* Threads */}
                <section>
                  <label className="label mb-2 flex items-center justify-between">
                    <span>Parallel Threads</span>
                    <span className="text-[10px] font-semibold text-violet-600 bg-violet-50 border border-violet-200 px-1.5 py-0.5 rounded">
                      {threadCount === 0 ? `Auto (${suggestThreads(rows.length || 1)})` : `${threadCount}×`}
                    </span>
                  </label>
                  <input
                    type="range" min={0} max={30} step={1}
                    value={threadCount}
                    onChange={e => { setThreadCount(Number(e.target.value)) }}
                    className="w-full accent-violet-600"
                  />
                  <div className="flex justify-between text-[10px] text-surface-400 mt-1 px-0.5">
                    <span>Auto</span>
                    <span>10×</span>
                    <span>20×</span>
                    <span>30×</span>
                  </div>
                  <p className="text-[10px] text-surface-400 mt-1.5">Default is 30×. Higher values are faster but increase the risk of rate limiting.</p>
                </section>

                {/* Upscale */}
                <section>
                  <div className="flex items-center justify-between mb-2">
                    <label className="label">Upscale After Gen</label>
                    <button
                      onClick={() => { const v = !upscaleEnabled; setUpscaleEnabled(v); localStorage.setItem(LS('upscale_enabled'), String(v)) }}
                      className={cn(
                        'relative inline-flex h-5 w-9 items-center rounded-full transition-colors',
                        upscaleEnabled ? 'bg-violet-600' : 'bg-surface-200',
                      )}
                    >
                      <span className={cn('inline-block h-3.5 w-3.5 transform rounded-full bg-white shadow transition-transform', upscaleEnabled ? 'translate-x-4' : 'translate-x-0.5')} />
                    </button>
                  </div>
                  {upscaleEnabled && (
                    <div className="space-y-1.5">
                      {UPSCALE_RESOLUTIONS.map(({ key, label }) => {
                        const { w, h } = resolveUpscaleDimensions(ratio, key)
                        const isSelected = upscaleResolution === key
                        return (
                          <button
                            key={key}
                            onClick={() => { setUpscaleResolution(key); localStorage.setItem(LS('upscale_res'), key) }}
                            className={cn(
                              'w-full flex items-center justify-between px-3 py-2 rounded-lg border text-xs font-medium transition-all',
                              isSelected
                                ? 'border-violet-500 bg-violet-50 text-violet-700'
                                : 'border-surface-200 text-surface-600 hover:border-surface-300 hover:bg-surface-50',
                            )}
                          >
                            <span className="font-semibold">{label}</span>
                            <span className={cn('text-[10px] font-mono', isSelected ? 'text-violet-500' : 'text-surface-400')}>
                              {w}×{h}
                            </span>
                          </button>
                        )
                      })}
                      <p className="text-[10px] text-surface-400 pt-0.5 px-0.5">
                        Upscales each image immediately after generation.
                      </p>
                    </div>
                  )}
                </section>

              </div>
            </motion.aside>
          )}
        </AnimatePresence>

        {/* ── Main Area ── */}
        <main className="flex-1 flex flex-col overflow-hidden relative min-w-0">

          {/* ── Prompt Input ── */}
          <div className="bg-white border-b border-surface-200 shrink-0 px-5 pt-4 pb-3">

            {!cookie.trim() && (
              <div className="mb-3 flex items-center gap-2 px-3 py-2 rounded-lg bg-amber-50 border border-amber-200 text-xs text-amber-700">
                <Key size={12} className="shrink-0" />
                Paste your {backend === 'whisk' ? 'Google session cookie' : 'bearer token'} in Settings to start.
              </div>
            )}

            <div className="flex items-start gap-3">
              {/* Textarea with drag & drop zone */}
              <div
                className={cn(
                  "flex-1 relative rounded-lg transition-all duration-200",
                  isDragging && "ring-2 ring-violet-400 ring-offset-2"
                )}
                onDragEnter={handleDragEnter}
                onDragOver={handleDragOver}
                onDragLeave={handleDragLeave}
                onDrop={handleDrop}
              >
                <textarea
                  className="input w-full resize-none text-sm leading-relaxed font-mono"
                  rows={5}
                  placeholder={"Paste prompts here — one per line, auto-numbered:\n\n1  Professional YouTube thumbnail, 16:9...\n2  Cinematic portrait of a doctor...\n3  Abstract background with neon colors...\n\nOr drag & drop a .txt / .csv / .md / .json file"}
                  value={rawText}
                  onChange={e => setRawText(e.target.value)}
                  onPaste={handlePaste}
                  onKeyDown={e => { if (e.key === 'Enter' && (e.metaKey || e.ctrlKey)) handleGenerateAll() }}
                />

                {/* Row count badge */}
                {rows.length > 0 && (
                  <div className="absolute top-2 right-2 px-1.5 py-0.5 rounded text-[10px] font-semibold bg-violet-100 text-violet-600 pointer-events-none select-none">
                    {rows.length}
                  </div>
                )}

                {/* Drag overlay */}
                <AnimatePresence>
                  {isDragging && (
                    <motion.div
                      initial={{ opacity: 0 }}
                      animate={{ opacity: 1 }}
                      exit={{ opacity: 0 }}
                      transition={{ duration: 0.15 }}
                      className="absolute inset-0 z-10 flex flex-col items-center justify-center gap-2 rounded-lg border-2 border-dashed border-violet-400 bg-violet-50/90 backdrop-blur-sm"
                    >
                      <Upload size={24} className="text-violet-500" />
                      <span className="text-sm font-medium text-violet-600">Drop file to import prompts</span>
                      <span className="text-[10px] text-violet-400">.txt, .csv, .md, .json</span>
                    </motion.div>
                  )}
                </AnimatePresence>
              </div>

              {/* Quick action panel */}
              <div className="flex flex-col gap-2 shrink-0 pt-0.5">
                <button
                  className="btn-primary flex items-center gap-2 px-4 py-2.5 disabled:opacity-40 disabled:cursor-not-allowed whitespace-nowrap"
                  onClick={handleGenerateAll}
                  disabled={!canGenerate || (pendingCount === 0 && failedCount === 0)}
                >
                  {isRunning ? <Loader size={14} className="animate-spin" /> : <Sparkles size={14} />}
                  {isRunning
                    ? `${genDone + 1}/${genTotal}…`
                    : (() => {
                        const total = pendingCount + failedCount
                        return total > 0 ? `Generate (${total})` : 'Generate'
                      })()
                  }
                </button>
                <div className="text-[10px] text-surface-400 text-center">⌘+Enter</div>

                {/* Import File button */}
                <button
                  className="flex items-center justify-center gap-1.5 px-3 py-1.5 rounded-lg border border-surface-200 bg-white text-[11px] font-medium text-surface-600 hover:bg-surface-50 hover:border-surface-300 transition-all"
                  onClick={() => fileInputRef.current?.click()}
                  title="Import prompts from file (.txt, .csv, .md, .json)"
                >
                  <Upload size={12} />
                  Import
                </button>
                <input
                  ref={fileInputRef}
                  type="file"
                  accept=".txt,.csv,.md,.json"
                  multiple
                  className="hidden"
                  onChange={e => { if (e.target.files) handleFileImport(e.target.files); e.target.value = '' }}
                />
              </div>
            </div>

            {/* Status bar */}
            <div className="flex items-center justify-between mt-2.5 px-0.5">
              <div className="flex items-center gap-3 text-[11px]">
                {isRunning && (
                  <span className="flex items-center gap-1 text-violet-600">
                    <Loader size={10} className="animate-spin" /> {genStage}
                  </span>
                )}
                {!isRunning && errorMsg && (
                  <span className="flex items-center gap-1 text-red-500"><XCircle size={10} /> {errorMsg}</span>
                )}
                {!isRunning && !errorMsg && rows.length > 0 && (
                  <span className="text-surface-400">
                    {doneCount > 0 && <span className="text-green-600 font-medium mr-1">✓ {doneCount} done</span>}
                    {failedCount > 0 && <span className="text-red-500 font-medium mr-1">✗ {failedCount} failed</span>}
                    {pendingCount > 0 && <span className="text-surface-400">○ {pendingCount} pending</span>}
                  </span>
                )}
              </div>
              <span className="text-[10px] text-surface-400 bg-surface-100 px-2 py-1 rounded-md font-medium">{ratio} · {style}</span>
            </div>
          </div>

          {/* ── Results Table ── */}
          <div className="flex-1 overflow-hidden flex flex-col min-h-0">

            {/* Table toolbar */}
            <div className="bg-white border-b border-surface-200 px-5 py-2.5 flex items-center justify-between shrink-0">
              <div className="flex items-center gap-3">
                <span className="text-sm font-semibold text-surface-700">Generation Results</span>
                <span className="text-[11px] bg-surface-100 text-surface-500 border border-surface-200 px-2 py-0.5 rounded-full font-medium">
                  Total: {rows.length}
                </span>
              </div>
              <div className="flex items-center gap-1.5">
                <button
                  onClick={() => generateRows(selectedRows)}
                  disabled={selected.size === 0 || isRunning}
                  className={cn(
                    'flex items-center gap-1.5 px-3 py-1.5 rounded-lg border text-[11px] font-medium transition-all',
                    selected.size > 0 && !isRunning
                      ? 'border-violet-300 bg-violet-50 text-violet-700 hover:bg-violet-100'
                      : 'border-surface-200 text-surface-400 cursor-not-allowed opacity-50',
                  )}
                >
                  <RefreshCw size={11} /> Regenerate Selected
                </button>
                <button
                  onClick={() => generateRows(failedRows)}
                  disabled={failedCount === 0 || isRunning}
                  className={cn(
                    'flex items-center gap-1.5 px-3 py-1.5 rounded-lg border text-[11px] font-medium transition-all',
                    failedCount > 0 && !isRunning
                      ? 'border-red-300 bg-red-50 text-red-700 hover:bg-red-100'
                      : 'border-surface-200 text-surface-400 cursor-not-allowed opacity-50',
                  )}
                >
                  <AlertCircle size={11} /> Regenerate Failed
                </button>
                <button
                  onClick={clearResults}
                  disabled={rows.length === 0}
                  className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg border border-surface-200 text-[11px] font-medium text-surface-500 hover:border-surface-300 hover:bg-surface-50 transition-all disabled:opacity-40 disabled:cursor-not-allowed"
                >
                  <Trash2 size={11} /> Clear
                </button>
              </div>
            </div>

            {/* Table */}
            {rows.length === 0 ? (
              <div className="flex-1 flex flex-col items-center justify-center text-surface-300 gap-3 select-none">
                <div className="w-14 h-14 rounded-2xl bg-surface-100 flex items-center justify-center">
                  <ImageIcon size={24} className="opacity-50" />
                </div>
                <p className="text-sm">Paste prompts above to populate the table</p>
              </div>
            ) : (
              <div className="flex-1 overflow-y-auto">
                <table className="w-full border-collapse text-sm">
                  <thead className="sticky top-0 z-10">
                    <tr className="bg-surface-50 border-b border-surface-200">
                      {/* Select all */}
                      <th className="w-10 px-3 py-2.5 text-left">
                        <button onClick={toggleSelectAll} className="text-surface-400 hover:text-violet-600 transition-colors">
                          {allSelected ? <CheckSquare size={14} className="text-violet-600" /> : <Square size={14} />}
                        </button>
                      </th>
                      <th className="w-10 px-2 py-2.5 text-left text-[11px] font-semibold text-surface-500">#</th>
                      <th className="w-20 px-2 py-2.5 text-left text-[11px] font-semibold text-surface-500">Image</th>
                      <th className="w-32 px-3 py-2.5 text-left text-[11px] font-semibold text-surface-500">Status</th>
                      <th className="px-3 py-2.5 text-left text-[11px] font-semibold text-surface-500">Prompt</th>
                      <th className="w-16 px-2 py-2.5" />
                    </tr>
                  </thead>
                  <tbody>
                    <AnimatePresence initial={false}>
                      {rows.map((row, idx) => (
                        <motion.tr
                          key={row.id}
                          initial={{ opacity: 0, y: -4 }}
                          animate={{ opacity: 1, y: 0 }}
                          exit={{ opacity: 0 }}
                          transition={{ duration: 0.15 }}
                          className={cn(
                            'border-b border-surface-100 transition-colors',
                            selected.has(row.id) ? 'bg-violet-50' : 'bg-white hover:bg-surface-50',
                            row.status === 'generating' && 'bg-violet-50/60',
                          )}
                        >
                          {/* Checkbox */}
                          <td className="px-3 py-2.5">
                            <button onClick={() => toggleSelect(row.id)} className="text-surface-400 hover:text-violet-600 transition-colors">
                              {selected.has(row.id) ? <CheckSquare size={14} className="text-violet-600" /> : <Square size={14} />}
                            </button>
                          </td>

                          {/* Index */}
                          <td className="px-2 py-2.5">
                            <span className={cn(
                              'w-6 h-6 rounded-full text-[11px] font-bold flex items-center justify-center select-none',
                              row.status === 'done' ? 'bg-green-100 text-green-700' :
                              row.status === 'failed' ? 'bg-red-100 text-red-700' :
                              row.status === 'generating' ? 'bg-violet-100 text-violet-700' :
                              'bg-surface-100 text-surface-500',
                            )}>
                              {idx + 1}
                            </span>
                          </td>

                          {/* Image */}
                          <td className="px-2 py-2.5">
                            <div
                              className={cn('w-16 h-11 rounded-lg overflow-hidden border flex items-center justify-center cursor-pointer transition-all',
                                row.imageSrc ? 'border-surface-200 hover:border-violet-400 hover:shadow-md' : 'border-dashed border-surface-200 bg-surface-50'
                              )}
                              onClick={() => row.imageSrc && setLightbox(row)}
                            >
                              {row.imageSrc ? (
                                <img src={row.imageSrc} alt="" className="w-full h-full object-cover" />
                              ) : row.status === 'generating' ? (
                                <Loader size={14} className="text-violet-400 animate-spin" />
                              ) : (
                                <ImageIcon size={12} className="text-surface-300" />
                              )}
                            </div>
                          </td>

                          {/* Status */}
                          <td className="px-3 py-2.5">
                            <StatusBadge status={row.status} errorMsg={row.errorMsg} />
                            {row.createdAt && (
                              <p className="text-[9px] text-surface-400 mt-0.5">{row.createdAt}</p>
                            )}
                          </td>

                          {/* Prompt */}
                          <td className="px-3 py-2.5 max-w-0">
                            <p className="text-xs text-surface-700 leading-relaxed line-clamp-2" title={row.prompt}>
                              {row.prompt}
                            </p>
                            {row.status === 'failed' && row.errorMsg && (
                              <p className="text-[10px] text-red-500 mt-0.5 truncate">{row.errorMsg}</p>
                            )}
                          </td>

                          {/* Actions */}
                          <td className="px-2 py-2.5">
                            <div className="flex items-center gap-1 justify-end">
                              {row.imageSrc && (
                                <button
                                  className="p-1.5 rounded-lg text-surface-400 hover:text-violet-600 hover:bg-violet-50 transition-all"
                                  onClick={() => handleDownload(row)}
                                  title="Download"
                                >
                                  <Download size={13} />
                                </button>
                              )}
                              {(row.status === 'done' || row.status === 'failed') && (
                                <button
                                  className="p-1.5 rounded-lg text-surface-400 hover:text-violet-600 hover:bg-violet-50 transition-all"
                                  onClick={() => generateRows([row])}
                                  disabled={isRunning}
                                  title="Regenerate"
                                >
                                  <RefreshCw size={13} />
                                </button>
                              )}
                            </div>
                          </td>
                        </motion.tr>
                      ))}
                    </AnimatePresence>
                  </tbody>
                </table>
              </div>
            )}

            {/* Stats footer */}
            {rows.length > 0 && (
              <Tooltip.Provider>
                <div className="shrink-0 border-t border-surface-200 bg-white px-5 py-2.5 flex items-center gap-6">
                  <div className="flex items-center gap-1.5 text-[11px] font-medium text-green-700">
                    <CheckCircle2 size={12} />
                    <span>{doneCount} Completed</span>
                    <StatHelp
                      label="Completed"
                      help="Images that finished generating successfully and are ready to preview or download."
                      tone="success"
                    />
                  </div>
                  <div
                    className={cn(
                      'flex items-center gap-1.5 text-[11px] font-medium text-red-600 transition-colors',
                      failedCount > 0 && 'cursor-pointer hover:text-red-700 hover:underline'
                    )}
                    onClick={() => failedCount > 0 && setShowFailedModal(true)}
                  >
                    <XCircle size={12} />
                    <span>{failedCount} Failed</span>
                    <StatHelp
                      label="Failed"
                      help="Prompts that failed during generation. Click this status to review each error."
                      tone="error"
                    />
                  </div>
                  <div className="flex items-center gap-1.5 text-[11px] font-medium text-surface-500">
                    <Clock size={12} />
                    <span>{pendingCount + generatingCount} Pending</span>
                    <StatHelp
                      label="Pending"
                      help="Prompts that are still waiting or currently being processed."
                    />
                  </div>
                  {selected.size > 0 && (
                    <span className="ml-auto text-[11px] text-violet-600 font-medium">{selected.size} selected</span>
                  )}
                </div>
              </Tooltip.Provider>
            )}
          </div>

          {/* ── Progress Popup ── */}
          <AnimatePresence>
            {isRunning && (
              <motion.div
                initial={{ opacity: 0, y: 24 }} animate={{ opacity: 1, y: 0 }}
                exit={{ opacity: 0, y: 24 }} transition={{ duration: 0.25 }}
                className="absolute bottom-6 right-6 w-[340px] bg-white rounded-2xl shadow-2xl border border-surface-200 overflow-hidden"
              >
                <div className="h-1 bg-gradient-to-r from-violet-500 via-indigo-500 to-violet-500 animate-pulse" />
                <div className="p-4">
                  <div className="flex items-center justify-between mb-3">
                    <div className="flex items-center gap-2">
                      <div className="w-6 h-6 rounded-lg bg-violet-100 flex items-center justify-center">
                        <Sparkles size={12} className="text-violet-600" />
                      </div>
                      <div>
                        <p className="text-xs font-semibold text-surface-800">
                          {genTotal > 1 ? `Batch: ${genDone}/${genTotal} done` : 'Generating…'}
                        </p>
                        <p className="text-[10px] text-surface-400">
                          {backend === 'whisk' ? 'Whisk · IMAGEN 3.5' : `Flow · ${flowModel}`}
                          {genTotal > 1 && ` · ${Math.min(threadCount > 0 ? threadCount : suggestThreads(genTotal), genTotal - genDone)} parallel`}
                        </p>
                      </div>
                    </div>
                    <span className="text-xl font-bold text-violet-600 tabular-nums">
                      {genTotal > 0 ? Math.round((genDone / genTotal) * 100) : Math.round(genPct)}%
                    </span>
                  </div>

                  {/* Batch pills */}
                  {genTotal > 1 && (
                    <div className="flex gap-1 mb-3 flex-wrap">
                      {Array.from({ length: genTotal }).map((_, i) => (
                        <div key={i} className={cn('h-1.5 flex-1 min-w-[6px] rounded-full transition-all duration-300',
                          i < genDone ? 'bg-violet-500' : i === genDone ? 'bg-violet-300 animate-pulse' : 'bg-surface-200')} />
                      ))}
                    </div>
                  )}

                  <div className="h-1.5 bg-surface-100 rounded-full overflow-hidden mb-3">
                    <motion.div className="h-full bg-gradient-to-r from-violet-500 to-indigo-500 rounded-full"
                      animate={{ width: `${genTotal > 0 ? Math.round((genDone / genTotal) * 100) : genPct}%` }}
                      transition={{ duration: 0.4, ease: 'easeOut' }} />
                  </div>

                  <div className="flex items-center gap-1.5 text-[11px] text-surface-500">
                    <Loader size={10} className="animate-spin text-violet-500" />
                    {genStage}
                  </div>

                  {/* Stage dots */}
                  <div className="flex items-center gap-1 mt-3">
                    {GEN_STAGES.slice(0, -1).map((s, i) => (
                      <div key={s.key} className="flex items-center flex-1">
                        <div className={cn('w-1.5 h-1.5 rounded-full shrink-0 transition-all',
                          genPct >= s.pct ? 'bg-violet-500 scale-125' : genPct >= (GEN_STAGES[i-1]?.pct ?? 0) ? 'bg-violet-300 animate-pulse' : 'bg-surface-200')} />
                        {i < GEN_STAGES.length - 2 && (
                          <div className={cn('h-0.5 flex-1 mx-0.5 transition-all', genPct >= s.pct ? 'bg-violet-400' : 'bg-surface-200')} />
                        )}
                      </div>
                    ))}
                  </div>

                  {/* Stop button */}
                  <button
                    onClick={handleStop}
                    className="mt-3 w-full flex items-center justify-center gap-1.5 py-1.5 rounded-lg border border-red-200 bg-red-50 text-red-600 text-[11px] font-semibold hover:bg-red-100 hover:border-red-300 transition-all"
                  >
                    <Square size={10} className="fill-red-500 text-red-500" /> Stop
                  </button>
                </div>
              </motion.div>
            )}
          </AnimatePresence>

        </main>
      </div>

      {/* ── Output Folder Conflict Modal ── */}
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
                Choose whether to continue in this folder or close and pick another folder.
              </p>
              {outputConflictDialog.videoCount > 0 && (
                <div className="mt-4 rounded-xl border border-amber-200 bg-amber-50 px-4 py-3 text-xs leading-5 text-amber-800">
                  Video files stay safe. AI Gen only writes image files, but this warning prevents mixing a new image batch into a video folder by accident.
                  {outputConflictDialog.videoSampleNames.length > 0 && (
                    <div className="mt-1 font-medium">
                      Videos: {outputConflictDialog.videoSampleNames.join(', ')}
                    </div>
                  )}
                </div>
              )}
              {outputConflictDialog.sampleNames.length > 0 && (
                <div className="mt-3 rounded-xl border border-surface-200 bg-surface-50 px-4 py-3 text-xs leading-5 text-surface-500">
                  Existing images in folder: {outputConflictDialog.sampleNames.join(', ')}
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

      {/* ── Lightbox ── */}
      <AnimatePresence>
        {lightbox && (
          <motion.div
            initial={{ opacity: 0 }} animate={{ opacity: 1 }} exit={{ opacity: 0 }}
            className="fixed inset-0 z-50 bg-black/80 backdrop-blur-sm flex items-center justify-center p-6"
            onClick={() => setLightbox(null)}
          >
            {/* Prev arrow */}
            <button
              className={cn(
                'absolute left-4 top-1/2 -translate-y-1/2 z-10 w-10 h-10 rounded-full bg-white/20 hover:bg-white/40 text-white flex items-center justify-center transition-all',
                !lightboxPrev && 'opacity-0 pointer-events-none'
              )}
              onClick={e => { e.stopPropagation(); if (lightboxPrev) setLightbox(lightboxPrev) }}
            >
              <ChevronLeft size={22} />
            </button>

            {/* Next arrow */}
            <button
              className={cn(
                'absolute right-4 top-1/2 -translate-y-1/2 z-10 w-10 h-10 rounded-full bg-white/20 hover:bg-white/40 text-white flex items-center justify-center transition-all',
                !lightboxNext && 'opacity-0 pointer-events-none'
              )}
              onClick={e => { e.stopPropagation(); if (lightboxNext) setLightbox(lightboxNext) }}
            >
              <ChevronRight size={22} />
            </button>

            <motion.div
              initial={{ scale: 0.92 }} animate={{ scale: 1 }} exit={{ scale: 0.92 }}
              className="relative max-w-3xl w-full bg-white rounded-2xl overflow-hidden shadow-2xl"
              onClick={e => e.stopPropagation()}
            >
              <img src={lightbox.imageSrc} alt={lightbox.prompt} className="w-full object-contain max-h-[72vh]" />
              <div className="p-4 flex items-start justify-between gap-4">
                <div className="flex-1 min-w-0">
                  <div className="flex items-center gap-2 mb-1">
                    <p className="text-sm font-medium text-surface-800 line-clamp-2">{lightbox.prompt}</p>
                    {lightboxImages.length > 1 && (
                      <span className="shrink-0 text-xs text-surface-400 bg-surface-100 px-1.5 py-0.5 rounded">
                        {lightboxIndex + 1} / {lightboxImages.length}
                      </span>
                    )}
                  </div>
                  <div className="flex items-center gap-2 flex-wrap text-xs text-surface-400">
                    <span className="bg-surface-100 px-1.5 py-0.5 rounded">{ratio}</span>
                    <span>{style}</span>
                    <span className="text-violet-500 font-medium">{backend === 'whisk' ? 'Whisk' : 'Google Flow'}</span>
                    {lightbox.seed && <span>seed: {lightbox.seed}</span>}
                    {lightbox.createdAt && <span>{lightbox.createdAt}</span>}
                  </div>
                </div>
                <button className="btn-secondary flex items-center gap-1.5 text-xs shrink-0" onClick={() => handleDownload(lightbox)}>
                  <Download size={13} /> Download
                </button>
              </div>
            </motion.div>
          </motion.div>
        )}
      </AnimatePresence>

      {/* ── Failed Prompts Modal ── */}
      <AnimatePresence>
        {showFailedModal && (
          <motion.div
            initial={{ opacity: 0 }} animate={{ opacity: 1 }} exit={{ opacity: 0 }}
            transition={{ duration: 0.18 }}
            className="fixed inset-0 z-50 bg-black/30 flex items-center justify-center p-6"
            onClick={() => setShowFailedModal(false)}
          >
            <motion.div
              initial={{ opacity: 0, scale: 0.96, y: 8 }}
              animate={{ opacity: 1, scale: 1, y: 0 }}
              exit={{ opacity: 0, scale: 0.96, y: 8 }}
              transition={{ duration: 0.18, ease: 'easeOut' }}
              className="bg-white rounded-xl shadow-xl w-full max-w-2xl overflow-hidden"
              onClick={e => e.stopPropagation()}
            >
              {/* Header */}
              <div className="flex items-center justify-between px-5 py-3.5 border-b border-surface-200">
                <div className="flex items-center gap-2">
                  <XCircle size={14} className="text-red-500" />
                  <h3 className="text-sm font-semibold text-surface-800">
                    Failed Prompts ({failedCount})
                  </h3>
                </div>
                <button
                  onClick={() => setShowFailedModal(false)}
                  className="w-7 h-7 flex items-center justify-center rounded-md hover:bg-surface-100 text-surface-400 hover:text-surface-600 transition-colors"
                  aria-label="Close"
                >
                  <X size={14} />
                </button>
              </div>

              {/* Table */}
              <div className="overflow-y-auto max-h-[60vh]">
                <table className="w-full text-[12px]">
                  <thead className="sticky top-0 bg-surface-50 border-b border-surface-200">
                    <tr>
                      <th className="text-left px-4 py-2.5 text-[11px] font-medium text-surface-500 uppercase tracking-wide w-10">#</th>
                      <th className="text-left px-4 py-2.5 text-[11px] font-medium text-surface-500 uppercase tracking-wide">Prompt</th>
                      <th className="text-left px-4 py-2.5 text-[11px] font-medium text-surface-500 uppercase tracking-wide w-40">Error</th>
                      <th className="px-4 py-2.5 w-10" />
                    </tr>
                  </thead>
                  <tbody>
                    {failedRows.map((row, idx) => (
                      <tr key={row.id} className="border-b border-surface-100 last:border-0 hover:bg-red-50/40 transition-colors">
                        <td className="px-4 py-2.5 text-surface-400 font-mono">{idx + 1}</td>
                        <td className="px-4 py-2.5 max-w-0">
                          <span
                            className="block truncate text-surface-700"
                            title={row.prompt}
                          >
                            {row.prompt}
                          </span>
                        </td>
                        <td className="px-4 py-2.5">
                          <span
                            className="block truncate text-red-500 text-[11px]"
                            title={row.errorMsg}
                          >
                            {row.errorMsg || 'Unknown error'}
                          </span>
                        </td>
                        <td className="px-4 py-2.5">
                          <button
                            onClick={() => {
                              navigator.clipboard.writeText(row.prompt)
                              setCopiedRowId(row.id)
                              setTimeout(() => setCopiedRowId(null), 1000)
                            }}
                            className="w-6 h-6 flex items-center justify-center rounded hover:bg-surface-100 text-surface-400 hover:text-surface-700 transition-colors"
                            aria-label="Copy prompt"
                          >
                            {copiedRowId === row.id
                              ? <Check size={12} className="text-green-500" />
                              : <Copy size={12} />
                            }
                          </button>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>

              {/* Footer */}
              <div className="flex items-center justify-between px-5 py-3 border-t border-surface-200 bg-surface-50">
                <button
                  onClick={() => {
                    navigator.clipboard.writeText(failedRows.map(r => r.prompt).join('\n'))
                    setCopiedAll(true)
                    setTimeout(() => setCopiedAll(false), 1500)
                  }}
                  className="flex items-center gap-1.5 text-[12px] font-medium text-surface-600 hover:text-surface-800 px-3 py-1.5 rounded-lg hover:bg-surface-200 transition-colors"
                >
                  {copiedAll
                    ? <><Check size={12} className="text-green-500" /><span className="text-green-600">Copied!</span></>
                    : <><Copy size={12} /><span>Copy All Prompts</span></>
                  }
                </button>
                <button
                  onClick={() => setShowFailedModal(false)}
                  className="px-4 py-1.5 text-[12px] font-medium bg-surface-800 text-white rounded-lg hover:bg-surface-700 transition-colors"
                >
                  Close
                </button>
              </div>
            </motion.div>
          </motion.div>
        )}
      </AnimatePresence>

    </div>
  )
}
