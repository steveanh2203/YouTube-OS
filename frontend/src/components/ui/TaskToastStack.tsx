import { useEffect, useState } from 'react'
import { AnimatePresence, motion } from 'framer-motion'
import {
  ArrowRight,
  AudioWaveform,
  CheckCircle2,
  FileText,
  Film,
  Music,
  MessageSquareReply,
  Radio,
  Scissors,
  Search,
  Square,
  Upload,
  XCircle,
  Zap,
} from 'lucide-react'
import { cutAutomateApi } from '@/lib/api'
import { useTaskStore, type BackgroundTask } from '@/store/task.store'
import { useAppStore, type ProjectSubView } from '@/store/app.store'
import { cn } from '@/lib/utils'

const TOOL_ICON: Record<ProjectSubView, React.ElementType> = {
  'cut-automate':     Scissors,
  'fast-edit':        Film,
  'ai-gen':           Zap,
  'ai-audio':         Music,
  'srt-gen':          FileText,
  'raw-seo':          Search,
  'roxy-upload':      Upload,
  'youtube-reply':    MessageSquareReply,
  livestream:         Radio,
  parents:            Film,
  children:           Film,
  'resource-prep':    Film,
  'sora-gen':         Film,
  'audio-visualizer': AudioWaveform,
}

function useAutoDismiss(task: BackgroundTask) {
  const { removeTask } = useTaskStore()

  useEffect(() => {
    if (task.status !== 'done' && task.status !== 'error' && task.status !== 'cancelled') return
    const delay = task.status === 'done' ? 4500 : 6500
    const timer = window.setTimeout(() => removeTask(task.id), delay)
    return () => window.clearTimeout(timer)
  }, [task.id, task.status, removeTask])
}

function TaskToast({ task }: { task: BackgroundTask }) {
  useAutoDismiss(task)
  const { removeTask } = useTaskStore()
  const {
    setActivePanelId,
    setSplitView,
    setPanelMainView,
    setPanelSubView,
    selectPanelParent,
    selectPanelChild,
  } = useAppStore()

  const ToolIcon = TOOL_ICON[task.toolId] ?? Film
  const isDone = task.status === 'done'
  const isError = task.status === 'error'
  const isCancelled = task.status === 'cancelled'
  const barColor = isDone ? 'bg-emerald-500' : isError ? 'bg-rose-500' : 'bg-amber-500'
  const iconBg = isDone ? 'bg-emerald-50' : isError ? 'bg-rose-50' : 'bg-amber-50'
  const iconColor = isDone ? 'text-emerald-500' : isError ? 'text-rose-500' : 'text-amber-600'
  const statusLabel = isDone ? 'Completed' : isError ? 'Error' : 'Stopped'

  return (
    <div className="w-[312px] overflow-hidden rounded-2xl border border-surface-200 bg-white shadow-xl">
      <div className={cn('h-[3px] w-full', barColor)} />

      <div className="space-y-2 px-3.5 py-3">
        <div className="flex items-start gap-2.5">
          <div className={cn('mt-0.5 flex h-8 w-8 shrink-0 items-center justify-center rounded-xl', iconBg, iconColor)}>
            {isDone ? <CheckCircle2 size={15} /> : isError ? <XCircle size={15} /> : <Square size={13} />}
          </div>

          <div className="min-w-0 flex-1">
            <p className="truncate text-xs font-semibold leading-snug text-surface-900">{task.label}</p>
            <p className="flex items-center gap-1 text-[11px] leading-snug text-surface-500">
              <ToolIcon size={9} className="shrink-0" />
              {task.toolLabel} · {statusLabel}
            </p>
          </div>

          <button
            onClick={() => removeTask(task.id)}
            aria-label="Dismiss task notification"
            className="mt-0.5 shrink-0 text-surface-300 transition-colors hover:text-surface-500"
          >
            <svg width="12" height="12" viewBox="0 0 12 12" fill="none">
              <path d="M1 1l10 10M11 1L1 11" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" />
            </svg>
          </button>
        </div>

        {task.message ? (
          <p
            className={cn(
              'px-1 text-[11px] leading-snug',
              isDone ? 'text-emerald-700' : isError ? 'text-rose-600' : isCancelled ? 'text-amber-700' : 'text-amber-700',
            )}
          >
            {task.message}
          </p>
        ) : null}

        <button
          onClick={() => {
            const targetPanelId = task.panelId ?? 'left'
            if (targetPanelId === 'right') setSplitView(true)
            setActivePanelId(targetPanelId)
            setPanelMainView(targetPanelId, task.targetMainView ?? 'projects')
            setPanelSubView(targetPanelId, task.targetProjectSubView ?? task.toolId)
            selectPanelParent(targetPanelId, task.targetParentId ?? null)
            selectPanelChild(targetPanelId, task.targetChildId ?? null)
            removeTask(task.id)
          }}
          className={cn(
            'flex w-full items-center justify-center gap-1.5 rounded-xl px-3 py-2 text-[11px] font-medium transition-colors',
            isDone
              ? 'bg-emerald-50 text-emerald-700 hover:bg-emerald-100'
              : isError
                ? 'bg-rose-50 text-rose-700 hover:bg-rose-100'
                : 'bg-amber-50 text-amber-700 hover:bg-amber-100',
          )}
        >
          Open {task.toolLabel}
          <ArrowRight size={11} />
        </button>
      </div>
    </div>
  )
}

/** Circular SVG progress ring — thay thế spinner icon trong task card */
function ProgressRing({ progress, size = 44 }: { progress: number; size?: number }) {
  const r = (size - 6) / 2
  const circ = 2 * Math.PI * r
  const offset = circ - (progress / 100) * circ
  return (
    <svg width={size} height={size} className="shrink-0 -rotate-90">
      <circle cx={size / 2} cy={size / 2} r={r} fill="none" stroke="rgba(139,92,246,0.15)" strokeWidth="5" />
      <circle
        cx={size / 2} cy={size / 2} r={r} fill="none"
        stroke="url(#prog-grad)" strokeWidth="5"
        strokeLinecap="round"
        strokeDasharray={circ}
        strokeDashoffset={offset}
        style={{ transition: 'stroke-dashoffset 0.4s ease-out' }}
      />
      <defs>
        <linearGradient id="prog-grad" x1="0%" y1="0%" x2="100%" y2="0%">
          <stop offset="0%" stopColor="#8b5cf6" />
          <stop offset="100%" stopColor="#6366f1" />
        </linearGradient>
      </defs>
    </svg>
  )
}

function RunningCutTaskDock({ tasks }: { tasks: BackgroundTask[] }) {
  const [stoppingIds, setStoppingIds] = useState<Record<string, boolean>>({})
  const [hidden, setHidden] = useState(false)

  const stopTask = async (task: BackgroundTask) => {
    if (!task.jobId) {
      useTaskStore.getState().completeTask(task.id, 'cancelled', 'Stopped by user.')
      return
    }

    setStoppingIds(prev => ({ ...prev, [task.id]: true }))
    try {
      await cutAutomateApi.stopJob(task.jobId)
    } catch {
      // Fallback to local state transition if stop endpoint is unavailable or fails.
    } finally {
      useTaskStore.getState().completeTask(task.id, 'cancelled', 'Stopped by user.')
      setStoppingIds(prev => {
        const next = { ...prev }
        delete next[task.id]
        return next
      })
    }
  }

  // Re-show popup when new tasks arrive
  useEffect(() => {
    if (tasks.length > 0) setHidden(false)
  }, [tasks.length])

  if (tasks.length === 0 || hidden) return null

  return (
    <div className="pointer-events-none fixed inset-0 z-[9999] flex items-center justify-center">
      <motion.div
        className="pointer-events-auto w-[min(460px,calc(100vw-2rem))]"
        initial={{ opacity: 0, scale: 0.96, y: 12 }}
        animate={{ opacity: 1, scale: 1, y: 0 }}
        exit={{ opacity: 0, scale: 0.96, y: 8 }}
        transition={{ type: 'spring', stiffness: 340, damping: 28 }}
      >
        {/* Card ngoài — light */}
        <div className="overflow-hidden rounded-3xl bg-white shadow-[0_24px_64px_rgba(15,23,42,0.16)] ring-1 ring-slate-200">

          {/* Header */}
          <div className="flex items-center justify-between px-5 py-4">
            <div className="flex items-center gap-3">
              {/* Pulsing dots animation */}
              <div className="flex items-center gap-1" aria-hidden="true">
                {[0, 1, 2].map(i => (
                  <span
                    key={i}
                    className="h-2 w-2 rounded-full bg-violet-500"
                    style={{ animation: `dot-pulse 1.2s ease-in-out ${i * 0.2}s infinite` }}
                  />
                ))}
              </div>
              <div>
                <p className="text-sm font-semibold text-slate-800">Cut Automate running</p>
                <p className="text-[11px] text-slate-400">
                  {tasks.length} job{tasks.length > 1 ? 's' : ''} running · UI vẫn dùng bình thường
                </p>
              </div>
            </div>

            {/* X để ẩn */}
            <button
              onClick={() => setHidden(true)}
              aria-label="Hide popup"
              className="rounded-lg p-1.5 text-slate-400 transition-colors hover:bg-slate-100 hover:text-slate-600"
            >
              <svg width="13" height="13" viewBox="0 0 13 13" fill="none">
                <path d="M1 1l11 11M12 1L1 12" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" />
              </svg>
            </button>
          </div>

          {/* Divider */}
          <div className="mx-5 h-px bg-slate-100" />

          {/* Task list */}
          <div className="max-h-[min(420px,calc(100dvh-160px))] overflow-y-auto px-5 py-4">
            <div className="space-y-3">
              <AnimatePresence initial={false}>
                {tasks.map(task => {
                  const progress = Math.max(2, Math.min(98, Math.round(task.progress ?? 0)))
                  const stopping = !!stoppingIds[task.id]

                  return (
                    <motion.div
                      key={task.id}
                      layout
                      initial={{ opacity: 0, y: 10 }}
                      animate={{ opacity: 1, y: 0 }}
                      exit={{ opacity: 0, y: 8, transition: { duration: 0.14 } }}
                      transition={{ type: 'spring', stiffness: 320, damping: 28 }}
                    >
                      {/* Task card */}
                      <div className="rounded-2xl bg-slate-50 p-4 ring-1 ring-slate-200">

                        {/* Row: progress ring + info + Stop */}
                        <div className="flex items-center gap-3">
                          {/* SVG Progress Ring */}
                          <div className="relative shrink-0">
                            <ProgressRing progress={progress} size={44} />
                            <span className="absolute inset-0 flex items-center justify-center text-[10px] font-bold text-violet-600 rotate-90">
                              {progress}
                            </span>
                          </div>

                          <div className="min-w-0 flex-1">
                            <p className="truncate text-sm font-semibold text-slate-800">{task.label}</p>
                            <p className="mt-0.5 truncate text-[11px] text-slate-400">
                              {task.message || 'Running in background…'}
                            </p>
                          </div>

                          {/* STOP button */}
                          <button
                            onClick={() => stopTask(task)}
                            disabled={stopping}
                            aria-label={`Stop ${task.label}`}
                            className={cn(
                              'shrink-0 rounded-xl px-3 py-1.5 text-[11px] font-semibold transition-all',
                              stopping
                                ? 'cursor-not-allowed bg-rose-100 text-rose-400'
                                : 'bg-rose-500 text-white hover:bg-rose-600 active:scale-95',
                            )}
                          >
                            {stopping ? 'Stopping…' : '■ Stop'}
                          </button>
                        </div>

                        {/* Progress bar */}
                        <div className="mt-3 overflow-hidden rounded-full bg-slate-200 h-1.5">
                          <div
                            className="h-full rounded-full transition-[width] duration-500 ease-out"
                            style={{
                              width: `${progress}%`,
                              background: 'linear-gradient(90deg, #8b5cf6, #6366f1)',
                            }}
                          />
                        </div>
                      </div>
                    </motion.div>
                  )
                })}
              </AnimatePresence>
            </div>
          </div>
        </div>

        {/* Keyframes cho pulsing dots */}
        <style>{`
          @keyframes dot-pulse {
            0%, 100% { opacity: 0.25; transform: scale(0.75); }
            50%       { opacity: 1;   transform: scale(1); }
          }
        `}</style>
      </motion.div>
    </div>
  )
}


export default function TaskToastStack() {
  const tasks = useTaskStore(s => s.tasks)
  const runningCutTasks = tasks.filter(task => task.toolId === 'cut-automate' && task.status === 'running')
  const visibleToasts = tasks.filter(task => task.status !== 'running')

  return (
    <>
      <div className="pointer-events-none fixed right-5 top-5 z-[9998] flex flex-col items-end gap-2.5">
        <AnimatePresence initial={false}>
          {visibleToasts.map(task => (
            <motion.div
              key={task.id}
              layout
              initial={{ opacity: 0, x: 20, scale: 0.95 }}
              animate={{ opacity: 1, x: 0, scale: 1 }}
              exit={{ opacity: 0, x: 16, scale: 0.95, transition: { duration: 0.15 } }}
              transition={{ type: 'spring', stiffness: 380, damping: 30 }}
              className="pointer-events-auto"
            >
              <TaskToast task={task} />
            </motion.div>
          ))}
        </AnimatePresence>
      </div>

      <RunningCutTaskDock tasks={runningCutTasks} />
    </>
  )
}
