import { useState, useEffect, useRef } from 'react'
import { open as tauriOpen } from '@tauri-apps/plugin-dialog'
import { useAppStore } from '@/store/app.store'
import { usePanelContext } from '@/contexts/PanelContext'
import {
  Scissors, Play, Loader, CheckCircle, AlertCircle, X,
  Zap, Film, Music, Image, Layers, Trash2,
  RefreshCcw, Shuffle, FolderOpen,
  FolderCheck, FolderPlus, ChevronDown, ChevronUp,
} from 'lucide-react'
import { cutAutomateApi, type CutAutomateTaskResponse, type CutAutonateScanResult, type CutAutomateInitResult, type CutAutomateJobData, type CutAutomateJobResponse } from '@/lib/api'
import { PathBreadcrumb } from '@/components/ui/PathBreadcrumb'
import { cn } from '@/lib/utils'
import { taskStore } from '@/store/task.store'

// ─── helpers ──────────────────────────────────────────────────────────────────

async function pickDirectory(): Promise<string | null> {
  try {
    const selected = await tauriOpen({ directory: true, multiple: false })
    if (selected && typeof selected === 'string') return selected
  } catch { /* Tauri not available */ }
  return null
}

/** Build absolute subfolder path from project root */
function sub(root: string, folder: string): string {
  return root.replace(/\/+$/, '') + '/' + folder
}

// ─── Types ────────────────────────────────────────────────────────────────────

type StepKey = '01' | '02' | '03' | '04' | '05' | '06' | '07' | '08_le' | '08_chan' | '10' | '11'

interface StepDef {
  key: StepKey
  number: string
  label: string
  sublabel: string
  icon: React.ElementType
  color: string       // bg color class for icon bg
  iconColor: string   // text color for icon
  borderColor: string // border accent
}

interface RunningJobBinding {
  jobId: string
  stepKey: StepKey
  stepLabel: string
  stepSubtitle: string
  taskId: string
  startedAt: number
  progress: number
  message: string
  status: CutAutomateJobData['status']
}

const STEP_PROGRESS_ESTIMATE_MS: Record<StepKey, number> = {
  '01': 60_000,
  '02': 150_000,
  '03': 75_000,
  '04': 45_000,
  '05': 55_000,
  '06': 55_000,
  '07': 20_000,
  '08_le': 12_000,
  '08_chan': 12_000,
  '10': 90_000,
  '11': 100_000,
}

function simulateProgress(stepKey: StepKey, startedAt: number): number {
  const elapsed = Date.now() - startedAt
  const estimate = STEP_PROGRESS_ESTIMATE_MS[stepKey] ?? 60_000
  const progress = 8 + (elapsed / estimate) * 84
  return Math.max(8, Math.min(92, Math.round(progress)))
}

const STEPS: StepDef[] = [
  { key: '01', number: '01', label: 'Cut stock',       sublabel: 'Split the video into segments',       icon: Scissors,  color: 'bg-violet-100', iconColor: 'text-violet-600', borderColor: 'border-violet-200' },
  { key: '02', number: '02', label: 'Edit & Render',   sublabel: 'Overlay, EQ, background track',   icon: Film,      color: 'bg-blue-100',   iconColor: 'text-blue-600',   borderColor: 'border-blue-200'   },
  { key: '03', number: '03', label: 'Extract images',        sublabel: 'Extract frames → JPG',          icon: Image,     color: 'bg-sky-100',    iconColor: 'text-sky-600',    borderColor: 'border-sky-200'    },
  { key: '04', number: '04', label: 'Extract MP3',        sublabel: 'Export the audio track',                icon: Music,     color: 'bg-green-100',  iconColor: 'text-green-600',  borderColor: 'border-green-200'  },
  { key: '05', number: '05', label: 'Merge odd clips',          sublabel: 'Join clips 1, 3, 5, …',             icon: Layers,    color: 'bg-orange-100', iconColor: 'text-orange-600', borderColor: 'border-orange-200' },
  { key: '06', number: '06', label: 'Merge even clips',        sublabel: 'Join clips 2, 4, 6, …',             icon: Layers,    color: 'bg-amber-100',  iconColor: 'text-amber-600',  borderColor: 'border-amber-200'  },
  { key: '07', number: '07', label: 'Reset',            sublabel: 'Delete intermediate files',             icon: RefreshCcw,color: 'bg-red-100',    iconColor: 'text-red-600',    borderColor: 'border-red-200'    },
  { key: '08_le',  number: '08', label: 'Delete odd images',  sublabel: 'Delete frames 1, 3, 5, …',           icon: Trash2,    color: 'bg-rose-100',   iconColor: 'text-rose-600',   borderColor: 'border-rose-200'   },
  { key: '08_chan', number: '09', label: 'Delete even images',sublabel: 'Delete frames 2, 4, 6, …',          icon: Trash2,    color: 'bg-pink-100',   iconColor: 'text-pink-600',   borderColor: 'border-pink-200'   },
  { key: '10', number: '10', label: 'Random images',      sublabel: 'Shuffle JPG → slideshow video',   icon: Shuffle,   color: 'bg-teal-100',   iconColor: 'text-teal-600',   borderColor: 'border-teal-200'   },
  { key: '11', number: '11', label: 'Random videos',    sublabel: 'Shuffle .mp4 → merge H.264',        icon: Film,      color: 'bg-indigo-100', iconColor: 'text-indigo-600', borderColor: 'border-indigo-200' },
]

// ─── Result banner ────────────────────────────────────────────────────────────

function ResultBanner({ result }: { result: CutAutomateTaskResponse }) {
  return (
    <div className={cn(
      'flex items-start gap-2 px-3 py-2.5 rounded-lg border text-xs',
      result.ok
        ? 'bg-green-50 border-green-200 text-green-800'
        : 'bg-red-50 border-red-200 text-red-800',
    )}>
      {result.ok
        ? <CheckCircle size={13} className="mt-0.5 shrink-0 text-green-600" />
        : <AlertCircle size={13} className="mt-0.5 shrink-0 text-red-600" />}
      <span className="leading-relaxed">{result.message}</span>
    </div>
  )
}

// ─── Project Setup Panel ─────────────────────────────────────────────────────

function ProjectSetupPanel({
  projectDir, setProjectDir, onProjectReady,
}: {
  projectDir: string
  setProjectDir: (v: string) => void
  onProjectReady: (root: string) => void
}) {
  const [expanded, setExpanded]           = useState(true)
  const [scanResult, setScanResult]       = useState<CutAutonateScanResult | null>(null)
  const [initResult, setInitResult]       = useState<CutAutomateInitResult | null>(null)
  const [scanning, setScanning]           = useState(false)
  const [initing, setIniting]             = useState(false)
  const [scanMsg, setScanMsg]             = useState<{ ok: boolean; text: string } | null>(null)
  const [initMsg, setInitMsg]             = useState<{ ok: boolean; text: string } | null>(null)

  const pick = async () => {
    const d = await pickDirectory()
    if (d) {
      setProjectDir(d)
      setScanResult(null)
      setInitResult(null)
      setScanMsg(null)
      setInitMsg(null)
    }
  }

  const handleScan = async () => {
    if (!projectDir) return
    setScanning(true)
    setScanResult(null)
    setScanMsg(null)
    try {
      const res = await cutAutomateApi.scanProject(projectDir)
      setScanMsg({ ok: res.ok, text: res.message })
      if (res.ok && res.data) {
        setScanResult(res.data)
        onProjectReady(projectDir)
      }
    } catch (e: any) {
      setScanMsg({ ok: false, text: e?.message ?? 'Unknown error' })
    } finally {
      setScanning(false)
    }
  }

  const handleInit = async () => {
    if (!projectDir) return
    setIniting(true)
    setInitResult(null)
    setInitMsg(null)
    try {
      const res = await cutAutomateApi.initProject(projectDir)
      setInitMsg({ ok: res.ok, text: res.message })
      if (res.ok && res.data) {
        setInitResult(res.data)
        // re-scan after init to refresh status
        const scan = await cutAutomateApi.scanProject(projectDir)
        if (scan.ok && scan.data) setScanResult(scan.data)
        onProjectReady(projectDir)
      }
    } catch (e: any) {
      setInitMsg({ ok: false, text: e?.message ?? 'Unknown error' })
    } finally {
      setIniting(false)
    }
  }

  return (
    <div className="rounded-2xl border border-indigo-200 bg-white mb-5 overflow-hidden">
      {/* Panel header */}
      <button
        className="w-full flex items-center gap-3 px-5 py-3.5 text-left hover:bg-indigo-50/50 transition-colors"
        onClick={() => setExpanded(p => !p)}
      >
        <div className="w-8 h-8 rounded-lg bg-indigo-100 flex items-center justify-center shrink-0">
          <FolderCheck size={15} className="text-indigo-600" />
        </div>
        <div className="flex-1 min-w-0">
          <p className="text-sm font-semibold text-surface-900">Initialize project</p>
          <p className="text-xs text-surface-400">Scan and create the standard folder structure</p>
        </div>
        {scanResult && (
          scanResult.is_complete
            ? <span className="text-xs text-green-600 bg-green-50 border border-green-200 px-2 py-0.5 rounded-full mr-1">Complete</span>
            : <span className="text-xs text-amber-600 bg-amber-50 border border-amber-200 px-2 py-0.5 rounded-full mr-1">Missing {scanResult.missing.length}</span>
        )}
        {expanded ? <ChevronUp size={14} className="text-surface-400 shrink-0" /> : <ChevronDown size={14} className="text-surface-400 shrink-0" />}
      </button>

      {/* Panel body */}
      {expanded && (
        <div className="px-5 pb-5 space-y-4 border-t border-indigo-100 pt-4">
          {/* Path picker */}
          <PathBreadcrumb
            label="Project folder"
            value={projectDir}
            onChange={setProjectDir}
            onPickDirectory={pick}
            placeholder="/path/to/my-project/"
          />

          {/* Action buttons */}
          <div className="flex gap-2">
            <button
              className={cn(
                'flex items-center gap-1.5 px-4 py-2 rounded-xl text-sm font-medium transition-all',
                projectDir && !scanning
                  ? 'bg-surface-100 hover:bg-surface-200 text-surface-700 cursor-pointer'
                  : 'bg-surface-50 text-surface-300 cursor-not-allowed',
              )}
              onClick={handleScan}
              disabled={!projectDir || scanning}
            >
              {scanning ? <Loader size={13} className="animate-spin" /> : <FolderCheck size={13} />}
              Scan
            </button>
            <button
              className={cn(
                'flex items-center gap-1.5 px-4 py-2 rounded-xl text-sm font-medium transition-all',
                projectDir && !initing
                  ? 'bg-indigo-500 hover:bg-indigo-600 text-white cursor-pointer shadow-sm'
                  : 'bg-surface-100 text-surface-300 cursor-not-allowed',
              )}
              onClick={handleInit}
              disabled={!projectDir || initing}
            >
              {initing ? <Loader size={13} className="animate-spin" /> : <FolderPlus size={13} />}
              Initialize / repair structure
            </button>
          </div>

          {/* Scan result — compact folder chips */}
          {scanResult && (
            <div className="rounded-xl border border-surface-200 bg-surface-50 p-3">
              <div className="flex items-center justify-between mb-2">
                <span className="text-xs font-medium text-surface-500">Folder structure</span>
                {scanResult.is_complete
                  ? <span className="text-[10px] text-green-600 flex items-center gap-1 font-medium"><CheckCircle size={10} /> Complete</span>
                  : <span className="text-[10px] text-amber-600 flex items-center gap-1 font-medium"><AlertCircle size={10} /> Missing {scanResult.missing.length}</span>}
              </div>
              <div className="grid grid-cols-2 gap-1">
                {scanResult.required.map(dir => {
                  const present = scanResult.present.includes(dir)
                  return (
                    <div key={dir} className={cn(
                      'flex items-center gap-1.5 px-2 py-1 rounded-lg text-[11px] font-mono',
                      present ? 'bg-green-50 text-green-700' : 'bg-amber-50 text-amber-700',
                    )}>
                      {present
                        ? <CheckCircle size={9} className="text-green-500 shrink-0" />
                        : <AlertCircle size={9} className="text-amber-500 shrink-0" />}
                      <span className="truncate">{dir}</span>
                    </div>
                  )
                })}
              </div>
            </div>
          )}

          {/* Init result summary — compact */}
          {initResult && initResult.created.length > 0 && (
            <div className="rounded-xl border border-green-200 bg-green-50 px-3 py-2">
              <p className="text-[11px] font-medium text-green-700 mb-1.5 flex items-center gap-1">
                <FolderPlus size={10} /> Created {initResult.created.length} folders:
              </p>
              <div className="flex flex-wrap gap-1">
                {initResult.created.map(dir => (
                  <span key={dir} className="text-[10px] font-mono text-green-700 bg-green-100 px-1.5 py-0.5 rounded">{dir}</span>
                ))}
              </div>
            </div>
          )}

          {/* Scan/Init messages */}
          {scanMsg && (
            <div className={cn(
              'flex items-start gap-2 px-3 py-2 rounded-lg border text-xs',
              scanMsg.ok ? 'bg-green-50 border-green-200 text-green-800' : 'bg-red-50 border-red-200 text-red-800',
            )}>
              {scanMsg.ok ? <CheckCircle size={12} className="mt-0.5 shrink-0 text-green-600" /> : <AlertCircle size={12} className="mt-0.5 shrink-0 text-red-600" />}
              {scanMsg.text}
            </div>
          )}
          {initMsg && (
            <div className={cn(
              'flex items-start gap-2 px-3 py-2 rounded-lg border text-xs',
              initMsg.ok ? 'bg-green-50 border-green-200 text-green-800' : 'bg-red-50 border-red-200 text-red-800',
            )}>
              {initMsg.ok ? <CheckCircle size={12} className="mt-0.5 shrink-0 text-green-600" /> : <AlertCircle size={12} className="mt-0.5 shrink-0 text-red-600" />}
              {initMsg.text}
            </div>
          )}
        </div>
      )}
    </div>
  )
}

// ─── Modal ────────────────────────────────────────────────────────────────────

interface ModalProps {
  step: StepDef
  onClose: () => void
  // shared dirs
  videoDir: string; setVideoDir: (v: string) => void
  catStockDir: string; setCatStockDir: (v: string) => void
  editDir: string; setEditDir: (v: string) => void
  taChangDir: string; setTaChangDir: (v: string) => void
  tachMp3Dir: string; setTachMp3Dir: (v: string) => void
  gopLeDir: string; setGopLeDir: (v: string) => void
  gopChanDir: string; setGopChanDir: (v: string) => void
  photoRandomDir: string; setPhotoRandomDir: (v: string) => void
  stockRandomDir: string; setStockRandomDir: (v: string) => void
  projectDir: string; setProjectDir: (v: string) => void
  segmentTime: number; setSegmentTime: (v: number) => void
  fpsFraction: string; setFpsFraction: (v: string) => void
  photoFps: number; setPhotoFps: (v: number) => void
  running: Partial<Record<StepKey, boolean>>
  results: Partial<Record<StepKey, CutAutomateTaskResponse>>
  ffmpegOk: boolean | null
  onRun: (key: StepKey, fn: () => Promise<{ ok: boolean; message: string; data: CutAutomateJobData | null }>) => void
}

function StepModal(props: ModalProps) {
  const { step, onClose, ffmpegOk, running, results, onRun } = props
  const overlayRef = useRef<HTMLDivElement>(null)

  // close on backdrop click only if not running
  const handleOverlayClick = (e: React.MouseEvent) => {
    if (e.target === overlayRef.current && !running[step.key]) onClose()
  }

  const Icon = step.icon
  const isRunning = !!running[step.key]
  const result = results[step.key] ?? null

  const pick = async (setter: (v: string) => void) => {
    const d = await pickDirectory()
    if (d) setter(d)
  }

  // ── per-step content ──────────────────────────────────────────────────────
  let body: React.ReactNode = null
  let canRun = false
  let runFn: (() => Promise<CutAutomateJobResponse>) | null = null

  if (step.key === '01') {
    canRun = !!props.videoDir && !!props.catStockDir && !!ffmpegOk
    runFn = () => cutAutomateApi.startJob('01', {
      input_dir: props.videoDir,
      output_dir: props.catStockDir,
      segment_time: props.segmentTime,
    })
    body = (
      <>
        <PathBreadcrumb label="Source video (input)" value={props.videoDir} onChange={props.setVideoDir}
          onPickDirectory={() => pick(props.setVideoDir)} placeholder="/path/to/00.videogoc/" />
        <PathBreadcrumb label="Output (01.catstock)" value={props.catStockDir} onChange={props.setCatStockDir}
          onPickDirectory={() => pick(props.setCatStockDir)} placeholder="/path/to/01.catstock/" />
        <div>
          <label className="label">Segment time (seconds)</label>
          <input className="input" type="number" min={1} max={120} value={props.segmentTime}
            onChange={e => props.setSegmentTime(+e.target.value)} />
        </div>
        <p className="text-xs text-surface-400">Split the source video into {props.segmentTime}s segments with stream copy only, so it stays fast.</p>
      </>
    )
  } else if (step.key === '02') {
    canRun = !!props.videoDir && !!props.editDir && !!ffmpegOk
    runFn = () => cutAutomateApi.startJob('02', {
      input_dir: props.videoDir,
      output_dir: props.editDir,
      bg_wav: null,
    })
    body = (
      <>
        <PathBreadcrumb label="Source video (input)" value={props.videoDir} onChange={props.setVideoDir}
          onPickDirectory={() => pick(props.setVideoDir)} placeholder="/path/to/00.videogoc/" />
        <PathBreadcrumb label="Output (02.Edit)" value={props.editDir} onChange={props.setEditDir}
          onPickDirectory={() => pick(props.setEditDir)} placeholder="/path/to/02.Edit/" />
        <p className="text-xs text-surface-400">Render with a crop overlay, brightness +8%, saturation 1.1, audio EQ, and background track <code className="font-mono">bg/ct.wav</code>.</p>
      </>
    )
  } else if (step.key === '03') {
    canRun = !!props.videoDir && !!props.taChangDir && !!ffmpegOk
    runFn = () => cutAutomateApi.startJob('03', {
      input_dir: props.videoDir,
      output_dir: props.taChangDir,
      fps_fraction: props.fpsFraction,
    })
    body = (
      <>
        <PathBreadcrumb label="Source video (input)" value={props.videoDir} onChange={props.setVideoDir}
          onPickDirectory={() => pick(props.setVideoDir)} placeholder="/path/to/00.videogoc/" />
        <PathBreadcrumb label="Output (03.tachanh)" value={props.taChangDir} onChange={props.setTaChangDir}
          onPickDirectory={() => pick(props.setTaChangDir)} placeholder="/path/to/03.tachanh/" />
        <div>
          <label className="label">FPS (e.g. 1/6 = 1 frame every 6 seconds)</label>
          <input className="input font-mono text-sm" value={props.fpsFraction}
            onChange={e => props.setFpsFraction(e.target.value)} placeholder="1/6" />
        </div>
        <p className="text-xs text-surface-400">Extract frames, apply NL-Means noise reduction and unsharp mask, then export JPG.</p>
      </>
    )
  } else if (step.key === '04') {
    canRun = !!props.videoDir && !!props.tachMp3Dir && !!ffmpegOk
    runFn = () => cutAutomateApi.startJob('04', {
      input_dir: props.videoDir,
      output_dir: props.tachMp3Dir,
    })
    body = (
      <>
        <PathBreadcrumb label="Source video (input)" value={props.videoDir} onChange={props.setVideoDir}
          onPickDirectory={() => pick(props.setVideoDir)} placeholder="/path/to/00.videogoc/" />
        <PathBreadcrumb label="Output (04.tachmp3)" value={props.tachMp3Dir} onChange={props.setTachMp3Dir}
          onPickDirectory={() => pick(props.setTachMp3Dir)} placeholder="/path/to/04.tachmp3/" />
        <p className="text-xs text-surface-400">Extract the audio track and export a high-quality MP3 (libmp3lame -q:a 0).</p>
      </>
    )
  } else if (step.key === '05') {
    canRun = !!props.catStockDir && !!props.gopLeDir && !!ffmpegOk
    runFn = () => cutAutomateApi.startJob('05', {
      input_dir: props.catStockDir,
      output_dir: props.gopLeDir,
      output_name: 'video_gop_le.mp4',
    })
    body = (
      <>
        <PathBreadcrumb label="Input (01.catstock)" value={props.catStockDir} onChange={props.setCatStockDir}
          onPickDirectory={() => pick(props.setCatStockDir)} placeholder="/path/to/01.catstock/" />
        <PathBreadcrumb label="Output (05.gop_le)" value={props.gopLeDir} onChange={props.setGopLeDir}
          onPickDirectory={() => pick(props.setGopLeDir)} placeholder="/path/to/05.gop_le/" />
        <p className="text-xs text-surface-400">Join odd-numbered clips (0001, 0003, 0005, …) into one video.</p>
      </>
    )
  } else if (step.key === '06') {
    canRun = !!props.catStockDir && !!props.gopChanDir && !!ffmpegOk
    runFn = () => cutAutomateApi.startJob('06', {
      input_dir: props.catStockDir,
      output_dir: props.gopChanDir,
      output_name: 'video_gop_chan.mp4',
    })
    body = (
      <>
        <PathBreadcrumb label="Input (01.catstock)" value={props.catStockDir} onChange={props.setCatStockDir}
          onPickDirectory={() => pick(props.setCatStockDir)} placeholder="/path/to/01.catstock/" />
        <PathBreadcrumb label="Output (06.gop_chan)" value={props.gopChanDir} onChange={props.setGopChanDir}
          onPickDirectory={() => pick(props.setGopChanDir)} placeholder="/path/to/06.gop_chan/" />
        <p className="text-xs text-surface-400">Join even-numbered clips (0002, 0004, 0006, …) into one video.</p>
      </>
    )
  } else if (step.key === '07') {
    canRun = !!props.projectDir
    runFn = () => cutAutomateApi.startJob('07', {
      project_dir: props.projectDir,
      dirs: null,
    })
    body = (
      <>
        <PathBreadcrumb label="Project folder" value={props.projectDir} onChange={props.setProjectDir}
          onPickDirectory={() => pick(props.setProjectDir)} placeholder="/path/to/project/" />
        <div className="flex items-center gap-2 px-3 py-2 rounded-lg bg-red-50 border border-red-200 text-red-700 text-xs">
          <AlertCircle size={13} />
          This will delete all files in: 01.catstock, 02.Edit, 03.tachanh, 04.tachmp3, 05.gop_le, 06.gop_chan
        </div>
      </>
    )
  } else if (step.key === '08_le') {
    canRun = !!props.taChangDir
    runFn = () => cutAutomateApi.startJob('08_le', {
      image_dir: props.taChangDir,
      dry_run: false,
    })
    body = (
      <>
        <PathBreadcrumb label="Image folder (03.tachanh)" value={props.taChangDir} onChange={props.setTaChangDir}
          onPickDirectory={() => pick(props.setTaChangDir)} placeholder="/path/to/03.tachanh/" />
        <p className="text-xs text-surface-400">Delete 0001.jpg, 0003.jpg, 0005.jpg, … in the folder.</p>
      </>
    )
  } else if (step.key === '08_chan') {
    canRun = !!props.taChangDir
    runFn = () => cutAutomateApi.startJob('08_chan', {
      image_dir: props.taChangDir,
      dry_run: false,
    })
    body = (
      <>
        <PathBreadcrumb label="Image folder (03.tachanh)" value={props.taChangDir} onChange={props.setTaChangDir}
          onPickDirectory={() => pick(props.setTaChangDir)} placeholder="/path/to/03.tachanh/" />
        <p className="text-xs text-surface-400">Delete 0002.jpg, 0004.jpg, 0006.jpg, … in the folder.</p>
      </>
    )
  } else if (step.key === '10') {
    canRun = !!props.taChangDir && !!props.photoRandomDir && !!ffmpegOk
    runFn = () => cutAutomateApi.startJob('10', {
      image_dir: props.taChangDir,
      output_dir: props.photoRandomDir,
      fps: props.photoFps,
      output_name: 'video_random.mp4',
    })
    body = (
      <>
        <PathBreadcrumb label="Image input (03.tachanh)" value={props.taChangDir} onChange={props.setTaChangDir}
          onPickDirectory={() => pick(props.setTaChangDir)} placeholder="/path/to/03.tachanh/" />
        <PathBreadcrumb label="Output (10.gop_photo_random)" value={props.photoRandomDir} onChange={props.setPhotoRandomDir}
          onPickDirectory={() => pick(props.setPhotoRandomDir)} placeholder="/path/to/10.gop_photo_random/" />
        <div>
          <label className="label">FPS slideshow</label>
          <input className="input" type="number" min={1} max={30} value={props.photoFps}
            onChange={e => props.setPhotoFps(+e.target.value)} />
        </div>
        <p className="text-xs text-surface-400">Shuffle all .jpg files, build a {props.photoFps} fps slideshow, and scale to 1920×1080.</p>
      </>
    )
  } else if (step.key === '11') {
    canRun = !!props.catStockDir && !!props.stockRandomDir && !!ffmpegOk
    runFn = () => cutAutomateApi.startJob('11', {
      input_dir: props.catStockDir,
      output_dir: props.stockRandomDir,
      output_name: 'gop_stock_random.mp4',
    })
    body = (
      <>
        <PathBreadcrumb label="Input (01.catstock)" value={props.catStockDir} onChange={props.setCatStockDir}
          onPickDirectory={() => pick(props.setCatStockDir)} placeholder="/path/to/01.catstock/" />
        <PathBreadcrumb label="Output (11.gop_video_stock_random)" value={props.stockRandomDir} onChange={props.setStockRandomDir}
          onPickDirectory={() => pick(props.setStockRandomDir)} placeholder="/path/to/11.gop_video_stock_random/" />
        <p className="text-xs text-surface-400">Shuffle the .mp4 files and re-encode with H.264 CRF=20 + AAC 192k.</p>
      </>
    )
  }

  return (
    <div
      ref={overlayRef}
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 backdrop-blur-sm"
      onClick={handleOverlayClick}
    >
      <div
        className={cn(
          'w-full max-w-lg mx-4 bg-white rounded-2xl shadow-2xl border',
          step.borderColor,
          'animate-in fade-in zoom-in-95 duration-150',
        )}
        style={{ boxShadow: '0 25px 60px rgba(0,0,0,0.18)' }}
      >
        {/* Modal header */}
        <div className={cn('flex items-center gap-3 px-5 py-4 border-b', step.borderColor)}>
          <div className={cn('w-9 h-9 rounded-xl flex items-center justify-center shrink-0', step.color)}>
            <Icon size={17} className={step.iconColor} />
          </div>
          <div className="flex-1 min-w-0">
            <h2 className="text-sm font-semibold text-surface-900">Step {step.number} - {step.label}</h2>
            <p className="text-xs text-surface-400">{step.sublabel}</p>
          </div>
          {result?.ok && <CheckCircle size={16} className="text-green-500 shrink-0" />}
          <button
            className="btn-icon text-surface-400 hover:text-surface-700 hover:bg-surface-100 ml-1"
            onClick={onClose}
          >
            <X size={15} />
          </button>
        </div>

        {/* Modal body */}
        <div className="px-5 py-4 space-y-3">
          {body}
          {result && <ResultBanner result={result} />}
        </div>

        {/* Modal footer */}
        <div className="space-y-3 px-5 pb-5">
          <div className="rounded-xl border border-surface-200 bg-surface-50 px-3.5 py-3 text-xs leading-relaxed text-surface-500">
            Khi chạy, tiến trình sẽ nổi ở góc phải dưới. Bạn vẫn có thể tiếp tục dùng các step khác trong Cut Image song song.
          </div>

          <button
            className={cn(
              'w-full flex items-center justify-center gap-2 px-4 py-2.5 rounded-xl text-sm font-semibold transition-all duration-150',
              'shadow-sm',
              canRun && !isRunning
                ? 'bg-primary-500 text-white hover:bg-primary-600 cursor-pointer'
                : 'bg-surface-200 text-surface-400 cursor-not-allowed',
            )}
            onClick={() => {
              if (canRun && runFn && !isRunning) {
                onRun(step.key, runFn)
                onClose()
              }
            }}
            disabled={!canRun || isRunning}
          >
            {isRunning
              ? <><Loader size={14} className="animate-spin" /> Already running…</>
              : <><Play size={14} /> Run step {step.number} in background</>
            }
          </button>
        </div>
      </div>
    </div>
  )
}

// ─── Step card ────────────────────────────────────────────────────────────────

function StepCard({ step, running, result, onClick }: {
  step: StepDef
  running: boolean
  result: CutAutomateTaskResponse | null
  onClick: () => void
}) {
  const Icon = step.icon
  const status: 'idle' | 'running' | 'ok' | 'error' =
    running ? 'running'
    : result?.ok === true  ? 'ok'
    : result?.ok === false ? 'error'
    : 'idle'

  return (
    <button
      onClick={onClick}
      className={cn(
        'group relative flex flex-col gap-3 p-4 rounded-2xl border bg-white text-left cursor-pointer',
        'transition-all duration-200 hover:shadow-md hover:-translate-y-0.5',
        status === 'ok'    ? 'border-green-200 bg-green-50/40'
        : status === 'error' ? 'border-red-200 bg-red-50/30'
        : step.borderColor,
      )}
    >
      {/* Step number badge */}
      <div className="absolute top-3 right-3 text-[10px] font-bold text-surface-300 tabular-nums">
        {step.number}
      </div>

      {/* Icon */}
      <div className={cn('w-10 h-10 rounded-xl flex items-center justify-center', step.color)}>
        {status === 'running'
          ? <Loader size={18} className={cn(step.iconColor, 'animate-spin')} />
          : status === 'ok'
            ? <CheckCircle size={18} className="text-green-600" />
            : status === 'error'
              ? <AlertCircle size={18} className="text-red-500" />
              : <Icon size={18} className={step.iconColor} />
        }
      </div>

      {/* Text */}
      <div className="min-w-0">
        <p className="text-sm font-semibold text-surface-900 leading-tight">{step.label}</p>
        <p className="text-xs text-surface-400 mt-0.5 leading-relaxed">{step.sublabel}</p>
      </div>

      {/* Status dot */}
      <div className={cn(
        'absolute bottom-3 right-3 w-1.5 h-1.5 rounded-full',
        status === 'ok'      ? 'bg-green-500'
        : status === 'error' ? 'bg-red-500'
        : status === 'running' ? 'bg-amber-400 animate-pulse'
        : 'bg-surface-200',
      )} />
    </button>
  )
}

// ═════════════════════════════════════════════════════════════════════════════
// ─── MAIN COMPONENT ──────────────────────────────────────────────────────────
// ═════════════════════════════════════════════════════════════════════════════

export default function CutAutomate() {
  const { selectedChildId } = usePanelContext()
  const { childProjects } = useAppStore()
  const child = childProjects.find(c => c.id === selectedChildId)

  // ── FFmpeg status ─────────────────────────────────────────────────────────
  const [ffmpegOk, setFfmpegOk] = useState<boolean | null>(null)
  useEffect(() => {
    cutAutomateApi.status()
      .then(s => setFfmpegOk(s.ffmpeg_available))
      .catch(() => setFfmpegOk(false))
  }, [])

  // ── Shared directory fields ───────────────────────────────────────────────
  const [videoDir, setVideoDir]             = useState('')
  const [catStockDir, setCatStockDir]       = useState('')
  const [editDir, setEditDir]               = useState('')
  const [taChangDir, setTaChangDir]         = useState('')
  const [tachMp3Dir, setTachMp3Dir]         = useState('')
  const [gopLeDir, setGopLeDir]             = useState('')
  const [gopChanDir, setGopChanDir]         = useState('')
  const [photoRandomDir, setPhotoRandomDir] = useState('')
  const [stockRandomDir, setStockRandomDir] = useState('')
  const [projectDir, setProjectDir]         = useState('')

  /** Called after scan or init — auto-fill all step dirs from project root */
  const fillDirsFromRoot = (root: string) => {
    setVideoDir(sub(root, '00.videogoc'))
    setCatStockDir(sub(root, '01.catstock'))
    setEditDir(sub(root, '02.Edit'))
    setTaChangDir(sub(root, '03.tachanh'))
    setTachMp3Dir(sub(root, '04.tachmp3'))
    setGopLeDir(sub(root, '05.gop_le'))
    setGopChanDir(sub(root, '06.gop_chan'))
    setPhotoRandomDir(sub(root, '10.gop_photo_random'))
    setStockRandomDir(sub(root, '11.gop_video_stock_random'))
  }

  // ── Advanced options ──────────────────────────────────────────────────────
  const [segmentTime, setSegmentTime] = useState(10)
  const [fpsFraction, setFpsFraction] = useState('1/6')
  const [photoFps, setPhotoFps]       = useState(1)

  // ── Per-step running/result state ─────────────────────────────────────────
  const [running, setRunning] = useState<Partial<Record<StepKey, boolean>>>({})
  const [results, setResults] = useState<Partial<Record<StepKey, CutAutomateTaskResponse>>>({})
  const [runningJobs, setRunningJobs] = useState<Record<string, RunningJobBinding>>({})
  const runningJobsRef = useRef<Record<string, RunningJobBinding>>({})

  useEffect(() => {
    runningJobsRef.current = runningJobs
  }, [runningJobs])

  const run = async (
    key: StepKey,
    fn: () => Promise<{ ok: boolean; message: string; data: CutAutomateJobData | null }>,
  ) => {
    const stepDef = STEPS.find(s => s.key === key)
    if (!stepDef) return

    setRunning(prev => ({ ...prev, [key]: true }))
    try {
      const started = await fn()
      if (!started.ok || !started.data) {
        const errRes = { ok: false, message: started.message || 'Unable to start task', data: null }
        setResults(prev => ({ ...prev, [key]: errRes }))
        setRunning(prev => ({ ...prev, [key]: false }))
        return
      }

      const taskId = taskStore.add({
        toolId: 'cut-automate',
        toolLabel: 'Cut Image',
        label: `Step ${stepDef.number} - ${stepDef.label}`,
        message: stepDef.sublabel,
        progress: 8,
        canCancel: true,
        jobId: started.data.job_id,
      })

      const binding: RunningJobBinding = {
        jobId: started.data.job_id,
        stepKey: key,
        stepLabel: stepDef.label,
        stepSubtitle: stepDef.sublabel,
        taskId,
        startedAt: Date.now(),
        progress: 8,
        message: stepDef.sublabel,
        status: 'running',
      }

      setRunningJobs(prev => ({ ...prev, [started.data!.job_id]: binding }))
    } catch (e: any) {
      const errRes = { ok: false, message: e?.message ?? 'Unknown error', data: null }
      setResults(prev => ({ ...prev, [key]: errRes }))
      setRunning(prev => ({ ...prev, [key]: false }))
    }
  }

  useEffect(() => {
    if (Object.keys(runningJobs).length === 0) return

    const tick = window.setInterval(() => {
      const entries = Object.entries(runningJobsRef.current)
      if (entries.length === 0) return

      void Promise.all(entries.map(async ([jobId, binding]) => {
        try {
          const snapshot = await cutAutomateApi.getJob(jobId)
          if (!snapshot.ok || !snapshot.data) return

          if (snapshot.data.status === 'running') {
            taskStore.update(binding.taskId, {
              progress: simulateProgress(binding.stepKey, binding.startedAt),
              message: snapshot.data.stop_requested ? 'Stopping…' : binding.stepSubtitle,
            })
            return
          }

          const finalResult = snapshot.data.result ?? {
            ok: snapshot.data.status === 'done',
            message: snapshot.data.message,
            data: null,
          }

          setResults(prev => ({ ...prev, [binding.stepKey]: finalResult }))
          setRunning(prev => ({ ...prev, [binding.stepKey]: false }))

          if (snapshot.data.status === 'cancelled') {
            taskStore.complete(binding.taskId, 'cancelled', finalResult.message || 'Stopped by user.')
          } else {
            taskStore.complete(binding.taskId, finalResult.ok ? 'done' : 'error', finalResult.message)
          }

          setRunningJobs(prev => {
            const next = { ...prev }
            delete next[jobId]
            return next
          })
        } catch (error: any) {
          setResults(prev => ({
            ...prev,
            [binding.stepKey]: { ok: false, message: error?.message ?? 'Failed to fetch job status', data: null },
          }))
          setRunning(prev => ({ ...prev, [binding.stepKey]: false }))
          taskStore.complete(binding.taskId, 'error', error?.message ?? 'Failed to fetch job status')
          setRunningJobs(prev => {
            const next = { ...prev }
            delete next[jobId]
            return next
          })
        }
      }))
    }, 800)

    return () => window.clearInterval(tick)
  }, [runningJobs])

  // ── Active modal ──────────────────────────────────────────────────────────
  const [activeStep, setActiveStep] = useState<StepKey | null>(null)
  const activeStepDef = activeStep ? STEPS.find(s => s.key === activeStep) ?? null : null

  const sharedProps = {
    videoDir, setVideoDir,
    catStockDir, setCatStockDir,
    editDir, setEditDir,
    taChangDir, setTaChangDir,
    tachMp3Dir, setTachMp3Dir,
    gopLeDir, setGopLeDir,
    gopChanDir, setGopChanDir,
    photoRandomDir, setPhotoRandomDir,
    stockRandomDir, setStockRandomDir,
    projectDir, setProjectDir,
    segmentTime, setSegmentTime,
    fpsFraction, setFpsFraction,
    photoFps, setPhotoFps,
    running, results, ffmpegOk,
    onRun: run,
  }

  const doneCount = Object.values(results).filter(r => r?.ok).length

  return (
    <div className="flex flex-col h-full bg-surface-50">

      {/* ── Header ─────────────────────────────────────────────────────────── */}
      <div className="flex items-center gap-3 px-6 py-4 border-b border-surface-200 bg-white shrink-0">
        <div className="w-9 h-9 rounded-xl bg-indigo-100 flex items-center justify-center">
          <Scissors size={17} className="text-indigo-600" />
        </div>
        <div className="flex-1 min-w-0">
          <h1 className="page-title">Cut Automate</h1>
          <p className="page-sub">{child?.name ?? 'No child selected'} · Automated video processing pipeline</p>
        </div>

        {/* Progress */}
        {doneCount > 0 && (
          <span className="text-xs text-surface-500 tabular-nums">{doneCount}/{STEPS.length} xong</span>
        )}

        {/* FFmpeg badge */}
        {ffmpegOk !== null && (
          ffmpegOk
            ? <span className="flex items-center gap-1.5 text-xs text-green-600 bg-green-50 border border-green-200 px-2.5 py-1 rounded-full">
                <Zap size={11} /> FFmpeg ready
              </span>
            : <span className="flex items-center gap-1.5 text-xs text-amber-600 bg-amber-50 border border-amber-200 px-2.5 py-1 rounded-full">
                <AlertCircle size={11} /> FFmpeg unavailable
              </span>
        )}
      </div>

      {/* ── Main content ───────────────────────────────────────────────────── */}
      <div className="flex-1 overflow-y-auto p-6">

        {/* FFmpeg warning */}
        {ffmpegOk === false && (
          <div className="flex items-center gap-2 px-4 py-3 mb-5 rounded-xl bg-amber-50 border border-amber-200 text-amber-700 text-sm">
            <AlertCircle size={14} />
            FFmpeg is not installed - most steps require it to work.
          </div>
        )}

        {/* Project setup */}
        <ProjectSetupPanel
          projectDir={projectDir}
          setProjectDir={setProjectDir}
          onProjectReady={fillDirsFromRoot}
        />

        {/* Quick setup hint */}
        {Object.values(results).length === 0 && (
          <div className="flex items-start gap-3 px-4 py-3 mb-5 rounded-xl bg-blue-50 border border-blue-100 text-blue-700 text-sm">
            <FolderOpen size={15} className="mt-0.5 shrink-0" />
            <div>
              <p className="font-medium">Where do I start?</p>
              <p className="text-xs mt-0.5 text-blue-600">Click any card to configure and run that step. Paths are preserved between steps.</p>
            </div>
          </div>
        )}

        {/* Card grid */}
        <div className="grid grid-cols-2 gap-3 sm:grid-cols-3 lg:grid-cols-4">
          {STEPS.map(step => (
            <StepCard
              key={step.key}
              step={step}
              running={!!running[step.key]}
              result={results[step.key] ?? null}
              onClick={() => setActiveStep(step.key)}
            />
          ))}
        </div>

        {/* Legend */}
        <div className="flex items-center gap-4 mt-6 text-xs text-surface-400">
          <span className="flex items-center gap-1.5"><span className="w-1.5 h-1.5 rounded-full bg-surface-200 inline-block" /> Not run</span>
          <span className="flex items-center gap-1.5"><span className="w-1.5 h-1.5 rounded-full bg-amber-400 inline-block" /> Running</span>
          <span className="flex items-center gap-1.5"><span className="w-1.5 h-1.5 rounded-full bg-green-500 inline-block" /> Success</span>
          <span className="flex items-center gap-1.5"><span className="w-1.5 h-1.5 rounded-full bg-red-500 inline-block" /> Error</span>
        </div>
      </div>

      {/* ── Step modal ─────────────────────────────────────────────────────── */}
      {activeStepDef && (
        <StepModal
          step={activeStepDef}
          onClose={() => setActiveStep(null)}
          {...sharedProps}
        />
      )}
    </div>
  )
}
