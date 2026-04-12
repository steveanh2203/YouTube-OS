import { useState, useEffect, useCallback } from 'react'
import { open as tauriOpen } from '@tauri-apps/plugin-dialog'
import { useAppStore } from '@/store/app.store'
import { usePanelContext } from '@/contexts/PanelContext'
import {
  Film, FolderOpen, Play, Loader, CheckCircle, AlertCircle,
  Settings2, Image, ArrowDownUp,
  Volume2, Zap, ChevronDown, ChevronUp, Eye,
  Clapperboard,
} from 'lucide-react'
import { fastEditApi, type FastEditRenderRequest, type FastEditRenderResponse,
  type FastEditBatchRenameResponse,
  type FastEditSystemStatus,
} from '@/lib/api'
import { cn } from '@/lib/utils'
import { PathBreadcrumb } from '@/components/ui/PathBreadcrumb'
import { taskStore } from '@/store/task.store'

// ─── Tab type ─────────────────────────────────────────────────────────────────

type Tab = 'compose' | 'rename'

const TABS: { id: Tab; label: string; icon: React.ElementType }[] = [
  { id: 'compose', label: 'Compose & Render', icon: Clapperboard },
  { id: 'rename',  label: 'Batch Rename',     icon: ArrowDownUp },
]

// ─── Directory picker helpers ────────────────────────────────────────────────

async function pickDirectory(): Promise<string | null> {
  try {
    const selected = await tauriOpen({ directory: true, multiple: false })
    if (selected && typeof selected === 'string') return selected
  } catch { /* Tauri not available */ }
  return null
}

// ─── Section wrapper ──────────────────────────────────────────────────────────

function Section({
  title, icon: Icon, children, collapsible = false, defaultOpen = true,
}: {
  title: string
  icon: React.ElementType
  children: React.ReactNode
  collapsible?: boolean
  defaultOpen?: boolean
}) {
  const [open, setOpen] = useState(defaultOpen)
  return (
    <div className="rounded-xl border border-surface-200 bg-white overflow-hidden shadow-sm">
      <button
        className={cn(
          'flex items-center gap-2.5 w-full px-5 py-3 text-left',
          'bg-white',
          collapsible && 'cursor-pointer hover:bg-surface-50 transition-colors',
          open && collapsible && 'border-b border-surface-100',
        )}
        onClick={() => collapsible && setOpen(p => !p)}
        disabled={!collapsible}
      >
        <div className="w-6 h-6 rounded-md bg-primary-50 flex items-center justify-center shrink-0">
          <Icon size={13} className="text-primary-500" />
        </div>
        <span className="text-sm font-semibold text-surface-800 flex-1">{title}</span>
        {collapsible && (
          open
            ? <ChevronUp size={13} className="text-surface-300" />
            : <ChevronDown size={13} className="text-surface-300" />
        )}
      </button>
      {open && <div className="px-5 py-4 space-y-4">{children}</div>}
    </div>
  )
}

// ─── Inline stat badge ────────────────────────────────────────────────────────

function FileBadge({ count, label }: { count: number; label: string }) {
  return (
    <span className="inline-flex items-center gap-1 px-2 py-0.5 rounded-full bg-primary-50 text-primary-700 text-xs font-medium">
      <span className="font-bold">{count}</span> {label}
    </span>
  )
}

// ═════════════════════════════════════════════════════════════════════════════
// ─── COMPOSE TAB ─────────────────────────────────────────────────────────────
// ═════════════════════════════════════════════════════════════════════════════

function ComposeTab() {

  // ── Status ───────────────────────────────────────────────────────────────
  const [status, setStatus] = useState<FastEditSystemStatus | null>(null)

  useEffect(() => {
    fastEditApi.status().then(setStatus).catch(() => {})
  }, [])

  // ── Directories ──────────────────────────────────────────────────────────
  const [audioDir, setAudioDir] = useState('')
  const [imageDir, setImageDir] = useState('')
  const [outputDir, setOutputDir] = useState('')
  const [subtitleDir, setSubtitleDir] = useState('')

  // ── File counts ──────────────────────────────────────────────────────────
  const [audioCount, setAudioCount] = useState<number | null>(null)
  const [imageCount, setImageCount] = useState<number | null>(null)

  const refreshCounts = useCallback(async () => {
    if (audioDir.trim()) {
      fastEditApi.fileCount(audioDir, 'audio').then(r => setAudioCount(r.total)).catch(() => setAudioCount(null))
    } else {
      setAudioCount(null)
    }
    if (imageDir.trim()) {
      fastEditApi.fileCount(imageDir, 'image').then(r => setImageCount(r.total)).catch(() => setImageCount(null))
    } else {
      setImageCount(null)
    }
  }, [audioDir, imageDir])

  useEffect(() => { refreshCounts() }, [refreshCounts])

  // ── Render settings ──────────────────────────────────────────────────────
  const [createIndividual, setCreateIndividual] = useState(true)
  const [createCombined, setCreateCombined] = useState(true)
  const [frameRate, setFrameRate] = useState(30)
  const [resW, setResW] = useState(1920)
  const [resH, setResH] = useState(1080)
  const [videoCodec, setVideoCodec] = useState('libx264')
  const [videoBitrate, setVideoBitrate] = useState('5M')
  const [audioBitrate, setAudioBitrate] = useState('192k')
  const [hwAccel, setHwAccel] = useState(false)
  const [keepIntermediate, setKeepIntermediate] = useState(false)
  const [combinedFilename, setCombinedFilename] = useState('combined_final.mp4')
  const [syncMode, setSyncMode] = useState('standard')

  // ── Render state ─────────────────────────────────────────────────────────
  const [rendering, setRendering] = useState(false)
  const [renderResult, setRenderResult] = useState<FastEditRenderResponse | null>(null)

  const canRender = !rendering && audioDir.trim() && imageDir.trim() && outputDir.trim()
    && status?.ready_to_render

  const handleRender = async () => {
    setRendering(true)
    setRenderResult(null)
    const taskId = taskStore.add({
      toolId: 'fast-edit',
      toolLabel: 'Fast Edit',
      label: 'Render video',
    })
    try {
      const req: FastEditRenderRequest = {
        audio_directory: audioDir,
        image_directory: imageDir,
        output_directory: outputDir,
        subtitle_directory: subtitleDir || null,
        create_individual: createIndividual,
        create_combined: createCombined,
        frame_rate: frameRate,
        resolution_width: resW,
        resolution_height: resH,
        video_codec: videoCodec,
        video_bitrate: videoBitrate,
        audio_bitrate: audioBitrate,
        burn_subtitles: false,
        use_hardware_acceleration: hwAccel,
        keep_intermediate: keepIntermediate,
        combined_filename: combinedFilename,
        sync_mode: syncMode,
        video_filters: [],
        audio_filters: [],
        background_music_directory: null,
        logo_file: null,
        logo_enabled: false,
        logo_size: 80,
        logo_opacity: 0.8,
        subtitle_style: null,
        animation: null,
        transition: null,
      }
      const res = await fastEditApi.render(req)
      setRenderResult(res)
      taskStore.complete(taskId, res.ok ? 'done' : 'error', res.message)
    } catch (err: any) {
      setRenderResult({ ok: false, message: err?.message ?? 'Render failed', scenes: [], combined: null, total_duration: 0 })
      taskStore.complete(taskId, 'error', err?.message ?? 'Render failed')
    } finally {
      setRendering(false)
    }
  }

  const successCount = renderResult?.scenes.filter(s => s.success).length ?? 0
  const failCount    = renderResult?.scenes.filter(s => !s.success).length ?? 0

  return (
    <div className="space-y-4">

      {/* FFmpeg warning */}
      {status && !status.ready_to_render && (
        <div className="flex items-center gap-2.5 px-4 py-3 rounded-xl bg-amber-50 border border-amber-200 text-amber-700 text-sm">
          <AlertCircle size={15} className="shrink-0" />
          <span>FFmpeg is not available - install FFmpeg to render.</span>
        </div>
      )}

      {/* ── Source directories ── */}
      <Section title="Source folders" icon={FolderOpen}>
        <div className="space-y-3">
          <div>
            <PathBreadcrumb
              label="Audio folder *"
              value={audioDir}
              onChange={setAudioDir}
              onPickDirectory={async () => { const d = await pickDirectory(); if (d) setAudioDir(d) }}
              placeholder="/path/to/audio/"
            />
            {audioCount !== null && (
              <div className="mt-1.5 ml-1">
                <FileBadge count={audioCount} label="file audio" />
              </div>
            )}
          </div>
          <div>
            <PathBreadcrumb
              label="Image folder *"
              value={imageDir}
              onChange={setImageDir}
              onPickDirectory={async () => { const d = await pickDirectory(); if (d) setImageDir(d) }}
              placeholder="/path/to/images/"
            />
            {imageCount !== null && (
              <div className="mt-1.5 ml-1">
                <FileBadge count={imageCount} label="image files" />
              </div>
            )}
          </div>
          <PathBreadcrumb
            label="Output folder *"
            value={outputDir}
            onChange={setOutputDir}
            onPickDirectory={async () => { const d = await pickDirectory(); if (d) setOutputDir(d) }}
            placeholder="/path/to/output/"
          />
          <PathBreadcrumb
            label="Subtitle folder (optional)"
            value={subtitleDir}
            onChange={setSubtitleDir}
            onPickDirectory={async () => { const d = await pickDirectory(); if (d) setSubtitleDir(d) }}
            placeholder="Leave blank if not needed"
          />
        </div>
      </Section>

      {/* ── Render settings ── */}
      <Section title="Render settings" icon={Settings2} collapsible defaultOpen={false}>
        <div className="grid grid-cols-2 gap-3">
          <div>
            <label className="label">FPS</label>
            <input className="input" type="number" value={frameRate} onChange={e => setFrameRate(+e.target.value)} />
          </div>
          <div>
            <label className="label">Sync Mode</label>
            <select className="input" value={syncMode} onChange={e => setSyncMode(e.target.value)}>
              <option value="standard">Standard</option>
              <option value="sync_audio">Sync Audio</option>
              <option value="sync_images">Sync Images</option>
            </select>
          </div>
          <div>
            <label className="label">Width (px)</label>
            <input className="input" type="number" value={resW} onChange={e => setResW(+e.target.value)} />
          </div>
          <div>
            <label className="label">Height (px)</label>
            <input className="input" type="number" value={resH} onChange={e => setResH(+e.target.value)} />
          </div>
          <div>
            <label className="label">Video Codec</label>
            <select className="input" value={videoCodec} onChange={e => setVideoCodec(e.target.value)}>
              <option value="libx264">H.264 (libx264)</option>
              <option value="libx265">H.265 (libx265)</option>
              <option value="h264_videotoolbox">H.264 VideoToolbox</option>
            </select>
          </div>
          <div>
            <label className="label">Video Bitrate</label>
            <input className="input" value={videoBitrate} onChange={e => setVideoBitrate(e.target.value)} />
          </div>
          <div>
            <label className="label">Audio Bitrate</label>
            <input className="input" value={audioBitrate} onChange={e => setAudioBitrate(e.target.value)} />
          </div>
          <div>
            <label className="label">Combined Filename</label>
            <input className="input" value={combinedFilename} onChange={e => setCombinedFilename(e.target.value)} />
          </div>
        </div>

        {/* Checkboxes */}
        <div className="grid grid-cols-2 gap-2 pt-1">
          {[
            { label: 'Render each scene', checked: createIndividual, set: setCreateIndividual },
            { label: 'Combine final video',   checked: createCombined,   set: setCreateCombined },
            { label: 'HW Acceleration',   checked: hwAccel,           set: setHwAccel },
            { label: 'Keep intermediate files', checked: keepIntermediate, set: setKeepIntermediate },
          ].map(({ label, checked, set }) => (
            <label key={label} className="flex items-center gap-2 text-sm cursor-pointer select-none py-1">
              <input
                type="checkbox"
                checked={checked}
                onChange={e => set(e.target.checked)}
                className="accent-primary-500 w-4 h-4"
              />
              <span className="text-surface-700">{label}</span>
            </label>
          ))}
        </div>
      </Section>

      {/* ── Render button + status ── */}
      <div className="flex items-center justify-between pt-1">
        <div className="text-xs">
          {status?.ready_to_render
            ? <span className="flex items-center gap-1.5 text-green-600 font-medium"><Zap size={12} /> FFmpeg ready</span>
            : <span className="flex items-center gap-1.5 text-amber-600"><AlertCircle size={12} /> FFmpeg not ready</span>
          }
        </div>
        <button
          className="btn-primary px-5 py-2 text-sm gap-2"
          onClick={handleRender}
          disabled={!canRender}
        >
          {rendering
            ? <><Loader size={14} className="animate-spin" /> Rendering...</>
            : <><Play size={14} /> Start render</>
          }
        </button>
      </div>

      {/* ── Render result ── */}
      {renderResult && (
        <div className={cn(
          'rounded-xl border p-4',
          renderResult.ok ? 'border-green-200 bg-green-50' : 'border-red-200 bg-red-50',
        )}>
          <div className="flex items-start gap-2.5 mb-2">
            {renderResult.ok
              ? <CheckCircle size={16} className="text-green-600 mt-0.5 shrink-0" />
              : <AlertCircle size={16} className="text-red-600 mt-0.5 shrink-0" />}
            <div>
              <p className={cn('text-sm font-semibold', renderResult.ok ? 'text-green-700' : 'text-red-700')}>
                {renderResult.message}
              </p>
              {renderResult.scenes.length > 0 && (
                <p className="text-xs text-surface-500 mt-0.5">
                  {successCount} succeeded · {failCount} failed · Total {renderResult.total_duration.toFixed(1)}s
                </p>
              )}
            </div>
          </div>
          {renderResult.scenes.length > 0 && (
            <div className="max-h-48 overflow-y-auto mt-3 space-y-1">
              {renderResult.scenes.map(s => (
                <div key={s.index} className={cn(
                  'flex items-center gap-2 text-xs px-3 py-1.5 rounded-lg',
                  s.success ? 'bg-green-100 text-green-800' : 'bg-red-100 text-red-800',
                )}>
                  <span className="font-mono w-5 text-right shrink-0 opacity-60">{s.index + 1}</span>
                  <span className="flex-1 truncate font-mono">{s.output_path.split('/').pop()}</span>
                  <span className="shrink-0 opacity-70">{s.duration.toFixed(1)}s</span>
                  {s.error && (
                    <span className="text-red-600 truncate max-w-[200px]" title={s.error}>{s.error}</span>
                  )}
                </div>
              ))}
            </div>
          )}
        </div>
      )}
    </div>
  )
}

// ═════════════════════════════════════════════════════════════════════════════
// ─── BATCH RENAME TAB ────────────────────────────────────────────────────────
// ═════════════════════════════════════════════════════════════════════════════

function RenameTab() {
  const [directory, setDirectory] = useState('')
  const [assetType, setAssetType] = useState('audio')
  const [prefix, setPrefix] = useState('')
  const [startIndex, setStartIndex] = useState(1)
  const [padWidth, setPadWidth] = useState(3)
  const [separator, setSeparator] = useState('_')
  const [lowercaseExt, setLowercaseExt] = useState(true)
  const [fileCount, setFileCount] = useState<number | null>(null)

  const [running, setRunning] = useState(false)
  const [result, setResult] = useState<FastEditBatchRenameResponse | null>(null)

  useEffect(() => {
    if (!directory.trim()) { setFileCount(null); return }
    fastEditApi.fileCount(directory, assetType)
      .then(r => setFileCount(r.total))
      .catch(() => setFileCount(null))
  }, [directory, assetType])

  const handleRename = async () => {
    if (!directory.trim()) return
    setRunning(true)
    setResult(null)
    const taskId = taskStore.add({
      toolId: 'fast-edit',
      toolLabel: 'Fast Edit',
      label: 'Batch rename files',
    })
    try {
      const res = await fastEditApi.rename({
        directory,
        asset_type: assetType,
        prefix: prefix || undefined,
        start_index: startIndex,
        pad_width: padWidth,
        separator,
        lowercase_extension: lowercaseExt,
      })
      setResult(res)
      taskStore.complete(taskId, res.ok ? 'done' : 'error', res.message)
    } catch (err: any) {
      setResult({ ok: false, message: err?.message ?? 'Rename failed', results: [], total: 0, succeeded: 0, failed: 0 })
      taskStore.complete(taskId, 'error', err?.message ?? 'Rename failed')
    } finally {
      setRunning(false)
    }
  }

  const ext = assetType === 'audio' ? '.mp3' : '.jpg'
  const previewName = `${prefix || (assetType === 'audio' ? 'audio' : 'img')}${separator}${String(startIndex).padStart(padWidth, '0')}${ext}`

  const ASSET_TYPES = [
    { id: 'audio', label: 'Audio', icon: Volume2, desc: 'mp3, wav, flac, m4a, ogg' },
    { id: 'image', label: 'Image', icon: Image,   desc: 'jpg, png, webp, bmp, gif' },
  ]

  return (
    <div className="space-y-4">
      <Section title="Batch Rename" icon={ArrowDownUp}>
        <div>
          <PathBreadcrumb
            label="Folder"
            value={directory}
            onChange={setDirectory}
            onPickDirectory={async () => { const d = await pickDirectory(); if (d) setDirectory(d) }}
            placeholder="/path/to/files/"
          />
          {fileCount !== null && (
            <div className="mt-1.5 ml-1">
              <FileBadge count={fileCount} label={`file ${assetType}`} />
            </div>
          )}
        </div>

        {/* Asset type toggle */}
        <div>
          <label className="label">File type</label>
          <div className="flex gap-2">
            {ASSET_TYPES.map(({ id, label, icon: Icon, desc }) => (
              <button
                key={id}
                className={cn(
                  'flex items-center gap-2.5 flex-1 px-4 py-3 rounded-xl border-2 transition-all duration-150 text-left',
                  assetType === id
                    ? 'border-primary-400 bg-primary-50 shadow-sm'
                    : 'border-surface-200 bg-white hover:border-surface-300 hover:bg-surface-50',
                )}
                onClick={() => setAssetType(id)}
              >
                <div className={cn(
                  'w-9 h-9 rounded-lg flex items-center justify-center shrink-0',
                  assetType === id ? 'bg-primary-100' : 'bg-surface-100',
                )}>
                  <Icon size={18} className={assetType === id ? 'text-primary-600' : 'text-surface-400'} />
                </div>
                <div className="min-w-0">
                  <p className={cn('text-sm font-semibold', assetType === id ? 'text-primary-700' : 'text-surface-700')}>
                    {label}
                  </p>
                  <p className="text-xs text-surface-400 truncate">{desc}</p>
                </div>
              </button>
            ))}
          </div>
        </div>

        {/* Naming config */}
        <div className="grid grid-cols-3 gap-3">
          <div>
            <label className="label">Prefix</label>
            <input className="input" value={prefix} onChange={e => setPrefix(e.target.value)} placeholder={assetType === 'audio' ? 'audio' : 'img'} />
          </div>
          <div>
            <label className="label">Separator</label>
            <input className="input text-center font-mono" value={separator} onChange={e => setSeparator(e.target.value)} />
          </div>
          <div>
            <label className="label">Start Index</label>
            <input className="input" type="number" min={0} value={startIndex} onChange={e => setStartIndex(+e.target.value)} />
          </div>
        </div>

        <div className="grid grid-cols-2 gap-3">
          <div>
            <label className="label">Pad Width</label>
            <input className="input" type="number" min={1} max={6} value={padWidth} onChange={e => setPadWidth(+e.target.value)} />
          </div>
          <div>
            <label className="label flex items-center gap-1"><Eye size={12} /> Preview</label>
            <div className="flex items-center h-[38px] px-3 rounded-lg bg-primary-50 border border-primary-100">
              <span className="font-mono text-xs text-primary-600 font-semibold">{previewName}</span>
            </div>
          </div>
        </div>

        <label className="flex items-center gap-2 text-sm cursor-pointer select-none">
          <input type="checkbox" checked={lowercaseExt} onChange={e => setLowercaseExt(e.target.checked)} className="accent-primary-500 w-4 h-4" />
          <span className="text-surface-700">Lowercase file extensions</span>
        </label>
      </Section>

      <div className="flex justify-end">
        <button className="btn-primary px-5 py-2 text-sm gap-2" onClick={handleRename} disabled={running || !directory.trim()}>
          {running
            ? <><Loader size={14} className="animate-spin" /> Renaming...</>
            : <><ArrowDownUp size={14} /> Rename</>
          }
        </button>
      </div>

      {result && (
        <div className={cn(
          'rounded-xl border p-4',
          result.ok ? 'border-green-200 bg-green-50' : 'border-red-200 bg-red-50',
        )}>
          <div className="flex items-start gap-2.5 mb-2">
            {result.ok
              ? <CheckCircle size={16} className="text-green-600 mt-0.5 shrink-0" />
              : <AlertCircle size={16} className="text-red-600 mt-0.5 shrink-0" />}
            <div>
              <p className={cn('text-sm font-semibold', result.ok ? 'text-green-700' : 'text-red-700')}>
                {result.message}
              </p>
              <p className="text-xs text-surface-500 mt-0.5">
                Total: {result.total} · Succeeded: {result.succeeded} · Failed: {result.failed}
              </p>
            </div>
          </div>
          {result.results.length > 0 && (
            <div className="max-h-48 overflow-y-auto mt-3 space-y-1">
              {result.results.map((r, i) => (
                <div key={i} className={cn(
                  'flex items-center gap-2 text-xs px-3 py-1.5 rounded-lg',
                  r.success ? 'bg-green-100 text-green-800' : 'bg-red-100 text-red-800',
                )}>
                  <span className="font-mono truncate flex-1">{r.original_path.split('/').pop()}</span>
                  <span className="text-surface-400 shrink-0">→</span>
                  <span className="font-mono truncate flex-1 text-right">{r.new_path.split('/').pop()}</span>
                </div>
              ))}
            </div>
          )}
        </div>
      )}
    </div>
  )
}

// ═════════════════════════════════════════════════════════════════════════════
// ─── MAIN COMPONENT ──────────────────────────────────────────────────────────
// ═════════════════════════════════════════════════════════════════════════════

export default function FastEdit() {
  const { selectedChildId } = usePanelContext()
  const { childProjects } = useAppStore()
  const child = childProjects.find(c => c.id === selectedChildId)

  const [activeTab, setActiveTab] = useState<Tab>('compose')

  return (
    <div className="flex flex-col h-full bg-surface-50">

      {/* ── Header ── */}
      <div className="flex items-center gap-3 px-6 py-4 border-b border-surface-200 bg-white shrink-0">
        <div className="w-8 h-8 rounded-lg bg-rose-100 flex items-center justify-center shrink-0">
          <Film size={16} className="text-rose-600" />
        </div>
        <div className="flex-1 min-w-0">
          <h1 className="text-sm font-bold text-surface-900">Fast Edit</h1>
          <p className="text-[11px] text-surface-400 truncate">
            {child?.name ?? 'No child selected'} · Compose, render & batch rename
          </p>
        </div>
      </div>

      {/* ── Tabs ── */}
      <div className="flex gap-1 border-b border-surface-200 bg-white px-5 shrink-0">
        {TABS.map(({ id, label, icon: Icon }) => (
          <button
            key={id}
            className={cn(
              'flex items-center gap-1.5 px-4 py-2.5 text-xs font-semibold border-b-2 transition-colors',
              activeTab === id
                ? 'border-primary-500 text-primary-600'
                : 'border-transparent text-surface-400 hover:text-surface-700 hover:border-surface-200',
            )}
            onClick={() => setActiveTab(id)}
          >
            <Icon size={13} />
            {label}
          </button>
        ))}
      </div>

      {/* ── Tab content ── */}
      <div className="flex-1 overflow-y-auto p-5">
        {activeTab === 'compose' && <ComposeTab />}
        {activeTab === 'rename'  && <RenameTab />}
      </div>
    </div>
  )
}
