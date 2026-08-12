import { useState, useCallback, useRef } from 'react'
import { useExtensionSocket } from '@/hooks/useExtensionSocket'
import { toast } from '@/store/toast.store'
import { MediaPicker } from '@/components/media/MediaPicker'
import type { MediaAsset } from '@/lib/media'
import { useAppStore } from '@/store/app.store'
import { usePanelContext } from '@/contexts/PanelContext'
import {
  FileText, Play, Copy, Download, Loader,
  Scissors, RotateCcw, CheckCircle, AlertCircle, Hash, Upload,
} from 'lucide-react'
import { srtApi } from '@/lib/api'
import { cn, unknownErrorMessage } from '@/lib/utils'
import { taskStore } from '@/store/task.store'

// ─── Sentence splitter ────────────────────────────────────────────────────────
// Strips leading list markers: "1.", "1)", "(1)", "1-", "1:" etc.
const LEADING_LIST_MARKER_REGEX = /^\s*(?:\(?\d{1,4}\)?[.)]|(?:\d{1,4}\s*[-:]))\s+/

function stripLeadingListMarker(line: string): string {
  return line.replace(LEADING_LIST_MARKER_REGEX, '').trim()
}

function splitIntoNumberedSentences(raw: string): string {
  const lines = raw
    .replace(/\r\n/g, '\n')
    .replace(/\r/g, '\n')
    .split('\n')
    .map(stripLeadingListMarker)
    .filter(line => line !== '')

  if (lines.length === 0) return raw
  return lines.map((s, i) => `${i + 1}. ${s}`).join('\n')
}

function countSentences(text: string): number {
  return text.split('\n').filter(l => l.trim()).length
}

function isNumbered(text: string): boolean {
  const lines = text.split('\n').filter(l => l.trim())
  if (lines.length === 0) return false
  return lines.filter(l => /^\d+[.)]\s/.test(l.trim())).length > lines.length * 0.7
}

// ─── Component ────────────────────────────────────────────────────────────────
export default function SRTGen() {
  const { childProjects } = useAppStore()
  const { selectedChildId } = usePanelContext()
  const child = childProjects.find(c => c.id === selectedChildId)

  const [srtPath, setSrtPath] = useState('')
  const [srtAssetName, setSrtAssetName] = useState('')
  const [contentText, setContentText] = useState('')
  const [loading, setLoading] = useState(false)
  const [result, setResult] = useState<{ ok: boolean; output: string; message: string } | null>(null)
  const [importError, setImportError] = useState('')
  const fileInputRef = useRef<HTMLInputElement>(null)

  // ── Extension bridge — nhận script từ Claude extension ───────────────────
  useExtensionSocket('srt_gen', (msg) => {
    setContentText(prev => {
      const trimmed = prev.trim()
      return trimmed ? `${trimmed}\n${msg.content}` : msg.content
    })
    toast.success('⚡ Nhận script từ Claude!', 'Đã fill nội dung vào ô SRT', 4000)
  })

  const selectSrt = (asset: MediaAsset) => {
    setSrtPath(asset.reference)
    setSrtAssetName(asset.original_name)
  }

  // ── Auto-split on paste ──────────────────────────────────────────────────────
  const handlePaste = useCallback(() => {
    // Let React update state first, then process
    setTimeout(() => {
      setContentText(prev => splitIntoNumberedSentences(prev))
    }, 0)
  }, [])

  // ── Manual split button ──────────────────────────────────────────────────────
  const handleSplit = () => {
    setContentText(prev => splitIntoNumberedSentences(prev))
  }

  // ── Reset numbering ──────────────────────────────────────────────────────────
  const handleReset = () => {
    // Strip leading "N. " from all lines
    setContentText(prev =>
      prev
        .split('\n')
        .map(l => l.replace(/^\d+[.)]\s*/, '').trim())
        .filter(l => l)
        .join('\n')
    )
  }

  // ── Import script text with the browser FileReader API ──────────────────────
  const handleImportClick = useCallback(() => {
    fileInputRef.current?.click()
  }, [])

  const handleFileChange = useCallback((e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0]
    if (!file) return
    setImportError('')
    const reader = new FileReader()
    reader.onload = (evt) => {
      const text = evt.target?.result as string
      if (text) setContentText(splitIntoNumberedSentences(text))
    }
    reader.onerror = () => setImportError(`Cannot read "${file.name}" — make sure it is a plain-text file.`)
    reader.readAsText(file, 'UTF-8')
    e.target.value = '' // allow re-selecting the same file
  }, [])

  // ── Generate ────────────────────────────────────────────────────────────────
  const handleGenerate = async () => {
    if (!srtPath.trim() || !contentText.trim()) return
    setLoading(true)
    setResult(null)
    const taskId = taskStore.add({ toolId: 'srt-gen', toolLabel: 'SRT Gen', label: 'Generate SRT' })
    try {
      const res = await srtApi.generate(srtPath.trim(), contentText)
      setResult({ ok: res.ok, output: res.srt_output, message: res.message })
      taskStore.complete(taskId, res.ok ? 'done' : 'error', res.message)
    } catch (err: unknown) {
      const msg = unknownErrorMessage(err, 'API error')
      setResult({ ok: false, output: '', message: msg })
      taskStore.complete(taskId, 'error', msg)
    } finally {
      setLoading(false)
    }
  }

  const handleCopy = () => {
    if (result?.output) navigator.clipboard.writeText(result.output)
  }

  const handleDownload = async () => {
    if (!result?.output) return
    const url = URL.createObjectURL(new Blob([result.output], { type: 'application/x-subrip;charset=utf-8' }))
    const anchor = document.createElement('a')
    anchor.href = url
    anchor.download = `${child?.name ?? 'output'}.srt`
    anchor.click()
    URL.revokeObjectURL(url)
  }

  const sentenceCount = countSentences(contentText)
  const numbered = isNumbered(contentText)
  const canGenerate = !loading && srtPath.trim() && contentText.trim()

  return (
    <div className="flex flex-col h-full">
      {/* Header */}
      <div className="flex items-center gap-3 px-6 py-4 border-b border-surface-200 bg-surface-0">
        <div className="w-8 h-8 rounded-lg bg-violet-500/10 flex items-center justify-center">
          <FileText size={16} className="text-violet-300" />
        </div>
        <div>
          <h1 className="page-title">SRT Generator</h1>
          <p className="page-sub">{child?.name ?? 'No child selected'} · Merge script content into a source SRT</p>
        </div>
      </div>

      <div className="flex-1 overflow-y-auto p-6 space-y-5">
        {/* SRT File */}
        <div>
          <label className="label">Source SRT file</label>
          <div className="flex gap-2">
            <input
              className="input font-mono text-xs flex-1"
              placeholder="Choose an SRT from Media Library"
              value={srtAssetName}
              readOnly
            />
            <MediaPicker
              kind="subtitle"
              accept=".srt,.vtt"
              label="SRT File"
              selectedName={srtAssetName}
              className="shrink-0"
              onSelect={selectSrt}
            />
          </div>
          <p className="text-xs text-surface-400 mt-1">
            Upload any standard SRT file; the generated result downloads through your browser.
          </p>
        </div>

        {/* Content / Script */}
        <div>
          {/* Hidden file input */}
          <input
            ref={fileInputRef}
            type="file"
            className="hidden"
            accept=".txt,.md,.csv,.tsv,.json,.xml,.html,.htm,.srt,.vtt,.ass,.log,.rtf,.py,.js,.ts,.jsx,.tsx,.yaml,.yml,.toml,.ini,.cfg,.conf,.tex,.rst,.org,.wiki,.nfo,.sub,.sbv,.lrc"
            onChange={handleFileChange}
          />

          {/* Label row + action buttons */}
          <div className="flex items-center justify-between mb-1.5">
            <label className="label mb-0">Content / Script</label>
            <div className="flex items-center gap-2">
              <button
                className="btn-secondary text-xs py-1 px-2.5 h-auto"
                onClick={handleImportClick}
                title="Import content from a text file (.txt, .md, .srt, .vtt, and more)"
              >
                <Upload size={11} />
                Import file
              </button>
              {contentText.trim() && (
                <button
                  className="btn-secondary text-xs py-1 px-2 h-auto"
                  onClick={handleReset}
                  title="Remove numbering and return to plain text"
                >
                  <RotateCcw size={11} />
                  Reset numbering
                </button>
              )}
              <button
                className={cn(
                  'btn-secondary text-xs py-1 px-2.5 h-auto',
                  numbered && 'border-violet-300 text-violet-300 bg-violet-500/10'
                )}
                onClick={handleSplit}
                disabled={!contentText.trim()}
                title="Split sentences and number them"
              >
                <Scissors size={11} />
                Split &amp; number
              </button>
            </div>
          </div>

          <textarea
            className="textarea font-mono text-xs"
            rows={12}
            placeholder={`Paste your script here — the system will split and number it automatically.\n\nExample after splitting:\n1. If you are over 75 and you are not eating these five seeds,\n2. your retina is likely losing protective compounds faster...\n3. That process is silent, it is gradual,`}
            value={contentText}
            onChange={e => setContentText(e.target.value)}
            onPaste={handlePaste}
          />

          {/* Import error */}
          {importError && (
            <p className="flex items-center gap-1.5 text-xs text-red-300 mt-1">
              <AlertCircle size={11} className="shrink-0" />
              {importError}
            </p>
          )}

          {/* Status bar */}
          <div className="flex items-center justify-between mt-1.5">
            <div className="flex items-center gap-2">
              <p className="text-xs text-surface-400">
                <span className="font-medium text-surface-600">{sentenceCount}</span> sentences
              </p>
              {contentText.trim() && numbered && (
                <span className="inline-flex items-center gap-1 text-xs text-violet-300 bg-violet-500/10 border border-violet-500/20 rounded px-1.5 py-0.5">
                  <Hash size={10} />
                  Numbered
                </span>
              )}
              {contentText.trim() && !numbered && (
                <span className="inline-flex items-center gap-1 text-xs text-amber-300 bg-amber-500/10 border border-amber-500/20 rounded px-1.5 py-0.5">
                  <Scissors size={10} />
                  Not numbered yet - click &quot;Split sentences&quot;
                </span>
              )}
            </div>
            {sentenceCount > 0 && (
              <p className="text-xs text-surface-300">
                {srtPath.trim() ? 'Ready to generate' : 'Choose an SRT file to continue'}
              </p>
            )}
          </div>
        </div>

        {/* Generate button */}
        <div className="flex justify-end">
          <button
            className="btn-primary"
            onClick={handleGenerate}
            disabled={!canGenerate}
          >
            {loading
              ? <Loader size={14} className="animate-spin" />
              : <Play size={14} />}
            {loading ? 'Processing...' : 'Generate SRT'}
          </button>
        </div>

        {/* Result */}
        {result && (
          <div className={cn(
            'card p-4',
            result.ok ? 'border-green-500/20 bg-green-500/10' : 'border-red-500/20 bg-red-500/10'
          )}>
            <div className="flex items-start justify-between mb-3 gap-3">
              <div className="flex items-start gap-2">
                {result.ok
                  ? <CheckCircle size={16} className="text-green-300 mt-0.5 shrink-0" />
                  : <AlertCircle size={16} className="text-red-300 mt-0.5 shrink-0" />}
                <p className={cn(
                  'text-sm font-medium leading-snug',
                  result.ok ? 'text-green-300' : 'text-red-300'
                )}>
                  {result.message}
                </p>
              </div>
              {result.ok && (
                <div className="flex gap-2 shrink-0">
                  <button className="btn-secondary text-xs" onClick={handleCopy}>
                    <Copy size={12} /> Copy
                  </button>
                  <button className="btn-primary text-xs" onClick={handleDownload}>
                    <Download size={12} /> Download .srt
                  </button>
                </div>
              )}
            </div>
            {result.output && (
              <pre className="text-xs font-mono bg-surface-0 rounded-md p-3 border border-surface-200 max-h-64 overflow-y-auto whitespace-pre-wrap">
                {result.output}
              </pre>
            )}
          </div>
        )}
      </div>
    </div>
  )
}
