import { useEffect, useRef } from 'react'
import { AnimatePresence, motion } from 'framer-motion'
import { CheckCircle2, XCircle, Loader, ArrowRight, Scissors, Zap, Music, Film, Upload, FileText, Search, Clapperboard, Radio } from 'lucide-react'
import { useTaskStore, type BackgroundTask } from '@/store/task.store'
import { useAppStore, type ProjectSubView } from '@/store/app.store'
import { cn } from '@/lib/utils'

// ─── Tool icon map ─────────────────────────────────────────────────────────────

const TOOL_ICON: Record<ProjectSubView, React.ElementType> = {
  'cut-automate':  Scissors,
  'fast-edit':     Film,
  'ai-gen':        Zap,
  'ai-audio':      Music,
  'srt-gen':       FileText,
  'raw-seo':       Search,
  'roxy-upload':   Upload,
  'animation':     Clapperboard,
  'livestream':    Radio,
  // Non-tool views — fallback icon (won't appear in task toasts)
  'parents':       Film,
  'children':      Film,
  'resource-prep': Film,
}

// ─── Auto-dismiss hook ─────────────────────────────────────────────────────────

function useAutoDismiss(task: BackgroundTask) {
  const { removeTask } = useTaskStore()
  const timerRef = useRef<ReturnType<typeof setTimeout> | null>(null)

  useEffect(() => {
    if (task.status === 'done' || task.status === 'error') {
      const delay = task.status === 'done' ? 5000 : 8000
      timerRef.current = setTimeout(() => removeTask(task.id), delay)
    }
    return () => { if (timerRef.current) clearTimeout(timerRef.current) }
  }, [task.status, task.id, removeTask])
}

// ─── Single task toast ─────────────────────────────────────────────────────────

function TaskToast({ task }: { task: BackgroundTask }) {
  useAutoDismiss(task)
  const { removeTask } = useTaskStore()
  const setProjectSubView = useAppStore(s => s.setProjectSubView)

  const ToolIcon = TOOL_ICON[task.toolId] ?? Film

  const isRunning = task.status === 'running'
  const isDone    = task.status === 'done'
  const isError   = task.status === 'error'

  const barColor    = isDone ? 'bg-emerald-500' : isError ? 'bg-rose-500' : 'bg-indigo-400'
  const iconBg      = isDone ? 'bg-emerald-50'  : isError ? 'bg-rose-50'  : 'bg-indigo-50'
  const iconColor   = isDone ? 'text-emerald-500': isError ? 'text-rose-500': 'text-indigo-500'
  const statusLabel = isDone ? 'Completed'      : isError ? 'Error'      : 'Running…'

  return (
    <div className="w-[300px] bg-white rounded-xl shadow-lg border border-surface-100 overflow-hidden">
      {/* Top colour bar */}
      <div className={cn('h-[3px] w-full', barColor)} />

      <div className="px-3.5 py-3 space-y-2">
        {/* Row 1: icon + label + close */}
        <div className="flex items-start gap-2.5">
          <div className={cn('mt-0.5 shrink-0 w-7 h-7 rounded-lg flex items-center justify-center', iconBg, iconColor)}>
            {isRunning
              ? <Loader size={14} className="animate-spin" />
              : isDone
                ? <CheckCircle2 size={14} />
                : <XCircle size={14} />
            }
          </div>

          <div className="flex-1 min-w-0">
            <p className="text-xs font-semibold text-surface-800 leading-snug truncate">
              {task.label}
            </p>
            <p className="text-[11px] text-surface-400 leading-snug flex items-center gap-1">
              <ToolIcon size={9} className="shrink-0" />
              {task.toolLabel} · {statusLabel}
            </p>
          </div>

          <button
            onClick={() => removeTask(task.id)}
            className="shrink-0 mt-0.5 text-surface-300 hover:text-surface-500 transition-colors"
          >
            <svg width="12" height="12" viewBox="0 0 12 12" fill="none">
              <path d="M1 1l10 10M11 1L1 11" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" />
            </svg>
          </button>
        </div>

        {/* Row 2: progress bar (running) or result message */}
        {isRunning ? (
          <div className="h-1 bg-surface-100 rounded-full overflow-hidden">
            <div
              className="h-full w-full rounded-full animate-shimmer"
              style={{ background: 'linear-gradient(90deg, transparent 0%, #818cf8 50%, transparent 100%)', backgroundSize: '200% 100%' }}
            />
          </div>
        ) : task.message ? (
          <p className={cn(
            'text-[11px] leading-snug px-1',
            isDone ? 'text-emerald-700' : 'text-rose-600',
          )}>
            {task.message}
          </p>
        ) : null}

        {/* Row 3: navigate button — always shown */}
        <button
          onClick={() => {
            setProjectSubView(task.toolId)
            removeTask(task.id)
          }}
          className={cn(
            'w-full flex items-center justify-center gap-1.5 px-3 py-1.5 rounded-lg text-[11px] font-medium transition-colors',
            isDone
              ? 'bg-emerald-50 text-emerald-700 hover:bg-emerald-100'
              : isError
                ? 'bg-rose-50 text-rose-700 hover:bg-rose-100'
                : 'bg-indigo-50 text-indigo-600 hover:bg-indigo-100',
          )}
        >
          Open {task.toolLabel}
          <ArrowRight size={11} />
        </button>
      </div>
    </div>
  )
}

// ─── Stack ─────────────────────────────────────────────────────────────────────

export default function TaskToastStack() {
  const tasks = useTaskStore(s => s.tasks)

  return (
    <div
      className="fixed z-[9998] flex flex-col-reverse gap-2.5 items-end pointer-events-none"
      // sit just above the regular ToastContainer (bottom-5 right-5 + ~310px clearance)
      style={{ bottom: '1.25rem', right: '345px' }}
    >
      <AnimatePresence initial={false}>
        {tasks.map(task => (
          <motion.div
            key={task.id}
            layout
            initial={{ opacity: 0, x: 20, scale: 0.95 }}
            animate={{ opacity: 1, x: 0,  scale: 1    }}
            exit={{    opacity: 0, x: 16, scale: 0.95, transition: { duration: 0.15 } }}
            transition={{ type: 'spring', stiffness: 380, damping: 30 }}
            className="pointer-events-auto"
          >
            <TaskToast task={task} />
          </motion.div>
        ))}
      </AnimatePresence>
    </div>
  )
}
