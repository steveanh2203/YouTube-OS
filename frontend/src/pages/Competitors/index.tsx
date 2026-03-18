import { useState } from 'react'
import { motion, AnimatePresence } from 'framer-motion'
import { useAppStore, type CompetitorPurpose } from '@/store/app.store'
import {
  Target, Trash2, ExternalLink, Search, Filter,
  Youtube, Users, Tag, TrendingUp, FileText,
} from 'lucide-react'
import { cn } from '@/lib/utils'

// ─── helpers ────────────────────────────────────────────────────────────────

export function extractVideoId(url: string): string | null {
  try {
    const u = new URL(url)
    if (u.hostname.includes('youtu.be')) return u.pathname.slice(1)
    if (u.hostname.includes('youtube.com')) {
      if (u.pathname.includes('/shorts/')) return u.pathname.split('/shorts/')[1].split('/')[0]
      return u.searchParams.get('v')
    }
  } catch { /* ignore */ }
  return null
}

const PURPOSE_META: Record<CompetitorPurpose, { label: string; color: string; icon: React.ElementType }> = {
  rewrite:   { label: 'Rewrite',   color: 'bg-violet-100 text-violet-700',  icon: FileText },
  reference: { label: 'Reference', color: 'bg-blue-100 text-blue-700',     icon: Tag },
  trending:  { label: 'Trending',  color: 'bg-amber-100 text-amber-700',   icon: TrendingUp },
  script:    { label: 'Script',    color: 'bg-green-100 text-green-700',   icon: FileText },
}

// ─── Competitors Page ────────────────────────────────────────────────────────

export default function CompetitorsPage() {
  const { competitors, parentProjects, childProjects, deleteCompetitor } = useAppStore()

  const [search, setSearch]           = useState('')
  const [filterParent, setFilterParent] = useState<string>('all')
  const [filterPurpose, setFilterPurpose] = useState<string>('all')

  // stats
  const totalLinks     = competitors.length
  const uniqueChannels = new Set(competitors.map(c => c.channel)).size
  const byPurpose      = (['rewrite', 'reference', 'trending', 'script'] as CompetitorPurpose[])
    .map(p => ({ purpose: p, count: competitors.filter(c => c.purpose === p).length }))

  // filtered list
  const filtered = competitors.filter(c => {
    const matchSearch  = !search || c.title.toLowerCase().includes(search.toLowerCase()) ||
                         c.channel.toLowerCase().includes(search.toLowerCase())
    const matchParent  = filterParent  === 'all' || c.parentProjectId === filterParent
    const matchPurpose = filterPurpose === 'all' || c.purpose === filterPurpose
    return matchSearch && matchParent && matchPurpose
  })

  return (
    <div className="flex flex-col h-full">
      {/* Header */}
      <div className="px-6 py-4 border-b border-surface-200 bg-white">
        <div className="flex items-center gap-2">
          <Target size={18} className="text-primary-500" />
          <h1 className="page-title">Competitors</h1>
        </div>
        <p className="page-sub mt-0.5">Aggregated competitor links across all projects</p>
      </div>

      <div className="flex-1 overflow-y-auto p-6 space-y-5">

        {/* Stats */}
        <div className="grid grid-cols-4 gap-4">
          <motion.div initial={{ opacity: 0, y: 10 }} animate={{ opacity: 1, y: 0 }} transition={{ delay: 0 }}
            className="card p-4 flex items-center gap-3">
            <div className="w-9 h-9 rounded-lg bg-primary-50 flex items-center justify-center shrink-0">
              <Youtube size={18} className="text-primary-500" />
            </div>
            <div>
              <p className="text-2xl font-bold text-surface-900">{totalLinks}</p>
              <p className="text-xs text-surface-400">Total Links</p>
            </div>
          </motion.div>

          <motion.div initial={{ opacity: 0, y: 10 }} animate={{ opacity: 1, y: 0 }} transition={{ delay: 0.06 }}
            className="card p-4 flex items-center gap-3">
            <div className="w-9 h-9 rounded-lg bg-violet-50 flex items-center justify-center shrink-0">
              <Users size={18} className="text-violet-500" />
            </div>
            <div>
              <p className="text-2xl font-bold text-surface-900">{uniqueChannels}</p>
              <p className="text-xs text-surface-400">Unique Channels</p>
            </div>
          </motion.div>

          {byPurpose.slice(0, 2).map(({ purpose, count }, i) => {
            const meta = PURPOSE_META[purpose]
            const Icon = meta.icon
            return (
              <motion.div key={purpose} initial={{ opacity: 0, y: 10 }} animate={{ opacity: 1, y: 0 }} transition={{ delay: 0.06 * (i + 2) }}
                className="card p-4 flex items-center gap-3">
                <div className={cn('w-9 h-9 rounded-lg flex items-center justify-center shrink-0', meta.color.replace('text-', 'bg-').replace('-700', '-100'))}>
                  <Icon size={18} className={meta.color.split(' ')[1]} />
                </div>
                <div>
                  <p className="text-2xl font-bold text-surface-900">{count}</p>
                  <p className="text-xs text-surface-400">{meta.label}</p>
                </div>
              </motion.div>
            )
          })}
        </div>

        {/* Filters */}
        <motion.div initial={{ opacity: 0, y: 8 }} animate={{ opacity: 1, y: 0 }} transition={{ delay: 0.2 }}
          className="flex items-center gap-3">
          {/* Search */}
          <div className="relative flex-1 max-w-xs">
            <Search size={14} className="absolute left-3 top-1/2 -translate-y-1/2 text-surface-400" />
            <input
              value={search}
              onChange={e => setSearch(e.target.value)}
              placeholder="Search by title, channel..."
              className="w-full pl-8 pr-3 py-2 text-sm border border-surface-200 rounded-lg bg-white
                         focus:outline-none focus:ring-2 focus:ring-primary-200 focus:border-primary-400"
            />
          </div>

          {/* Filter by parent */}
          <div className="flex items-center gap-1.5">
            <Filter size={13} className="text-surface-400" />
            <select
              value={filterParent}
              onChange={e => setFilterParent(e.target.value)}
              className="text-sm border border-surface-200 rounded-lg px-2.5 py-2 bg-white
                         focus:outline-none focus:ring-2 focus:ring-primary-200 cursor-pointer"
            >
              <option value="all">All projects</option>
              {parentProjects.map(p => (
                <option key={p.id} value={p.id}>{p.name}</option>
              ))}
            </select>
          </div>

          {/* Filter by purpose */}
          <select
            value={filterPurpose}
            onChange={e => setFilterPurpose(e.target.value)}
            className="text-sm border border-surface-200 rounded-lg px-2.5 py-2 bg-white
                       focus:outline-none focus:ring-2 focus:ring-primary-200 cursor-pointer"
          >
            <option value="all">All purposes</option>
            {(Object.keys(PURPOSE_META) as CompetitorPurpose[]).map(p => (
              <option key={p} value={p}>{PURPOSE_META[p].label}</option>
            ))}
          </select>
        </motion.div>

        {/* Table */}
        <motion.div initial={{ opacity: 0, y: 8 }} animate={{ opacity: 1, y: 0 }} transition={{ delay: 0.25 }}
          className="card overflow-hidden">
          {filtered.length === 0 ? (
            <div className="flex flex-col items-center justify-center py-16 text-surface-400">
              <Target size={36} className="mb-3 opacity-30" />
              <p className="text-sm font-medium">No competitors yet</p>
              <p className="text-xs mt-1">Go to a Child Project and click "Add Competitor" to add links</p>
            </div>
          ) : (
            <table className="w-full text-sm">
              <thead>
                <tr className="border-b border-surface-100 bg-surface-50">
                  <th className="text-left px-4 py-3 text-xs font-semibold text-surface-500 uppercase tracking-wide w-10"></th>
                  <th className="text-left px-4 py-3 text-xs font-semibold text-surface-500 uppercase tracking-wide">Video</th>
                  <th className="text-left px-4 py-3 text-xs font-semibold text-surface-500 uppercase tracking-wide">Channel</th>
                  <th className="text-left px-4 py-3 text-xs font-semibold text-surface-500 uppercase tracking-wide">Project</th>
                   <th className="text-left px-4 py-3 text-xs font-semibold text-surface-500 uppercase tracking-wide">Purpose</th>
                  <th className="text-left px-4 py-3 text-xs font-semibold text-surface-500 uppercase tracking-wide">Notes</th>
                   <th className="text-left px-4 py-3 text-xs font-semibold text-surface-500 uppercase tracking-wide">Date Added</th>
                  <th className="px-4 py-3 w-16"></th>
                </tr>
              </thead>
              <tbody>
                <AnimatePresence>
                  {filtered.map((c, i) => {
                    const child  = childProjects.find(p => p.id === c.childProjectId)
                    const parent = parentProjects.find(p => p.id === c.parentProjectId)
                    const meta   = PURPOSE_META[c.purpose]
                    return (
                      <motion.tr
                        key={c.id}
                        initial={{ opacity: 0 }}
                        animate={{ opacity: 1 }}
                        exit={{ opacity: 0 }}
                        transition={{ delay: i * 0.03 }}
                        className="border-b border-surface-100 hover:bg-surface-50 transition-colors"
                      >
                        {/* Thumbnail */}
                        <td className="px-4 py-3">
                          <img
                            src={c.thumbnail}
                            alt=""
                            className="w-10 h-7 object-cover rounded"
                            onError={e => { (e.target as HTMLImageElement).style.display = 'none' }}
                          />
                        </td>
                        {/* Title */}
                        <td className="px-4 py-3 max-w-[220px]">
                          <a
                            href={c.url}
                            target="_blank"
                            rel="noopener noreferrer"
                            className="font-medium text-surface-800 hover:text-primary-600 transition-colors
                                       line-clamp-2 flex items-start gap-1 group cursor-pointer"
                          >
                            <span className="line-clamp-2">{c.title}</span>
                            <ExternalLink size={12} className="shrink-0 mt-0.5 opacity-0 group-hover:opacity-100 transition-opacity" />
                          </a>
                        </td>
                        {/* Channel */}
                        <td className="px-4 py-3 text-surface-500 whitespace-nowrap">{c.channel}</td>
                        {/* Project */}
                        <td className="px-4 py-3">
                          <div className="flex flex-col gap-0.5">
                            {parent && <span className="text-xs font-medium text-surface-700">{parent.name}</span>}
                            {child  && <span className="text-xs text-surface-400">{child.name}</span>}
                          </div>
                        </td>
                        {/* Purpose */}
                        <td className="px-4 py-3">
                          <span className={cn('px-2 py-0.5 rounded-full text-xs font-medium', meta.color)}>
                            {meta.label}
                          </span>
                        </td>
                        {/* Notes */}
                        <td className="px-4 py-3 text-surface-400 max-w-[140px]">
                          <span className="line-clamp-1 text-xs">{c.notes || '—'}</span>
                        </td>
                        {/* Date */}
                        <td className="px-4 py-3 text-surface-400 text-xs whitespace-nowrap">
                           {new Date(c.addedAt).toLocaleDateString('en-US')}
                        </td>
                        {/* Actions */}
                        <td className="px-4 py-3">
                          <button
                            onClick={() => deleteCompetitor(c.id)}
                            className="btn-icon text-surface-300 hover:text-red-500 hover:bg-red-50 transition-colors"
                            title="Delete"
                          >
                            <Trash2 size={14} />
                          </button>
                        </td>
                      </motion.tr>
                    )
                  })}
                </AnimatePresence>
              </tbody>
            </table>
          )}
        </motion.div>

      </div>
    </div>
  )
}
