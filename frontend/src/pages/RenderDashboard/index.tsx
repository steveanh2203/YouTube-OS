import { useState } from 'react'
import { motion } from 'framer-motion'
import { useAppStore } from '@/store/app.store'
import {
  RefreshCw, Play, Loader, CheckCircle, XCircle, Clock,
  Film, Video, Zap, Layers,
} from 'lucide-react'
import { cn } from '@/lib/utils'

const STATUS_CONFIG = {
  pending:    { label: 'Pending',    cls: 'badge-neutral', icon: Clock },
  processing: { label: 'Processing', cls: 'badge-info',    icon: Loader },
  done:       { label: 'Done',       cls: 'badge-success', icon: CheckCircle },
  failed:     { label: 'Failed',     cls: 'badge-error',   icon: XCircle },
}

export default function RenderDashboard() {
  const { capcutProjects, isLoading, setLoading } = useAppStore()
  const [selected, setSelected] = useState<Set<string>>(new Set())

  const totalVideos   = capcutProjects.reduce((s, p) => s + p.videoCount, 0)
  const totalSelected = capcutProjects.reduce((s, p) => s + p.selectedCount, 0)
  const totalDone     = capcutProjects.reduce((s, p) => s + p.completedCount, 0)

  const toggle = (id: string) => setSelected(prev => {
    const next = new Set(prev)
    next.has(id) ? next.delete(id) : next.add(id)
    return next
  })

  const handleSync = (label: string) => {
    setLoading(true, `Syncing ${label}...`)
    setTimeout(() => setLoading(false), 2500)
  }

  return (
    <div className="flex flex-col h-full">
      {/* Header */}
      <div className="flex items-center justify-between px-6 py-4 border-b border-surface-200 bg-white">
        <div>
          <h1 className="page-title">Render Dashboard</h1>
          <p className="page-sub mt-0.5">Manage and sync CapCut projects</p>
        </div>
        <div className="flex items-center gap-2">
          <button className="btn-secondary" onClick={() => handleSync('all')}>
            <RefreshCw size={13} className={isLoading ? 'animate-spin' : ''} />
            Sync All
          </button>
          <button className="btn-primary" onClick={() => handleSync('selected')}>
            <Zap size={13} />
            Auto Render
          </button>
        </div>
      </div>

      {/* Stats */}
      <div className="grid grid-cols-3 gap-4 px-6 py-4 border-b border-surface-200 bg-surface-50">
        <div className="stat-card">
          <div className="flex items-center gap-2">
            <Film size={14} className="text-primary-500" />
            <span className="stat-label">Loaded Videos</span>
          </div>
          <span className="stat-value">{totalVideos}</span>
        </div>
        <div className="stat-card">
          <div className="flex items-center gap-2">
            <Video size={14} className="text-amber-500" />
            <span className="stat-label">Selected</span>
          </div>
          <span className="stat-value">{totalSelected}</span>
        </div>
        <div className="stat-card">
          <div className="flex items-center gap-2">
            <CheckCircle size={14} className="text-green-500" />
            <span className="stat-label">Completed</span>
          </div>
          <span className="stat-value">{totalDone}</span>
        </div>
      </div>

      {/* Sync Actions */}
      <div className="flex items-center gap-2 px-6 py-3 border-b border-surface-200 bg-white">
        <span className="text-xs font-medium text-surface-500 mr-1">Sync:</span>
        {['Audio', 'Images', 'Captions', 'SRT'].map(op => (
          <button key={op} className="btn-ghost text-xs py-1 px-2.5" onClick={() => handleSync(op)}>
            <Layers size={11} />
            {op}
          </button>
        ))}
      </div>

      {/* Table */}
      <div className="flex-1 overflow-y-auto p-6">
        <div className="card overflow-hidden">
          <table className="w-full text-sm">
            <thead>
              <tr className="border-b border-surface-200 bg-surface-50">
                <th className="w-8 px-4 py-2.5">
                  <input type="checkbox" className="rounded"
                    checked={selected.size === capcutProjects.length && capcutProjects.length > 0}
                    onChange={e => setSelected(e.target.checked ? new Set(capcutProjects.map(p => p.id)) : new Set())} />
                </th>
                <th className="px-4 py-2.5 text-left text-xs font-semibold text-surface-500">Project</th>
                <th className="px-4 py-2.5 text-left text-xs font-semibold text-surface-500">Status</th>
                <th className="px-4 py-2.5 text-center text-xs font-semibold text-surface-500">Videos</th>
                <th className="px-4 py-2.5 text-center text-xs font-semibold text-surface-500">Selected</th>
                <th className="px-4 py-2.5 text-center text-xs font-semibold text-surface-500">Done</th>
                <th className="px-4 py-2.5 text-left text-xs font-semibold text-surface-500">Progress</th>
                <th className="px-4 py-2.5" />
              </tr>
            </thead>
            <tbody>
              {capcutProjects.map((p, i) => {
                const { label, cls, icon: StatusIcon } = STATUS_CONFIG[p.status]
                const pct = p.videoCount > 0 ? Math.round((p.completedCount / p.videoCount) * 100) : 0

                return (
                  <motion.tr
                    key={p.id}
                    initial={{ opacity: 0 }}
                    animate={{ opacity: 1 }}
                    transition={{ delay: i * 0.04 }}
                    className={cn(
                      'border-b border-surface-100 hover:bg-surface-50 transition-colors',
                      selected.has(p.id) && 'bg-primary-50/40',
                    )}
                  >
                    <td className="px-4 py-3">
                      <input type="checkbox" className="rounded"
                        checked={selected.has(p.id)} onChange={() => toggle(p.id)} />
                    </td>
                    <td className="px-4 py-3">
                      <p className="font-medium text-surface-900">{p.name}</p>
                      <p className="text-xs text-surface-400 truncate max-w-[200px]">{p.path}</p>
                    </td>
                    <td className="px-4 py-3">
                      <span className={cn('badge flex items-center gap-1 w-fit', cls)}>
                        <StatusIcon size={10} className={p.status === 'processing' ? 'animate-spin' : ''} />
                        {label}
                      </span>
                    </td>
                    <td className="px-4 py-3 text-center text-surface-700">{p.videoCount}</td>
                    <td className="px-4 py-3 text-center text-surface-700">{p.selectedCount}</td>
                    <td className="px-4 py-3 text-center text-green-600 font-medium">{p.completedCount}</td>
                    <td className="px-4 py-3">
                      <div className="flex items-center gap-2">
                        <div className="flex-1 h-1.5 bg-surface-200 rounded-full overflow-hidden">
                          <div
                            className="h-full bg-primary-500 rounded-full transition-all duration-500"
                            style={{ width: `${pct}%` }}
                          />
                        </div>
                        <span className="text-xs text-surface-500 w-7 text-right">{pct}%</span>
                      </div>
                    </td>
                    <td className="px-4 py-3">
                      <button className="btn-icon text-surface-400 hover:text-primary-600 hover:bg-primary-50"
                        title="Run">
                        <Play size={13} />
                      </button>
                    </td>
                  </motion.tr>
                )
              })}
            </tbody>
          </table>
        </div>
      </div>
    </div>
  )
}
