import { motion } from 'framer-motion'
import { useAppStore } from '@/store/app.store'
import { FolderOpen, FileVideo, Film, CheckCircle, Clock, AlertCircle } from 'lucide-react'

export default function Analytics() {
  const { parentProjects, childProjects, capcutProjects, renderJobs } = useAppStore()

  const renderDone   = renderJobs.filter(j => j.status === 'done').length
  const renderTotal  = renderJobs.length
  const renderPct    = renderTotal > 0 ? Math.round((renderDone / renderTotal) * 100) : 0

  const safe = (n: number) => (isNaN(n) || !isFinite(n) ? 0 : n)

  const stats = [
    {
      label: 'Parent Projects', value: safe(parentProjects.length),
      icon: FolderOpen, color: 'text-primary-500', bg: 'bg-primary-50',
      sub: 'Total project groups',
    },
    {
      label: 'Child Projects', value: safe(childProjects.length),
      icon: FileVideo, color: 'text-violet-500', bg: 'bg-violet-50',
      sub: `Across ${safe(parentProjects.length)} parent projects`,
    },
    {
      label: 'CapCut Projects', value: safe(capcutProjects.length),
      icon: Film, color: 'text-amber-500', bg: 'bg-amber-50',
      sub: 'Loaded from disk',
    },
    {
      label: 'Completed', value: safe(childProjects.filter(c => c.status === 'done').length),
      icon: CheckCircle, color: 'text-green-500', bg: 'bg-green-50',
      sub: 'Child projects done',
    },
    {
      label: 'Draft', value: safe(childProjects.filter(c => c.status === 'Draft').length),
      icon: Clock, color: 'text-surface-400', bg: 'bg-surface-100',
      sub: 'Awaiting processing',
    },
    {
      label: 'Render Failed', value: safe(renderJobs.filter(j => j.status === 'failed').length),
      icon: AlertCircle, color: 'text-red-500', bg: 'bg-red-50',
      sub: 'Render errors',
    },
  ]

  return (
    <div className="flex flex-col h-full">
      {/* Header */}
      <div className="px-6 py-4 border-b border-surface-200 bg-white">
        <h1 className="page-title">Analytics</h1>
        <p className="page-sub mt-0.5">Operational overview</p>
      </div>

      <div className="flex-1 overflow-y-auto p-6">
        {/* Stat grid */}
        <div className="grid grid-cols-3 gap-4 mb-6">
          {stats.map((s, i) => (
            <motion.div
              key={s.label}
              initial={{ opacity: 0, y: 12 }}
              animate={{ opacity: 1, y: 0 }}
              transition={{ delay: i * 0.06 }}
              className="card p-5"
            >
              <div className="flex items-start justify-between mb-3">
                <div className={`w-9 h-9 rounded-lg ${s.bg} flex items-center justify-center`}>
                  <s.icon size={18} className={s.color} />
                </div>
                <span className="text-3xl font-bold text-surface-900">{s.value}</span>
              </div>
              <p className="text-sm font-semibold text-surface-800">{s.label}</p>
              <p className="text-xs text-surface-400 mt-0.5">{s.sub}</p>
            </motion.div>
          ))}
        </div>

        {/* Render progress */}
        <motion.div
          initial={{ opacity: 0, y: 12 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ delay: 0.4 }}
          className="card p-5 mb-4"
        >
          <div className="flex items-center justify-between mb-3">
            <h2 className="text-sm font-semibold text-surface-800">Overall Render Progress</h2>
            <span className="text-sm font-bold text-primary-600">{renderPct}%</span>
          </div>
          <div className="h-2 bg-surface-200 rounded-full overflow-hidden">
            <motion.div
              className="h-full bg-primary-500 rounded-full"
              initial={{ width: 0 }}
              animate={{ width: `${renderPct}%` }}
              transition={{ duration: 0.8, ease: 'easeOut', delay: 0.5 }}
            />
          </div>
          <div className="flex justify-between mt-1.5">
            <span className="text-xs text-surface-400">{renderDone} / {renderTotal} jobs</span>
            <span className="text-xs text-surface-400">{renderTotal - renderDone} remaining</span>
          </div>
        </motion.div>

        {/* Project breakdown */}
        <motion.div
          initial={{ opacity: 0, y: 12 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ delay: 0.5 }}
          className="card p-5"
        >
          <h2 className="text-sm font-semibold text-surface-800 mb-3">Project Breakdown</h2>
          <div className="space-y-3">
            {parentProjects.map(p => {
              const kids = childProjects.filter(c => c.parentId === p.id)
              const done = kids.filter(c => c.status === 'done').length
              const pct  = kids.length > 0 ? Math.round((done / kids.length) * 100) : 0
              return (
                <div key={p.id}>
                  <div className="flex items-center justify-between mb-1">
                    <div className="flex items-center gap-2">
                      <FolderOpen size={13} className="text-primary-400" />
                      <span className="text-sm font-medium text-surface-700">{p.name}</span>
                    </div>
                    <span className="text-xs text-surface-500">{done}/{kids.length} done</span>
                  </div>
                  <div className="h-1.5 bg-surface-200 rounded-full overflow-hidden">
                    <div className="h-full bg-primary-400 rounded-full" style={{ width: `${pct}%` }} />
                  </div>
                </div>
              )
            })}
            {parentProjects.length === 0 && (
              <p className="text-sm text-surface-400 text-center py-4">No projects yet</p>
            )}
          </div>
        </motion.div>
      </div>
    </div>
  )
}
