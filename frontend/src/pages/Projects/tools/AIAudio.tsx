import { useState, useEffect, useRef, useCallback, useMemo } from 'react'
import { useAppStore } from '@/store/app.store'
import { usePanelContext } from '@/contexts/PanelContext'
import {
  Mic, Play, Square, Download, RefreshCw, ChevronDown, ChevronUp, ChevronRight, Plus,
  CheckCircle, XCircle, Loader, Zap, MessageSquare,
  Volume2, AlertTriangle, Trash2,
} from 'lucide-react'
import { cn } from '@/lib/utils'
import { taskStore } from '@/store/task.store'
import {
  listVoices, listModels, createTtsTask, getTaskStatus,
  createDialogueAudio, getHealth,
  type ELVoice, type ELModel, type ELVoiceSettings,
  type DialogueItem, type HealthStatus,
} from '@/lib/audioService'

// ─── Types ────────────────────────────────────────────────────────────────────

type SegStatus = 'idle' | 'pending' | 'generating' | 'done' | 'error'
type Tab = 'tts' | 'dialogue'

interface Segment {
  id: string
  text: string
  status: SegStatus
  taskId?: string
  audioUrl?: string
  error?: string
}

interface Speaker {
  id: string
  label: string
  voiceId: string
  modelId: string
  stability: number
  isExpanded: boolean
  voicePickerOpen: boolean
}

// ─── Constants ────────────────────────────────────────────────────────────────

const CONCURRENCY   = 3
const POLL_INTERVAL = 2000
const MAX_POLLS     = 120

const DEFAULT_SETTINGS: ELVoiceSettings = {
  stability: 0.5,
  similarity_boost: 0.75,
  style: 0.0,
  use_speaker_boost: true,
}

const SLIDER_FIELDS = [
  { key: 'stability'       as const, label: 'Stability',   color: 'from-blue-400 to-blue-600' },
  { key: 'similarity_boost'as const, label: 'Similarity',  color: 'from-violet-400 to-violet-600' },
  { key: 'style'           as const, label: 'Style',       color: 'from-rose-400 to-rose-600' },
]

// ─── Helpers ──────────────────────────────────────────────────────────────────

const uid = () => Math.random().toString(36).slice(2, 9)

function buildSegments(raw: string, existing: Segment[]): Segment[] {
  const lines = raw
    .split('\n')
    .map(l => l.replace(/^[\d]+[.)]\s*/, '').trim())
    .filter(Boolean)

  return lines.map((text, i) => {
    const prev = existing[i]
    // Re-use existing segment if text unchanged (preserves done/error state)
    if (prev && prev.text === text) return prev
    return { id: uid(), text, status: 'idle' as SegStatus }
  })
}

function parseDialogueLines(raw: string): { label: string; text: string }[] {
  return raw
    .split('\n')
    .map(l => l.trim())
    .filter(Boolean)
    .flatMap(l => {
      const m = l.match(/^([A-Z](?:\d+)?)>\s*(?:\[.*?\]\s*)?(.+)$/)
      return m ? [{ label: m[1], text: m[2] }] : []
    })
}

// ─── Sub-components ───────────────────────────────────────────────────────────

function VoiceAvatar({ name }: { name: string }) {
  return (
    <div className="w-8 h-8 rounded-full bg-gradient-to-br from-violet-400 to-violet-600 flex items-center justify-center text-white text-sm font-bold shrink-0 select-none">
      {name.charAt(0).toUpperCase()}
    </div>
  )
}

function SliderSetting({
  label, color, value, onChange,
}: {
  label: string; color: string; value: number; onChange: (v: number) => void
}) {
  const pct = Math.round(value * 100)
  return (
    <div className="space-y-1.5">
      <div className="flex items-center justify-between">
        <span className="text-xs font-medium text-surface-600">{label}</span>
        <span className="text-xs font-semibold text-surface-900 tabular-nums w-8 text-right">{pct}%</span>
      </div>
      <div className="relative h-2 bg-surface-100 rounded-full overflow-hidden">
        <div
          className={cn('absolute left-0 top-0 h-full rounded-full bg-gradient-to-r', color)}
          style={{ width: `${pct}%` }}
        />
        <input
          type="range" min={0} max={1} step={0.01}
          value={value}
          onChange={e => onChange(parseFloat(e.target.value))}
          className="absolute inset-0 w-full h-full opacity-0 cursor-pointer"
        />
      </div>
    </div>
  )
}

function SegmentCard({
  seg, index, playing, isGenerating, voiceId,
  onPlay, onDownload, onRegenerate, onDelete,
}: {
  seg: Segment
  index: number
  playing: boolean
  isGenerating: boolean
  voiceId: string
  onPlay: () => void
  onDownload: () => void
  onRegenerate: () => void
  onDelete: () => void
}) {
  const isError      = seg.status === 'error'
  const isDone       = seg.status === 'done'
  const isProcessing = seg.status === 'generating' || seg.status === 'pending'

  return (
    <div className={cn(
      'group rounded-xl border bg-white transition-all',
      isError      ? 'border-red-200 bg-red-50/30'   :
      isDone       ? 'border-green-200/60'            :
      isProcessing ? 'border-blue-200/60 bg-blue-50/20' :
                     'border-surface-200 hover:border-surface-300',
    )}>
      <div className="flex items-center gap-3 px-4 py-3">
        {/* Number badge */}
        <div className={cn(
          'w-7 h-7 rounded-full flex items-center justify-center text-xs font-bold shrink-0',
          isError      ? 'bg-red-100 text-red-600'     :
          isDone       ? 'bg-green-100 text-green-700' :
          isProcessing ? 'bg-blue-100 text-blue-600'   :
                         'bg-surface-100 text-surface-500',
        )}>
          {isProcessing
            ? <Loader size={12} className="animate-spin" />
            : index + 1}
        </div>

        {/* Text */}
        <p className="flex-1 text-sm text-surface-800 leading-snug min-w-0">{seg.text}</p>

        {/* Actions */}
        <div className="flex items-center gap-0.5 shrink-0 opacity-0 group-hover:opacity-100 transition-opacity">
          <button
            className="btn-icon text-surface-400 hover:text-primary-600 hover:bg-primary-50"
            onClick={onRegenerate}
            disabled={isGenerating || !voiceId || isProcessing}
            title="Regenerate"
          >
            <RefreshCw size={13} />
          </button>

          {isDone && (
            <button
              className={cn(
                'btn-icon transition-colors',
                playing
                  ? 'text-primary-600 bg-primary-50'
                  : 'text-surface-400 hover:text-primary-600 hover:bg-primary-50',
              )}
              onClick={onPlay}
              title={playing ? 'Stop' : 'Play'}
            >
              {playing ? <Square size={13} /> : <Play size={13} />}
            </button>
          )}

          {isDone && (
            <button
              className="btn-icon text-surface-400 hover:text-surface-700 hover:bg-surface-100"
              onClick={onDownload}
              title="Download"
            >
              <Download size={13} />
            </button>
          )}

          <button
            className="btn-icon text-surface-300 hover:text-red-500 hover:bg-red-50"
            onClick={onDelete}
            title="Delete"
          >
            <Trash2 size={13} />
          </button>
        </div>

        {/* Status icon (always visible when done/error) */}
        {isDone && (
          <CheckCircle size={14} className="text-green-500 shrink-0 group-hover:hidden" />
        )}
        {isError && (
          <XCircle size={14} className="text-red-400 shrink-0 group-hover:hidden" />
        )}
      </div>

      {/* Error message */}
      {isError && seg.error && (
        <div className="flex items-center gap-1.5 px-4 pb-3 text-xs text-red-600">
          <AlertTriangle size={11} />
          {seg.error}
        </div>
      )}
    </div>
  )
}

// ─── Main Component ───────────────────────────────────────────────────────────

export default function AIAudio() {
  const { childProjects } = useAppStore()
  const { selectedChildId } = usePanelContext()
  const child = childProjects.find(c => c.id === selectedChildId)

  const [tab, setTab] = useState<Tab>('tts')

  // Voice / model
  const [voices, setVoices]                   = useState<ELVoice[]>([])
  const [models, setModels]                   = useState<ELModel[]>([])
  const [voicesLoading, setVoicesLoading]     = useState(false)
  const [voiceSearch, setVoiceSearch]         = useState('')
  const [showVoicePicker, setShowVoicePicker] = useState(false)
  const voicePickerRef = useRef<HTMLDivElement>(null)

  // TTS
  const [script, setScript]             = useState('')
  const [segments, setSegments]         = useState<Segment[]>([])
  const [voiceId, setVoiceId]           = useState('')
  const [modelId, setModelId]           = useState('eleven_multilingual_v2')
  const [settings, setSettings]         = useState<ELVoiceSettings>(DEFAULT_SETTINGS)
  const [isGenerating, setIsGenerating] = useState(false)
  const [filter, setFilter]             = useState<'all' | 'issues'>('all')
  const [jumpTo, setJumpTo]             = useState('')

  // Playback
  const [playingId, setPlayingId] = useState<string | null>(null)
  const audioRef = useRef<HTMLAudioElement | null>(null)

  // Dialogue
  const [dlgScript, setDlgScript]         = useState('')
  const [speakers, setSpeakers]           = useState<Speaker[]>([
    { id: uid(), label: 'A', voiceId: '', modelId: 'eleven_multilingual_v2', stability: 0.5, isExpanded: true,  voicePickerOpen: false },
    { id: uid(), label: 'B', voiceId: '', modelId: 'eleven_multilingual_v2', stability: 0.5, isExpanded: false, voicePickerOpen: false },
  ])
  const [dlgGenerating, setDlgGenerating] = useState(false)
  const [dlgAudioUrl, setDlgAudioUrl]     = useState<string | null>(null)
  const [dlgError, setDlgError]           = useState<string | null>(null)
  const [dlgDelay, setDlgDelay]           = useState(0)
  const [dlgLoudness, setDlgLoudness]     = useState(false)
  const [dlgTranscript, setDlgTranscript] = useState(false)
  const [dlgOpenPickerId, setDlgOpenPickerId] = useState<string | null>(null)
  const [dlgVoiceSearch, setDlgVoiceSearch]   = useState('')
  const dlgPickerRef = useRef<HTMLDivElement>(null)

  // Health
  const [health, setHealth] = useState<{ elevenlabs: HealthStatus; minimax: HealthStatus } | null>(null)

  // ── Load on mount ─────────────────────────────────────────────────────────
  useEffect(() => {
    setVoicesLoading(true)
    Promise.all([listVoices(), listModels()])
      .then(([v, m]) => {
        setVoices(v)
        const el = m.filter(x => x.model_id.includes('eleven'))
        setModels(el)
        if (v.length > 0) setVoiceId(v[0].voice_id)
        if (el.length > 0) setModelId(el[0].model_id)
      })
      .catch(console.error)
      .finally(() => setVoicesLoading(false))
    getHealth().then(setHealth).catch(() => {})
  }, [])

  // Close voice picker on outside click
  useEffect(() => {
    const h = (e: MouseEvent) => {
      if (voicePickerRef.current && !voicePickerRef.current.contains(e.target as Node))
        setShowVoicePicker(false)
      if (dlgPickerRef.current && !dlgPickerRef.current.contains(e.target as Node))
        setDlgOpenPickerId(null)
    }
    document.addEventListener('mousedown', h)
    return () => document.removeEventListener('mousedown', h)
  }, [])

  // Auto-parse script live
  useEffect(() => {
    setSegments(prev => buildSegments(script, prev))
  }, [script])

  // ── Derived ───────────────────────────────────────────────────────────────
  const selectedVoice = voices.find(v => v.voice_id === voiceId)

  const filteredVoices = useMemo(
    () => voiceSearch
      ? voices.filter(v => v.name.toLowerCase().includes(voiceSearch.toLowerCase()))
      : voices,
    [voices, voiceSearch],
  )

  const doneCount  = segments.filter(s => s.status === 'done').length
  const errCount   = segments.filter(s => s.status === 'error').length
  const totalCount = segments.length

  const visibleSegs = filter === 'issues'
    ? segments.filter(s => s.status === 'error')
    : segments

  // ── Generation ────────────────────────────────────────────────────────────
  const updateSeg = useCallback((id: string, patch: Partial<Segment>) => {
    setSegments(prev => prev.map(s => s.id === id ? { ...s, ...patch } : s))
  }, [])

  const generateOne = useCallback(async (seg: Segment) => {
    if (!voiceId) return
    updateSeg(seg.id, { status: 'generating', error: undefined })
    try {
      const taskId = await createTtsTask(seg.text, voiceId, modelId, settings)
      updateSeg(seg.id, { taskId, status: 'generating' })
      for (let i = 0; i < MAX_POLLS; i++) {
        await new Promise(r => setTimeout(r, POLL_INTERVAL))
        const res = await getTaskStatus(taskId)
        if (res.status === 'done') {
          updateSeg(seg.id, { status: 'done', audioUrl: res.metadata?.audio_url })
          return
        }
        if (res.status === 'error') {
          updateSeg(seg.id, { status: 'error', error: res.error_message ?? 'Failed' })
          return
        }
      }
      updateSeg(seg.id, { status: 'error', error: 'Generation timed out after 240 seconds.' })
    } catch (err: unknown) {
      updateSeg(seg.id, { status: 'error', error: (err as Error)?.message ?? 'Unknown error' })
    }
  }, [voiceId, modelId, settings, updateSeg])

  const handleGenerateAll = async () => {
    if (!voiceId || segments.length === 0 || isGenerating) return
    setIsGenerating(true)
    setSegments(prev => prev.map(s =>
      s.status !== 'done' ? { ...s, status: 'pending', error: undefined } : s
    ))
    const toRun = segments.filter(s => s.status !== 'done')
    const taskId = taskStore.add({
      toolId: 'ai-audio',
      toolLabel: 'AI Audio',
      label: `TTS ${toRun.length} segments`,
    })
    for (let i = 0; i < toRun.length; i += CONCURRENCY)
      await Promise.all(toRun.slice(i, i + CONCURRENCY).map(s => generateOne(s)))
    setIsGenerating(false)
    const failCount = segments.filter(s => s.status === 'error').length
    taskStore.complete(taskId, failCount === 0 ? 'done' : 'error',
      failCount === 0
        ? `${toRun.length} segments completed`
        : `${toRun.length - failCount} done, ${failCount} failed`
    )
  }

  const handleRegenerateErrors = async () => {
    const errors = segments.filter(s => s.status === 'error')
    if (!voiceId || errors.length === 0 || isGenerating) return
    setIsGenerating(true)
    const taskId = taskStore.add({
      toolId: 'ai-audio',
      toolLabel: 'AI Audio',
      label: `Retry TTS ${errors.length} failed segments`,
    })
    for (let i = 0; i < errors.length; i += CONCURRENCY)
      await Promise.all(errors.slice(i, i + CONCURRENCY).map(s => generateOne(s)))
    setIsGenerating(false)
    taskStore.complete(taskId, 'done', `Retry complete for ${errors.length} segments`)
  }

  // ── Playback ──────────────────────────────────────────────────────────────
  const handlePlay = (seg: Segment) => {
    if (!seg.audioUrl) return
    if (playingId === seg.id) {
      audioRef.current?.pause(); setPlayingId(null); return
    }
    if (audioRef.current) {
      audioRef.current.pause()
      audioRef.current.src = seg.audioUrl
      audioRef.current.play()
      setPlayingId(seg.id)
      audioRef.current.onended = () => setPlayingId(null)
    }
  }

  const downloadSeg = async (seg: Segment, idx: number) => {
    if (!seg.audioUrl) return
    const blob = await fetch(seg.audioUrl).then(r => r.blob())
    const a = document.createElement('a')
    a.href = URL.createObjectURL(blob)
    a.download = `segment_${String(idx + 1).padStart(2, '0')}.mp3`
    a.click()
  }

  const downloadAll = async () => {
    const done = segments.filter(s => s.status === 'done' && s.audioUrl)
    for (let i = 0; i < done.length; i++) {
      await downloadSeg(done[i], segments.indexOf(done[i]))
      if (i < done.length - 1) await new Promise(r => setTimeout(r, 300))
    }
  }

  const deleteSeg = (id: string) => {
    if (playingId === id) { audioRef.current?.pause(); setPlayingId(null) }
    setSegments(prev => prev.filter(s => s.id !== id))
  }

  // ── Jump to segment ───────────────────────────────────────────────────────
  const handleJump = () => {
    const n = parseInt(jumpTo, 10)
    if (!n || n < 1 || n > segments.length) return
    const el = document.getElementById(`seg-${segments[n - 1].id}`)
    el?.scrollIntoView({ behavior: 'smooth', block: 'center' })
    setJumpTo('')
  }

  // ── Dialogue ──────────────────────────────────────────────────────────────
  const handleGenerateDialogue = async () => {
    const lines = parseDialogueLines(dlgScript)
    if (lines.length === 0) { setDlgError('The script is empty or has an invalid format'); return }
    const spkMap = new Map(speakers.map(s => [s.label, s.voiceId]))
    const inputs: DialogueItem[] = []
    for (const { label, text } of lines) {
      const vid = spkMap.get(label)
      if (!vid) { setDlgError(`Speaker "${label}" does not have a voice assigned`); return }
      inputs.push({ text, voice_id: vid })
    }
    setDlgGenerating(true); setDlgError(null); setDlgAudioUrl(null)
    const taskId = taskStore.add({
      toolId: 'ai-audio',
      toolLabel: 'AI Audio',
      label: 'Create dialogue audio',
    })
    try {
      const blob = await createDialogueAudio(inputs)
      setDlgAudioUrl(URL.createObjectURL(blob))
      taskStore.complete(taskId, 'done', 'Dialogue audio completed')
    } catch (err: unknown) {
      setDlgError((err as Error)?.message ?? 'Dialogue generation failed')
      taskStore.complete(taskId, 'error', (err as Error)?.message ?? 'Dialogue generation failed')
    } finally { setDlgGenerating(false) }
  }

  // ── Guard ─────────────────────────────────────────────────────────────────
  if (!child) return (
    <div className="flex items-center justify-center h-full text-surface-400 text-sm">
      Select a child project first.
    </div>
  )

  // ─────────────────────────────────────────────────────────────────────────
  return (
    <div className="flex flex-col h-full">
      <audio ref={audioRef} className="hidden" />

      {/* ── Header ────────────────────────────────────────────────────────── */}
      <div className="flex items-center justify-between px-6 py-4 border-b border-surface-200 bg-white">
        <div className="flex items-center gap-3">
          <div className="w-8 h-8 rounded-lg bg-violet-100 flex items-center justify-center">
            <Mic size={16} className="text-violet-600" />
          </div>
          <div>
            <h1 className="page-title">AI Audio</h1>
            <p className="page-sub">{child.title || child.name} · ElevenLabs TTS</p>
          </div>
        </div>
        {health && (
          <span className={cn(
            'px-2.5 py-1 rounded-full text-xs font-medium',
            health.elevenlabs === 'good'     ? 'bg-green-100 text-green-700' :
            health.elevenlabs === 'degraded' ? 'bg-amber-100 text-amber-700' :
                                              'bg-red-100 text-red-700',
          )}>
            ElevenLabs: {health.elevenlabs}
          </span>
        )}
      </div>

      {/* ── Tab bar ───────────────────────────────────────────────────────── */}
      <div className="flex border-b border-surface-200 bg-white px-6">
        {([
          { key: 'tts',      label: 'TTS Studio',     Icon: Volume2 },
          { key: 'dialogue', label: 'Dialogue Studio', Icon: MessageSquare },
        ] as const).map(({ key, label, Icon }) => (
          <button
            key={key}
            onClick={() => setTab(key)}
            className={cn(
              'flex items-center gap-1.5 px-4 py-2.5 text-sm font-medium border-b-2 -mb-px transition-colors',
              tab === key
                ? 'border-primary-600 text-primary-600'
                : 'border-transparent text-surface-500 hover:text-surface-700',
            )}
          >
            <Icon size={13} /> {label}
          </button>
        ))}
      </div>

      {/* ── Content ───────────────────────────────────────────────────────── */}
      <div className="flex-1 overflow-hidden flex">

        {/* ════════════════════════ TTS TAB ══════════════════════════════════ */}
        {tab === 'tts' && (
          <>
            {/* ── Left panel: config + script ────────────────────────────── */}
            <div className="w-80 shrink-0 border-r border-surface-200 flex flex-col overflow-hidden">
              <div className="flex-1 overflow-y-auto p-4 space-y-4">

                {/* Voice selector */}
                <div ref={voicePickerRef} className="relative">
                  <label className="label mb-1.5">Voice</label>

                  {/* Selected voice card */}
                  <button
                    className={cn(
                      'w-full flex items-center gap-3 p-3 rounded-xl border-2 transition-all text-left',
                      showVoicePicker
                        ? 'border-primary-400 bg-primary-50/30'
                        : 'border-surface-200 bg-white hover:border-surface-300',
                    )}
                    onClick={() => setShowVoicePicker(p => !p)}
                  >
                    {voicesLoading
                      ? <div className="w-8 h-8 rounded-full bg-surface-100 animate-pulse shrink-0" />
                      : selectedVoice
                        ? <VoiceAvatar name={selectedVoice.name} />
                        : <div className="w-8 h-8 rounded-full bg-surface-100 flex items-center justify-center shrink-0">
                            <Mic size={14} className="text-surface-400" />
                          </div>
                    }
                    <div className="flex-1 min-w-0">
                      <p className={cn('text-sm font-semibold truncate', selectedVoice ? 'text-surface-900' : 'text-surface-400')}>
                        {voicesLoading ? 'Loading...' : selectedVoice?.name ?? 'Select a voice'}
                      </p>
                      {selectedVoice?.category && (
                        <p className="text-xs text-surface-400">{selectedVoice.category}</p>
                      )}
                    </div>
                    <ChevronDown size={14} className={cn('shrink-0 text-surface-400 transition-transform', showVoicePicker && 'rotate-180')} />
                  </button>

                  {/* Dropdown */}
                  {showVoicePicker && (
                    <div className="absolute top-full left-0 right-0 z-50 mt-1 bg-white rounded-xl border border-surface-200 shadow-xl overflow-hidden">
                      <div className="p-2.5 border-b border-surface-100">
                        <input
                          className="input text-sm w-full"
                          placeholder="Search voices..."
                          value={voiceSearch}
                          onChange={e => setVoiceSearch(e.target.value)}
                          autoFocus
                        />
                      </div>
                      <div className="max-h-56 overflow-y-auto">
                        {filteredVoices.length === 0 && (
                          <p className="text-xs text-surface-400 px-4 py-3">No matches found</p>
                        )}
                        {filteredVoices.map(v => (
                          <button
                            key={v.voice_id}
                            className={cn(
                              'w-full flex items-center gap-2.5 px-3 py-2 text-sm text-left hover:bg-surface-50 transition-colors',
                              v.voice_id === voiceId && 'bg-primary-50',
                            )}
                            onClick={() => { setVoiceId(v.voice_id); setShowVoicePicker(false); setVoiceSearch('') }}
                          >
                            <VoiceAvatar name={v.name} />
                            <div className="min-w-0">
                              <p className={cn('font-medium truncate', v.voice_id === voiceId ? 'text-primary-700' : 'text-surface-800')}>
                                {v.name}
                              </p>
                              {v.category && (
                                <span className="text-xs text-surface-400">{v.category}</span>
                              )}
                            </div>
                            {v.voice_id === voiceId && (
                              <CheckCircle size={14} className="text-primary-500 shrink-0 ml-auto" />
                            )}
                          </button>
                        ))}
                      </div>
                    </div>
                  )}
                </div>

                {/* Model selector */}
                <div>
                  <label className="label mb-1.5">Model</label>
                  <div className="relative">
                    <select
                      className="w-full appearance-none bg-white border-2 border-surface-200 hover:border-surface-300 focus:border-primary-400 focus:ring-2 focus:ring-primary-100 rounded-xl px-3 py-2.5 text-sm text-surface-800 font-medium outline-none transition-all cursor-pointer pr-8"
                      value={modelId}
                      onChange={e => setModelId(e.target.value)}
                    >
                      {models.length === 0 && (
                        <option value="eleven_multilingual_v2">eleven_multilingual_v2</option>
                      )}
                      {models.map(m => (
                        <option key={m.model_id} value={m.model_id}>{m.name}</option>
                      ))}
                    </select>
                    <ChevronDown size={13} className="absolute right-3 top-1/2 -translate-y-1/2 text-surface-400 pointer-events-none" />
                  </div>
                </div>

                {/* Voice settings — always visible */}
                <div className="space-y-3 pt-1">
                  <p className="text-xs font-semibold text-surface-500 uppercase tracking-wide">Voice Settings</p>
                  {SLIDER_FIELDS.map(({ key, label, color }) => (
                    <SliderSetting
                      key={key}
                      label={label}
                      color={color}
                      value={settings[key] ?? 0}
                      onChange={v => setSettings(prev => ({ ...prev, [key]: v }))}
                    />
                  ))}
                </div>

                {/* Divider */}
                <div className="border-t border-surface-100" />

                {/* Script input */}
                <div>
                  <div className="flex items-center justify-between mb-1.5">
                    <label className="label">Script</label>
                    <span className="text-xs text-surface-400">
                      {segments.length} lines
                    </span>
                  </div>
                  <div className="relative rounded-lg border border-surface-200 overflow-hidden focus-within:border-primary-400 focus-within:ring-2 focus-within:ring-primary-100">
                    {/* Line numbers */}
                    <div className="flex">
                      <div
                        className="shrink-0 w-8 bg-surface-50 border-r border-surface-100 text-right pr-2 py-2.5 select-none pointer-events-none"
                        aria-hidden
                      >
                        {(script.split('\n').length > 0 ? script.split('\n') : ['']).map((_, i) => (
                          <div key={i} className="text-xs leading-5 text-surface-300 font-mono">{i + 1}</div>
                        ))}
                      </div>
                      <textarea
                        className="flex-1 resize-none text-xs font-mono leading-5 p-2.5 bg-white outline-none text-surface-800 placeholder:text-surface-300"
                        rows={12}
                        placeholder={"First line\nSecond line\n..."}
                        value={script}
                        onChange={e => setScript(e.target.value)}
                        spellCheck={false}
                      />
                    </div>
                  </div>
                  <p className="text-xs text-surface-400 mt-1">Each line becomes one audio segment</p>
                </div>
              </div>

              {/* Generate buttons */}
              <div className="p-4 border-t border-surface-200 space-y-2">
                {errCount > 0 && (
                  <button
                    className="w-full btn-secondary text-xs justify-center"
                    onClick={handleRegenerateErrors}
                    disabled={isGenerating || !voiceId}
                  >
                    <RefreshCw size={12} className={isGenerating ? 'animate-spin' : ''} />
                    Regenerate Errors ({errCount})
                  </button>
                )}
                <button
                  className="w-full btn-primary justify-center"
                  onClick={handleGenerateAll}
                  disabled={isGenerating || !voiceId || segments.length === 0}
                >
                  {isGenerating
                    ? <><Loader size={14} className="animate-spin" /> Generating...</>
                    : <><Zap size={14} /> Generate All ({segments.filter(s => s.status !== 'done').length})</>}
                </button>
              </div>
            </div>

            {/* ── Right panel: segments preview ──────────────────────────── */}
            <div className="flex-1 flex flex-col overflow-hidden">
              {/* Stats bar */}
              <div className="flex items-center gap-3 px-5 py-3 border-b border-surface-200 bg-white">
                <div>
                  <p className="text-sm font-semibold text-surface-900">Segments Preview</p>
                  <p className="text-xs text-surface-400">Editing project: <span className="font-medium text-surface-600">{child.title || child.name}</span></p>
                </div>

                <div className="flex items-center gap-2 ml-auto">
                  {/* Stats badges */}
                  <span className="flex items-center gap-1.5 px-2.5 py-1 rounded-full bg-surface-100 text-xs font-medium text-surface-600">
                    Total: {totalCount}
                  </span>
                  {doneCount > 0 && (
                    <span className="flex items-center gap-1.5 px-2.5 py-1 rounded-full bg-green-100 text-xs font-medium text-green-700">
                      Done: {doneCount}
                    </span>
                  )}
                  {errCount > 0 && (
                    <span className="flex items-center gap-1.5 px-2.5 py-1 rounded-full bg-red-100 text-xs font-medium text-red-700">
                      Errors: {errCount}
                    </span>
                  )}
                </div>

                {/* Filter + jump */}
                <div className="flex items-center gap-1.5">
                  <button
                    onClick={() => setFilter('all')}
                    className={cn(
                      'px-3 py-1 text-xs font-medium rounded-lg transition-colors',
                      filter === 'all' ? 'bg-surface-900 text-white' : 'text-surface-600 hover:bg-surface-100',
                    )}
                  >All</button>
                  <button
                    onClick={() => setFilter('issues')}
                    className={cn(
                      'px-3 py-1 text-xs font-medium rounded-lg transition-colors',
                      filter === 'issues' ? 'bg-surface-900 text-white' : 'text-surface-600 hover:bg-surface-100',
                    )}
                  >Issues</button>
                  <div className="flex items-center gap-1 border border-surface-200 rounded-lg overflow-hidden">
                    <input
                      type="number"
                      className="w-14 px-2 py-1 text-xs text-surface-700 outline-none"
                      placeholder="#"
                      value={jumpTo}
                      onChange={e => setJumpTo(e.target.value)}
                      onKeyDown={e => e.key === 'Enter' && handleJump()}
                      min={1} max={segments.length}
                    />
                    <button
                      className="px-2 py-1 text-xs font-medium text-surface-600 hover:bg-surface-100 border-l border-surface-200"
                      onClick={handleJump}
                    >Jump</button>
                  </div>
                </div>

                {doneCount > 0 && (
                  <button className="btn-secondary text-xs shrink-0" onClick={downloadAll}>
                    <Download size={12} /> Download All
                  </button>
                )}
              </div>

              {/* Segment cards */}
              <div className="flex-1 overflow-y-auto p-4 space-y-2">
                {segments.length === 0 ? (
                  <div className="flex flex-col items-center justify-center h-full text-surface-400">
                    <Mic size={32} className="mb-3 opacity-20" />
                    <p className="text-sm font-medium">Enter a script on the left to begin</p>
                    <p className="text-xs mt-1 opacity-70">Each line will automatically become one segment</p>
                  </div>
                ) : (
                  visibleSegs.map((seg) => {
                    const idx = segments.indexOf(seg)
                    return (
                      <div key={seg.id} id={`seg-${seg.id}`}>
                        <SegmentCard
                          seg={seg}
                          index={idx}
                          playing={playingId === seg.id}
                          isGenerating={isGenerating}
                          voiceId={voiceId}
                          onPlay={() => handlePlay(seg)}
                          onDownload={() => downloadSeg(seg, idx)}
                          onRegenerate={() => generateOne(seg)}
                          onDelete={() => deleteSeg(seg.id)}
                        />
                      </div>
                    )
                  })
                )}
                {filter === 'issues' && visibleSegs.length === 0 && (
                  <div className="flex flex-col items-center justify-center h-40 text-surface-400">
                    <CheckCircle size={28} className="mb-2 text-green-400" />
                    <p className="text-sm">No failed segments</p>
                  </div>
                )}
              </div>
            </div>
          </>
        )}

        {/* ════════════════════ DIALOGUE TAB ═══════════════════════════════ */}
        {tab === 'dialogue' && (
          <>
            {/* ── Left panel: script ─────────────────────────────────────── */}
            <div className="w-80 shrink-0 border-r border-surface-200 flex flex-col overflow-hidden">
              <div className="flex-1 overflow-y-auto p-4 space-y-3">
                <div className="flex items-center justify-between">
                  <label className="label">Dialogue Script</label>
                  <span className="text-xs font-mono bg-surface-100 text-surface-500 px-2 py-0.5 rounded">
                    A&gt; text
                  </span>
                </div>
                <div className="relative rounded-lg border border-surface-200 overflow-hidden focus-within:border-primary-400 focus-within:ring-2 focus-within:ring-primary-100">
                  <div className="flex">
                    <div
                      className="shrink-0 w-8 bg-surface-50 border-r border-surface-100 text-right pr-2 py-2.5 select-none pointer-events-none"
                      aria-hidden
                    >
                      {(dlgScript.split('\n').length > 0 ? dlgScript.split('\n') : ['']).map((_, i) => (
                        <div key={i} className="text-xs leading-5 text-surface-300 font-mono">{i + 1}</div>
                      ))}
                    </div>
                    <textarea
                      className="flex-1 resize-none text-xs font-mono leading-5 p-2.5 bg-white outline-none text-surface-800 placeholder:text-surface-300"
                      rows={16}
                      placeholder={"A> Hello!\nB> Hi there.\nA> How are you?"}
                      value={dlgScript}
                      onChange={e => setDlgScript(e.target.value)}
                      spellCheck={false}
                    />
                  </div>
                </div>
                <p className="text-xs text-surface-400">{parseDialogueLines(dlgScript).length} lines · Format: <code className="font-mono">A&gt; text</code></p>
              </div>
            </div>

            {/* ── Right panel: speakers + settings ───────────────────────── */}
            <div className="flex-1 flex flex-col overflow-hidden">
              <div className="flex-1 overflow-y-auto p-4 space-y-3">

                {/* Speaker cards */}
                <p className="text-xs font-semibold text-surface-500 uppercase tracking-wide px-1">Speakers</p>

                {speakers.map(spk => {
                  const spkVoice = voices.find(v => v.voice_id === spk.voiceId)
                  return (
                    <div
                      key={spk.id}
                      className="rounded-xl border border-surface-200 bg-white overflow-hidden"
                    >
                      {/* Card header */}
                      <button
                        className="w-full flex items-center gap-3 px-4 py-3 hover:bg-surface-50 transition-colors text-left"
                        onClick={() => setSpeakers(prev => prev.map(s =>
                          s.id === spk.id ? { ...s, isExpanded: !s.isExpanded } : s
                        ))}
                      >
                        <span className="w-7 h-7 rounded-full bg-gradient-to-br from-primary-400 to-primary-600 text-white text-xs font-bold flex items-center justify-center shrink-0">
                          {spk.label}
                        </span>
                        <div className="flex-1 min-w-0">
                          <p className="text-sm font-medium text-surface-800">Speaker {spk.label}</p>
                          <p className="text-xs text-surface-400 truncate">
                            {spkVoice ? spkVoice.name : 'No voice selected'}
                          </p>
                        </div>
                        {spk.isExpanded
                          ? <ChevronUp size={14} className="shrink-0 text-surface-400" />
                          : <ChevronRight size={14} className="shrink-0 text-surface-400" />
                        }
                        {speakers.length > 2 && (
                          <button
                            className="btn-icon text-surface-300 hover:text-red-500 hover:bg-red-50 ml-1"
                            onClick={e => {
                              e.stopPropagation()
                              setSpeakers(prev => prev.filter(s => s.id !== spk.id))
                            }}
                          >
                            <XCircle size={13} />
                          </button>
                        )}
                      </button>

                      {/* Expanded body */}
                      {spk.isExpanded && (
                        <div className="px-4 pb-4 space-y-3 border-t border-surface-100">
                          <div className="pt-3 space-y-3">

                            {/* Voice picker */}
                            <div ref={dlgOpenPickerId === spk.id ? dlgPickerRef : undefined} className="relative">
                              <label className="label mb-1.5">Voice</label>
                              {(() => {
                                const sv = voices.find(v => v.voice_id === spk.voiceId)
                                const isOpen = dlgOpenPickerId === spk.id
                                const filtered = dlgVoiceSearch
                                  ? voices.filter(v => v.name.toLowerCase().includes(dlgVoiceSearch.toLowerCase()))
                                  : voices
                                return (
                                  <>
                                    <button
                                      className={cn(
                                        'w-full flex items-center gap-2.5 p-2.5 rounded-xl border-2 transition-all text-left',
                                        isOpen
                                          ? 'border-primary-400 bg-primary-50/30'
                                          : 'border-surface-200 bg-white hover:border-surface-300',
                                      )}
                                      onClick={() => {
                                        setDlgOpenPickerId(isOpen ? null : spk.id)
                                        setDlgVoiceSearch('')
                                      }}
                                    >
                                      {sv
                                        ? <VoiceAvatar name={sv.name} />
                                        : <div className="w-8 h-8 rounded-full bg-surface-100 flex items-center justify-center shrink-0">
                                            <Mic size={13} className="text-surface-400" />
                                          </div>
                                      }
                                      <div className="flex-1 min-w-0">
                                        <p className={cn('text-sm font-semibold truncate', sv ? 'text-surface-900' : 'text-surface-400')}>
                                          {sv?.name ?? 'Select a voice'}
                                        </p>
                                        {sv?.category && <p className="text-xs text-surface-400">{sv.category}</p>}
                                      </div>
                                      <ChevronDown size={13} className={cn('shrink-0 text-surface-400 transition-transform', isOpen && 'rotate-180')} />
                                    </button>

                                    {isOpen && (
                                      <div className="absolute top-full left-0 right-0 z-50 mt-1 bg-white rounded-xl border border-surface-200 shadow-xl overflow-hidden">
                                        <div className="p-2 border-b border-surface-100">
                                          <input
                                            className="input text-sm w-full"
                                            placeholder="Search voices..."
                                            value={dlgVoiceSearch}
                                            onChange={e => setDlgVoiceSearch(e.target.value)}
                                            autoFocus
                                          />
                                        </div>
                                        <div className="max-h-48 overflow-y-auto">
                                          {filtered.length === 0 && (
                                            <p className="text-xs text-surface-400 px-4 py-3">No matches found</p>
                                          )}
                                          {filtered.map(v => (
                                            <button
                                              key={v.voice_id}
                                              className={cn(
                                                'w-full flex items-center gap-2.5 px-3 py-2 text-sm text-left hover:bg-surface-50 transition-colors',
                                                v.voice_id === spk.voiceId && 'bg-primary-50',
                                              )}
                                              onClick={() => {
                                                setSpeakers(prev => prev.map(s =>
                                                  s.id === spk.id ? { ...s, voiceId: v.voice_id } : s
                                                ))
                                                setDlgOpenPickerId(null)
                                                setDlgVoiceSearch('')
                                              }}
                                            >
                                              <VoiceAvatar name={v.name} />
                                              <div className="min-w-0">
                                                <p className={cn('font-medium truncate text-sm', v.voice_id === spk.voiceId ? 'text-primary-700' : 'text-surface-800')}>
                                                  {v.name}
                                                </p>
                                                {v.category && <span className="text-xs text-surface-400">{v.category}</span>}
                                              </div>
                                              {v.voice_id === spk.voiceId && (
                                                <CheckCircle size={13} className="text-primary-500 shrink-0 ml-auto" />
                                              )}
                                            </button>
                                          ))}
                                        </div>
                                      </div>
                                    )}
                                  </>
                                )
                              })()}
                            </div>

                            {/* Model picker */}
                            <div>
                              <label className="label mb-1.5">Model</label>
                              <div className="relative">
                                <select
                                  className="w-full appearance-none bg-white border-2 border-surface-200 hover:border-surface-300 focus:border-primary-400 focus:ring-2 focus:ring-primary-100 rounded-xl px-3 py-2.5 text-sm text-surface-800 font-medium outline-none transition-all cursor-pointer pr-8"
                                  value={spk.modelId}
                                  onChange={e => setSpeakers(prev => prev.map(s =>
                                    s.id === spk.id ? { ...s, modelId: e.target.value } : s
                                  ))}
                                >
                                  {models.length === 0 && (
                                    <option value="eleven_multilingual_v2">eleven_multilingual_v2</option>
                                  )}
                                  {models.map(m => (
                                    <option key={m.model_id} value={m.model_id}>{m.name}</option>
                                  ))}
                                </select>
                                <ChevronDown size={13} className="absolute right-3 top-1/2 -translate-y-1/2 text-surface-400 pointer-events-none" />
                              </div>
                            </div>

                            {/* Stability slider */}
                            <SliderSetting
                              label="Stability"
                              color="from-blue-400 to-blue-600"
                              value={spk.stability}
                              onChange={v => setSpeakers(prev => prev.map(s =>
                                s.id === spk.id ? { ...s, stability: v } : s
                              ))}
                            />
                          </div>
                        </div>
                      )}
                    </div>
                  )
                })}

                {/* Add speaker */}
                <button
                  className="w-full flex items-center justify-center gap-2 px-4 py-2.5 rounded-xl border border-dashed border-surface-300 text-sm text-surface-500 hover:border-primary-400 hover:text-primary-600 hover:bg-primary-50/30 transition-colors"
                  onClick={() => setSpeakers(prev => [
                    ...prev,
                    {
                      id: uid(),
                      label: String.fromCharCode(65 + prev.length),
                      voiceId: '',
                      modelId: 'eleven_multilingual_v2',
                      stability: 0.5,
                      isExpanded: true,
                      voicePickerOpen: false,
                    },
                  ])}
                  disabled={speakers.length >= 10}
                >
                  <Plus size={14} /> Add speaker
                </button>

                {/* Divider */}
                <div className="border-t border-surface-100 pt-1">
                  <p className="text-xs font-semibold text-surface-500 uppercase tracking-wide px-1 mb-3">Settings</p>

                  {/* Delay slider */}
                  <div className="space-y-1.5 mb-3">
                    <div className="flex items-center justify-between">
                      <span className="text-xs font-medium text-surface-600">Delay between segments</span>
                      <span className="text-xs font-semibold text-surface-900 tabular-nums">{dlgDelay.toFixed(1)}s</span>
                    </div>
                    <div className="relative h-2 bg-surface-100 rounded-full overflow-hidden">
                      <div
                        className="absolute left-0 top-0 h-full rounded-full bg-gradient-to-r from-indigo-400 to-indigo-600"
                        style={{ width: `${(dlgDelay / 3) * 100}%` }}
                      />
                      <input
                        type="range" min={0} max={3} step={0.1}
                        value={dlgDelay}
                        onChange={e => setDlgDelay(parseFloat(e.target.value))}
                        className="absolute inset-0 w-full h-full opacity-0 cursor-pointer"
                      />
                    </div>
                  </div>

                  {/* Loudness normalization toggle */}
                  <button
                    className="w-full flex items-center justify-between px-3 py-2.5 rounded-xl border border-surface-200 bg-white hover:bg-surface-50 transition-colors mb-2"
                    onClick={() => setDlgLoudness(p => !p)}
                  >
                    <div>
                      <p className="text-sm font-medium text-surface-800 text-left">Loudness normalization</p>
                      <p className="text-xs text-surface-400">Beta</p>
                    </div>
                    <div className={cn(
                      'w-9 h-5 rounded-full transition-colors relative shrink-0',
                      dlgLoudness ? 'bg-primary-500' : 'bg-surface-200',
                    )}>
                      <div className={cn(
                        'absolute top-0.5 w-4 h-4 bg-white rounded-full shadow transition-transform',
                        dlgLoudness ? 'translate-x-4' : 'translate-x-0.5',
                      )} />
                    </div>
                  </button>

                  {/* Export transcript toggle */}
                  <button
                    className="w-full flex items-center justify-between px-3 py-2.5 rounded-xl border border-surface-200 bg-white hover:bg-surface-50 transition-colors"
                    onClick={() => setDlgTranscript(p => !p)}
                  >
                    <p className="text-sm font-medium text-surface-800">Export transcript</p>
                    <div className={cn(
                      'w-9 h-5 rounded-full transition-colors relative shrink-0',
                      dlgTranscript ? 'bg-primary-500' : 'bg-surface-200',
                    )}>
                      <div className={cn(
                        'absolute top-0.5 w-4 h-4 bg-white rounded-full shadow transition-transform',
                        dlgTranscript ? 'translate-x-4' : 'translate-x-0.5',
                      )} />
                    </div>
                  </button>
                </div>

                {/* Error */}
                {dlgError && (
                  <div className="flex items-center gap-2 rounded-xl px-4 py-3 bg-red-50 border border-red-200 text-sm text-red-700">
                    <AlertTriangle size={14} className="shrink-0" />
                    {dlgError}
                  </div>
                )}

                {/* Result audio */}
                {dlgAudioUrl && (
                  <div className="rounded-xl border border-green-200 bg-green-50/30 p-4 space-y-2">
                    <p className="text-sm font-semibold text-surface-700">Results</p>
                    <audio controls src={dlgAudioUrl} className="w-full" />
                    <div className="flex justify-end">
                      <a href={dlgAudioUrl} download="dialogue.mp3" className="btn-secondary text-xs">
                        <Download size={11} /> Download
                      </a>
                    </div>
                  </div>
                )}
              </div>

              {/* Generate button */}
              <div className="p-4 border-t border-surface-200">
                <button
                  className="w-full btn-primary justify-center"
                  onClick={handleGenerateDialogue}
                  disabled={dlgGenerating || !dlgScript.trim()}
                >
                  {dlgGenerating
                    ? <><Loader size={14} className="animate-spin" /> Generating...</>
                    : <><Zap size={14} /> Generate Dialogue</>}
                </button>
              </div>
            </div>
          </>
        )}
      </div>
    </div>
  )
}
