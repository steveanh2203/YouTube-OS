import { useState, useEffect, useRef, useCallback, useMemo } from 'react'
import { useExtensionSocket } from '@/hooks/useExtensionSocket'
import { save as tauriSave } from '@tauri-apps/plugin-dialog'
import { toast } from '@/store/toast.store'
import { useAppStore } from '@/store/app.store'
import { usePanelContext } from '@/contexts/PanelContext'
import {
  Mic, Play, Square, Download, RefreshCw, ChevronDown, ChevronUp, ChevronRight, Plus,
  CheckCircle, XCircle, Loader, Zap, MessageSquare,
  Volume2, AlertTriangle, Trash2, Heart, Search, X, Copy,
} from 'lucide-react'
import { cn } from '@/lib/utils'
import { taskStore } from '@/store/task.store'
import {
  listVoices, listSharedVoices, listModels, generateTtsAudio, createDialogueAudio, getHealth, saveAudioZipFile,
  type AudioProvider, type VoiceOption, type ModelOption, type ELVoiceSettings,
  type MiniMaxVoiceSettings, type DialogueItem, type HealthStatus,
} from '@/lib/audioService'

// ─── Types ────────────────────────────────────────────────────────────────────

type SegStatus = 'idle' | 'pending' | 'generating' | 'done' | 'error'
type Tab = 'tts' | 'dialogue'
type ElevenVoiceTab = 'default' | 'mine' | 'favorites' | 'custom'
type MiniMaxVoiceTab = 'all' | 'mine'

interface Segment {
  id: string
  text: string
  status: SegStatus
  taskId?: string
  audioUrl?: string
  error?: string
}

interface BatchProgress {
  total: number
  completed: number
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

const AUDIO_BATCH_THREADS = 7

const DEFAULT_SETTINGS: ELVoiceSettings = {
  stability: 0.5,
  similarity_boost: 0.75,
  style: 0.0,
  use_speaker_boost: true,
}

const DEFAULT_MINIMAX_SETTINGS: MiniMaxVoiceSettings = {
  speed: 1,
  vol: 1,
  pitch: 0,
  emotion: 'neutral',
}

const DEFAULT_ELEVEN_MODEL = 'eleven_multilingual_v2'
const DEFAULT_MINIMAX_MODEL = 'speech-02-hd'

// Hardcoded fallback — proxy /v1m/common/config không phải lúc nào cũng trả đủ models
const KNOWN_MINIMAX_MODELS: import('@/lib/audioService').ModelOption[] = [
  { model_id: 'speech-02-hd',      name: 'Speech 2.8 HD Preview',     description: 'Latest · highest quality', provider: 'minimax' },
  { model_id: 'speech-02-turbo',   name: 'Speech 2.8 Turbo',          description: 'Latest · fast',            provider: 'minimax' },
  { model_id: 'speech-01-hd',      name: 'Speech 2.5 HD Preview',     description: 'HD quality',               provider: 'minimax' },
  { model_id: 'speech-01-turbo',   name: 'Speech 2.5 Turbo',          description: 'Fast',                     provider: 'minimax' },
]
const ELEVEN_FAVORITES_KEY = 'autocapcut_eleven_voice_favorites'
const ELEVEN_CUSTOM_VOICES_KEY = 'autocapcut_eleven_custom_voices'

const TTS_LANGUAGE_OPTIONS = [
  { value: 'auto', label: 'Auto detect', badge: 'Recommended' },
  { value: 'en', label: 'English', badge: undefined },
  { value: 'vi', label: 'Vietnamese', badge: undefined },
] as const

// ─── Helpers ──────────────────────────────────────────────────────────────────

const uid = () => Math.random().toString(36).slice(2, 9)

const segmentFileName = (idx: number) => `audio_${String(idx + 1).padStart(3, '0')}.mp3`

const archiveFileName = (label?: string) => {
  const base = (label || 'ai-audio')
    .trim()
    .split('')
    .filter((char) => char >= ' ' && char !== '\u007f')
    .join('')
    .replace(/[<>:"/\\|?*]+/g, '-')
    .replace(/\s+/g, '-')
    .replace(/-+/g, '-')
    .replace(/^-|-$/g, '')

  return `${base || 'ai-audio'}-audio.zip`
}

function formatVoiceMeta(value?: string) {
  if (!value) return ''
  return value
    .replace(/_/g, ' ')
    .replace(/\b\w/g, (char) => char.toUpperCase())
}

function formatModelName(value?: string) {
  if (!value) return ''

  const knownTokens: Record<string, string> = {
    ai: 'AI',
    api: 'API',
    hd: 'HD',
    sd: 'SD',
    tts: 'TTS',
    v1: 'V1',
    v2: 'V2',
    v3: 'V3',
  }

  return value
    .split(/[-_]+/)
    .filter(Boolean)
    .map((token) => {
      if (/^\d+(\.\d+)?$/.test(token)) return token
      return knownTokens[token.toLowerCase()] ?? `${token.charAt(0).toUpperCase()}${token.slice(1)}`
    })
    .join(' ')
}

function describeStability(value: number) {
  if (value < 0.34) return 'Creative'
  if (value < 0.67) return 'Balanced'
  return 'Robust'
}

function describeSimilarity(value: number) {
  if (value < 0.34) return 'Flexible'
  if (value < 0.67) return 'Natural'
  return 'Matched'
}

function describeStyle(value: number) {
  if (value < 0.2) return 'Neutral'
  if (value < 0.6) return 'Stylized'
  return 'Expressive'
}

function getVoiceTags(voice: VoiceOption) {
  if (voice.tags?.length) return voice.tags.slice(0, 3)
  return [
    formatVoiceMeta(voice.labels?.accent),
    formatVoiceMeta(voice.labels?.gender),
    formatVoiceMeta(voice.labels?.age),
  ].filter(Boolean).slice(0, 3)
}

function isMiniMaxPersonalVoice(voice: VoiceOption) {
  return voice.provider === 'minimax' && (
    voice.voice_type === 'voice_cloning' ||
    voice.voice_type === 'voice_generation' ||
    voice.source === 'my-voice'
  )
}

function matchesVoiceSearch(voice: VoiceOption, keyword: string) {
  if (!keyword.trim()) return true
  const normalized = keyword.trim().toLowerCase()
  const haystack = [
    voice.voice_id,
    voice.name,
    voice.description,
    voice.category,
    voice.source,
    voice.labels?.accent,
    voice.labels?.gender,
    voice.labels?.age,
    voice.labels?.descriptive,
    voice.labels?.language,
    voice.labels?.locale,
    ...(voice.tags ?? []),
  ]
    .filter(Boolean)
    .join(' ')
    .toLowerCase()

  return haystack.includes(normalized)
}

function loadFavoriteVoices() {
  try {
    const raw = localStorage.getItem(ELEVEN_FAVORITES_KEY)
    if (!raw) return []
    const parsed = JSON.parse(raw)
    return Array.isArray(parsed) ? parsed.filter((item): item is string => typeof item === 'string') : []
  } catch {
    return []
  }
}

function loadCustomVoices(): Array<{ voice_id: string; name: string }> {
  try {
    const raw = localStorage.getItem(ELEVEN_CUSTOM_VOICES_KEY)
    if (!raw) return []
    const parsed = JSON.parse(raw)
    if (!Array.isArray(parsed)) return []
    return parsed.filter((item): item is { voice_id: string; name: string } => (
      Boolean(item) &&
      typeof item.voice_id === 'string' &&
      typeof item.name === 'string'
    ))
  } catch {
    return []
  }
}

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
    <div className="flex h-10 w-10 shrink-0 select-none items-center justify-center rounded-2xl border border-violet-100 bg-violet-50 text-sm font-semibold text-violet-700">
      {name.charAt(0).toUpperCase()}
    </div>
  )
}

function VoiceTag({ label }: { label: string }) {
  return (
    <span className="inline-flex items-center rounded-full border border-surface-200 bg-white px-2.5 py-1 text-xs font-medium text-surface-600">
      {label}
    </span>
  )
}

function ElevenVoiceCard({
  voice,
  selected,
  favorite,
  previewing,
  onUse,
  onToggleFavorite,
  onTogglePreview,
}: {
  voice: VoiceOption
  selected: boolean
  favorite: boolean
  previewing: boolean
  onUse: () => void
  onToggleFavorite: () => void
  onTogglePreview: () => void
}) {
  const tags = getVoiceTags(voice).slice(0, 3)
  const summary = voice.description?.trim() || voice.category || tags.join(' · ') || 'Voice profile'
  const sourceLabel = voice.source === 'custom'
    ? 'Custom'
    : voice.source === 'my-voice'
      ? 'My Voice'
      : voice.source === 'shared'
        ? 'Shared'
        : 'Premade'

  return (
    <div
      className={cn(
        'group flex min-h-[188px] flex-col rounded-xl border bg-white p-3.5 transition-colors',
        selected
          ? 'border-primary-300 bg-primary-50/30'
          : 'border-surface-200 hover:border-surface-300 hover:bg-surface-50/40',
      )}
    >
      <div className="min-w-0">
        <h3 className="line-clamp-1 text-[15px] font-semibold leading-5 text-surface-900">
          {voice.name}
        </h3>
        <p className="mt-1.5 line-clamp-2 text-[13px] leading-6 text-surface-500">
          {summary}
        </p>
      </div>

      <div className="mt-3.5 flex min-h-6 flex-wrap gap-1.5">
        {(tags.length > 0 ? tags.slice(0, 3) : [voice.category || sourceLabel]).map((tag) => (
          <VoiceTag key={`${voice.voice_id}-${tag}`} label={tag} />
        ))}
      </div>

      <div className="mt-auto pt-4">
        <div className="flex items-center justify-between gap-3 border-t border-surface-100 pt-2.5">
        <div className="flex items-center gap-2">
          <button
            type="button"
            className={cn(
              'btn-icon h-7 w-7 rounded-lg',
              favorite
                ? 'bg-rose-50 text-rose-500 hover:bg-rose-100'
                : 'text-surface-400 hover:bg-surface-100 hover:text-surface-700',
            )}
            onClick={onToggleFavorite}
            title={favorite ? 'Remove favorite' : 'Save favorite'}
          >
            <Heart size={14} className={favorite ? 'fill-current' : ''} />
          </button>

          <button
            type="button"
            className="btn-icon h-7 w-7 rounded-lg text-surface-400 hover:bg-surface-100 hover:text-surface-700"
            onClick={() => {
              navigator.clipboard.writeText(voice.voice_id)
                .then(() => toast.success('Đã copy', 'Voice ID đã được copy'))
                .catch(() => toast.error('Copy lỗi', 'Không copy được Voice ID'))
            }}
            title="Copy voice ID"
          >
            <Copy size={14} />
          </button>

          <button
            type="button"
            className="btn-icon h-7 w-7 rounded-lg text-surface-400 hover:bg-surface-100 hover:text-primary-600"
            onClick={onTogglePreview}
            disabled={!voice.preview_url}
            title={previewing ? 'Stop preview' : 'Play preview'}
          >
            {previewing ? <Square size={14} /> : <Play size={14} />}
          </button>
        </div>

        <button
          type="button"
          className={cn(
            'h-9 rounded-lg px-3.5 text-sm font-semibold transition-colors',
            selected
              ? 'bg-primary-600 text-white hover:bg-primary-700'
              : 'border border-surface-200 bg-white text-surface-700 hover:border-primary-200 hover:text-primary-700',
          )}
          onClick={onUse}
        >
          {selected ? 'Using' : 'Use'}
        </button>
        </div>
      </div>
    </div>
  )
}

function SliderSetting({
  label, color, value, onChange, min = 0, max = 1, step = 0.01, formatValue, helperText,
}: {
  label: string
  color: string
  value: number
  onChange: (v: number) => void
  min?: number
  max?: number
  step?: number
  formatValue?: (v: number) => string
  helperText?: string
}) {
  const pct = ((value - min) / (max - min)) * 100
  const displayValue = formatValue ? formatValue(value) : `${Math.round(value * 100)}%`
  const knobLeft = pct <= 0
    ? '0px'
    : pct >= 100
      ? 'calc(100% - 14px)'
      : `calc(${pct}% - 7px)`

  return (
    <div className="rounded-xl border border-surface-200 bg-white px-3 py-2.5">
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0">
          <p className="text-sm font-medium text-surface-800">{label}</p>
          {helperText && (
            <p className="mt-0.5 text-[11px] text-surface-400">{helperText}</p>
          )}
        </div>
        <span className="shrink-0 rounded-full border border-surface-200 bg-surface-50 px-2 py-0.5 text-[11px] font-medium text-surface-600 tabular-nums">
          {displayValue}
        </span>
      </div>

      <div className="mt-2">
        <div className="relative h-4">
          <div className="absolute inset-x-0 top-1/2 h-2 -translate-y-1/2 rounded-full bg-surface-100 overflow-hidden" />
          <div
            className={cn('absolute left-0 top-1/2 h-2 -translate-y-1/2 rounded-full bg-gradient-to-r', color)}
            style={{ width: `${pct}%` }}
          />
          <div
            className="absolute top-1/2 h-3.5 w-3.5 -translate-y-1/2 rounded-full border-2 border-white bg-surface-700 shadow-[0_2px_8px_rgba(15,23,42,0.14)] transition-[left] duration-200"
            style={{ left: knobLeft }}
          />
          <input
            type="range" min={min} max={max} step={step}
            value={value}
            onChange={e => onChange(parseFloat(e.target.value))}
            className="absolute inset-0 h-full w-full cursor-pointer appearance-none bg-transparent opacity-0"
          />
        </div>
        <div
          className="mt-1 flex items-center justify-between text-[10px] font-medium text-surface-400 tabular-nums"
        >
          <span>{formatValue ? formatValue(min) : `${Math.round(min * 100)}%`}</span>
          <span>{formatValue ? formatValue(max) : `${Math.round(max * 100)}%`}</span>
        </div>
      </div>
    </div>
  )
}

function ElevenToggleRow({
  label,
  badge,
  checked,
  onToggle,
}: {
  label: string
  badge?: string
  checked: boolean
  onToggle: () => void
}) {
  return (
    <button
      type="button"
      className="flex w-full items-center gap-3 px-3 py-3 text-left transition-colors hover:bg-surface-50"
      onClick={onToggle}
    >
      <span className={cn(
        'relative h-6 w-10 shrink-0 rounded-full transition-colors',
        checked ? 'bg-primary-600' : 'bg-surface-200',
      )}>
        <span className={cn(
          'absolute top-1/2 h-4 w-4 -translate-y-1/2 rounded-full bg-white shadow-sm transition-all',
          checked ? 'left-5' : 'left-1',
        )} />
      </span>

      <span className="flex min-w-0 items-center gap-2">
        <span className="border-b border-dashed border-surface-300 text-sm font-medium text-surface-800">
          {label}
        </span>
        {badge && (
          <span className="rounded-full border border-surface-200 bg-surface-50 px-2 py-0.5 text-[11px] font-medium text-surface-600">
            {badge}
          </span>
        )}
      </span>
    </button>
  )
}

function ElevenSlider({
  label,
  value,
  leftLabel,
  rightLabel,
  stateLabel,
  onChange,
}: {
  label: string
  value: number
  leftLabel: string
  rightLabel: string
  stateLabel: string
  onChange: (value: number) => void
}) {
  const pct = Math.max(0, Math.min(100, value * 100))
  const knobLeft = pct <= 0
    ? '0px'
    : pct >= 100
      ? 'calc(100% - 20px)'
      : `calc(${pct}% - 10px)`

  return (
    <div className="space-y-2 rounded-xl border border-surface-200 bg-white px-3 py-3">
      <div className="flex items-center justify-between gap-3">
        <div className="inline-flex border-b border-dashed border-surface-300 pb-0.5 text-sm font-medium text-surface-800">
          {label}: {stateLabel}
        </div>
        <span className="rounded-full bg-surface-50 px-2 py-0.5 text-[11px] font-medium text-surface-500 tabular-nums">
          {Math.round(value * 100)}%
        </span>
      </div>

      <div className="flex items-center justify-between text-[11px] text-surface-400">
        <span>{leftLabel}</span>
        <span>{rightLabel}</span>
      </div>

      <div className="relative h-5">
        <div className="absolute inset-x-0 top-1/2 h-1.5 -translate-y-1/2 rounded-full bg-surface-100" />
        <div
          className="absolute left-0 top-1/2 h-1.5 -translate-y-1/2 rounded-full bg-primary-500"
          style={{ width: `${pct}%` }}
        />
        <div
          className="absolute top-1/2 h-4 w-4 -translate-y-1/2 rounded-full border-2 border-white bg-primary-600 shadow-[0_2px_8px_rgba(15,23,42,0.14)]"
          style={{ left: knobLeft }}
        />
        <input
          type="range"
          min={0}
          max={1}
          step={0.01}
          value={value}
          onChange={e => onChange(parseFloat(e.target.value))}
          className="absolute inset-0 h-full w-full cursor-pointer appearance-none bg-transparent opacity-0"
        />
      </div>
    </div>
  )
}

function SegmentCard({
  seg, index, isGenerating, playing, progressPercent, voiceId,
  onPlay, onDownload, onRegenerate, onDelete,
}: {
  seg: Segment
  index: number
  isGenerating: boolean
  playing: boolean
  progressPercent: number
  voiceId: string
  onPlay: () => void
  onDownload: () => void
  onRegenerate: () => void
  onDelete: () => void
}) {
  const isError      = seg.status === 'error'
  const isDone       = seg.status === 'done'
  const isGeneratingSeg = seg.status === 'generating'
  const isQueued = seg.status === 'pending'
  const isProcessing = isGeneratingSeg || isQueued
  const filename = segmentFileName(index)

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
        {isGeneratingSeg ? (
          <div className="w-24 shrink-0">
            <div className="mb-1 flex items-center justify-between text-[10px] font-semibold uppercase tracking-[0.12em] text-blue-600">
              <span>Gen</span>
              <span>{progressPercent}%</span>
            </div>
            <div className="h-2 overflow-hidden rounded-full bg-blue-100">
              <div
                className="h-full rounded-full bg-gradient-to-r from-blue-500 to-sky-400 transition-[width] duration-300"
                style={{ width: `${progressPercent}%` }}
              />
            </div>
          </div>
        ) : (
          <div className={cn(
            'shrink-0 flex items-center justify-center text-xs font-bold',
            isQueued
              ? 'min-w-[66px] rounded-full px-3 py-1.5 bg-blue-100 text-blue-700'
              : 'w-7 h-7 rounded-full',
            isError      ? 'bg-red-100 text-red-600'     :
            isDone       ? 'bg-green-100 text-green-700' :
            !isQueued    ? 'bg-surface-100 text-surface-500' : '',
          )}>
            {isQueued ? 'Queued' : index + 1}
          </div>
        )}

        {/* Text */}
        <div className="flex-1 min-w-0">
          <p className="text-sm text-surface-800 leading-snug">{seg.text}</p>
          {isDone && (
            <p className="mt-0.5 text-xs font-mono text-surface-400">{filename}</p>
          )}
        </div>

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

function SummaryStat({
  label,
  value,
  tone = 'neutral',
}: {
  label: string
  value: number
  tone?: 'neutral' | 'success' | 'error'
}) {
  const labelClass = tone === 'success'
    ? 'text-green-600'
    : tone === 'error'
      ? 'text-red-500'
      : 'text-surface-400'

  const valueClass = tone === 'success'
    ? 'text-green-700'
    : tone === 'error'
      ? 'text-red-600'
      : 'text-surface-800'

  return (
    <div className="flex items-center gap-1.5 font-sans">
      <span className={cn('text-xs font-medium leading-none', labelClass)}>{label}</span>
      <span className={cn('text-sm font-semibold leading-none tabular-nums', valueClass)}>
        {value}
      </span>
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
  const [provider, setProvider]               = useState<AudioProvider>('elevenlabs')
  const [voices, setVoices]                   = useState<VoiceOption[]>([])
  const [minimaxVoices, setMinimaxVoices]     = useState<VoiceOption[]>([])
  const [sharedVoices, setSharedVoices]       = useState<VoiceOption[]>([])
  const [models, setModels]                   = useState<ModelOption[]>([])
  const [dialogueVoices, setDialogueVoices]   = useState<VoiceOption[]>([])
  const [dialogueModels, setDialogueModels]   = useState<ModelOption[]>([])
  const [voicesLoading, setVoicesLoading]     = useState(false)
  const [voicesError, setVoicesError]         = useState<string | null>(null)
  const [voicesRetryKey, setVoicesRetryKey]   = useState(0)
  const [minimaxVoicesLoading, setMinimaxVoicesLoading] = useState(false)
  const [sharedVoicesLoading, setSharedVoicesLoading] = useState(false)
  const [voiceSearch, setVoiceSearch]         = useState('')
  const [showVoicePicker, setShowVoicePicker] = useState(false)
  const [showElevenVoiceLibrary, setShowElevenVoiceLibrary] = useState(false)
  const [elevenVoiceTab, setElevenVoiceTab] = useState<ElevenVoiceTab>('default')
  const [minimaxVoiceTab, setMinimaxVoiceTab] = useState<MiniMaxVoiceTab>('all')
  const [favoriteVoiceIds, setFavoriteVoiceIds] = useState<string[]>(loadFavoriteVoices)
  const [customVoices, setCustomVoices] = useState<Array<{ voice_id: string; name: string }>>(loadCustomVoices)
  const [customVoiceIdInput, setCustomVoiceIdInput] = useState('')
  const [customVoiceNameInput, setCustomVoiceNameInput] = useState('')
  const [customVoiceError, setCustomVoiceError] = useState('')
  const [previewVoiceId, setPreviewVoiceId] = useState<string | null>(null)
  const voicePickerRef = useRef<HTMLDivElement>(null)
  const previewAudioRef = useRef<HTMLAudioElement | null>(null)

  // TTS
  const [script, setScript]             = useState('')
  const [segments, setSegments]         = useState<Segment[]>([])
  const [voiceId, setVoiceId]           = useState('')
  const [modelId, setModelId]           = useState(DEFAULT_ELEVEN_MODEL)
  const [settings, setSettings]         = useState<ELVoiceSettings>(DEFAULT_SETTINGS)
  const [ttsLanguage, setTtsLanguage]   = useState<(typeof TTS_LANGUAGE_OPTIONS)[number]['value']>('auto')
  const [ttsLoudness, setTtsLoudness]   = useState(false)
  const [ttsTranscript, setTtsTranscript] = useState(true)
  const [minimaxSettings, setMinimaxSettings] = useState<MiniMaxVoiceSettings>(DEFAULT_MINIMAX_SETTINGS)
  const [isGenerating, setIsGenerating] = useState(false)
  const [batchProgress, setBatchProgress] = useState<BatchProgress | null>(null)
  const [filter, setFilter]             = useState<'all' | 'issues'>('all')
  const [jumpTo, setJumpTo]             = useState('')

  // Playback
  const [playingId, setPlayingId] = useState<string | null>(null)
  const audioRef = useRef<HTMLAudioElement | null>(null)

  // Dialogue
  const [dlgScript, setDlgScript]         = useState('')
  const [speakers, setSpeakers]           = useState<Speaker[]>([
    { id: uid(), label: 'A', voiceId: '', modelId: DEFAULT_ELEVEN_MODEL, stability: 0.5, isExpanded: true,  voicePickerOpen: false },
    { id: uid(), label: 'B', voiceId: '', modelId: DEFAULT_ELEVEN_MODEL, stability: 0.5, isExpanded: false, voicePickerOpen: false },
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

  // ── Extension bridge — nhận script từ Claude extension ───────────────────
  // Fill đúng tab đang active: TTS → setScript, Dialogue → setDlgScript
  useExtensionSocket('ai_audio', (msg) => {
    if (tab === 'tts') {
      setScript(prev => prev.trim() ? `${prev.trim()}\n${msg.content}` : msg.content)
      toast.success('⚡ Nhận script từ Claude!', 'Đã fill vào ô TTS', 4000)
    } else {
      setDlgScript(prev => prev.trim() ? `${prev.trim()}\n${msg.content}` : msg.content)
      toast.success('⚡ Nhận script từ Claude!', 'Đã fill vào ô Dialogue', 4000)
    }
  })

  // ── Load on mount ─────────────────────────────────────────────────────────
  useEffect(() => {
    Promise.all([listVoices('elevenlabs'), listModels('elevenlabs')])
      .then(([v, m]) => {
        setDialogueVoices(v)
        setDialogueModels(m)
        setSpeakers(prev => prev.map((speaker, index) => ({
          ...speaker,
          voiceId: speaker.voiceId || v[index]?.voice_id || speaker.voiceId,
          modelId: speaker.modelId || m[0]?.model_id || DEFAULT_ELEVEN_MODEL,
        })))
      })
      .catch(console.error)

    let cancelled = false
    const refreshHealth = async () => {
      try {
        const next = await getHealth()
        if (!cancelled) setHealth(next)
      } catch {
        // ignore health polling errors
      }
    }

    void refreshHealth()
    const timer = window.setInterval(() => {
      void refreshHealth()
    }, 20000)

    return () => {
      cancelled = true
      window.clearInterval(timer)
    }
  }, [])

  useEffect(() => {
    let cancelled = false
    setVoicesLoading(true)
    setVoicesError(null)

    Promise.all([
      listVoices(provider),
      listModels(provider),
    ])
      .then(([nextVoices, nextModels]) => {
        if (cancelled) return
        setVoicesError(null)
        setVoices(nextVoices)
        if (provider === 'minimax') setMinimaxVoices(nextVoices)

        // Merge known models với API response — API đôi khi thiếu models mới
        const mergedModels = provider === 'minimax'
          ? [
              ...KNOWN_MINIMAX_MODELS,
              ...nextModels.filter(m => !KNOWN_MINIMAX_MODELS.some(k => k.model_id === m.model_id)),
            ]
          : nextModels
        setModels(mergedModels)

        setVoiceId(prev => nextVoices.find(v => v.voice_id === prev)?.voice_id ?? nextVoices[0]?.voice_id ?? '')
        setModelId(prev => mergedModels.find(m => m.model_id === prev)?.model_id ?? mergedModels[0]?.model_id ?? (
          provider === 'minimax' ? DEFAULT_MINIMAX_MODEL : DEFAULT_ELEVEN_MODEL
        ))
      })
      .catch((err: unknown) => {
        if (!cancelled) {
          console.error(err)
          const msg = err instanceof Error ? err.message : String(err)
          const isRateLimit = msg.includes('429') || msg.includes('too many') || msg.toLowerCase().includes('queue')
          setVoicesError(isRateLimit
            ? 'Proxy quá tải (rate limited) — thử lại sau vài giây'
            : `Không tải được voices: ${msg}`
          )
          setVoices([])
          setModels(provider === 'minimax' ? KNOWN_MINIMAX_MODELS : [])
          setVoiceId('')
          setModelId(provider === 'minimax' ? DEFAULT_MINIMAX_MODEL : DEFAULT_ELEVEN_MODEL)
        }
      })
      .finally(() => {
        if (!cancelled) setVoicesLoading(false)
      })

    return () => {
      cancelled = true
    }
  }, [provider, voicesRetryKey])

  useEffect(() => {
    if (provider !== 'elevenlabs') {
      setShowElevenVoiceLibrary(false)
      setElevenVoiceTab('default')
      setPreviewVoiceId(null)
      previewAudioRef.current?.pause()
    }
  }, [provider])

  useEffect(() => {
    localStorage.setItem(ELEVEN_FAVORITES_KEY, JSON.stringify(favoriteVoiceIds))
  }, [favoriteVoiceIds])

  useEffect(() => {
    localStorage.setItem(ELEVEN_CUSTOM_VOICES_KEY, JSON.stringify(customVoices))
  }, [customVoices])

  useEffect(() => {
    if (provider !== 'elevenlabs' || !showElevenVoiceLibrary || sharedVoices.length > 0 || sharedVoicesLoading) return

    let cancelled = false
    setSharedVoicesLoading(true)
    listSharedVoices()
      .then((nextVoices) => {
        if (!cancelled) setSharedVoices(nextVoices)
      })
      .catch((err) => {
        console.error(err)
        if (!cancelled) setSharedVoices([])
      })
      .finally(() => {
        if (!cancelled) setSharedVoicesLoading(false)
      })

    return () => {
      cancelled = true
    }
  }, [provider, showElevenVoiceLibrary, sharedVoices.length, sharedVoicesLoading])

  useEffect(() => {
    if ((provider !== 'minimax' && !showElevenVoiceLibrary) || minimaxVoices.length > 0) return
    let cancelled = false
    setMinimaxVoicesLoading(true)
    listVoices('minimax')
      .then((nextVoices) => {
        if (!cancelled) setMinimaxVoices(nextVoices)
      })
      .catch(console.error)
      .finally(() => {
        if (!cancelled) setMinimaxVoicesLoading(false)
      })
    return () => {
      cancelled = true
    }
  }, [provider, showElevenVoiceLibrary, minimaxVoices.length])

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

  useEffect(() => {
    const handleKeyDown = (event: KeyboardEvent) => {
      if (event.key === 'Escape') {
        setShowElevenVoiceLibrary(false)
        setShowVoicePicker(false)
      }
    }
    document.addEventListener('keydown', handleKeyDown)
    return () => document.removeEventListener('keydown', handleKeyDown)
  }, [])

  useEffect(() => () => {
    previewAudioRef.current?.pause()
    previewAudioRef.current = null
  }, [])

  // Auto-parse script live
  useEffect(() => {
    setSegments(prev => buildSegments(script, prev))
  }, [script])

  // ── Derived ───────────────────────────────────────────────────────────────
  const customVoiceOptions = useMemo<VoiceOption[]>(
    () => customVoices.map((voice) => ({
      voice_id: voice.voice_id,
      name: voice.name,
      category: 'Custom',
      tags: ['Custom'],
      source: 'custom',
      provider: 'elevenlabs',
    })),
    [customVoices],
  )

  const miniMaxMyVoices = useMemo(
    () => minimaxVoices.filter(isMiniMaxPersonalVoice),
    [minimaxVoices],
  )

  const elevenLibraryVoices = useMemo(() => {
    const merged: VoiceOption[] = []
    const seen = new Set<string>()
    for (const voice of [...voices, ...sharedVoices]) {
      if (voice.provider !== 'elevenlabs' || seen.has(voice.voice_id)) continue
      seen.add(voice.voice_id)
      merged.push(voice)
    }
    return merged
  }, [sharedVoices, voices])

  const allLibraryVoices = useMemo(() => {
    const merged: VoiceOption[] = []
    const seen = new Set<string>()
    for (const voice of [...elevenLibraryVoices, ...miniMaxMyVoices, ...customVoiceOptions]) {
      if (seen.has(voice.voice_id)) continue
      seen.add(voice.voice_id)
      merged.push(voice)
    }
    return merged
  }, [customVoiceOptions, elevenLibraryVoices, miniMaxMyVoices])

  const elevenVoiceMap = useMemo(() => {
    const entries = new Map<string, VoiceOption>()
    for (const voice of [...elevenLibraryVoices, ...customVoiceOptions]) {
      if (!entries.has(voice.voice_id)) entries.set(voice.voice_id, voice)
    }
    return entries
  }, [customVoiceOptions, elevenLibraryVoices])

  const selectedVoice = provider === 'elevenlabs'
    ? elevenVoiceMap.get(voiceId)
    : minimaxVoices.find(v => v.voice_id === voiceId) ?? voices.find(v => v.voice_id === voiceId)
  const selectedModel = models.find((model) => model.model_id === modelId)
  const selectedMiniMaxModelLabel = selectedModel?.name ?? formatModelName(DEFAULT_MINIMAX_MODEL)
  const selectedLanguage = TTS_LANGUAGE_OPTIONS.find((item) => item.value === ttsLanguage) ?? TTS_LANGUAGE_OPTIONS[0]
  const selectedVoiceDescriptor = selectedVoice?.description?.trim()
    || selectedVoice?.labels?.descriptive?.trim()
    || selectedVoice?.category?.trim()
  const showNewestElevenBadge = /v3|alpha|new/i.test(`${selectedModel?.name ?? ''} ${modelId}`)

  const filteredVoices = useMemo(
    () => {
      const activeVoices = minimaxVoiceTab === 'mine' ? miniMaxMyVoices : minimaxVoices
      return voiceSearch
        ? activeVoices.filter(v => matchesVoiceSearch(v, voiceSearch))
        : activeVoices
    },
    [miniMaxMyVoices, minimaxVoiceTab, minimaxVoices, voiceSearch],
  )

  const elevenFavoriteVoices = useMemo(
    () => favoriteVoiceIds
      .map((voiceId) => allLibraryVoices.find((voice) => voice.voice_id === voiceId))
      .filter((voice): voice is VoiceOption => Boolean(voice)),
    [allLibraryVoices, favoriteVoiceIds],
  )

  const activeElevenVoices = useMemo(() => {
    if (elevenVoiceTab === 'mine') return miniMaxMyVoices
    if (elevenVoiceTab === 'favorites') return elevenFavoriteVoices
    if (elevenVoiceTab === 'custom') return customVoiceOptions
    return elevenLibraryVoices
  }, [customVoiceOptions, elevenFavoriteVoices, elevenLibraryVoices, elevenVoiceTab, miniMaxMyVoices])

  const filteredElevenVoices = useMemo(
    () => activeElevenVoices.filter((voice) => matchesVoiceSearch(voice, voiceSearch)),
    [activeElevenVoices, voiceSearch],
  )

  const isElevenVoiceLibraryLoading = useMemo(() => {
    if (elevenVoiceTab === 'default') return voicesLoading
    if (elevenVoiceTab === 'mine') return minimaxVoicesLoading
    return false
  }, [elevenVoiceTab, minimaxVoicesLoading, voicesLoading])

  const activeHealth = tab === 'dialogue'
    ? health?.elevenlabs
    : provider === 'minimax'
      ? health?.minimax
      : health?.elevenlabs

  const doneCount  = segments.filter(s => s.status === 'done').length
  const errCount   = segments.filter(s => s.status === 'error').length
  const totalCount = segments.length
  const batchProgressPercent = batchProgress
    ? Math.round((batchProgress.completed / Math.max(batchProgress.total, 1)) * 100)
    : 0

  const visibleSegs = filter === 'issues'
    ? segments.filter(s => s.status === 'error')
    : segments

  // ── Generation ────────────────────────────────────────────────────────────
  const updateSeg = useCallback((id: string, patch: Partial<Segment>) => {
    setSegments(prev => prev.map(s => s.id === id ? { ...s, ...patch } : s))
  }, [])

  const generateOne = useCallback(async (seg: Segment): Promise<boolean> => {
    if (!voiceId) return false
    updateSeg(seg.id, { status: 'generating', error: undefined })
    try {
      const audioUrl = await generateTtsAudio(
        provider,
        seg.text,
        voiceId,
        modelId,
        provider === 'minimax' ? minimaxSettings : settings,
      )
      updateSeg(seg.id, { status: 'done', audioUrl })
      return true
    } catch (err: unknown) {
      updateSeg(seg.id, { status: 'error', error: (err as Error)?.message ?? 'Unknown error' })
      return false
    }
  }, [voiceId, modelId, minimaxSettings, provider, settings, updateSeg])

  const runGenerationBatch = useCallback(async (
    items: Segment[],
    taskLabel: string,
  ): Promise<{ failed: number; total: number } | null> => {
    if (!voiceId || items.length === 0 || isGenerating) return null

    setIsGenerating(true)
    setBatchProgress({ total: items.length, completed: 0 })

    const taskId = taskStore.add({
      toolId: 'ai-audio',
      toolLabel: 'AI Audio',
      label: taskLabel,
    })

    let completed = 0
    let failed = 0

    try {
      for (let i = 0; i < items.length; i += AUDIO_BATCH_THREADS) {
        const chunk = items.slice(i, i + AUDIO_BATCH_THREADS)
        await Promise.all(chunk.map(async (item) => {
          const ok = await generateOne(item)
          if (!ok) failed += 1
          completed += 1
          setBatchProgress({ total: items.length, completed })
        }))
      }

      return { failed, total: items.length }
    } finally {
      setIsGenerating(false)
      setBatchProgress(null)
      taskStore.complete(taskId, failed === 0 ? 'done' : 'error',
        failed === 0
          ? `${items.length} segments completed`
          : `${items.length - failed} done, ${failed} failed`,
      )
    }
  }, [generateOne, isGenerating, voiceId])

  const handleGenerateAll = async () => {
    if (!voiceId || segments.length === 0 || isGenerating) return
    setSegments(prev => prev.map(s =>
      s.status !== 'done' ? { ...s, status: 'pending', error: undefined } : s
    ))
    const toRun = segments.filter(s => s.status !== 'done')
    await runGenerationBatch(toRun, `TTS ${toRun.length} segments`)
  }

  const handleRegenerateErrors = async () => {
    const errors = segments.filter(s => s.status === 'error')
    if (!voiceId || errors.length === 0 || isGenerating) return
    setSegments(prev => prev.map(s =>
      s.status === 'error' ? { ...s, status: 'pending', error: undefined } : s
    ))
    await runGenerationBatch(errors, `Retry TTS ${errors.length} failed segments`)
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

  const handlePreviewVoice = useCallback((voice: VoiceOption) => {
    if (!voice.preview_url) return

    if (!previewAudioRef.current) {
      previewAudioRef.current = new Audio()
    }

    const audio = previewAudioRef.current
    if (previewVoiceId === voice.voice_id) {
      audio.pause()
      audio.currentTime = 0
      setPreviewVoiceId(null)
      return
    }

    audio.pause()
    audio.src = voice.preview_url
    audio.currentTime = 0
    audio.onended = () => setPreviewVoiceId(null)
    void audio.play()
      .then(() => setPreviewVoiceId(voice.voice_id))
      .catch(() => setPreviewVoiceId(null))
  }, [previewVoiceId])

  const toggleFavoriteVoice = useCallback((voiceIdToToggle: string) => {
    setFavoriteVoiceIds((prev) => (
      prev.includes(voiceIdToToggle)
        ? prev.filter((item) => item !== voiceIdToToggle)
        : [voiceIdToToggle, ...prev]
    ))
  }, [])

  const handleUseElevenVoice = useCallback((voice: VoiceOption) => {
    if (voice.provider === 'minimax') {
      setProvider('minimax')
      setMinimaxVoiceTab('mine')
    } else {
      setProvider('elevenlabs')
    }
    setVoiceId(voice.voice_id)
    setShowElevenVoiceLibrary(false)
    setVoiceSearch('')
  }, [])

  const handleAddCustomVoice = useCallback(() => {
    const nextId = customVoiceIdInput.trim()
    const nextName = customVoiceNameInput.trim()

    if (!nextId || !nextName) {
      setCustomVoiceError('Điền đủ Voice ID với Display Name.')
      return
    }
    if (customVoices.some((voice) => voice.voice_id === nextId)) {
      setCustomVoiceError('Voice ID này đã có rồi.')
      return
    }

    const nextVoice = { voice_id: nextId, name: nextName }
    setCustomVoices((prev) => [nextVoice, ...prev])
    setProvider('elevenlabs')
    setVoiceId(nextId)
    setCustomVoiceIdInput('')
    setCustomVoiceNameInput('')
    setCustomVoiceError('')
    setVoiceSearch('')
    setShowElevenVoiceLibrary(false)
  }, [customVoiceIdInput, customVoiceNameInput, customVoices])

  const handleDeleteCustomVoice = useCallback((voiceIdToDelete: string) => {
    setCustomVoices((prev) => prev.filter((voice) => voice.voice_id !== voiceIdToDelete))
    setFavoriteVoiceIds((prev) => prev.filter((voiceIdValue) => voiceIdValue !== voiceIdToDelete))
    if (voiceId === voiceIdToDelete && provider === 'elevenlabs') {
      const fallbackVoice = elevenLibraryVoices[0]
      setVoiceId(fallbackVoice?.voice_id ?? '')
    }
  }, [elevenLibraryVoices, provider, voiceId])

  const resetElevenSettings = useCallback(() => {
    setSettings(DEFAULT_SETTINGS)
    setTtsLanguage('auto')
    setTtsLoudness(false)
    setTtsTranscript(true)
  }, [])

  const resetMiniMaxSettings = useCallback(() => {
    setMinimaxSettings(DEFAULT_MINIMAX_SETTINGS)
  }, [])

  const downloadSeg = async (seg: Segment, idx: number) => {
    if (!seg.audioUrl) return
    const blob = await fetch(seg.audioUrl).then(r => r.blob())
    const a = document.createElement('a')
    a.href = URL.createObjectURL(blob)
    a.download = segmentFileName(idx)
    a.click()
    setTimeout(() => URL.revokeObjectURL(a.href), 1000)
  }

  const downloadAll = async () => {
    const done = segments.filter(s => s.status === 'done' && s.audioUrl)
    if (done.length === 0) return

    try {
      const savePath = await tauriSave({
        defaultPath: archiveFileName(child?.title || child?.name),
        filters: [{ name: 'ZIP files', extensions: ['zip'] }],
      })

      if (!savePath || typeof savePath !== 'string') return

      const taskId = taskStore.add({
        toolId: 'ai-audio',
        toolLabel: 'AI Audio',
        label: `Zip ${done.length} segments`,
      })

      const files = await Promise.all(done.map(async (seg) => {
        const idx = segments.indexOf(seg)
        const res = await fetch(seg.audioUrl as string)
        if (!res.ok) {
          throw new Error(`Cannot fetch audio for segment ${idx + 1}`)
        }
        return {
          filename: segmentFileName(idx),
          blob: await res.blob(),
        }
      }))

      const saveRes = await saveAudioZipFile(savePath, files)
      if (!saveRes.ok) {
        throw new Error(saveRes.message)
      }

      taskStore.complete(taskId, 'done', `Saved zip for ${done.length} segments`)
      toast.success('Download xong', `Đã gom ${done.length} file vào zip.`)
    } catch (err: unknown) {
      const msg = err instanceof Error ? err.message : 'Download all failed'
      toast.error('Download all lỗi', msg)
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
            <p className="page-sub">
              {child.title || child.name}
              {' · '}
              {tab === 'dialogue'
                ? 'ElevenLabs Dialogue'
                : provider === 'minimax'
                  ? 'MiniMax TTS'
                  : 'ElevenLabs TTS'}
            </p>
          </div>
        </div>
        {health && activeHealth && (
          <span className={cn(
            'px-2.5 py-1 rounded-full text-xs font-medium',
            activeHealth === 'good'     ? 'bg-green-100 text-green-700' :
            activeHealth === 'degraded' ? 'bg-amber-100 text-amber-700' :
                                          'bg-red-100 text-red-700',
          )}>
            {tab === 'dialogue' ? 'ElevenLabs' : provider === 'minimax' ? 'MiniMax' : 'ElevenLabs'}: {activeHealth}
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
                <div className="space-y-2">
                  <label className="label mb-1.5">Provider</label>
                  <div className="grid grid-cols-2 gap-2">
                    {([
                      { key: 'elevenlabs', label: 'ElevenLabs' },
                      { key: 'minimax', label: 'MiniMax' },
                    ] as const).map((item) => (
                      <button
                        key={item.key}
                        type="button"
                        className={cn(
                          'rounded-xl border px-3 py-2 text-sm font-medium transition-colors',
                          provider === item.key
                            ? 'border-primary-400 bg-primary-50 text-primary-700'
                            : 'border-surface-200 bg-white text-surface-600 hover:bg-surface-50',
                        )}
                        onClick={() => {
                          setProvider(item.key)
                          setShowVoicePicker(false)
                          setVoiceSearch('')
                        }}
                      >
                        {item.label}
                      </button>
                    ))}
                  </div>
                </div>

                {provider === 'elevenlabs' ? (
                  <div className="space-y-4">
                    {voicesError && (
                      <div className="flex items-center justify-between gap-2 rounded-lg border border-amber-200 bg-amber-50 px-3 py-2">
                        <p className="text-xs text-amber-700">{voicesError}</p>
                        <button
                          onClick={() => { setVoicesError(null); setVoicesRetryKey(k => k + 1) }}
                          className="shrink-0 text-xs font-semibold text-amber-700 underline cursor-pointer"
                        >Retry</button>
                      </div>
                    )}
                    <div className="space-y-1.5">
                      <label className="label !mb-0 text-surface-600">Select a voice</label>
                      <button
                        type="button"
                        className="flex w-full items-center justify-between gap-3 rounded-xl border border-surface-200 bg-white px-3 py-2.5 text-left transition-colors hover:bg-surface-50"
                        onClick={() => {
                          setShowElevenVoiceLibrary(true)
                          setVoiceSearch('')
                          setElevenVoiceTab('default')
                        }}
                      >
                        <div className="min-w-0 flex-1">
                          <p className={cn('truncate text-sm font-medium', selectedVoice ? 'text-surface-900' : 'text-surface-400')}>
                            {voicesLoading
                              ? 'Loading voices...'
                              : selectedVoice?.name ?? 'Select a voice'}
                          </p>
                          {selectedVoiceDescriptor && (
                            <p className="truncate text-xs text-surface-400">
                              {selectedVoiceDescriptor}
                            </p>
                          )}
                        </div>
                        <ChevronRight size={16} className="shrink-0 text-surface-400" />
                      </button>
                    </div>

                    <div className="space-y-1.5">
                      <label className="label !mb-0 text-surface-600">Select a model</label>
                      <div className="relative">
                        <select
                          className="absolute inset-0 z-10 h-full w-full cursor-pointer opacity-0"
                          value={modelId}
                          onChange={e => setModelId(e.target.value)}
                        >
                          {models.length === 0 && (
                            <option value={DEFAULT_ELEVEN_MODEL}>{DEFAULT_ELEVEN_MODEL}</option>
                          )}
                          {models.map(m => (
                            <option key={m.model_id} value={m.model_id}>{m.name}</option>
                          ))}
                        </select>
                        <div className="pointer-events-none flex min-h-[46px] items-center justify-between gap-3 rounded-xl border border-surface-200 bg-white px-3 py-2.5">
                          <div className="flex min-w-0 items-center gap-2">
                            <span className="truncate text-sm font-medium text-surface-900">
                              {selectedModel?.name ?? DEFAULT_ELEVEN_MODEL}
                            </span>
                            {showNewestElevenBadge && (
                              <span className="rounded-full border border-surface-200 bg-surface-50 px-2 py-0.5 text-[11px] font-medium text-surface-600">
                                Newest
                              </span>
                            )}
                          </div>
                          <ChevronRight size={16} className="shrink-0 text-surface-400" />
                        </div>
                      </div>
                      <p className="text-xs text-surface-400">Read document for V3.</p>
                    </div>

                    <div className="space-y-1.5">
                      <label className="label !mb-0 text-surface-600">Select language</label>
                      <div className="relative">
                        <select
                          className="absolute inset-0 z-10 h-full w-full cursor-pointer opacity-0"
                          value={ttsLanguage}
                          onChange={e => setTtsLanguage(e.target.value as (typeof TTS_LANGUAGE_OPTIONS)[number]['value'])}
                        >
                          {TTS_LANGUAGE_OPTIONS.map((item) => (
                            <option key={item.value} value={item.value}>{item.label}</option>
                          ))}
                        </select>
                        <div className="pointer-events-none flex min-h-[46px] items-center justify-between gap-3 rounded-xl border border-surface-200 bg-white px-3 py-2.5">
                          <div className="flex min-w-0 items-center gap-2">
                            <span className="truncate text-sm font-medium text-surface-900">
                              {selectedLanguage.label}
                            </span>
                            {selectedLanguage.badge && (
                              <span className="rounded-full border border-surface-200 bg-surface-50 px-2 py-0.5 text-[11px] font-medium text-surface-600">
                                {selectedLanguage.badge}
                              </span>
                            )}
                          </div>
                          <ChevronDown size={16} className="shrink-0 text-surface-400" />
                        </div>
                      </div>
                    </div>

                    <div className="space-y-3 pt-1">
                      <ElevenSlider
                        label="Stability"
                        value={settings.stability}
                        leftLabel="Creative"
                        rightLabel="Robust"
                        stateLabel={describeStability(settings.stability)}
                        onChange={v => setSettings(prev => ({ ...prev, stability: v }))}
                      />
                      <ElevenSlider
                        label="Similarity"
                        value={settings.similarity_boost}
                        leftLabel="Flexible"
                        rightLabel="Matched"
                        stateLabel={describeSimilarity(settings.similarity_boost)}
                        onChange={v => setSettings(prev => ({ ...prev, similarity_boost: v }))}
                      />
                      <ElevenSlider
                        label="Style"
                        value={settings.style}
                        leftLabel="Neutral"
                        rightLabel="Expressive"
                        stateLabel={describeStyle(settings.style)}
                        onChange={v => setSettings(prev => ({ ...prev, style: v }))}
                      />
                    </div>

                    <div className="space-y-3 pt-1">
                      <div className="overflow-hidden rounded-xl border border-surface-200 bg-white divide-y divide-surface-100">
                        <ElevenToggleRow
                          label="Loudness normalization (Beta)"
                          checked={ttsLoudness}
                          onToggle={() => setTtsLoudness(prev => !prev)}
                        />
                        <ElevenToggleRow
                          label="Export transcript"
                          badge="+15%"
                          checked={ttsTranscript}
                          onToggle={() => setTtsTranscript(prev => !prev)}
                        />
                      </div>
                      <div className="flex justify-end">
                        <button
                          type="button"
                          className="inline-flex items-center gap-1.5 text-xs font-medium text-surface-500 transition-colors hover:text-surface-700"
                          onClick={resetElevenSettings}
                        >
                          <RefreshCw size={13} />
                          Reset values
                        </button>
                      </div>
                    </div>
                  </div>
                ) : (
                  <>
                    {voicesError && (
                      <div className="flex items-center justify-between gap-2 rounded-lg border border-amber-200 bg-amber-50 px-3 py-2">
                        <p className="text-xs text-amber-700">{voicesError}</p>
                        <button
                          onClick={() => { setVoicesError(null); setVoicesRetryKey(k => k + 1) }}
                          className="shrink-0 text-xs font-semibold text-amber-700 underline cursor-pointer"
                        >Retry</button>
                      </div>
                    )}
                    <div ref={voicePickerRef} className="relative space-y-1.5">
                      <label className="label !mb-0 text-surface-600">Select a voice</label>

                      <button
                        className={cn(
                          'w-full flex items-center justify-between gap-3 rounded-xl border border-surface-200 bg-white px-3 py-2.5 text-left transition-colors',
                          showVoicePicker
                            ? 'border-primary-400 bg-primary-50/30'
                            : 'hover:bg-surface-50',
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
                            <p className={cn('text-sm font-medium truncate', selectedVoice ? 'text-surface-900' : 'text-surface-400')}>
                              {voicesLoading
                                ? 'Loading...'
                                : selectedVoice?.name ?? 'Select a voice'}
                            </p>
                            {selectedVoiceDescriptor && (
                              <p className="truncate text-xs text-surface-400">
                                {selectedVoiceDescriptor}
                              </p>
                            )}
                          </div>
                        <ChevronDown size={16} className={cn('shrink-0 text-surface-400 transition-transform', showVoicePicker && 'rotate-180')} />
                      </button>

                      {showVoicePicker && (
                        <div className="absolute top-full left-0 right-0 z-50 mt-1 overflow-hidden rounded-xl border border-surface-200 bg-white shadow-xl">
                          <div className="space-y-3 border-b border-surface-100 p-3">
                            <div className="inline-flex rounded-xl bg-surface-100 p-1">
                              {([
                                { key: 'all', label: 'All Voices' },
                                { key: 'mine', label: 'My Voices' },
                              ] as const).map((item) => (
                                <button
                                  key={item.key}
                                  type="button"
                                  className={cn(
                                    'rounded-lg px-3 py-1.5 text-xs font-medium transition-colors',
                                    minimaxVoiceTab === item.key
                                      ? 'bg-white text-surface-900 shadow-sm'
                                      : 'text-surface-500 hover:text-surface-700',
                                  )}
                                  onClick={() => setMinimaxVoiceTab(item.key)}
                                >
                                  {item.label}
                                </button>
                              ))}
                            </div>
                            <div className="relative">
                              <Search size={15} className="pointer-events-none absolute left-3 top-1/2 -translate-y-1/2 text-surface-400" />
                              <input
                                className="input h-10 rounded-xl pl-9 pr-3 text-sm w-full"
                                placeholder="Search voices..."
                                value={voiceSearch}
                                onChange={e => setVoiceSearch(e.target.value)}
                                autoFocus
                              />
                            </div>
                          </div>
                          <div className="max-h-64 overflow-y-auto p-1.5">
                            {filteredVoices.length === 0 && (
                              <p className="text-xs text-surface-400 px-4 py-3">No matches found</p>
                            )}
                            {filteredVoices.map(v => (
                              <button
                                key={v.voice_id}
                                className={cn(
                                  'w-full flex items-center gap-2.5 rounded-lg px-3 py-2 text-sm text-left transition-colors hover:bg-surface-50',
                                  v.voice_id === voiceId && 'bg-primary-50 text-primary-700',
                                )}
                                onClick={() => { setVoiceId(v.voice_id); setShowVoicePicker(false); setVoiceSearch('') }}
                              >
                                <VoiceAvatar name={v.name} />
                                <div className="min-w-0 flex-1">
                                  <p className={cn('font-medium truncate', v.voice_id === voiceId ? 'text-primary-700' : 'text-surface-800')}>
                                    {v.name}
                                  </p>
                                  {(v.description || v.category) && (
                                    <p className="truncate text-xs text-surface-400">
                                      {v.description || v.category}
                                    </p>
                                  )}
                                </div>
                                {v.voice_id === voiceId && (
                                  <CheckCircle size={14} className="ml-auto shrink-0 text-primary-500" />
                                )}
                              </button>
                            ))}
                          </div>
                        </div>
                      )}
                    </div>

                    <div className="space-y-1.5">
                      <label className="label !mb-0 text-surface-600">Select a model</label>
                      <div className="relative">
                        <select
                          className="absolute inset-0 z-10 h-full w-full cursor-pointer opacity-0"
                          value={modelId}
                          onChange={e => setModelId(e.target.value)}
                        >
                          {KNOWN_MINIMAX_MODELS.map(m => (
                            <option key={m.model_id} value={m.model_id}>{m.name}</option>
                          ))}
                        </select>
                        <div className="pointer-events-none flex min-h-[50px] items-center gap-3 rounded-xl border border-surface-200 bg-white px-3 py-2.5">
                          <div className="min-w-0 flex-1">
                            <p className="text-sm font-semibold text-surface-900 truncate">
                              {KNOWN_MINIMAX_MODELS.find(m => m.model_id === modelId)?.name ?? selectedMiniMaxModelLabel}
                            </p>
                            <p className="text-xs text-surface-400 mt-0.5">
                              {KNOWN_MINIMAX_MODELS.find(m => m.model_id === modelId)?.description ?? ''}
                            </p>
                          </div>
                          <ChevronDown size={16} className="shrink-0 text-surface-400" />
                        </div>
                      </div>
                    </div>

                    <div className="space-y-3 pt-1">
                      <div className="space-y-3">
                          <SliderSetting
                            label="Speed"
                            color="from-blue-400 to-blue-600"
                            value={minimaxSettings.speed}
                            min={0.5}
                            max={2}
                            step={0.05}
                            helperText="Tăng hoặc giảm nhịp đọc tổng thể."
                            formatValue={(v) => `${v.toFixed(2)}x`}
                            onChange={v => setMinimaxSettings(prev => ({ ...prev, speed: v }))}
                          />
                          <SliderSetting
                            label="Volume"
                            color="from-violet-400 to-violet-600"
                            value={minimaxSettings.vol}
                            min={0}
                            max={2}
                            step={0.05}
                            helperText="Kiểm soát độ lớn của giọng đọc."
                            formatValue={(v) => `${Math.round(v * 100)}%`}
                            onChange={v => setMinimaxSettings(prev => ({ ...prev, vol: v }))}
                          />
                          <SliderSetting
                            label="Pitch"
                            color="from-rose-400 to-rose-600"
                            value={minimaxSettings.pitch}
                            min={-12}
                            max={12}
                            step={1}
                            helperText="Nâng hoặc hạ cao độ giọng."
                            formatValue={(v) => `${v > 0 ? '+' : ''}${v}`}
                            onChange={v => setMinimaxSettings(prev => ({ ...prev, pitch: v }))}
                          />
                          <div className="rounded-xl border border-surface-200 bg-white px-3 py-3">
                            <div className="flex items-start justify-between gap-3">
                              <div>
                                <label className="text-sm font-medium text-surface-800">Emotion</label>
                                <p className="mt-0.5 text-[11px] text-surface-400">Chọn cảm xúc nền cho đoạn audio.</p>
                              </div>
                              <span className="rounded-full border border-surface-200 bg-surface-50 px-2 py-0.5 text-[11px] font-medium text-surface-600">
                                {minimaxSettings.emotion}
                              </span>
                            </div>
                            <div className="relative mt-2.5">
                              <select
                                className="w-full appearance-none rounded-xl border border-surface-200 bg-white px-3 py-2.5 text-sm font-medium text-surface-800 outline-none transition-all hover:bg-surface-50 focus:border-primary-400 focus:ring-2 focus:ring-primary-100 pr-8"
                                value={minimaxSettings.emotion}
                                onChange={e => setMinimaxSettings(prev => ({ ...prev, emotion: e.target.value }))}
                              >
                                {['neutral', 'happy', 'sad', 'angry', 'fearful', 'surprised'].map((emotion) => (
                                  <option key={emotion} value={emotion}>{emotion}</option>
                                ))}
                              </select>
                              <ChevronDown size={14} className="absolute right-3 top-1/2 -translate-y-1/2 text-surface-400 pointer-events-none" />
                            </div>
                          </div>
                      </div>
                      <div className="flex justify-end">
                        <button
                          type="button"
                          className="inline-flex items-center gap-1.5 text-xs font-medium text-surface-500 transition-colors hover:text-surface-700"
                          onClick={resetMiniMaxSettings}
                        >
                          <RefreshCw size={13} />
                          Reset values
                        </button>
                      </div>
                    </div>
                  </>
                )}

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
                    ? <><Loader size={14} className="animate-spin" /> Generating {batchProgressPercent}%</>
                    : <><Zap size={14} /> Generate All ({segments.filter(s => s.status !== 'done').length})</>}
                </button>
              </div>
            </div>

            {/* ── Right panel: segments preview ──────────────────────────── */}
            <div className="flex-1 flex flex-col overflow-hidden">
              {/* Stats bar */}
              <div className="space-y-3 border-b border-surface-200 bg-white px-5 py-4">
                <div className="flex flex-wrap items-start justify-between gap-3">
                  <div>
                    <p className="text-sm font-semibold text-surface-900">Segments Preview</p>
                    <p className="text-xs text-surface-400">Editing project: <span className="font-medium text-surface-600">{child.title || child.name}</span></p>
                  </div>

                  {doneCount > 0 && (
                    <button
                      className="inline-flex shrink-0 items-center gap-1.5 rounded-xl border border-surface-200 px-3 py-1.5 text-xs font-medium text-surface-600 transition-colors hover:border-surface-300 hover:bg-surface-50 hover:text-surface-800"
                      onClick={downloadAll}
                    >
                      <Download size={12} /> Download All
                    </button>
                  )}
                </div>

                <div className="flex flex-wrap items-center justify-between gap-3">
                  <div className="flex flex-wrap items-center gap-x-4 gap-y-1">
                    <SummaryStat label="Total" value={totalCount} />
                    <SummaryStat label="Done" value={doneCount} tone="success" />
                    {errCount > 0 && (
                      <SummaryStat label="Errors" value={errCount} tone="error" />
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
                </div>

                {isGenerating && batchProgress && (
                  <div className="rounded-2xl border border-blue-100 bg-blue-50/70 px-3.5 py-3">
                    <div className="flex items-center justify-between gap-3 text-xs font-medium text-blue-700">
                      <span>Generating audio batch</span>
                      <span>{batchProgress.completed}/{batchProgress.total} • {batchProgressPercent}%</span>
                    </div>
                    <div className="mt-2 h-2.5 overflow-hidden rounded-full bg-blue-100">
                      <div
                        className="h-full rounded-full bg-gradient-to-r from-blue-500 via-sky-500 to-cyan-400 transition-[width] duration-300"
                        style={{ width: `${batchProgressPercent}%` }}
                      />
                    </div>
                  </div>
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
                          isGenerating={isGenerating}
                          playing={playingId === seg.id}
                          progressPercent={batchProgressPercent}
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
                <div className="rounded-xl border border-surface-200 bg-surface-50 px-3 py-2 text-xs text-surface-500">
                  Dialogue Studio currently uses ElevenLabs only.
                </div>

                {/* Speaker cards */}
                <p className="text-xs font-semibold text-surface-500 uppercase tracking-wide px-1">Speakers</p>

                {speakers.map(spk => {
                  const spkVoice = dialogueVoices.find(v => v.voice_id === spk.voiceId)
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
                                const sv = dialogueVoices.find(v => v.voice_id === spk.voiceId)
                                const isOpen = dlgOpenPickerId === spk.id
                                const filtered = dlgVoiceSearch
                                  ? dialogueVoices.filter(v => v.name.toLowerCase().includes(dlgVoiceSearch.toLowerCase()))
                                  : dialogueVoices
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
                                  {dialogueModels.length === 0 && (
                                    <option value={DEFAULT_ELEVEN_MODEL}>{DEFAULT_ELEVEN_MODEL}</option>
                                  )}
                                  {dialogueModels.map(m => (
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
                      modelId: DEFAULT_ELEVEN_MODEL,
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

      {provider === 'elevenlabs' && showElevenVoiceLibrary && (
        <div className="fixed inset-0 z-[120] flex items-center justify-center bg-surface-950/45 p-6 backdrop-blur-sm">
          <div className="flex max-h-[88vh] w-full max-w-6xl flex-col overflow-hidden rounded-2xl border border-surface-200 bg-white shadow-[0_18px_48px_rgba(15,23,42,0.12)]">
            <div className="flex items-start justify-between gap-4 border-b border-surface-200 px-5 py-4">
              <div>
                <h2 className="text-2xl font-semibold leading-tight text-surface-900">Voice Library</h2>
                <p className="mt-1 text-sm text-surface-500">
                  Explore and select a voice for your project.
                </p>
              </div>
              <button
                type="button"
                className="btn-icon text-surface-400 hover:bg-surface-100 hover:text-surface-700"
                onClick={() => {
                  setShowElevenVoiceLibrary(false)
                  setPreviewVoiceId(null)
                  previewAudioRef.current?.pause()
                }}
              >
                <X size={16} />
              </button>
            </div>

            <div className="border-b border-surface-200 px-5 py-3.5">
              <div className="flex flex-col gap-4 lg:flex-row lg:items-center lg:justify-between">
                <div className="inline-flex rounded-xl bg-surface-100 p-1">
                  {([
                    { key: 'default', label: 'ElevenLabs', count: elevenLibraryVoices.length },
                    { key: 'mine', label: 'My Voices', count: miniMaxMyVoices.length },
                    { key: 'favorites', label: 'Favorites', count: elevenFavoriteVoices.length },
                    { key: 'custom', label: 'Custom Voice', count: customVoices.length },
                  ] as const).map((item) => (
                    <button
                      key={item.key}
                      type="button"
                      className={cn(
                        'rounded-lg px-3 py-1.5 text-sm font-medium transition-colors',
                        elevenVoiceTab === item.key
                          ? 'bg-white text-surface-900 shadow-sm'
                          : 'text-surface-500 hover:text-surface-700',
                      )}
                      onClick={() => {
                        setElevenVoiceTab(item.key)
                        setVoiceSearch('')
                      }}
                    >
                      {item.label}
                      <span className="ml-2 text-xs text-surface-400">{item.count}</span>
                    </button>
                  ))}
                </div>

                {elevenVoiceTab !== 'custom' && (
                  <div className="relative w-full lg:max-w-md">
                    <Search size={15} className="pointer-events-none absolute left-3 top-1/2 -translate-y-1/2 text-surface-400" />
                    <input
                      className="input h-10 rounded-xl pl-9 pr-3"
                      placeholder="Search by keyword or voice ID..."
                      value={voiceSearch}
                      onChange={(event) => setVoiceSearch(event.target.value)}
                      autoFocus
                    />
                  </div>
                )}
              </div>
            </div>

            <div className="flex-1 overflow-y-auto px-5 py-4">
              {elevenVoiceTab === 'custom' ? (
                <div className="grid gap-4 lg:grid-cols-[minmax(0,1fr)_360px]">
                  <div className="rounded-xl border border-surface-200 bg-surface-50/70 p-4">
                    <div className="flex items-center justify-between gap-3">
                      <div>
                        <h3 className="text-base font-semibold text-surface-900">Saved custom voices</h3>
                        <p className="mt-1 text-sm text-surface-500">
                          Add an ElevenLabs Voice ID once, then reuse it anytime.
                        </p>
                      </div>
                      <span className="rounded-full border border-surface-200 bg-white px-2 py-0.5 text-[11px] font-medium text-surface-500">
                        {customVoices.length} saved
                      </span>
                    </div>

                    <div className="mt-4 space-y-3">
                      {customVoices.length === 0 ? (
                        <div className="rounded-xl border border-dashed border-surface-200 bg-white px-5 py-8 text-center text-sm text-surface-400">
                          No custom voices yet.
                        </div>
                      ) : (
                        customVoices
                          .filter((voice) => matchesVoiceSearch({
                            voice_id: voice.voice_id,
                            name: voice.name,
                            provider: 'elevenlabs',
                            source: 'custom',
                          }, voiceSearch))
                          .map((voice) => (
                            <div
                              key={voice.voice_id}
                              className="flex items-center justify-between gap-3 rounded-xl border border-surface-200 bg-white px-3 py-2.5"
                            >
                              <div className="min-w-0">
                                <p className="truncate text-sm font-semibold text-surface-900">{voice.name}</p>
                                <p className="mt-1 truncate text-xs text-surface-400">{voice.voice_id}</p>
                              </div>
                              <div className="flex items-center gap-2">
                                <button
                                  type="button"
                                  className="rounded-lg bg-surface-900 px-3 py-2 text-xs font-semibold text-white transition-colors hover:bg-surface-800"
                                  onClick={() => handleUseElevenVoice({
                                    voice_id: voice.voice_id,
                                    name: voice.name,
                                    category: 'Custom',
                                    tags: ['Custom'],
                                    source: 'custom',
                                    provider: 'elevenlabs',
                                  })}
                                >
                                  Use
                                </button>
                                <button
                                  type="button"
                                  className="btn-icon text-surface-400 hover:bg-red-50 hover:text-red-600"
                                  onClick={() => handleDeleteCustomVoice(voice.voice_id)}
                                >
                                  <Trash2 size={14} />
                                </button>
                              </div>
                            </div>
                          ))
                      )}
                    </div>
                  </div>

                  <div className="rounded-xl border border-surface-200 bg-white p-4">
                    <h3 className="text-base font-semibold text-surface-900">Add custom voice</h3>
                    <p className="mt-1 text-sm text-surface-500">
                      Save any ElevenLabs Voice ID with a short label.
                    </p>

                    <div className="mt-4 space-y-4">
                      <div>
                        <label className="label mb-1.5">Voice ID</label>
                        <input
                          className="input w-full"
                          placeholder="e.g. pMsXgVXv3BLzUgSXRplE"
                          value={customVoiceIdInput}
                          onChange={(event) => {
                            setCustomVoiceIdInput(event.target.value)
                            if (customVoiceError) setCustomVoiceError('')
                          }}
                        />
                      </div>
                      <div>
                        <label className="label mb-1.5">Display Name</label>
                        <input
                          className="input w-full"
                          placeholder="e.g. Documentary Narrator"
                          value={customVoiceNameInput}
                          onChange={(event) => {
                            setCustomVoiceNameInput(event.target.value)
                            if (customVoiceError) setCustomVoiceError('')
                          }}
                        />
                      </div>
                      {customVoiceError && (
                        <p className="text-xs font-medium text-red-600">{customVoiceError}</p>
                      )}
                      <button
                        type="button"
                        className="btn-primary w-full justify-center"
                        onClick={handleAddCustomVoice}
                      >
                        Add and Use Voice
                      </button>
                    </div>
                  </div>
                </div>
              ) : isElevenVoiceLibraryLoading ? (
                <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-3">
                  {Array.from({ length: 6 }).map((_, index) => (
                    <div key={index} className="animate-pulse rounded-xl border border-surface-200 bg-white p-4">
                      <div className="flex items-start gap-3">
                        <div className="h-10 w-10 rounded-2xl bg-surface-100" />
                        <div className="min-w-0 flex-1 space-y-2">
                          <div className="h-4 w-3/4 rounded-full bg-surface-100" />
                          <div className="h-3 w-1/3 rounded-full bg-surface-100" />
                        </div>
                      </div>
                      <div className="mt-4 flex gap-2">
                        <div className="h-7 w-20 rounded-full bg-surface-100" />
                        <div className="h-7 w-16 rounded-full bg-surface-100" />
                      </div>
                      <div className="mt-8 flex items-center justify-between border-t border-surface-200 pt-3">
                        <div className="flex gap-2">
                          <div className="h-8 w-8 rounded-xl bg-surface-100" />
                          <div className="h-8 w-8 rounded-xl bg-surface-100" />
                        </div>
                        <div className="h-10 w-16 rounded-xl bg-surface-100" />
                      </div>
                    </div>
                  ))}
                </div>
              ) : filteredElevenVoices.length === 0 ? (
                <div className="flex h-full min-h-[280px] flex-col items-center justify-center rounded-xl border border-dashed border-surface-200 bg-surface-50/70 px-6 text-center">
                  <Mic size={28} className="mb-3 text-surface-300" />
                  <p className="text-base font-medium text-surface-700">
                    {elevenVoiceTab === 'favorites'
                      ? 'No favorite voices yet'
                      : elevenVoiceTab === 'mine'
                        ? 'No MiniMax voices yet'
                        : 'No voices found'}
                  </p>
                  <p className="mt-1 text-sm text-surface-400">
                    {elevenVoiceTab === 'favorites'
                      ? 'Tap the heart icon on any voice to keep it here.'
                      : elevenVoiceTab === 'mine'
                        ? 'Clone a MiniMax voice first, then it will show up here.'
                      : 'Try another keyword or switch to a different tab.'}
                  </p>
                </div>
              ) : (
                <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-3">
                  {filteredElevenVoices.map((voice) => (
                    <ElevenVoiceCard
                      key={`${voice.source}-${voice.voice_id}`}
                      voice={voice}
                      selected={voice.voice_id === voiceId}
                      favorite={favoriteVoiceIds.includes(voice.voice_id)}
                      previewing={previewVoiceId === voice.voice_id}
                      onToggleFavorite={() => toggleFavoriteVoice(voice.voice_id)}
                      onTogglePreview={() => handlePreviewVoice(voice)}
                      onUse={() => handleUseElevenVoice(voice)}
                    />
                  ))}
                </div>
              )}
            </div>
          </div>
        </div>
      )}
    </div>
  )
}
