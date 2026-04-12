import { useCallback, useEffect, useMemo, useState } from 'react'
import { motion, AnimatePresence } from 'framer-motion'
import { useAppStore } from '@/store/app.store'
import {
  ChevronRight, ExternalLink, RefreshCw, Search, Target, Trash2,
} from 'lucide-react'
import { toast } from '@/store/toast.store'
import { resolveYoutubeMetadata } from './utils'
import { cn } from '@/lib/utils'

const API = 'http://127.0.0.1:8765'

function mapStoreCompetitor(raw: Record<string, unknown>) {
  return {
    id: String(raw.id),
    videoId: (raw.video_id as string) ?? '',
    url: (raw.url as string) ?? '',
    title: (raw.title as string) ?? '',
    channel: (raw.channel as string) ?? '',
    thumbnail: (raw.thumbnail_url as string) ?? '',
    purpose: 'reference' as const,
    notes: (raw.notes as string) ?? '',
    childProjectId: raw.child_project_id != null ? String(raw.child_project_id) : null,
    parentProjectId: raw.parent_project_id != null ? String(raw.parent_project_id) : null,
    addedAt: raw.created_at ? String(raw.created_at) : new Date().toISOString(),
  }
}

// ─── Filter node types ───────────────────────────────────────────────────────
type FilterSelection =
  | { type: 'all' }
  | { type: 'parent'; parentId: string }
  | { type: 'child'; childId: string }

function formatDate(iso: string): string {
  try {
    return new Date(iso).toLocaleDateString('en-US', { month: 'short', day: 'numeric' })
  } catch {
    return '—'
  }
}

// ─── Skeleton row ────────────────────────────────────────────────────────────
function SkeletonRow() {
  return (
    <tr className="border-b border-surface-100">
      {[60, 24, 28, 16, 10].map((w, i) => (
        <td key={i} className="px-4 py-3">
          <div
            className="animate-pulse rounded bg-surface-100"
            style={{ width: `${w}%`, height: 14 }}
          />
        </td>
      ))}
    </tr>
  )
}

// ─── Main component ──────────────────────────────────────────────────────────
export default function CompetitorsPage() {
  const { competitors, deleteCompetitor, setCompetitors, parentProjects, childProjects } = useAppStore()

  const [search, setSearch] = useState('')
  const [loading, setLoading] = useState(false)
  const [filter, setFilter] = useState<FilterSelection>({ type: 'all' })
  const [expandedParents, setExpandedParents] = useState<Set<string>>(new Set())

  // ── Data loading ──────────────────────────────────────────────────────────
  const loadCompetitors = useCallback(async () => {
    setLoading(true)
    try {
      const res = await fetch(`${API}/api/competitors/`)
      if (!res.ok) throw new Error(`Failed to load competitors (${res.status})`)
      const data: Record<string, unknown>[] = await res.json()
      const items = data.map(mapStoreCompetitor)
      setCompetitors(items)

      const missingChannels = items.filter((item) => !item.channel.trim() && item.url.trim())
      if (missingChannels.length > 0) {
        const patched = await Promise.all(missingChannels.map(async (item) => {
          const meta = await resolveYoutubeMetadata(item.url)
          if (!meta.channelName.trim()) return null

          const patchRes = await fetch(`${API}/api/competitors/${item.id}`, {
            method: 'PATCH',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
              channel: meta.channelName,
              title: item.title.trim() || meta.title,
              thumbnail_url: item.thumbnail.trim() || meta.thumbnailUrl,
            }),
          })
          if (!patchRes.ok) return null
          return patchRes.json()
        }))

        const validPatched = patched.filter(Boolean) as Record<string, unknown>[]
        if (validPatched.length > 0) {
          const mergedById = new Map(items.map((item) => [item.id, item]))
          validPatched.map(mapStoreCompetitor).forEach((item) => mergedById.set(item.id, item))
          setCompetitors(Array.from(mergedById.values()))
        }
      }
    } catch (err) {
      const msg = err instanceof Error ? err.message : 'Could not load competitors.'
      toast.error('Failed', msg)
    } finally {
      setLoading(false)
    }
  }, [setCompetitors])

  useEffect(() => {
    void loadCompetitors()
  }, [loadCompetitors])

  const handleDeleteCompetitor = useCallback(async (id: string) => {
    try {
      const res = await fetch(`${API}/api/competitors/${id}`, { method: 'DELETE' })
      if (!res.ok) throw new Error(`Failed to delete competitor (${res.status})`)
      deleteCompetitor(id)
      toast.success('Deleted', 'Competitor removed.')
    } catch (err) {
      const msg = err instanceof Error ? err.message : 'Could not delete competitor.'
      toast.error('Failed', msg)
    }
  }, [deleteCompetitor])

  // ── Derived counts per project ────────────────────────────────────────────
  const countByParent = useMemo(() => {
    const map = new Map<string, number>()
    for (const c of competitors) {
      if (c.parentProjectId) {
        map.set(c.parentProjectId, (map.get(c.parentProjectId) ?? 0) + 1)
      }
    }
    return map
  }, [competitors])

  const countByChild = useMemo(() => {
    const map = new Map<string, number>()
    for (const c of competitors) {
      if (c.childProjectId) {
        map.set(c.childProjectId, (map.get(c.childProjectId) ?? 0) + 1)
      }
    }
    return map
  }, [competitors])

  // ── Filtered list ─────────────────────────────────────────────────────────
  const filtered = useMemo(() => {
    let list = competitors

    if (filter.type === 'parent') {
      list = list.filter((c) => c.parentProjectId === filter.parentId)
    } else if (filter.type === 'child') {
      list = list.filter((c) => c.childProjectId === filter.childId)
    }

    const query = search.trim().toLowerCase()
    if (query) {
      list = list.filter(
        (c) => c.title.toLowerCase().includes(query) || c.channel.toLowerCase().includes(query),
      )
    }

    return list
  }, [competitors, filter, search])

  // ── Filter tree helpers ───────────────────────────────────────────────────
  const toggleParentExpand = (parentId: string) => {
    setExpandedParents((prev) => {
      const next = new Set(prev)
      if (next.has(parentId)) next.delete(parentId)
      else next.add(parentId)
      return next
    })
  }

  const filterLabel = useMemo(() => {
    if (filter.type === 'all') return null
    if (filter.type === 'parent') {
      return parentProjects.find((p) => p.id === filter.parentId)?.name ?? 'this project'
    }
    return childProjects.find((c) => c.id === filter.childId)?.name ?? 'this project'
  }, [filter, parentProjects, childProjects])

  // ── Project lookup for table ──────────────────────────────────────────────
  const projectLabel = useCallback(
    (item: (typeof competitors)[number]): string => {
      if (item.childProjectId) {
        const child = childProjects.find((c) => c.id === item.childProjectId)
        if (child) return child.name
      }
      if (item.parentProjectId) {
        const parent = parentProjects.find((p) => p.id === item.parentProjectId)
        if (parent) return parent.name
      }
      return '—'
    },
    [childProjects, parentProjects],
  )

  // ── Parents that actually have competitors ────────────────────────────────
  const activeParents = useMemo(
    () => parentProjects.filter((p) => (countByParent.get(p.id) ?? 0) > 0),
    [parentProjects, countByParent],
  )

  return (
    <div className="flex flex-col h-full bg-white">

      {/* ── Header ─────────────────────────────────────────────────────── */}
      <div className="flex items-center gap-3 px-5 py-3.5 border-b border-surface-200 bg-white shrink-0">
        <div className="flex h-8 w-8 items-center justify-center rounded-lg bg-primary-50 text-primary-600 shrink-0">
          <Target size={16} />
        </div>
        <div className="flex-1 min-w-0">
          <div className="flex items-center gap-2">
            <h1 className="text-sm font-semibold text-surface-900">Competitors</h1>
            <span className="rounded-full bg-surface-100 px-2 py-0.5 text-xs font-medium text-surface-500">
              {competitors.length}
            </span>
          </div>
        </div>

        {/* Search */}
        <div className="relative w-56 shrink-0">
          <Search size={13} className="absolute left-2.5 top-1/2 -translate-y-1/2 text-surface-400 pointer-events-none" />
          <input
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            placeholder="Search title or channel…"
            className="w-full rounded-lg border border-surface-200 bg-surface-50 py-1.5 pl-8 pr-3 text-xs focus:border-primary-400 focus:outline-none focus:ring-2 focus:ring-primary-100 transition-colors"
          />
        </div>

        <button
          type="button"
          className="btn-secondary inline-flex items-center gap-1.5 px-2.5 py-1.5 text-xs shrink-0"
          onClick={() => void loadCompetitors()}
          disabled={loading}
          aria-label="Refresh competitors"
        >
          <RefreshCw size={12} className={cn(loading && 'animate-spin')} />
          Refresh
        </button>
      </div>

      {/* ── Body: filter tree + table ───────────────────────────────────── */}
      <div className="flex flex-1 overflow-hidden">

        {/* ── Left filter tree ─────────────────────────────────────────── */}
        <aside className="w-48 shrink-0 border-r border-surface-200 bg-surface-50 overflow-y-auto py-2">

          {/* All */}
          <button
            type="button"
            onClick={() => setFilter({ type: 'all' })}
            className={cn(
              'flex w-full items-center justify-between px-3 py-1.5 text-xs font-medium transition-colors cursor-pointer',
              filter.type === 'all'
                ? 'bg-primary-50 text-primary-700'
                : 'text-surface-600 hover:bg-surface-100 hover:text-surface-900',
            )}
          >
            <span>All competitors</span>
            <span className={cn(
              'rounded-full px-1.5 py-0.5 text-[10px] font-semibold tabular-nums',
              filter.type === 'all' ? 'bg-primary-100 text-primary-700' : 'bg-surface-200 text-surface-500',
            )}>
              {competitors.length}
            </span>
          </button>

          {/* Parent projects with children */}
          {activeParents.length > 0 && (
            <div className="mt-1 px-3 pb-1">
              <p className="mb-1 text-[10px] font-semibold uppercase tracking-wide text-surface-400">
                By project
              </p>
            </div>
          )}

          {activeParents.map((parent) => {
            const isExpanded = expandedParents.has(parent.id)
            const isParentActive = filter.type === 'parent' && filter.parentId === parent.id
            const parentCount = countByParent.get(parent.id) ?? 0
            const children = childProjects.filter(
              (c) => c.parentId === parent.id && (countByChild.get(c.id) ?? 0) > 0,
            )

            return (
              <div key={parent.id}>
                {/* Parent row */}
                <div className="flex items-center gap-0.5 pr-2">
                  <button
                    type="button"
                    onClick={() => toggleParentExpand(parent.id)}
                    className="flex h-6 w-5 shrink-0 items-center justify-center text-surface-400 hover:text-surface-600 cursor-pointer transition-colors"
                    aria-label={isExpanded ? 'Collapse' : 'Expand'}
                  >
                    <motion.span
                      animate={{ rotate: isExpanded ? 90 : 0 }}
                      transition={{ duration: 0.15 }}
                      className="flex items-center justify-center"
                    >
                      <ChevronRight size={12} />
                    </motion.span>
                  </button>

                  <button
                    type="button"
                    onClick={() => setFilter({ type: 'parent', parentId: parent.id })}
                    className={cn(
                      'flex flex-1 min-w-0 items-center justify-between py-1.5 pr-1 text-xs transition-colors cursor-pointer rounded',
                      isParentActive
                        ? 'text-primary-700 font-semibold'
                        : 'text-surface-600 hover:text-surface-900 font-medium',
                    )}
                  >
                    <span className="truncate">{parent.name}</span>
                    <span className={cn(
                      'ml-1 shrink-0 rounded-full px-1.5 py-0.5 text-[10px] font-semibold tabular-nums',
                      isParentActive ? 'bg-primary-100 text-primary-700' : 'bg-surface-200 text-surface-500',
                    )}>
                      {parentCount}
                    </span>
                  </button>
                </div>

                {/* Child rows */}
                <AnimatePresence initial={false}>
                  {isExpanded && (
                    <motion.div
                      initial={{ height: 0, opacity: 0 }}
                      animate={{ height: 'auto', opacity: 1 }}
                      exit={{ height: 0, opacity: 0 }}
                      transition={{ duration: 0.18, ease: 'easeInOut' }}
                      className="overflow-hidden"
                    >
                      {children.map((child) => {
                        const isChildActive = filter.type === 'child' && filter.childId === child.id
                        const childCount = countByChild.get(child.id) ?? 0
                        return (
                          <button
                            key={child.id}
                            type="button"
                            onClick={() => setFilter({ type: 'child', childId: child.id })}
                            className={cn(
                              'flex w-full items-center justify-between py-1 pl-8 pr-3 text-xs transition-colors cursor-pointer',
                              isChildActive
                                ? 'bg-primary-50 text-primary-700 font-semibold'
                                : 'text-surface-500 hover:bg-surface-100 hover:text-surface-800',
                            )}
                          >
                            <span className="truncate">{child.name}</span>
                            <span className={cn(
                              'ml-1 shrink-0 rounded-full px-1.5 py-0.5 text-[10px] font-semibold tabular-nums',
                              isChildActive ? 'bg-primary-100 text-primary-700' : 'bg-surface-200 text-surface-500',
                            )}>
                              {childCount}
                            </span>
                          </button>
                        )
                      })}
                    </motion.div>
                  )}
                </AnimatePresence>
              </div>
            )
          })}
        </aside>

        {/* ── Table area ───────────────────────────────────────────────── */}
        <div className="flex-1 overflow-auto">
          {loading ? (
            // Loading skeleton
            <table className="w-full text-sm">
              <thead>
                <tr className="border-b border-surface-200 bg-surface-50">
                  <th className="px-4 py-2.5 text-left text-[11px] font-semibold uppercase tracking-wide text-surface-400">Title</th>
                  <th className="px-4 py-2.5 text-left text-[11px] font-semibold uppercase tracking-wide text-surface-400">Channel</th>
                  <th className="px-4 py-2.5 text-left text-[11px] font-semibold uppercase tracking-wide text-surface-400">Project</th>
                  <th className="px-4 py-2.5 text-left text-[11px] font-semibold uppercase tracking-wide text-surface-400">Added</th>
                  <th className="w-16 px-4 py-2.5" />
                </tr>
              </thead>
              <tbody>
                <SkeletonRow />
                <SkeletonRow />
                <SkeletonRow />
                <SkeletonRow />
              </tbody>
            </table>
          ) : competitors.length === 0 ? (
            // Empty — no competitors at all
            <div className="flex h-full flex-col items-center justify-center gap-2 text-surface-400 py-20">
              <Target size={36} className="opacity-25" />
              <p className="text-sm font-medium text-surface-500">No competitors yet</p>
              <p className="text-xs text-surface-400">Save a competitor from Resource Prep to see it here.</p>
            </div>
          ) : filtered.length === 0 ? (
            // Empty — filtered, no results
            <div className="flex h-full flex-col items-center justify-center gap-2 text-surface-400 py-20">
              <Target size={36} className="opacity-25" />
              <p className="text-sm font-medium text-surface-500">
                No competitors for {filterLabel ?? 'this selection'}
              </p>
              <p className="text-xs text-surface-400">Try selecting a different project.</p>
            </div>
          ) : (
            // Data table
            <table className="w-full text-sm">
              <thead className="sticky top-0 z-10 bg-white">
                <tr className="border-b border-surface-200 bg-surface-50">
                  <th className="px-4 py-2.5 text-left text-[11px] font-semibold uppercase tracking-wide text-surface-400">
                    Title
                  </th>
                  <th className="px-4 py-2.5 text-left text-[11px] font-semibold uppercase tracking-wide text-surface-400 w-36">
                    Channel
                  </th>
                  <th className="px-4 py-2.5 text-left text-[11px] font-semibold uppercase tracking-wide text-surface-400 w-40">
                    Project
                  </th>
                  <th className="px-4 py-2.5 text-left text-[11px] font-semibold uppercase tracking-wide text-surface-400 w-24">
                    Added
                  </th>
                  <th className="w-16 px-4 py-2.5" />
                </tr>
              </thead>
              <tbody>
                <AnimatePresence initial={false}>
                  {filtered.map((item) => (
                    <motion.tr
                      key={item.id}
                      initial={{ opacity: 0, y: 4 }}
                      animate={{ opacity: 1, y: 0 }}
                      exit={{ opacity: 0 }}
                      transition={{ duration: 0.15 }}
                      className="group border-b border-surface-100 hover:bg-surface-50 transition-colors"
                    >
                      {/* Title — clickable, opens YouTube */}
                      <td className="px-4 py-2.5 max-w-0">
                        <a
                          href={item.url}
                          target="_blank"
                          rel="noopener noreferrer"
                          className="flex items-center gap-1.5 min-w-0 cursor-pointer"
                          title={item.title || item.url}
                        >
                          <span className="truncate text-sm font-medium text-surface-800 group-hover:text-primary-600 transition-colors">
                            {item.title || item.url || 'Untitled'}
                          </span>
                          <ExternalLink
                            size={12}
                            className="shrink-0 text-surface-300 group-hover:text-primary-400 transition-colors opacity-0 group-hover:opacity-100"
                          />
                        </a>
                      </td>

                      {/* Channel */}
                      <td className="px-4 py-2.5 w-36">
                        <span className="text-xs text-surface-500 truncate block">
                          {item.channel ? `@${item.channel.replace(/^@/, '')}` : '—'}
                        </span>
                      </td>

                      {/* Project */}
                      <td className="px-4 py-2.5 w-40">
                        <span className="inline-flex items-center rounded-md bg-surface-100 px-2 py-0.5 text-[11px] font-medium text-surface-600 truncate max-w-full">
                          {projectLabel(item)}
                        </span>
                      </td>

                      {/* Date */}
                      <td className="px-4 py-2.5 w-24">
                        <span className="text-xs text-surface-400 tabular-nums">
                          {formatDate(item.addedAt)}
                        </span>
                      </td>

                      {/* Actions */}
                      <td className="px-4 py-2.5 w-16 text-right">
                        <button
                          type="button"
                          onClick={() => void handleDeleteCompetitor(item.id)}
                          className="btn-icon text-surface-300 hover:bg-red-50 hover:text-red-500 opacity-0 group-hover:opacity-100 transition-all cursor-pointer"
                          aria-label="Delete competitor"
                          title="Delete"
                        >
                          <Trash2 size={13} />
                        </button>
                      </td>
                    </motion.tr>
                  ))}
                </AnimatePresence>
              </tbody>
            </table>
          )}
        </div>
      </div>
    </div>
  )
}
