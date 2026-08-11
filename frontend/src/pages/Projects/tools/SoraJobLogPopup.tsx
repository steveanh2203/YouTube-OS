import { useState, useEffect, useRef } from 'react'
import * as Dialog from '@radix-ui/react-dialog'
import { motion, AnimatePresence } from 'framer-motion'
import {
  X, Loader2, Terminal, CheckCircle2, XCircle,
  AlertTriangle, Info, Sparkles,
} from 'lucide-react'
import { cn } from '@/lib/utils'

const API    = ''
const POLL_MS = 1500

type LogLevel  = 'info' | 'success' | 'warn' | 'error'
type JobStatus = 'pending' | 'generating' | 'done' | 'failed'

interface LogEntry { ts: string; level: LogLevel; message: string }
interface SoraJobLogPopupProps {
  jobId: number; jobIndex: number; prompt: string
  status: JobStatus; open: boolean; onClose: () => void
}

// ── Step definitions — each has a distinct color matching design system ────────
const STEPS = [
  {
    id: 1, label: 'Generate',
    dot:   'bg-blue-500',
    ring:  'ring-blue-100',
    pill:  'bg-blue-500 text-white shadow-sm',
    idle:  'bg-surface-100 text-surface-400 border-surface-200',
    line:  'bg-blue-200',
    text:  'text-blue-700',
    keywords: ['generating', 'queued', 'task', 'create', 'waiting for task', 'prompt', 'collect', 'aspect', 'duration', 'button'],
  },
  {
    id: 2, label: 'Publish',
    dot:   'bg-violet-500',
    ring:  'ring-violet-100',
    pill:  'bg-violet-500 text-white shadow-sm',
    idle:  'bg-surface-100 text-surface-400 border-surface-200',
    line:  'bg-violet-200',
    text:  'text-violet-700',
    keywords: ['publish', 'public', 'permalink', 'sora post'],
  },
  {
    id: 3, label: 'Remove Watermark',
    dot:   'bg-orange-500',
    ring:  'ring-orange-100',
    pill:  'bg-orange-500 text-white shadow-sm',
    idle:  'bg-surface-100 text-surface-400 border-surface-200',
    line:  'bg-orange-200',
    text:  'text-orange-700',
    keywords: ['watermark', 'removing', 'snapzora', 'snapsora', 'no_watermark', 'no-watermark', 'clean url', 'direct url', 'remove watermark complete'],
  },
  {
    id: 4, label: 'Denoise',
    dot:   'bg-cyan-500',
    ring:  'ring-cyan-100',
    pill:  'bg-cyan-500 text-white shadow-sm',
    idle:  'bg-surface-100 text-surface-400 border-surface-200',
    line:  'bg-cyan-200',
    text:  'text-cyan-700',
    keywords: ['denoise', 'noise', 'cleaning pass', 'hqdn3d'],
  },
  {
    id: 5, label: 'Upscale 1080p',
    dot:   'bg-amber-500',
    ring:  'ring-amber-100',
    pill:  'bg-amber-500 text-white shadow-sm',
    idle:  'bg-surface-100 text-surface-400 border-surface-200',
    line:  'bg-amber-200',
    text:  'text-amber-700',
    keywords: ['upscale', '1080p', 'full hd', 'enhance start'],
  },
  {
    id: 6, label: 'Save to Folder',
    dot:   'bg-green-500',
    ring:  'ring-green-100',
    pill:  'bg-green-500 text-white shadow-sm',
    idle:  'bg-surface-100 text-surface-400 border-surface-200',
    line:  'bg-green-200',
    text:  'text-green-700',
    keywords: ['save to folder', 'saved to folder', 'saving final', 'video saved', 'done', 'complete', 'hoàn thành'],
  },
]

// ── Step icon per index ───────────────────────────────────────────────────────
function detectStep(message: string): number {
  const m = message.toLowerCase()
  for (const step of [...STEPS].reverse()) {
    if (step.keywords.some(k => m.includes(k))) return step.id
  }
  return 0
}

// ── Log level config ──────────────────────────────────────────────────────────
const LEVEL_CFG: Record<LogLevel, {
  bg: string; border: string; text: string; dim: string; Icon: React.ElementType
}> = {
  info:    { bg: 'bg-white',        border: 'border-l-blue-400',   text: 'text-surface-700', dim: 'text-blue-400',   Icon: Info },
  success: { bg: 'bg-green-50',     border: 'border-l-green-500',  text: 'text-green-800',   dim: 'text-green-500',  Icon: CheckCircle2 },
  warn:    { bg: 'bg-amber-50',     border: 'border-l-amber-500',  text: 'text-amber-800',   dim: 'text-amber-500',  Icon: AlertTriangle },
  error:   { bg: 'bg-red-50',       border: 'border-l-red-500',    text: 'text-red-800',     dim: 'text-red-500',    Icon: XCircle },
}

function formatTime(isoTs: string) {
  return isoTs.split('T')[1]?.slice(0, 8) ?? ''
}

function humanizeFailureMessage(value?: string | null) {
  const message = String(value || '').trim()
  if (!message) return ''
  if (/unable[_\s-]*to[_\s-]*generate/i.test(message)) {
    return 'Unable to generate detected. Sora could not create this video for the current prompt.'
  }
  if (/create request was not observed/i.test(message)) {
    return 'Generate request was not confirmed by Sora.'
  }
  return message
}

// ── Component ─────────────────────────────────────────────────────────────────
export default function SoraJobLogPopup({
  jobId, jobIndex, prompt, status, open, onClose,
}: SoraJobLogPopupProps) {
  const [logs, setLogs] = useState<LogEntry[]>([])
  const bottomRef       = useRef<HTMLDivElement>(null)
  const pollRef         = useRef<ReturnType<typeof setInterval> | null>(null)

  const fetchLogs = async () => {
    try {
      const res  = await fetch(`${API}/api/sora/job-logs/${jobId}`)
      const data = await res.json()
      setLogs(data.logs ?? [])
    } catch { /* ignore */ }
  }

  useEffect(() => {
    if (!open) return
    setLogs([])
    fetchLogs()
    const active = status === 'pending' || status === 'generating'
    if (active) pollRef.current = setInterval(fetchLogs, POLL_MS)
    return () => { if (pollRef.current) clearInterval(pollRef.current) }
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open, jobId, status])

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: 'smooth' })
  }, [logs.length])

  const currentStep = logs.reduce((max, e) => Math.max(max, detectStep(e.message)), 0)
  const isActive    = status === 'pending' || status === 'generating'
  const isFailed    = status === 'failed'
  const isDone      = status === 'done'
  const failureMessage = humanizeFailureMessage(
    [...logs].reverse().find((entry) => entry.level === 'error')?.message
  )

  return (
    <Dialog.Root open={open} onOpenChange={v => !v && onClose()}>
      <Dialog.Portal>
        {/* Overlay */}
        <Dialog.Overlay className="fixed inset-0 bg-black/20 backdrop-blur-[2px] z-50" />

        {/* Modal — white card per design system */}
        <Dialog.Content
          className="fixed left-1/2 top-1/2 -translate-x-1/2 -translate-y-1/2
                     w-[600px] max-h-[82vh] bg-white rounded-2xl shadow-xl z-50
                     flex flex-col focus:outline-none overflow-hidden
                     border border-surface-200"
          aria-describedby={undefined}
        >

          {/* ── Header ──────────────────────────────────────────── */}
          <div className="flex items-center justify-between px-5 py-4 border-b border-surface-100 shrink-0">
            <div className="flex items-center gap-3">
              {/* Icon — primary teal from design system */}
              <div className="w-9 h-9 rounded-xl bg-primary-50 border border-primary-100 flex items-center justify-center shrink-0">
                <Terminal size={15} className="text-primary-600" />
              </div>

              <div className="min-w-0">
                <Dialog.Title className="text-sm font-semibold text-surface-900 flex items-center gap-2">
                  Job #{jobIndex} — Generation Log
                  {isActive && (
                    <span className="inline-flex items-center gap-1 text-[10px] font-semibold
                                     bg-primary-50 text-primary-700 border border-primary-200
                                     px-2 py-0.5 rounded-full">
                      <span className="w-1.5 h-1.5 rounded-full bg-primary-500 animate-pulse" />
                      LIVE
                    </span>
                  )}
                  {isDone && (
                    <span className="inline-flex items-center gap-1 text-[10px] font-semibold
                                     bg-green-50 text-green-700 border border-green-200
                                     px-2 py-0.5 rounded-full">
                      <CheckCircle2 size={9} /> Done
                    </span>
                  )}
                  {isFailed && (
                    <span className="inline-flex items-center gap-1 text-[10px] font-semibold
                                     bg-red-50 text-red-700 border border-red-200
                                     px-2 py-0.5 rounded-full">
                      <XCircle size={9} /> Failed
                    </span>
                  )}
                </Dialog.Title>
                <p className="text-[11px] text-surface-400 mt-0.5 truncate max-w-[380px]">{prompt}</p>
              </div>
            </div>

            <Dialog.Close asChild>
              <button className="p-1.5 rounded-lg text-surface-400 hover:text-surface-600
                                 hover:bg-surface-100 transition-colors duration-150 cursor-pointer">
                <X size={15} />
              </button>
            </Dialog.Close>
          </div>

          {/* ── Step timeline — fixed layout, no overflow ────────── */}
          <div className="px-6 pt-4 pb-5 bg-surface-50 border-b border-surface-100 shrink-0">
            <div className="relative">
              {/* Background track — full width, centered on dots */}
              <div className="absolute top-4 left-0 right-0 h-0.5 bg-surface-200 z-0" />

              {/* Animated progress fill */}
              {(currentStep > 0 || isDone) && (
                <motion.div
                  className="absolute top-4 left-0 h-0.5 z-[1]
                             bg-gradient-to-r from-blue-400 via-violet-400 via-orange-400 via-amber-400 to-green-400"
                  initial={{ width: '0%' }}
                  animate={{
                    width: isDone
                      ? '100%'
                      : `${((Math.max(currentStep, 1) - 1) / (STEPS.length - 1)) * 100}%`,
                  }}
                  transition={{ duration: 0.5, ease: 'easeOut' }}
                />
              )}

              {/* Step nodes — spaced with justify-between */}
              <div className="relative z-[2] flex justify-between items-start">
                {STEPS.map((step) => {
                  const done   = isDone || currentStep > step.id
                  const active = isActive && currentStep === step.id
                  const fail   = isFailed && currentStep === step.id

                  return (
                    <div key={step.id} className="flex flex-col items-center gap-2" style={{ width: `${100 / STEPS.length}%` }}>
                      {/* Dot */}
                      <motion.div
                        animate={active ? { scale: [1, 1.1, 1] } : { scale: 1 }}
                        transition={{ repeat: Infinity, duration: 1.5 }}
                        className={cn(
                          'w-8 h-8 rounded-full flex items-center justify-center transition-all duration-300',
                          done   ? `${step.dot} ring-4 ${step.ring}` :
                          active ? `${step.dot} ring-4 ${step.ring}` :
                          fail   ? 'bg-red-500 ring-4 ring-red-100' :
                                   'bg-white border-2 border-surface-200',
                        )}
                      >
                        {active && <Loader2 size={13} className="animate-spin text-white" />}
                        {done   && <CheckCircle2 size={13} className="text-white" />}
                        {fail   && <XCircle size={13} className="text-white" />}
                        {!done && !active && !fail && (
                          <span className="text-[10px] font-bold text-surface-400">{step.id}</span>
                        )}
                      </motion.div>

                      {/* Label */}
                      <span className={cn(
                        'text-[10px] font-semibold text-center leading-tight transition-colors duration-200',
                        done   ? step.text :
                        active ? step.text :
                        fail   ? 'text-red-500' :
                                 'text-surface-400',
                      )}>
                        {step.label}
                      </span>

                      {/* Active step colored underline pill */}
                      {active && (
                        <motion.div
                          initial={{ scaleX: 0 }}
                          animate={{ scaleX: 1 }}
                          className={cn('h-0.5 w-10 rounded-full', step.dot)}
                        />
                      )}
                    </div>
                  )
                })}
              </div>
            </div>
          </div>

          {/* ── Log entries ─────────────────────────────────────── */}
          <div className="flex-1 overflow-y-auto p-4 space-y-1 bg-white">
            {failureMessage && (
              <div className="mb-3 rounded-xl border border-red-200 bg-red-50 px-3 py-2.5 text-[11px] font-medium leading-relaxed text-red-700">
                {failureMessage}
              </div>
            )}
            <AnimatePresence initial={false}>
              {logs.length === 0 ? (
                <div className="flex flex-col items-center justify-center py-14 gap-3 text-surface-300">
                  <div className="w-12 h-12 rounded-xl bg-surface-50 border border-surface-200 flex items-center justify-center">
                    <Sparkles size={20} className="text-surface-300" />
                  </div>
                  <p className="text-xs text-surface-400 text-center">
                    {isActive
                      ? 'Waiting for the extension to report progress...'
                      : 'No logs recorded for this job.'}
                  </p>
                  {isActive && <Loader2 size={14} className="animate-spin text-primary-400" />}
                </div>
              ) : (
                logs.map((entry, i) => {
                  const cfg = LEVEL_CFG[entry.level as LogLevel] || LEVEL_CFG.info
                  const { Icon } = cfg
                  return (
                    <motion.div
                      key={i}
                      initial={{ opacity: 0, y: 4 }}
                      animate={{ opacity: 1, y: 0 }}
                      transition={{ duration: 0.15 }}
                      className={cn(
                        'flex items-start gap-2.5 px-3 py-2 rounded-lg border-l-2 border border-surface-100 text-[11px] font-mono leading-relaxed',
                        cfg.bg, cfg.border,
                      )}
                    >
                      <Icon size={10} className={cn('mt-0.5 shrink-0', cfg.dim)} />
                      <span className="text-surface-400 tabular-nums shrink-0 select-none">
                        {formatTime(entry.ts)}
                      </span>
                      <span className={cn('break-all', cfg.text)}>{entry.message}</span>
                    </motion.div>
                  )
                })
              )}
            </AnimatePresence>
            <div ref={bottomRef} />
          </div>

          {/* ── Footer ──────────────────────────────────────────── */}
          <div className="px-5 py-3 border-t border-surface-100 bg-surface-50 shrink-0 flex items-center justify-between">
            <span className="text-[10px] text-surface-400">
              {logs.length > 0 ? `${logs.length} log entries` : 'Waiting for logs...'}
            </span>
            <div className="flex items-center gap-3">
              {isActive && (
                <span className="flex items-center gap-1.5 text-[10px] font-medium text-primary-600">
                  <span className="w-1.5 h-1.5 rounded-full bg-primary-500 animate-pulse" />
                  Polling every {POLL_MS / 1000}s
                </span>
              )}
              {isDone && (
                <span className="flex items-center gap-1 text-[10px] font-semibold text-green-600">
                  <CheckCircle2 size={10} /> 1080p video saved to folder
                </span>
              )}
              {isFailed && (
                <span className="flex items-center gap-1 text-[10px] font-semibold text-red-500">
                  <XCircle size={10} /> Job failed
                </span>
              )}
            </div>
          </div>

        </Dialog.Content>
      </Dialog.Portal>
    </Dialog.Root>
  )
}
