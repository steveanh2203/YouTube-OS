import { AnimatePresence, motion } from 'framer-motion'
import { CheckCircle2, XCircle, Info, AlertTriangle, X } from 'lucide-react'
import { useToastStore, type ToastType } from '@/store/toast.store'
import { cn } from '@/lib/utils'

const CONFIG: Record<ToastType, {
  icon: React.ReactNode
  bar: string
  iconBg: string
  iconColor: string
}> = {
  success: {
    icon: <CheckCircle2 size={18} />,
    bar: 'bg-emerald-500',
    iconBg: 'bg-emerald-50',
    iconColor: 'text-emerald-500',
  },
  error: {
    icon: <XCircle size={18} />,
    bar: 'bg-rose-500',
    iconBg: 'bg-rose-50',
    iconColor: 'text-rose-500',
  },
  info: {
    icon: <Info size={18} />,
    bar: 'bg-blue-500',
    iconBg: 'bg-blue-50',
    iconColor: 'text-blue-500',
  },
  warning: {
    icon: <AlertTriangle size={18} />,
    bar: 'bg-amber-400',
    iconBg: 'bg-amber-50',
    iconColor: 'text-amber-500',
  },
}

export default function ToastContainer() {
  const { toasts, remove } = useToastStore()

  return (
    <div className="fixed top-5 right-5 z-[9999] flex flex-col gap-2.5 items-end pointer-events-none">
      <AnimatePresence initial={false}>
        {toasts.map(t => {
          const cfg = CONFIG[t.type]
          return (
            <motion.div
              key={t.id}
              layout
              initial={{ opacity: 0, y: 16, scale: 0.95 }}
              animate={{ opacity: 1, y: 0,  scale: 1    }}
              exit={{    opacity: 0, y: 8,  scale: 0.95, transition: { duration: 0.15 } }}
              transition={{ type: 'spring', stiffness: 380, damping: 30 }}
              className="pointer-events-auto w-[320px] bg-white rounded-xl shadow-lg border border-surface-100 overflow-hidden"
            >
              {/* Colour bar on top */}
              <div className={cn('h-[3px] w-full', cfg.bar)} />

              <div className="flex items-start gap-3 px-4 py-3">
                {/* Icon */}
                <div className={cn('mt-0.5 shrink-0 w-7 h-7 rounded-lg flex items-center justify-center', cfg.iconBg, cfg.iconColor)}>
                  {cfg.icon}
                </div>

                {/* Text */}
                <div className="flex-1 min-w-0">
                  <p className="text-sm font-semibold text-surface-800 leading-snug">{t.title}</p>
                  {t.message && (
                    <p className="text-xs text-surface-500 mt-0.5 leading-snug">{t.message}</p>
                  )}
                </div>

                {/* Close */}
                <button
                  onClick={() => remove(t.id)}
                  className="shrink-0 mt-0.5 text-surface-300 hover:text-surface-500 transition-colors"
                >
                  <X size={14} />
                </button>
              </div>
            </motion.div>
          )
        })}
      </AnimatePresence>
    </div>
  )
}
