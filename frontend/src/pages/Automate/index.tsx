import { useMemo, useState } from 'react'
import { pickMediaFile, pickWorkspaceDirectory } from '@/lib/browserPickers'
import { roxyApi } from '@/lib/api'
import { loadRoxyConfig, normalizeRoxyHost } from '@/lib/roxy'
import { useAppStore } from '@/store/app.store'
import { toast } from '@/store/toast.store'
import { cn } from '@/lib/utils'
import { usePanelContext } from '@/contexts/PanelContext'
import {
  FileSpreadsheet, FolderOpen, Play, Plus, Sparkles, TimerReset, X,
} from 'lucide-react'
type SessionStatus = 'draft' | 'ready' | 'running'

interface ShortAutomateSession {
  id: string
  parentId: string
  name: string
  folderPath: string
  mappingPath: string
  status: SessionStatus
  startDate: string
  publishSlots: [string, string]
}

type ProgressStage = 'idle' | 'scanning' | 'uploading' | 'checking' | 'scheduling' | 'done' | 'error'
type ProgressTone = 'info' | 'success' | 'error'
type UploadState = 'queued' | 'uploading' | 'done' | 'error'
type CheckState = 'pending' | 'checking' | 'done' | 'error'
type ScheduleState = 'pending' | 'planned' | 'error'

interface ProgressLogEntry {
  id: string
  time: string
  message: string
  tone: ProgressTone
}

interface ProgressVideoItem {
  path: string
  name: string
  uploadState: UploadState
  checkState: CheckState
  scheduleState: ScheduleState
  scheduleLabel: string
  lane?: number | null
  errorMsg?: string
}
const DEFAULT_PUBLISH_SLOTS: [string, string] = ['20:30', '09:00']
const DEFAULT_START_DATE = new Date().toISOString().slice(0, 10)
const SHORT_AUTOMATE_MAX_ACTIVE_TABS = 2
const SHORT_AUTOMATE_CHECK_WAIT_SECONDS = 30
const SHORT_AUTOMATE_COOLDOWN_MS = 5000

function normalizePublishSlots(value: unknown): [string, string] {
  if (Array.isArray(value) && value.length >= 2) {
    const first = typeof value[0] === 'string' && value[0].trim() ? value[0] : DEFAULT_PUBLISH_SLOTS[0]
    const second = typeof value[1] === 'string' && value[1].trim() ? value[1] : DEFAULT_PUBLISH_SLOTS[1]
    return [first, second]
  }
  return [...DEFAULT_PUBLISH_SLOTS]
}

function normalizeSession(session: ShortAutomateSession): ShortAutomateSession {
  return {
    ...session,
    startDate: typeof (session as ShortAutomateSession & { startDate?: unknown }).startDate === 'string'
      && (session as ShortAutomateSession & { startDate?: string }).startDate?.trim()
      ? (session as ShortAutomateSession & { startDate?: string }).startDate!.trim()
      : DEFAULT_START_DATE,
    publishSlots: normalizePublishSlots((session as ShortAutomateSession & { publishSlots?: unknown }).publishSlots),
  }
}

function combineDateTime(date: string, time: string) {
  const baseDate = date || DEFAULT_START_DATE
  const baseTime = time || '00:00'
  return new Date(`${baseDate}T${baseTime}:00`)
}

function addDays(date: Date, days: number) {
  const next = new Date(date)
  next.setDate(next.getDate() + days)
  return next
}

function formatDate(value: Date) {
  const day = String(value.getDate()).padStart(2, '0')
  const month = String(value.getMonth() + 1).padStart(2, '0')
  const year = value.getFullYear()
  return `${day}/${month}/${year}`
}

function formatTime(value: Date) {
  const hour = String(value.getHours()).padStart(2, '0')
  const minute = String(value.getMinutes()).padStart(2, '0')
  return `${hour}:${minute}`
}

function formatLocalIso(value: Date) {
  const year = value.getFullYear()
  const month = String(value.getMonth() + 1).padStart(2, '0')
  const day = String(value.getDate()).padStart(2, '0')
  const hour = String(value.getHours()).padStart(2, '0')
  const minute = String(value.getMinutes()).padStart(2, '0')
  const second = String(value.getSeconds()).padStart(2, '0')
  return `${year}-${month}-${day}T${hour}:${minute}:${second}`
}

function formatClockNow() {
  const now = new Date()
  return `${String(now.getHours()).padStart(2, '0')}:${String(now.getMinutes()).padStart(2, '0')}:${String(now.getSeconds()).padStart(2, '0')}`
}

function sleep(ms: number) {
  return new Promise((resolve) => window.setTimeout(resolve, ms))
}

function buildSchedulePlan(startDate: string, publishSlots: [string, string], count: number) {
  const plan: Array<{ label: string; date: string; time: string; iso: string }> = []
  let previous: Date | null = null
  let currentDate = startDate || DEFAULT_START_DATE

  for (let index = 0; index < count; index += 1) {
    const slot = publishSlots[index % publishSlots.length] || DEFAULT_PUBLISH_SLOTS[index % DEFAULT_PUBLISH_SLOTS.length]
    let candidate = combineDateTime(currentDate, slot)

    if (previous && candidate.getTime() <= previous.getTime()) {
      candidate = addDays(candidate, 1)
    }

    plan.push({
      label: `Video ${index + 1}`,
      date: formatDate(candidate),
      time: formatTime(candidate),
      iso: formatLocalIso(candidate),
    })

    previous = candidate
    currentDate = candidate.toISOString().slice(0, 10)
  }

  return plan
}

function buildSessionName(parentName: string, count: number) {
  return `${parentName} Session ${String(count).padStart(2, '0')}`
}

function statusMeta(status: SessionStatus) {
  if (status === 'running') return { label: 'Running', tone: 'bg-amber-100 text-amber-700 border-amber-200' }
  if (status === 'ready') return { label: 'Ready', tone: 'bg-emerald-100 text-emerald-700 border-emerald-200' }
  return { label: 'Draft', tone: 'bg-surface-100 text-surface-500 border-surface-200' }
}

function basename(path: string) {
  if (!path) return ''
  return path.split('/').pop() ?? path
}

function summarizeUiError(raw: string) {
  const source = raw.trim()
  if (!source) return 'Có lỗi khi xử lý video.'

  const lower = source.toLowerCase()
  if (lower.includes('target window already closed') || lower.includes('web view not found')) {
    return 'Tab upload đã bị đóng hoặc reset.'
  }
  if (lower.includes('could not open schedule section')) {
    return 'Không mở được phần Schedule của YouTube.'
  }
  if (lower.includes('could not set schedule date')) {
    return 'Không set được ngày đăng.'
  }
  if (lower.includes('could not set schedule time')) {
    return 'Không set được giờ đăng.'
  }
  if (lower.includes('could not click final schedule') || lower.includes('final schedule button did not become ready')) {
    return 'Không bấm được nút Schedule cuối.'
  }
  if (lower.includes('did not accept the selected file')) {
    return 'YouTube chưa nhận file video này.'
  }
  if (lower.includes('did not return open profile connection data')) {
    return 'Roxy chưa trả về kết nối profile.'
  }

  const cleaned = source
    .replace(/^message:\s*/i, '')
    .split('\n')[0]
    .split('Debug bundle:')[0]
    .split('Stacktrace:')[0]
    .split('(Session info:')[0]
    .trim()

  if (!cleaned) return 'Có lỗi khi xử lý video.'
  if (cleaned.length <= 96) return cleaned
  return `${cleaned.slice(0, 93).trim()}...`
}

function splitSessionHeading(sessionName: string) {
  const match = sessionName.match(/^(.*?)(Session\s+\d+)$/i)
  if (!match) {
    return { projectLabel: sessionName, sessionLabel: 'Session 01' }
  }
  return {
    projectLabel: match[1].trim(),
    sessionLabel: match[2].trim(),
  }
}

function ProgressModal({
  open,
  onClose,
  sessionName,
  stage,
  progress,
  logs,
  videos,
  sessions,
}: {
  open: boolean
  onClose: () => void
  sessionName: string
  stage: ProgressStage
  progress: number
  logs: ProgressLogEntry[]
  videos: ProgressVideoItem[]
  sessions: Array<{ id: string; name: string; status: SessionStatus; active: boolean }>
}) {
  if (!open) return null

  const { projectLabel, sessionLabel } = splitSessionHeading(sessionName)
  const stageMeta: Record<ProgressStage, { label: string; tone: string }> = {
    idle: { label: 'Sẵn sàng', tone: 'bg-surface-100 text-surface-500' },
    scanning: { label: 'Đang scan folder', tone: 'bg-blue-100 text-blue-700' },
    uploading: { label: 'Đang upload YouTube', tone: 'bg-primary-100 text-primary-700' },
    checking: { label: 'Đang chờ checks', tone: 'bg-amber-100 text-amber-700' },
    scheduling: { label: 'Đang chuẩn bị lịch', tone: 'bg-amber-100 text-amber-700' },
    done: { label: 'Hoàn tất', tone: 'bg-emerald-100 text-emerald-700' },
    error: { label: 'Có lỗi', tone: 'bg-red-100 text-red-700' },
  }
  const completedCount = videos.filter((item) => item.scheduleState === 'planned').length
  const errorCount = videos.filter((item) => item.uploadState === 'error' || item.scheduleState === 'error').length
  const activeLanes = Array.from(new Set(videos.map((item) => item.lane).filter(Boolean))).length

  return (
    <div className="fixed inset-0 z-[70] flex items-center justify-center bg-surface-950/35 backdrop-blur-sm">
      <div className="mx-4 flex w-full max-w-[1120px] flex-col overflow-hidden rounded-[26px] border border-surface-200 bg-white shadow-2xl">
        <div className="border-b border-surface-200 px-5 py-4">
          <div className="flex items-start justify-between gap-4">
            <div className="min-w-0">
              <p className="text-[10px] font-bold uppercase tracking-[0.18em] text-primary-600">Short Automate Log</p>
              <h3 className="mt-2 flex flex-wrap items-center gap-2 text-[26px] font-bold tracking-tight text-surface-900">
                <span className="truncate">{projectLabel}</span>
                <span className="text-surface-300">●</span>
                <span>{sessionLabel}</span>
              </h3>
              <p className="mt-1 text-sm text-surface-500">Một project có nhiều session, nhưng modal chỉ soi đúng session đang chạy.</p>
            </div>

            <div className="flex shrink-0 items-center gap-3">
              <span className={cn('rounded-full px-3 py-1 text-xs font-semibold', stageMeta[stage].tone)}>
                {stageMeta[stage].label}
              </span>
              <button
                className="flex h-10 w-10 items-center justify-center rounded-2xl border border-surface-200 bg-white text-surface-500 transition-colors hover:text-surface-700"
                onClick={onClose}
              >
                <X size={16} />
              </button>
            </div>
          </div>

          <div className="mt-4 flex flex-wrap items-center gap-2">
            {sessions.map((session, index) => (
              <div
                key={session.id}
                className={cn(
                  'flex items-center gap-2 rounded-2xl border px-3 py-2 text-xs transition-colors',
                  session.active ? 'border-primary-200 bg-primary-50 text-primary-700' : 'border-surface-200 bg-surface-50 text-surface-500',
                )}
              >
                <span className="font-semibold">S{String(index + 1).padStart(2, '0')}</span>
                <span className="max-w-[120px] truncate">{splitSessionHeading(session.name).sessionLabel}</span>
                <span className={cn('rounded-full px-2 py-0.5 text-[10px] font-semibold', statusMeta(session.status).tone)}>
                  {statusMeta(session.status).label}
                </span>
              </div>
            ))}
          </div>
        </div>

        <div className="grid grid-cols-[minmax(0,1fr)_320px] gap-0">
          <div className="border-r border-surface-200 px-5 py-4">
            <div className="rounded-[24px] border border-surface-200 bg-surface-50/70 p-4">
              <div className="flex flex-wrap items-center justify-between gap-3">
                <div className="flex min-w-0 items-center gap-3">
                  <div>
                    <p className="text-[10px] font-bold uppercase tracking-[0.16em] text-surface-400">Session progress</p>
                    <p className="mt-1 text-sm font-semibold text-surface-900">{progress}% hoàn thành</p>
                  </div>
                  <div className="h-9 w-px bg-surface-200" />
                  <div className="flex flex-wrap items-center gap-2 text-xs text-surface-500">
                    <span><strong className="text-surface-900">{videos.length}</strong> video</span>
                    <span><strong className="text-surface-900">{completedCount}</strong> done</span>
                    <span><strong className="text-surface-900">{errorCount}</strong> lỗi</span>
                    <span><strong className="text-surface-900">{activeLanes}</strong> lane</span>
                  </div>
                </div>
                <div className="rounded-full bg-white px-3 py-1 text-xs font-semibold text-primary-600 ring-1 ring-surface-200">
                  Active {sessionLabel}
                </div>
              </div>

              <div className="mt-4 h-2 overflow-hidden rounded-full bg-surface-200">
                <div className="h-full rounded-full bg-primary-500 transition-all duration-300" style={{ width: `${progress}%` }} />
              </div>

              <div className="mt-4 flex flex-wrap gap-2 text-xs">
                {[
                  { key: 'scan', label: 'Scan folder', active: ['scanning', 'uploading', 'checking', 'scheduling', 'done'].includes(stage) },
                  { key: 'upload', label: 'Upload', active: ['uploading', 'checking', 'scheduling', 'done'].includes(stage) },
                  { key: 'checks', label: 'Checks', active: ['checking', 'scheduling', 'done'].includes(stage) },
                  { key: 'schedule', label: 'Schedule', active: ['scheduling', 'done'].includes(stage) },
                ].map((item) => (
                  <div
                    key={item.key}
                    className={cn(
                      'rounded-full border px-3 py-1.5 text-center font-semibold',
                      item.active ? 'border-primary-200 bg-primary-50 text-primary-700' : 'border-surface-200 bg-white text-surface-400',
                    )}
                  >
                    {item.label}
                  </div>
                ))}
              </div>
            </div>

            <div className="mt-4 rounded-[24px] border border-surface-200 bg-white">
              <div className="flex items-center justify-between gap-3 border-b border-surface-200 px-4 py-3">
                <div>
                  <p className="text-[10px] font-bold uppercase tracking-[0.16em] text-surface-400">Live timeline</p>
                  <p className="mt-1 text-sm font-semibold text-surface-900">Session đang chạy nói gì thì hiện ở đây</p>
                </div>
              </div>

              <div className="max-h-[360px] space-y-0 overflow-y-auto px-4 py-2">
                {logs.length === 0 && (
                  <div className="rounded-2xl border border-dashed border-surface-300 bg-surface-50 px-4 py-5 text-sm text-surface-500">
                    Chưa có log nào, bấm `Short Automate` là tiến trình sẽ nhảy ở đây.
                  </div>
                )}

                {logs.map((entry) => (
                  <div key={entry.id} className="flex gap-3 border-b border-dashed border-surface-200 py-3 last:border-b-0">
                    <div className="flex w-[78px] shrink-0 items-start gap-2 pt-0.5">
                      <span className={cn(
                        'mt-1.5 h-2.5 w-2.5 shrink-0 rounded-full',
                        entry.tone === 'success' ? 'bg-emerald-500' : entry.tone === 'error' ? 'bg-red-500' : 'bg-primary-500',
                      )} />
                      <p className="text-xs font-semibold text-surface-400">{entry.time}</p>
                    </div>
                    <div className="min-w-0 flex-1">
                      <p className="text-sm leading-6 text-surface-700">{entry.message}</p>
                    </div>
                  </div>
                ))}
              </div>
            </div>
          </div>

          <div className="px-4 py-4">
            <div className="rounded-[24px] border border-surface-200 bg-surface-50/80 p-4">
              <p className="text-[10px] font-bold uppercase tracking-[0.16em] text-surface-400">Queue summary</p>
              <p className="mt-1 text-sm font-semibold text-surface-900">Nhiều session thì vẫn soi từng video rất gọn</p>

              <div className="mt-4 space-y-2.5">
                {videos.length === 0 && (
                  <div className="rounded-2xl border border-dashed border-surface-300 bg-white px-4 py-5 text-sm text-surface-500">
                    Chờ scan folder để dựng queue video.
                  </div>
                )}

                {videos.map((video, index) => (
                  <div key={video.path} className="rounded-[20px] border border-surface-200 bg-white px-3.5 py-3">
                    <div className="flex items-start justify-between gap-3">
                      <div className="min-w-0">
                        <div className="flex items-center gap-2">
                          <p className="text-[11px] font-semibold uppercase tracking-[0.14em] text-surface-400">Video {index + 1}</p>
                          {video.lane ? (
                            <span className="rounded-full bg-primary-50 px-2 py-0.5 text-[10px] font-semibold text-primary-600">
                              Lane {video.lane}
                            </span>
                          ) : null}
                        </div>
                        <p className="mt-1 truncate text-sm font-semibold text-surface-900">{video.name}</p>
                      </div>
                      <span className={cn(
                        'rounded-full px-2.5 py-1 text-[11px] font-semibold',
                        video.uploadState === 'done'
                          ? 'bg-emerald-100 text-emerald-700'
                          : video.uploadState === 'uploading'
                            ? 'bg-primary-100 text-primary-700'
                            : video.uploadState === 'error'
                              ? 'bg-red-100 text-red-700'
                              : 'bg-surface-100 text-surface-500',
                      )}>
                        {video.uploadState === 'done'
                          ? 'Uploaded'
                          : video.uploadState === 'uploading'
                            ? 'Uploading'
                            : video.uploadState === 'error'
                              ? 'Error'
                              : 'Queued'}
                      </span>
                    </div>

                    <div className="mt-3 flex flex-wrap gap-2 text-[11px]">
                      <div className="rounded-full bg-surface-50 px-2.5 py-1 text-surface-600">
                        {video.uploadState === 'done'
                          ? 'Uploaded sang Roxy'
                          : video.uploadState === 'uploading'
                            ? 'Đang upload YouTube'
                            : video.uploadState === 'error'
                              ? (video.errorMsg || 'Upload lỗi')
                              : 'Chờ upload'}
                      </div>
                      <div className="rounded-full bg-surface-50 px-2.5 py-1 text-surface-600">
                        {video.checkState === 'done'
                          ? `Checks ${SHORT_AUTOMATE_CHECK_WAIT_SECONDS}s`
                          : video.checkState === 'checking'
                            ? `Đang check ${SHORT_AUTOMATE_CHECK_WAIT_SECONDS}s`
                            : video.checkState === 'error'
                              ? (video.errorMsg || 'Checks lỗi')
                              : 'Chờ checks'}
                      </div>
                      <div className="rounded-full bg-surface-50 px-2.5 py-1 text-surface-600">
                        {video.scheduleState === 'planned'
                          ? `Scheduled ${video.scheduleLabel}`
                          : video.scheduleState === 'error'
                            ? (video.errorMsg || 'Schedule lỗi')
                            : `Dự kiến ${video.scheduleLabel}`}
                      </div>
                    </div>
                  </div>
                ))}
              </div>
            </div>
          </div>
        </div>
      </div>
    </div>
  )
}

export default function AutomatePage() {
  const { parentProjects } = useAppStore()
  const { automateSubView } = usePanelContext()
  const [sessions, setSessions] = useState<ShortAutomateSession[]>([])
  const [selectedParentId, setSelectedParentId] = useState<string>(parentProjects[0]?.id ?? '')
  const [selectedSessionId, setSelectedSessionId] = useState<string | null>(null)
  const [progressOpen, setProgressOpen] = useState(false)
  const [progressStage, setProgressStage] = useState<ProgressStage>('idle')
  const [progressValue, setProgressValue] = useState(0)
  const [progressLogs, setProgressLogs] = useState<ProgressLogEntry[]>([])
  const [progressVideos, setProgressVideos] = useState<ProgressVideoItem[]>([])

  const selectedParent = parentProjects.find((item) => item.id === selectedParentId) ?? null
  const sessionsForParent = useMemo(
    () => sessions.filter((item) => item.parentId === selectedParentId),
    [sessions, selectedParentId],
  )
  const rawActiveSession =
    sessionsForParent.find((item) => item.id === selectedSessionId)
    ?? sessionsForParent[0]
    ?? null
  const activeSession = rawActiveSession ? normalizeSession(rawActiveSession) : null

  const pushProgressLog = (message: string, tone: ProgressTone = 'info') => {
    setProgressLogs((prev) => [
      ...prev,
      {
        id: `${Date.now()}-${Math.random().toString(36).slice(2, 7)}`,
        time: formatClockNow(),
        message,
        tone,
      },
    ])
  }

  const createSession = () => {
    if (!selectedParent) return
    const nextCount = sessions.filter((item) => item.parentId === selectedParent.id).length + 1
    const next: ShortAutomateSession = {
      id: `short-${Date.now()}`,
      parentId: selectedParent.id,
      name: buildSessionName(selectedParent.name, nextCount),
      folderPath: '',
      mappingPath: '',
      status: 'draft',
      startDate: DEFAULT_START_DATE,
      publishSlots: [...DEFAULT_PUBLISH_SLOTS] as [string, string],
    }
    setSessions((prev) => {
      const updated = [next, ...prev]
      setSelectedSessionId(next.id)
      return updated
    })
  }

  const patchSession = (sessionId: string, patch: Partial<ShortAutomateSession>) => {
    setSessions((prev) => prev.map((item) => {
      if (item.id !== sessionId) return item
      const next = normalizeSession({ ...item, ...patch } as ShortAutomateSession)
      const ready = !!next.folderPath
      if (next.status !== 'running') next.status = ready ? 'ready' : 'draft'
      return next
    }))
  }

  const pickFolder = async () => {
    if (!activeSession) return
    const picked = await pickWorkspaceDirectory(activeSession.name || 'Automate videos')
    if (picked && typeof picked === 'string') patchSession(activeSession.id, { folderPath: picked })
  }

  const pickMapping = async () => {
    if (!activeSession) return
    const picked = await pickMediaFile('.xlsx,.xls,.csv')
    if (picked) patchSession(activeSession.id, { mappingPath: picked.reference })
  }

  const runSession = async () => {
    if (!activeSession) return
    if (!selectedParent) return

    if (!activeSession.folderPath.trim()) {
      toast.error('Thiếu folder', 'Chọn folder video trước đã.')
      return
    }

    if (!selectedParent.roxyWorkspaceId || !selectedParent.roxyProfileId) {
      toast.error('Thiếu profile', 'Parent project chưa cắm Roxy workspace/profile.')
      return
    }

    const roxy = loadRoxyConfig()
    if (!roxy.apiToken.trim()) {
      toast.error('Thiếu Roxy API token', 'Mở Automate V2 hoặc Roxy Upload để cấu hình token trước.')
      return
    }

    setProgressOpen(true)
    setProgressStage('scanning')
    setProgressValue(5)
    setProgressLogs([])
    setProgressVideos([])
    pushProgressLog('Bắt đầu scan folder video short.')
    patchSession(activeSession.id, { status: 'running' })

    try {
      const scan = await roxyApi.folderVideos(activeSession.folderPath.trim())
      if (!scan.ok) {
        throw new Error(scan.message)
      }
      if (scan.videos.length === 0) {
        throw new Error('Folder chưa có video nào để upload.')
      }

      const plans = buildSchedulePlan(activeSession.startDate, activeSession.publishSlots, scan.videos.length)
      setProgressVideos(scan.videos.map((videoPath, index) => ({
        path: videoPath,
        name: basename(videoPath),
        uploadState: 'queued',
        checkState: 'pending',
        scheduleState: 'pending',
        scheduleLabel: `${plans[index]?.date}, ${plans[index]?.time}`,
        lane: null,
      })))
      setProgressValue(12)
      pushProgressLog(`Đã tìm thấy ${scan.videos.length} video trong folder.`, 'success')
      setProgressStage('uploading')

      const laneCount = Math.min(SHORT_AUTOMATE_MAX_ACTIVE_TABS, scan.videos.length)
      let nextIndex = 0
      let completed = 0
      let failed = 0

      const updateProgress = () => {
        const handled = completed + failed
        setProgressValue(Math.min(92, 12 + Math.round((handled / scan.videos.length) * 76)))
      }

      const runLane = async (lane: number) => {
        if (lane > 1) {
          pushProgressLog(`Lane ${lane} nghỉ ${(SHORT_AUTOMATE_COOLDOWN_MS / 1000).toFixed(0)}s rồi mới mở tab đầu tiên.`)
          await sleep(SHORT_AUTOMATE_COOLDOWN_MS)
        }

        while (nextIndex < scan.videos.length) {
          const index = nextIndex
          nextIndex += 1

          const videoPath = scan.videos[index]
          const plan = plans[index]
          const videoName = basename(videoPath)

          setProgressStage('uploading')
          setProgressVideos((prev) => prev.map((item) => (
            item.path === videoPath
              ? { ...item, uploadState: 'uploading', checkState: 'checking', lane }
              : item
          )))
          pushProgressLog(`Lane ${lane}: đang mở tab mới và upload ${videoName}.`)
          pushProgressLog(`Lane ${lane}: sẽ chờ ${SHORT_AUTOMATE_CHECK_WAIT_SECONDS}s để YouTube check content trước khi bấm Schedule.`)

          try {
            const res = await roxyApi.upload({
              api_host: normalizeRoxyHost(roxy.apiHost),
              api_token: roxy.apiToken.trim(),
              workspace_id: selectedParent.roxyWorkspaceId!,
              profile_id: selectedParent.roxyProfileId,
              video_path: videoPath,
              schedule_at: plan?.iso,
              close_after: false,
              close_tab_after: true,
              check_wait_seconds: SHORT_AUTOMATE_CHECK_WAIT_SECONDS,
            })
            if (!res.ok) {
              throw new Error(res.message)
            }

            completed += 1
            setProgressStage('scheduling')
            setProgressVideos((prev) => prev.map((item) => (
              item.path === videoPath
                ? {
                  ...item,
                  uploadState: 'done',
                  checkState: 'done',
                  scheduleState: res.schedule_applied ? 'planned' : 'error',
                  scheduleLabel: res.scheduled_at ? `${plan?.date}, ${plan?.time}` : item.scheduleLabel,
                  lane: null,
                }
                : item
            )))
            pushProgressLog(
              res.schedule_applied
                ? `Lane ${lane}: ${videoName} đã upload, chờ checks xong, save lịch và đóng tab.`
                : `Lane ${lane}: ${videoName} upload xong nhưng chưa save lịch.`,
              res.schedule_applied ? 'success' : 'error',
            )
          } catch (error) {
            failed += 1
            const shortError = summarizeUiError(error instanceof Error ? error.message : 'Upload lỗi')
            setProgressVideos((prev) => prev.map((item) => (
              item.path === videoPath
                ? {
                  ...item,
                  uploadState: 'error',
                  checkState: 'error',
                  scheduleState: 'error',
                  lane: null,
                  errorMsg: shortError,
                }
                : item
            )))
            pushProgressLog(
              `Lane ${lane}: ${videoName} lỗi, tab sẽ đóng và chuyển video kế tiếp.`,
              'error',
            )
            pushProgressLog(
              shortError,
              'error',
            )
          }

          updateProgress()

          if (nextIndex < scan.videos.length) {
            pushProgressLog(`Lane ${lane}: nghỉ ${(SHORT_AUTOMATE_COOLDOWN_MS / 1000).toFixed(0)}s rồi qua video kế tiếp.`)
            await sleep(SHORT_AUTOMATE_COOLDOWN_MS)
          }
        }
      }

      setProgressStage('checking')
      await Promise.all(
        Array.from({ length: laneCount }, (_value, laneIndex) => runLane(laneIndex + 1)),
      )

      setProgressStage('scheduling')
      setProgressValue(90)
      pushProgressLog('Đã hoàn tất bước schedule cho batch hiện tại.', 'success')
      patchSession(activeSession.id, { status: 'ready' })
      setProgressStage(failed > 0 ? 'error' : 'done')
      setProgressValue(100)
      pushProgressLog(
        failed > 0
          ? `Batch xong: ${completed} video đã schedule, ${failed} video lỗi hoặc cần xử lý lại.`
          : 'Batch upload + schedule đã hoàn tất.',
        failed > 0 ? 'error' : 'success',
      )
      if (failed > 0) {
        toast.error('Short Automate', `Đã schedule ${completed}/${scan.videos.length} video. Còn ${failed} video lỗi.`)
      } else {
        toast.success('Short Automate', `Đã đẩy ${scan.videos.length} video sang Roxy.`)
      }
    } catch (error) {
      const shortError = summarizeUiError(error instanceof Error ? error.message : 'Không mở được Roxy upload.')
      patchSession(activeSession.id, { status: 'ready' })
      setProgressStage('error')
      setProgressVideos((prev) => {
        const activeUploading = prev.find((item) => item.uploadState === 'uploading')
        if (!activeUploading) return prev
        return prev.map((item) => (
          item.path === activeUploading.path
            ? { ...item, uploadState: 'error', errorMsg: shortError }
            : item
        ))
      })
      pushProgressLog(
        shortError,
        'error',
      )
      toast.error(
        'Short Automate failed',
        shortError,
      )
    }
  }

  const schedulePreview = activeSession ? buildSchedulePlan(activeSession.startDate, activeSession.publishSlots, 2) : []
  const isRunning = ['scanning', 'uploading', 'checking', 'scheduling'].includes(progressStage)
  const canRun = !!activeSession?.folderPath && !!selectedParent && !isRunning

  return (
    <div className="flex h-full flex-col bg-surface-50">
      <ProgressModal
        open={progressOpen}
        onClose={() => setProgressOpen(false)}
        sessionName={activeSession?.name ?? 'Short Automate'}
        stage={progressStage}
        progress={progressValue}
        logs={progressLogs}
        videos={progressVideos}
        sessions={sessionsForParent.map((session) => ({
          id: session.id,
          name: session.name,
          status: session.status,
          active: session.id === activeSession?.id,
        }))}
      />
      <div className="border-b border-surface-200 bg-white px-6 py-4">
        <div className="flex items-start justify-between gap-4">
          <div>
            <h1 className="text-2xl font-bold tracking-tight text-surface-900">Automate</h1>
            <p className="mt-2 max-w-2xl text-sm text-surface-500">
              Chia session theo parent project. Làm `Short Automate` trước, giữ sẵn lane cho `Long Automate`.
            </p>
          </div>

          <div className="flex min-w-[340px] justify-end">
            <div className="inline-flex overflow-hidden rounded-[22px] border border-surface-200 bg-surface-50 shadow-sm">
              <div className="flex min-w-[156px] items-center gap-3 px-4 py-3">
                <div className="flex h-10 w-10 items-center justify-center rounded-2xl bg-white text-primary-600 ring-1 ring-surface-200">
                  <FolderOpen size={16} />
                </div>
                <div className="min-w-0">
                  <p className="text-[10px] font-bold uppercase tracking-[0.16em] text-surface-400">Parent projects</p>
                  <p className="mt-1 text-xl font-bold tracking-tight text-surface-900">{parentProjects.length}</p>
                </div>
              </div>

              <div className="w-px bg-surface-200" />

              <div className="flex min-w-[156px] items-center gap-3 px-4 py-3">
                <div className="flex h-10 w-10 items-center justify-center rounded-2xl bg-white text-primary-600 ring-1 ring-surface-200">
                  <Sparkles size={16} />
                </div>
                <div className="min-w-0">
                  <p className="text-[10px] font-bold uppercase tracking-[0.16em] text-surface-400">
                    {automateSubView === 'long' ? 'Long sessions' : 'Short sessions'}
                  </p>
                  <p className="mt-1 text-xl font-bold tracking-tight text-surface-900">{sessions.length}</p>
                </div>
              </div>
            </div>
          </div>
        </div>
      </div>

      <div className="grid flex-1 min-h-0 grid-cols-[320px_minmax(0,1fr)] gap-0">
        <aside className="border-r border-surface-200 bg-white p-4">
          {automateSubView === 'short' ? (
            <>
              <div className="rounded-3xl border border-surface-200 bg-surface-50/80 p-4 shadow-sm">
              <div className="mb-3 flex items-center justify-between gap-3">
                <div>
                  <p className="text-[10px] font-bold uppercase tracking-[0.18em] text-surface-400">Control</p>
                  <p className="mt-1 text-sm font-semibold text-surface-900">Parent project</p>
                </div>
                <div className="flex h-10 w-10 items-center justify-center rounded-2xl bg-white text-primary-600 shadow-sm ring-1 ring-surface-200">
                  <FolderOpen size={16} />
                </div>
              </div>

              <select
                className="input h-11 w-full rounded-2xl border-surface-200 bg-white text-sm font-medium shadow-sm"
                value={selectedParentId}
                onChange={(event) => {
                  const nextParentId = event.target.value
                  setSelectedParentId(nextParentId)
                  const firstSession = sessions.find((item) => item.parentId === nextParentId)
                  setSelectedSessionId(firstSession?.id ?? null)
                }}
              >
                {parentProjects.length === 0 && <option value="">- No parent projects yet -</option>}
                {parentProjects.map((project) => (
                  <option key={project.id} value={project.id}>{project.name}</option>
                ))}
              </select>

              <button
                className="btn-primary mt-3 h-11 w-full justify-center rounded-2xl text-sm shadow-sm"
                onClick={createSession}
                disabled={!selectedParent}
              >
                <Plus size={14} />
                New Session
              </button>
              </div>

              <div className="mt-4">
                <div className="mb-3 flex items-center justify-between">
                  <div>
                    <p className="text-[10px] font-bold uppercase tracking-[0.18em] text-surface-400">Sessions</p>
                    <p className="mt-1 text-sm font-semibold text-surface-900">Automate sessions</p>
                  </div>
                  <span className="inline-flex h-9 min-w-9 items-center justify-center rounded-2xl bg-surface-100 px-3 text-[11px] font-semibold text-surface-500 ring-1 ring-surface-200">
                    {sessionsForParent.length}
                  </span>
                </div>

                <div className="space-y-3">
                  {sessionsForParent.length === 0 && (
                    <div className="rounded-3xl border border-dashed border-surface-300 bg-surface-50/70 px-5 py-7 text-center">
                      <div className="mx-auto flex h-11 w-11 items-center justify-center rounded-2xl bg-white text-surface-400 shadow-sm ring-1 ring-surface-200">
                        <Sparkles size={16} />
                      </div>
                      <p className="mt-4 text-sm font-semibold text-surface-800">Chưa có session nào</p>
                      <p className="mt-1 text-xs leading-5 text-surface-500">
                        Tạo session mới để bắt đầu rename, map file Excel và lên lịch đăng short.
                      </p>
                    </div>
                  )}

                  {sessionsForParent.map((session) => {
                    const meta = statusMeta(session.status)
                    return (
                      <button
                        key={session.id}
                        className={cn(
                          'w-full rounded-2xl border px-4 py-4 text-left transition-colors',
                          activeSession?.id === session.id
                            ? 'border-primary-300 bg-primary-50/60'
                            : 'border-surface-200 bg-white hover:border-primary-200 hover:bg-surface-50',
                        )}
                        onClick={() => setSelectedSessionId(session.id)}
                      >
                        <div className="flex items-start justify-between gap-3">
                          <div className="min-w-0">
                            <p className="truncate text-sm font-semibold text-surface-900">{session.name}</p>
                            <p className="mt-1 text-xs text-surface-500">{session.folderPath ? basename(session.folderPath) : 'Chưa chọn folder'}</p>
                          </div>
                          <span className={cn('rounded-full border px-2 py-1 text-[11px] font-semibold', meta.tone)}>
                            {meta.label}
                          </span>
                        </div>
                      </button>
                    )
                  })}
                </div>
              </div>
            </>
          ) : (
            <div className="mt-4 rounded-3xl border border-dashed border-surface-300 bg-surface-50/70 px-5 py-7 text-center">
              <div className="mx-auto flex h-11 w-11 items-center justify-center rounded-2xl bg-white text-surface-400 shadow-sm ring-1 ring-surface-200">
                <TimerReset size={16} />
              </div>
              <p className="mt-4 text-sm font-semibold text-surface-800">Long Automate sẽ làm sau</p>
              <p className="mt-1 text-xs leading-5 text-surface-500">
                Chuyển mode bằng dropdown bên trên, không cần tab ngang nữa.
              </p>
            </div>
          )}
        </aside>

        <section className="min-h-0 overflow-y-auto p-6">
          {automateSubView === 'long' ? (
            <div className="flex min-h-full items-center justify-center">
              <div className="max-w-xl rounded-3xl border border-dashed border-surface-300 bg-white px-8 py-10 text-center shadow-sm">
                <div className="mx-auto flex h-14 w-14 items-center justify-center rounded-2xl bg-surface-100 text-surface-500">
                  <TimerReset size={24} />
                </div>
                <h2 className="mt-5 text-xl font-semibold text-surface-900">Long Automate sẽ làm sau</h2>
                <p className="mt-2 text-sm leading-6 text-surface-500">
                  Mình để riêng lane này để sau build flow cho video dài, không nhét chung vào short.
                </p>
              </div>
            </div>
          ) : !selectedParent ? (
            <div className="rounded-3xl border border-dashed border-surface-300 bg-white px-8 py-10 text-center shadow-sm">
              <p className="text-lg font-semibold text-surface-900">Chưa có parent project</p>
              <p className="mt-2 text-sm text-surface-500">Tạo parent project trước rồi quay lại đây để mở lane automate.</p>
            </div>
          ) : !activeSession ? (
            <div className="rounded-3xl border border-dashed border-surface-300 bg-white px-8 py-10 text-center shadow-sm">
              <p className="text-lg font-semibold text-surface-900">Chọn hoặc tạo một session</p>
              <p className="mt-2 text-sm text-surface-500">Mỗi parent project có thể giữ nhiều session automate riêng, để batch short không bị lẫn nhau.</p>
            </div>
          ) : (
            <div className="space-y-6">
                <div className="rounded-3xl border border-surface-200 bg-white p-6 shadow-sm">
                  <div className="flex flex-wrap items-start justify-between gap-4">
                    <div>
                      <p className="text-xs font-semibold uppercase tracking-[0.18em] text-primary-600">Short Automate</p>
                      <h2 className="mt-2 text-2xl font-bold tracking-tight text-surface-900">{activeSession.name}</h2>
                      <p className="mt-1 text-sm text-surface-500">
                        Parent project: <span className="font-medium text-surface-700">{selectedParent.name}</span>
                      </p>
                    </div>

                    <button
                      className="btn-primary"
                      disabled={!canRun}
                      onClick={runSession}
                    >
                      <Play size={14} />
                      Short Automate
                    </button>
                  </div>

                  <div className="mt-5 grid grid-cols-4 gap-4">
                    <div className="rounded-2xl bg-surface-50 px-4 py-4">
                      <p className="text-xs font-medium text-surface-400">Folder</p>
                      <p className="mt-2 text-sm font-semibold text-surface-900">{activeSession.folderPath ? 'Ready' : 'Pending'}</p>
                    </div>
                    <div className="rounded-2xl bg-surface-50 px-4 py-4">
                      <p className="text-xs font-medium text-surface-400">Excel map</p>
                      <p className="mt-2 text-sm font-semibold text-surface-900">{activeSession.mappingPath ? 'Imported' : 'Pending'}</p>
                    </div>
                    <div className="rounded-2xl bg-surface-50 px-4 py-4">
                      <p className="text-xs font-medium text-surface-400">Profile</p>
                      <p className="mt-2 text-sm font-semibold text-surface-900">{selectedParent.roxyProfileName || selectedParent.roxyProfileId || 'Not set'}</p>
                    </div>
                    <div className="rounded-2xl bg-surface-50 px-4 py-4">
                      <p className="text-xs font-medium text-surface-400">Schedule</p>
                      <p className="mt-2 text-sm font-semibold text-surface-900">2 daily slots</p>
                    </div>
                  </div>
                </div>

                <div className="grid grid-cols-[minmax(0,1fr)] gap-6">
                  <div className="rounded-3xl border border-surface-200 bg-white p-6 shadow-sm">
                    <h3 className="text-lg font-semibold text-surface-900">Session setup</h3>
                    <p className="mt-1 text-sm text-surface-500">Mình dựng sẵn đúng flow mày chốt, rename trước, rồi mới upload và schedule.</p>

                    <div className="mt-5 space-y-5">
                      <div>
                        <label className="label mb-2">Session name</label>
                        <input
                          className="input w-full text-sm"
                          value={activeSession.name}
                          onChange={(event) => patchSession(activeSession.id, { name: event.target.value })}
                        />
                      </div>

                      <div>
                        <label className="label mb-2">Video folder</label>
                        <div className="flex gap-2">
                          <input className="input flex-1 text-sm font-mono" value={activeSession.folderPath} readOnly placeholder="/Users/.../Short Batch" />
                          <button className="btn-secondary" onClick={pickFolder}>
                            <FolderOpen size={14} />
                            Browse
                          </button>
                        </div>
                      </div>

                      <div>
                        <div className="mb-2 flex items-center gap-2">
                          <label className="label">Excel / CSV mapping file</label>
                          <span className="rounded-full bg-surface-100 px-2 py-0.5 text-[10px] font-semibold uppercase tracking-[0.14em] text-surface-500 ring-1 ring-surface-200">
                            Optional
                          </span>
                        </div>
                        <div className="flex gap-2">
                          <input className="input flex-1 text-sm font-mono" value={activeSession.mappingPath} readOnly placeholder="video_001 -> title" />
                          <button className="btn-secondary" onClick={pickMapping}>
                            <FileSpreadsheet size={14} />
                            Import
                          </button>
                        </div>
                        <p className="mt-2 text-xs text-surface-400">
                          Có file thì rename theo mapping. Không có thì giữ tên file hiện tại.
                        </p>
                      </div>

                      <div className="grid grid-cols-2 gap-4">
                        <div>
                          <label className="label mb-2">Start date</label>
                          <input
                            className="input w-full text-sm"
                            type="date"
                            value={activeSession.startDate}
                            onChange={(event) => patchSession(activeSession.id, { startDate: event.target.value })}
                          />
                        </div>
                        <div />
                      </div>

                      <div className="grid grid-cols-2 gap-4">
                        <div>
                          <label className="label mb-2">Slot 1 time</label>
                          <input
                            className="input w-full text-sm"
                            type="time"
                            value={activeSession.publishSlots[0]}
                            onChange={(event) => patchSession(activeSession.id, {
                              publishSlots: [event.target.value, activeSession.publishSlots[1]],
                            })}
                          />
                        </div>
                        <div>
                          <label className="label mb-2">Slot 2 time</label>
                          <input
                            className="input w-full text-sm"
                            type="time"
                            value={activeSession.publishSlots[1]}
                            onChange={(event) => patchSession(activeSession.id, {
                              publishSlots: [activeSession.publishSlots[0], event.target.value],
                            })}
                          />
                        </div>
                      </div>

                      <div className="rounded-2xl border border-surface-200 bg-surface-50/80 p-4">
                        <div className="flex items-center justify-between gap-3">
                          <div>
                            <p className="text-[10px] font-bold uppercase tracking-[0.16em] text-surface-400">Schedule Preview</p>
                            <p className="mt-1 text-sm font-semibold text-surface-900">Mapping theo thứ tự video</p>
                          </div>
                          <div className="rounded-full bg-white px-2.5 py-1 text-[11px] font-semibold text-primary-600 ring-1 ring-surface-200">
                            2 slots/day
                          </div>
                        </div>

                        <div className="mt-4 space-y-2">
                          {schedulePreview.map((item) => (
                            <div key={item.label} className="flex items-center justify-between gap-3 rounded-2xl bg-white px-3 py-2 ring-1 ring-surface-200">
                              <span className="text-sm font-medium text-surface-700">{item.label}</span>
                              <span className="text-sm font-semibold text-surface-900">{item.date}, {item.time}</span>
                            </div>
                          ))}
                        </div>
                      </div>
                    </div>
                  </div>
                </div>
            </div>
          )}
        </section>
      </div>
    </div>
  )
}
