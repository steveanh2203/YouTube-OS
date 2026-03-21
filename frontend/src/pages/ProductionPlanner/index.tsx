import { useCallback, useEffect, useRef, useState } from 'react'
import { AnimatePresence, motion } from 'framer-motion'
import {
  AlertTriangle, ArrowRight, CalendarClock, CalendarDays, CheckCircle2,
  ChevronLeft, ChevronRight, ChevronsRight, Clock3, FolderKanban,
  GripVertical, Kanban, Loader2, PanelLeftClose, PanelLeftOpen,
  Plus, Search, Sparkles, X,
} from 'lucide-react'
import { useAppStore, type ChildProject, type ChildStatus, type ParentProject, type PlannerPriority, type PlannerStage } from '@/store/app.store'
import { usePanelContext } from '@/contexts/PanelContext'
import { cn } from '@/lib/utils'

const API = 'http://127.0.0.1:8765'

type DeadlineFilter = 'all' | 'overdue' | 'today' | 'this-week' | 'none'
type ViewMode = 'kanban' | 'timeline'

// ─── API shape ──────────────────────────────────────────────────────────────

interface ApiParentProject {
  id: number
  name: string
  author: string | null
  publisher: string | null
  copyright: string | null
  keywords_raw: string | null
  roxy_workspace_id: number | null
  roxy_profile_id: string | null
  roxy_profile_name: string | null
  created_at: string
}

// ─── Constants ──────────────────────────────────────────────────────────────

const PRIORITY_OPTIONS: { value: PlannerPriority; label: string; tone: string }[] = [
  { value: 'low',    label: 'Low',    tone: 'border-slate-200 bg-slate-100 text-slate-600' },
  { value: 'medium', label: 'Medium', tone: 'border-primary-200 bg-primary-50 text-primary-700' },
  { value: 'high',   label: 'High',   tone: 'border-amber-200 bg-amber-50 text-amber-700' },
  { value: 'urgent', label: 'Urgent', tone: 'border-rose-200 bg-rose-50 text-rose-700' },
]

const STAGE_META: Record<PlannerStage, {
  title: string
  badge: string
  accent: string
  surface: string
  border: string
  icon: React.ElementType
}> = {
  backlog: {
    title: 'Backlog',
    badge: 'text-slate-700 bg-slate-100 border-slate-200',
    accent: 'bg-slate-400',
    surface: 'bg-slate-50/80',
    border: 'border-slate-200',
    icon: FolderKanban,
  },
  ready: {
    title: 'Ready',
    badge: 'text-primary-700 bg-primary-50 border-primary-200',
    accent: 'bg-primary-500',
    surface: 'bg-primary-50/60',
    border: 'border-primary-200',
    icon: Sparkles,
  },
  editing: {
    title: 'Editing',
    badge: 'text-sky-700 bg-sky-50 border-sky-200',
    accent: 'bg-sky-500',
    surface: 'bg-sky-50/60',
    border: 'border-sky-200',
    icon: ChevronsRight,
  },
  published: {
    title: 'Published',
    badge: 'text-emerald-700 bg-emerald-50 border-emerald-200',
    accent: 'bg-emerald-500',
    surface: 'bg-emerald-50/60',
    border: 'border-emerald-200',
    icon: CheckCircle2,
  },
}

const BOARD_STAGES: PlannerStage[] = ['ready', 'editing', 'published']
const INSPECTOR_STAGES: PlannerStage[] = ['backlog', 'ready', 'editing', 'published']

// ─── Mapper helpers ──────────────────────────────────────────────────────────

function mapParent(raw: ApiParentProject, childCount: number): ParentProject {
  return {
    id: String(raw.id),
    name: raw.name,
    author: raw.author ?? '',
    publisher: raw.publisher ?? '',
    copyright: raw.copyright ?? '',
    keywordsRaw: raw.keywords_raw ?? '',
    roxyWorkspaceId: raw.roxy_workspace_id ?? null,
    roxyProfileId: raw.roxy_profile_id ?? '',
    roxyProfileName: raw.roxy_profile_name ?? '',
    childCount,
    createdAt: raw.created_at.slice(0, 10),
  }
}

function derivePlanningStage(rawStatus: ChildStatus | undefined, rawStage: unknown): PlannerStage {
  if (rawStage === 'backlog' || rawStage === 'ready' || rawStage === 'editing' || rawStage === 'published') return rawStage
  if (rawStatus === 'published') return 'published'
  if (rawStatus === 'editing') return 'editing'
  if (rawStatus === 'resource_prep') return 'ready'
  return 'backlog'
}

function mapChild(raw: Record<string, unknown>): ChildProject {
  const rawStatus = raw.status as ChildStatus | undefined
  return {
    id: String(raw.id),
    parentId: String(raw.parent_project_id),
    name: (raw.display_name as string) ?? `Video ${raw.video_number}`,
    status: rawStatus ?? 'draft',
    title: (raw.title as string) ?? '',
    description: (raw.description as string) ?? '',
    seedingComments: (raw.seeding_comments as string) ?? '',
    folderPath: (raw.base_folder_path as string) ?? '',
    planningStage: derivePlanningStage(rawStatus, raw.planning_stage),
    priority: (raw.priority as PlannerPriority) ?? 'medium',
    deadline: raw.deadline ? String(raw.deadline) : null,
    planningNote: (raw.planning_note as string) ?? '',
    roxyWorkspaceId: (raw.roxy_workspace_id as number) ?? null,
    roxyProfileId: (raw.roxy_profile_id as string) ?? '',
    roxyProfileName: (raw.roxy_profile_name as string) ?? '',
    prepTitleDone: (raw.prep_title_done as boolean) ?? false,
    prepDescDone: (raw.prep_desc_done as boolean) ?? false,
    prepSeedDone: (raw.prep_seed_done as boolean) ?? false,
    prepFolderDone: (raw.prep_folder_done as boolean) ?? false,
    createdAt: raw.created_at ? String(raw.created_at).slice(0, 10) : new Date().toISOString().slice(0, 10),
  }
}

// ─── Date helpers ────────────────────────────────────────────────────────────

function startOfToday() {
  const now = new Date()
  now.setHours(0, 0, 0, 0)
  return now
}

function endOfWeek(today: Date) {
  const end = new Date(today)
  end.setDate(today.getDate() + 6)
  end.setHours(23, 59, 59, 999)
  return end
}

function parseDate(value: string | null) {
  if (!value) return null
  const date = new Date(`${value}T00:00:00`)
  return Number.isNaN(date.getTime()) ? null : date
}

function isOverdue(child: ChildProject, today: Date) {
  const deadline = parseDate(child.deadline)
  return !!deadline && deadline < today && child.planningStage !== 'published'
}

function isDueToday(child: ChildProject, today: Date) {
  const deadline = parseDate(child.deadline)
  return !!deadline && deadline.getTime() === today.getTime() && child.planningStage !== 'published'
}

function matchesDeadlineFilter(child: ChildProject, filter: DeadlineFilter, today: Date) {
  const deadline = parseDate(child.deadline)
  if (filter === 'all') return true
  if (filter === 'none') return !deadline
  if (!deadline) return false
  if (filter === 'overdue') return deadline < today
  if (filter === 'today') return deadline.getTime() === today.getTime()
  return deadline >= today && deadline <= endOfWeek(today)
}

function priorityRank(priority: PlannerPriority) {
  return { urgent: 0, high: 1, medium: 2, low: 3 }[priority]
}

function sortChildren(children: ChildProject[]) {
  return [...children].sort((a, b) => {
    const priorityDiff = priorityRank(a.priority) - priorityRank(b.priority)
    if (priorityDiff !== 0) return priorityDiff
    const aDeadline = parseDate(a.deadline)?.getTime() ?? Number.MAX_SAFE_INTEGER
    const bDeadline = parseDate(b.deadline)?.getTime() ?? Number.MAX_SAFE_INTEGER
    if (aDeadline !== bDeadline) return aDeadline - bDeadline
    return a.name.localeCompare(b.name)
  })
}

function priorityTone(priority: PlannerPriority) {
  return PRIORITY_OPTIONS.find((item) => item.value === priority)?.tone ?? PRIORITY_OPTIONS[1].tone
}

function stageCount(children: ChildProject[], stage: PlannerStage) {
  return children.filter((child) => child.planningStage === stage).length
}

function dueLabel(child: ChildProject, today: Date) {
  const deadline = parseDate(child.deadline)
  if (!deadline) return 'No deadline'
  if (isOverdue(child, today)) return `Overdue · ${child.deadline}`
  if (isDueToday(child, today)) return 'Due today'
  return `Due ${child.deadline}`
}

// ─── Sub-components ──────────────────────────────────────────────────────────

function SkeletonCard() {
  return (
    <div className="w-full animate-pulse rounded-2xl border border-surface-200 bg-white p-4">
      <div className="h-3 w-1/3 rounded bg-surface-100" />
      <div className="mt-3 h-4 w-4/5 rounded bg-surface-200" />
      <div className="mt-2 h-3 w-2/3 rounded bg-surface-100" />
      <div className="mt-4 flex gap-2">
        <div className="h-5 w-14 rounded-full bg-surface-100" />
        <div className="h-5 w-20 rounded-full bg-surface-100" />
      </div>
    </div>
  )
}

function EmptyLane({ stage, onAdd }: { stage: PlannerStage; onAdd?: () => void }) {
  const messages: Record<PlannerStage, { title: string; body: string; cta?: string }> = {
    backlog:   { title: 'Ideas go here first', body: 'Drag cards in or add a new video idea.', cta: 'Add idea' },
    ready:     { title: 'Nothing ready yet', body: 'Promote a card from Backlog when it\'s ready to edit.' },
    editing:   { title: 'No active edits', body: 'Drag a Ready card here when you start working on it.' },
    published: { title: 'No published videos', body: 'Drag a card here when the video is live.' },
  }
  const { title, body, cta } = messages[stage]
  const meta = STAGE_META[stage]
  const Icon = meta.icon

  return (
    <div className="flex min-h-[120px] flex-col items-center justify-center gap-2 rounded-2xl border border-dashed border-current/20 px-4 py-6 text-center opacity-70">
      <Icon size={22} className="opacity-40" />
      <p className="text-sm font-medium">{title}</p>
      <p className="text-xs leading-5 opacity-70">{body}</p>
      {cta && onAdd && (
        <button
          onClick={onAdd}
          className="mt-1 flex items-center gap-1 rounded-full border border-current/20 px-3 py-1 text-xs font-medium transition-colors hover:border-current/40 cursor-pointer"
        >
          <Plus size={12} />
          {cta}
        </button>
      )}
    </div>
  )
}

function PlannerCard({
  child,
  parent,
  today,
  selected,
  celebrating,
  onSelect,
  onDragStart,
  onDragEnd,
}: {
  child: ChildProject
  parent?: ParentProject
  today: Date
  selected?: boolean
  celebrating?: boolean
  onSelect: () => void
  onDragStart: () => void
  onDragEnd: () => void
}) {
  const overdue  = isOverdue(child, today)
  const dueToday = isDueToday(child, today)

  // Single element: motion.div with drag + click + role=button.
  // WebKit (Tauri) blocks parent-div drag when child is <button>.
  // Solution: one element owns both drag and click, no nested button.
  return (
    <motion.div
      draggable
      onDragStart={(e) => {
        e.dataTransfer.effectAllowed = 'move'
        e.dataTransfer.setData('text/plain', child.id) // required for WebKit
        onDragStart()
      }}
      onDragEnd={onDragEnd}
      onClick={onSelect}
      role="button"
      tabIndex={0}
      onKeyDown={(e) => { if (e.key === 'Enter' || e.key === ' ') onSelect() }}
      animate={
        celebrating
          ? { scale: [1, 1.04, 1], backgroundColor: ['#ffffff', '#d1fae5', '#ffffff'] }
          : { scale: 1, backgroundColor: '#ffffff' }
      }
      transition={{ duration: 0.35 }}
      style={{ WebkitUserDrag: 'element', userSelect: 'none', cursor: 'grab' } as React.CSSProperties}
      className={cn(
        'group w-full rounded-2xl border bg-white p-4 text-left shadow-sm',
        'transition-shadow duration-150 hover:-translate-y-0.5 hover:shadow-md',
        'focus:outline-none focus:ring-2 focus:ring-primary-300 focus:ring-offset-1',
        'active:cursor-grabbing',
        selected    ? 'border-primary-300 ring-2 ring-primary-100' : 'border-surface-200',
        overdue     ? '!bg-rose-50/60'  : '',
        dueToday    ? '!bg-amber-50/60' : '',
        celebrating ? 'border-emerald-300' : ''
      )}
    >
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0 flex-1">
          <div className="flex items-center gap-1.5 text-[10px] font-semibold uppercase tracking-[0.16em] text-surface-400">
            <GripVertical size={12} className="shrink-0 opacity-0 transition-opacity group-hover:opacity-60" />
            <span className="truncate">{parent?.name ?? '—'}</span>
          </div>
          <p className="mt-1.5 text-sm font-semibold leading-snug text-surface-900">
            {child.title || child.name}
          </p>
          {child.planningNote && (
            <p className="mt-1 line-clamp-2 text-xs leading-relaxed text-surface-500">
              {child.planningNote}
            </p>
          )}
        </div>

        {(overdue || dueToday) && (
          <span className={cn(
            'shrink-0 rounded-full px-2 py-0.5 text-[10px] font-bold uppercase tracking-wide',
            overdue  ? 'bg-rose-100 text-rose-700'  : '',
            dueToday ? 'bg-amber-100 text-amber-700' : ''
          )}>
            {overdue ? 'Late' : 'Today'}
          </span>
        )}
      </div>

      <div className="mt-3 flex flex-wrap items-center gap-1.5">
        <span className={cn('rounded-full border px-2 py-0.5 text-[10px] font-semibold', priorityTone(child.priority))}>
          {child.priority}
        </span>
        <span className={cn(
          'rounded-full border px-2 py-0.5 text-[10px] font-medium',
          overdue   ? 'border-rose-200 bg-rose-50 text-rose-600'
          : dueToday ? 'border-amber-200 bg-amber-50 text-amber-600'
          : 'border-surface-200 bg-surface-50 text-surface-500'
        )}>
          {dueLabel(child, today)}
        </span>
      </div>
    </motion.div>
  )
}

// ─── Weekly Timeline view ────────────────────────────────────────────────────

function WeeklyTimeline({
  children: cards,
  parents,
  today,
  weekOffset,
  onWeekChange,
  onSelect,
  selectedId,
}: {
  children: ChildProject[]
  parents: ParentProject[]
  today: Date
  weekOffset: number
  onWeekChange: (delta: number) => void
  onSelect: (id: string) => void
  selectedId: string | null
}) {
  const weekStart = new Date(today)
  weekStart.setDate(today.getDate() - today.getDay() + 1 + weekOffset * 7) // Mon
  weekStart.setHours(0, 0, 0, 0)

  const days = Array.from({ length: 7 }, (_, i) => {
    const d = new Date(weekStart)
    d.setDate(weekStart.getDate() + i)
    return d
  })

  const DAY_NAMES = ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun']

  function cardsForDay(day: Date) {
    return cards.filter((c) => {
      if (!c.deadline) return false
      const dl = parseDate(c.deadline)
      return dl && dl.getFullYear() === day.getFullYear() && dl.getMonth() === day.getMonth() && dl.getDate() === day.getDate()
    })
  }

  const rangeLabel = (() => {
    const s = days[0]
    const e = days[6]
    const fmt = (d: Date) => `${d.getDate()} ${d.toLocaleString('en', { month: 'short' })}`
    return `${fmt(s)} — ${fmt(e)}, ${e.getFullYear()}`
  })()

  return (
    <div className="flex flex-1 min-h-0 flex-col">
      {/* Week nav */}
      <div className="flex items-center gap-3 px-6 py-3 border-b border-surface-100">
        <button
          onClick={() => onWeekChange(-1)}
          className="rounded-lg border border-surface-200 p-1.5 text-surface-500 hover:bg-surface-50 transition-colors cursor-pointer"
        >
          <ChevronLeft size={16} />
        </button>
        <span className="flex-1 text-center text-sm font-semibold text-surface-700">{rangeLabel}</span>
        <button
          onClick={() => onWeekChange(1)}
          className="rounded-lg border border-surface-200 p-1.5 text-surface-500 hover:bg-surface-50 transition-colors cursor-pointer"
        >
          <ChevronRight size={16} />
        </button>
      </div>

      {/* 7-day grid */}
      <div className="flex min-h-0 flex-1 overflow-x-auto px-4 py-4">
        <div className="grid h-full min-w-[900px] flex-1 grid-cols-7 gap-3">
          {days.map((day, i) => {
            const dayCards = cardsForDay(day)
            const isToday = day.getFullYear() === today.getFullYear() && day.getMonth() === today.getMonth() && day.getDate() === today.getDate()

            return (
              <div key={i} className={cn(
                'flex flex-col rounded-2xl border min-h-[200px]',
                isToday ? 'border-primary-200 bg-primary-50/40' : 'border-surface-200 bg-white/60'
              )}>
                {/* Day header */}
                <div className={cn(
                  'px-3 py-2.5 border-b',
                  isToday ? 'border-primary-200' : 'border-surface-100'
                )}>
                  <p className="text-[10px] font-bold uppercase tracking-widest text-surface-400">{DAY_NAMES[i]}</p>
                  <p className={cn(
                    'text-xl font-bold leading-none mt-0.5',
                    isToday ? 'text-primary-600' : 'text-surface-700'
                  )}>
                    {day.getDate()}
                  </p>
                </div>

                {/* Cards */}
                <div className="flex-1 space-y-2 overflow-y-auto p-2">
                  {dayCards.length === 0 ? (
                    <p className="text-center text-[10px] text-surface-300 pt-4">—</p>
                  ) : (
                    dayCards.map((c) => {
                      const parent = parents.find((p) => p.id === c.parentId)
                      const meta = STAGE_META[c.planningStage]
                      return (
                        <button
                          key={c.id}
                          onClick={() => onSelect(c.id)}
                          className={cn(
                            'w-full rounded-xl border p-2 text-left transition-all cursor-pointer',
                            'hover:shadow-sm focus:outline-none focus:ring-2 focus:ring-primary-300',
                            c.id === selectedId ? 'border-primary-300 bg-primary-50' : `${meta.surface} ${meta.border}`,
                            isOverdue(c, today) ? 'border-rose-200 bg-rose-50' : ''
                          )}
                        >
                          <p className="text-[10px] font-semibold uppercase tracking-wide opacity-60 truncate">
                            {parent?.name ?? '—'}
                          </p>
                          <p className="mt-0.5 text-xs font-semibold text-surface-900 line-clamp-2">
                            {c.title || c.name}
                          </p>
                          <div className="mt-1.5 flex items-center gap-1">
                            <span className={cn('rounded-full px-1.5 py-0.5 text-[9px] font-bold border', meta.badge)}>
                              {meta.title}
                            </span>
                            <span className={cn('rounded-full px-1.5 py-0.5 text-[9px] font-semibold border', priorityTone(c.priority))}>
                              {c.priority}
                            </span>
                          </div>
                        </button>
                      )
                    })
                  )}
                </div>
              </div>
            )
          })}
        </div>
      </div>
    </div>
  )
}

// ─── Main component ──────────────────────────────────────────────────────────

export default function ProductionPlanner() {
  const {
    parentProjects,
    childProjects,
    setParentProjects,
    setChildren,
    updateChild,
  } = useAppStore()

  const { setMainView, setProjectSubView, selectParent, selectChild } = usePanelContext()

  // ── Data state ──
  const [loading, setLoading]     = useState(false)
  const [error, setError]         = useState<string | null>(null)

  // ── Filter state ──
  const [parentFilter, setParentFilter]     = useState('all')
  const [priorityFilter, setPriorityFilter] = useState<'all' | PlannerPriority>('all')
  const [deadlineFilter, setDeadlineFilter] = useState<DeadlineFilter>('all')
  const [search, setSearch]                 = useState('')

  // ── UI state ──
  const [viewMode, setViewMode]             = useState<ViewMode>('kanban')
  const [backlogOpen, setBacklogOpen]       = useState(true)
  const [weekOffset, setWeekOffset]         = useState(0)

  // ── Drag state ──
  // useRef ensures handleDrop always reads the latest draggedChildId,
  // avoiding stale-closure bug where drop fires after dragend clears state.
  const [draggedChildId, setDraggedChildId] = useState<string | null>(null)
  const draggedChildIdRef = useRef<string | null>(null)
  const [dragTarget, setDragTarget]         = useState<PlannerStage | null>(null)

  // ── Inspector state ──
  const [selectedChildId, setSelectedChildId] = useState<string | null>(null)
  const [drawerStage, setDrawerStage]         = useState<PlannerStage>('backlog')
  const [drawerPriority, setDrawerPriority]   = useState<PlannerPriority>('medium')
  const [drawerDeadline, setDrawerDeadline]   = useState('')
  const [drawerNote, setDrawerNote]           = useState('')
  const [saving, setSaving]                   = useState(false)

  // ── Celebration state ──
  const [celebratingId, setCelebratingId]   = useState<string | null>(null)
  const [publishedToast, setPublishedToast] = useState<string | null>(null)

  // ── Refs ──
  const searchRef = useRef<HTMLInputElement>(null)

  // ── Data loading ──────────────────────────────────────────────────────────

  const loadPlannerData = useCallback(async () => {
    setLoading(true)
    setError(null)
    try {
      const [parentsRes, childrenRes] = await Promise.all([
        fetch(`${API}/api/parent-projects/`),
        fetch(`${API}/api/child-projects/`),
      ])
      if (!parentsRes.ok || !childrenRes.ok) throw new Error('Cannot connect to planner data')

      const rawParents: ApiParentProject[]          = await parentsRes.json()
      const rawChildren: Record<string, unknown>[]  = await childrenRes.json()
      const mappedChildren = rawChildren.map(mapChild)

      const counts = mappedChildren.reduce<Record<string, number>>((acc, child) => {
        acc[child.parentId] = (acc[child.parentId] ?? 0) + 1
        return acc
      }, {})

      setChildren(mappedChildren)
      setParentProjects(rawParents.map((p) => mapParent(p, counts[String(p.id)] ?? 0)))
    } catch {
      setError('Could not load planner data — showing cached state.')
    } finally {
      setLoading(false)
    }
  }, [setChildren, setParentProjects])

  useEffect(() => { loadPlannerData() }, [loadPlannerData])

  // ── Sync inspector fields when selection changes ──────────────────────────

  const selectedChild = childProjects.find((c) => c.id === selectedChildId) ?? null

  useEffect(() => {
    if (!selectedChild) return
    setDrawerStage(selectedChild.planningStage)
    setDrawerPriority(selectedChild.priority)
    setDrawerDeadline(selectedChild.deadline ?? '')
    setDrawerNote(selectedChild.planningNote)
  }, [selectedChild])

  // ── Keyboard shortcuts ────────────────────────────────────────────────────

  useEffect(() => {
    function onKeyDown(e: KeyboardEvent) {
      // ⌘K → focus search
      if ((e.metaKey || e.ctrlKey) && e.key === 'k') {
        e.preventDefault()
        searchRef.current?.focus()
        return
      }
      // Esc → close inspector
      if (e.key === 'Escape' && selectedChildId) {
        setSelectedChildId(null)
      }
    }
    window.addEventListener('keydown', onKeyDown)
    return () => window.removeEventListener('keydown', onKeyDown)
  }, [selectedChildId])

  // ── Derived data ──────────────────────────────────────────────────────────

  const today = startOfToday()

  const scopedChildren = childProjects.filter((child) => {
    if (parentFilter !== 'all' && child.parentId !== parentFilter) return false
    if (!search.trim()) return true
    const parentName = parentProjects.find((p) => p.id === child.parentId)?.name ?? ''
    const haystack = [child.name, child.title, child.planningNote, child.description, parentName].join(' ').toLowerCase()
    return haystack.includes(search.trim().toLowerCase())
  })

  const visibleChildren = scopedChildren.filter((child) => {
    if (priorityFilter !== 'all' && child.priority !== priorityFilter) return false
    return matchesDeadlineFilter(child, deadlineFilter, today)
  })

  const overdueCount  = scopedChildren.filter((c) => isOverdue(c, today)).length
  const dueTodayCount = scopedChildren.filter((c) => isDueToday(c, today)).length
  const readyCount    = stageCount(scopedChildren, 'ready')
  const editingCount  = stageCount(scopedChildren, 'editing')

  const backlogCards   = sortChildren(visibleChildren.filter((c) => c.planningStage === 'backlog'))
  const readyCards     = sortChildren(visibleChildren.filter((c) => c.planningStage === 'ready'))
  const editingCards   = sortChildren(visibleChildren.filter((c) => c.planningStage === 'editing'))
  const publishedCards = sortChildren(visibleChildren.filter((c) => c.planningStage === 'published'))

  const laneMap: Record<PlannerStage, ChildProject[]> = {
    backlog: backlogCards, ready: readyCards, editing: editingCards, published: publishedCards,
  }

  const stats = [
    { label: 'Overdue',   value: overdueCount,  sub: 'overdue',    icon: AlertTriangle, tone: 'text-rose-700   bg-rose-50   border-rose-200'    },
    { label: 'Due Today', value: dueTodayCount,  sub: 'due today',  icon: CalendarClock, tone: 'text-amber-700  bg-amber-50  border-amber-200'   },
    { label: 'Ready',     value: readyCount,     sub: 'ready',      icon: Sparkles,      tone: 'text-primary-700 bg-primary-50 border-primary-200' },
    { label: 'Editing',   value: editingCount,   sub: 'editing',    icon: ChevronsRight, tone: 'text-sky-700    bg-sky-50    border-sky-200'      },
  ]

  // ── Actions ───────────────────────────────────────────────────────────────

  async function patchPlanner(child: ChildProject, patch: Partial<ChildProject>) {
    const previous = child
    const next = { ...child, ...patch }
    updateChild(child.id, patch)
    try {
      const res = await fetch(`${API}/api/child-projects/${child.id}`, {
        method: 'PATCH',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          planning_stage: next.planningStage,
          priority:       next.priority,
          deadline:       next.deadline,
          planning_note:  next.planningNote,
        }),
      })
      if (!res.ok) throw new Error('patch failed')
      const raw = await res.json()
      updateChild(child.id, mapChild(raw))
    } catch {
      updateChild(child.id, previous)
      setError('Could not save — please try again.')
    }
  }

  async function handleDrop(e: React.DragEvent, stage: PlannerStage) {
    e.preventDefault()
    e.stopPropagation()
    // Read from ref — immune to stale closure even if dragend fired first
    const id = draggedChildIdRef.current
    if (!id) return
    const child = childProjects.find((c) => c.id === id)
    setDragTarget(null)
    setDraggedChildId(null)
    draggedChildIdRef.current = null
    if (!child || child.planningStage === stage) return

    await patchPlanner(child, { planningStage: stage })

    // Micro-celebration when promoted to Published
    if (stage === 'published') {
      setCelebratingId(id)
      setPublishedToast(child.title || child.name)
      setTimeout(() => { setCelebratingId(null); setPublishedToast(null) }, 2500)
    }
  }

  async function handleSaveInspector() {
    if (!selectedChild) return
    setSaving(true)
    const prevStage = selectedChild.planningStage
    await patchPlanner(selectedChild, {
      planningStage: drawerStage,
      priority:      drawerPriority,
      deadline:      drawerDeadline || null,
      planningNote:  drawerNote.trim(),
    })
    setSaving(false)

    // Celebration if moved to published via inspector
    if (drawerStage === 'published' && prevStage !== 'published') {
      setCelebratingId(selectedChild.id)
      setPublishedToast(selectedChild.title || selectedChild.name)
      setTimeout(() => { setCelebratingId(null); setPublishedToast(null) }, 2500)
    }
  }

  function openChildWorkspace(child: ChildProject) {
    selectParent(child.parentId)
    selectChild(child.id)
    setMainView('projects')
    setProjectSubView('resource-prep')
  }

  // ─── Render ───────────────────────────────────────────────────────────────

  return (
    <div className="flex h-full min-h-0 flex-col bg-gradient-to-b from-slate-50 to-slate-100/60 relative overflow-hidden">

      {/* ── Published celebration toast ── */}
      <AnimatePresence>
        {publishedToast && (
          <motion.div
            initial={{ opacity: 0, y: -20, scale: 0.96 }}
            animate={{ opacity: 1, y: 0, scale: 1 }}
            exit={{ opacity: 0, y: -12, scale: 0.96 }}
            transition={{ duration: 0.22 }}
            className="absolute top-4 left-1/2 z-50 -translate-x-1/2 flex items-center gap-2.5 rounded-2xl border border-emerald-200 bg-emerald-50 px-5 py-3 shadow-lg"
          >
            <CheckCircle2 size={16} className="text-emerald-600 shrink-0" />
            <span className="text-sm font-semibold text-emerald-800">
              Published! <span className="font-normal opacity-80 truncate max-w-[200px]">{publishedToast}</span>
            </span>
          </motion.div>
        )}
      </AnimatePresence>

      {/* ── Header ── */}
      <div className="border-b border-surface-200/80 bg-white/90 px-6 py-4 backdrop-blur-sm shrink-0">
        <div className="flex flex-wrap items-center justify-between gap-4">
          <div>
            <h1 className="text-2xl font-bold tracking-tight text-surface-900">Video Planner</h1>
            <p className="mt-0.5 text-sm text-surface-500">Plan, schedule and track every video from idea to publish.</p>
          </div>

          <div className="flex items-center gap-2">
            {/* View toggle */}
            <div className="flex items-center rounded-xl border border-surface-200 bg-surface-50 p-1">
              <button
                onClick={() => setViewMode('kanban')}
                className={cn(
                  'flex items-center gap-1.5 rounded-lg px-3 py-1.5 text-xs font-semibold transition-all cursor-pointer',
                  viewMode === 'kanban'
                    ? 'bg-white text-surface-900 shadow-sm'
                    : 'text-surface-500 hover:text-surface-700'
                )}
              >
                <Kanban size={13} />
                Kanban
              </button>
              <button
                onClick={() => setViewMode('timeline')}
                className={cn(
                  'flex items-center gap-1.5 rounded-lg px-3 py-1.5 text-xs font-semibold transition-all cursor-pointer',
                  viewMode === 'timeline'
                    ? 'bg-white text-surface-900 shadow-sm'
                    : 'text-surface-500 hover:text-surface-700'
                )}
              >
                <CalendarDays size={13} />
                Week
              </button>
            </div>

            {/* Refresh */}
            <button
              className="flex items-center gap-1.5 rounded-xl border border-surface-200 bg-white px-3 py-2 text-xs font-semibold text-surface-600 hover:bg-surface-50 transition-colors cursor-pointer disabled:opacity-50"
              onClick={() => loadPlannerData()}
              disabled={loading}
            >
              {loading ? <Loader2 size={13} className="animate-spin" /> : <Clock3 size={13} />}
              {loading ? 'Loading…' : 'Refresh'}
            </button>
          </div>
        </div>

        {/* ── Focus strip (stat cards) ── */}
        <div className="mt-4 grid grid-cols-2 gap-2 xl:grid-cols-4">
          {stats.map((s) => (
            <div key={s.label} className={cn('flex items-center gap-3 rounded-xl border px-4 py-3', s.tone)}>
              <s.icon size={16} className="shrink-0" />
              <div className="min-w-0">
                <div className="flex items-baseline gap-1.5">
                  <span className="text-2xl font-bold leading-none">{s.value}</span>
                  <span className="text-xs font-medium opacity-70">{s.sub}</span>
                </div>
                <p className="mt-0.5 text-[10px] font-semibold uppercase tracking-[0.14em] opacity-60">{s.label}</p>
              </div>
            </div>
          ))}
        </div>

        {/* ── Filters ── */}
        <div className="mt-3 flex flex-wrap items-center gap-2">
          {/* Search */}
          <div className="relative min-w-[200px] flex-1 max-w-xs">
            <Search size={13} className="absolute left-3 top-1/2 -translate-y-1/2 text-surface-400 pointer-events-none" />
            <input
              ref={searchRef}
              className="w-full rounded-xl border border-surface-200 bg-white py-2 pl-8 pr-3 text-sm text-surface-900 placeholder:text-surface-400 focus:border-primary-400 focus:outline-none focus:ring-2 focus:ring-primary-100 transition-colors"
              value={search}
              onChange={(e) => setSearch(e.target.value)}
              placeholder="Search videos… (⌘K)"
            />
          </div>

          {/* Channel filter — only show if there are multiple parents */}
          {parentProjects.length > 1 && (
            <div className="flex items-center gap-1 rounded-xl border border-surface-200 bg-white px-1 py-1">
              <button
                onClick={() => setParentFilter('all')}
                className={cn(
                  'rounded-lg px-3 py-1.5 text-xs font-semibold transition-all cursor-pointer',
                  parentFilter === 'all' ? 'bg-surface-900 text-white' : 'text-surface-500 hover:text-surface-800'
                )}
              >
                All
              </button>
              {parentProjects.slice(0, 4).map((p) => (
                <button
                  key={p.id}
                  onClick={() => setParentFilter(parentFilter === p.id ? 'all' : p.id)}
                  className={cn(
                    'rounded-lg px-3 py-1.5 text-xs font-semibold transition-all cursor-pointer truncate max-w-[120px]',
                    parentFilter === p.id ? 'bg-surface-900 text-white' : 'text-surface-500 hover:text-surface-800'
                  )}
                >
                  {p.name}
                </button>
              ))}
            </div>
          )}

          {/* Priority filter — pill chips */}
          <div className="flex items-center gap-1 rounded-xl border border-surface-200 bg-white px-1 py-1">
            <button
              onClick={() => setPriorityFilter('all')}
              className={cn(
                'rounded-lg px-3 py-1.5 text-xs font-semibold transition-all cursor-pointer',
                priorityFilter === 'all' ? 'bg-surface-900 text-white' : 'text-surface-500 hover:text-surface-800'
              )}
            >
              All
            </button>
            {PRIORITY_OPTIONS.map((o) => (
              <button
                key={o.value}
                onClick={() => setPriorityFilter(priorityFilter === o.value ? 'all' : o.value)}
                className={cn(
                  'rounded-lg px-3 py-1.5 text-xs font-semibold transition-all cursor-pointer',
                  priorityFilter === o.value
                    ? o.value === 'urgent' ? 'bg-rose-600 text-white'
                    : o.value === 'high'   ? 'bg-amber-500 text-white'
                    : o.value === 'medium' ? 'bg-primary-600 text-white'
                    : 'bg-slate-600 text-white'
                    : 'text-surface-500 hover:text-surface-800'
                )}
              >
                {o.label}
              </button>
            ))}
          </div>

          {/* Deadline filter — pill chips */}
          <div className="flex items-center gap-1 rounded-xl border border-surface-200 bg-white px-1 py-1">
            {([
              { value: 'all',       label: 'All' },
              { value: 'overdue',   label: 'Late' },
              { value: 'today',     label: 'Today' },
              { value: 'this-week', label: 'This week' },
              { value: 'none',      label: 'No date' },
            ] as { value: DeadlineFilter; label: string }[]).map((o) => (
              <button
                key={o.value}
                onClick={() => setDeadlineFilter(o.value)}
                className={cn(
                  'rounded-lg px-3 py-1.5 text-xs font-semibold transition-all cursor-pointer whitespace-nowrap',
                  deadlineFilter === o.value
                    ? o.value === 'overdue' ? 'bg-rose-600 text-white'
                    : o.value === 'today'   ? 'bg-amber-500 text-white'
                    : 'bg-surface-900 text-white'
                    : 'text-surface-500 hover:text-surface-800'
                )}
              >
                {o.label}
              </button>
            ))}
          </div>

          {/* Clear all */}
          {(search || parentFilter !== 'all' || priorityFilter !== 'all' || deadlineFilter !== 'all') && (
            <button
              onClick={() => { setSearch(''); setParentFilter('all'); setPriorityFilter('all'); setDeadlineFilter('all') }}
              className="flex items-center gap-1 rounded-xl border border-surface-200 bg-white px-3 py-2 text-xs font-semibold text-surface-500 hover:bg-rose-50 hover:border-rose-200 hover:text-rose-600 transition-colors cursor-pointer"
            >
              <X size={12} /> Clear filters
            </button>
          )}
        </div>
      </div>

      {/* ── Error banner ── */}
      <AnimatePresence>
        {error && (
          <motion.div
            initial={{ opacity: 0, height: 0 }}
            animate={{ opacity: 1, height: 'auto' }}
            exit={{ opacity: 0, height: 0 }}
            className="mx-6 mt-3 overflow-hidden rounded-xl border border-amber-200 bg-amber-50"
          >
            <div className="flex items-center justify-between px-4 py-2.5">
              <span className="text-xs font-medium text-amber-700">{error}</span>
              <button onClick={() => setError(null)} className="text-amber-500 hover:text-amber-700 cursor-pointer ml-3">
                <X size={14} />
              </button>
            </div>
          </motion.div>
        )}
      </AnimatePresence>

      {/* ── Empty (no projects at all) ── */}
      {childProjects.length === 0 && !loading ? (
        <div className="mx-6 my-6 flex flex-1 items-center justify-center rounded-3xl border border-dashed border-surface-200 bg-white/80">
          <div className="text-center px-8 py-12">
            <FolderKanban size={36} className="mx-auto mb-4 text-surface-300" />
            <p className="text-sm font-semibold text-surface-700">No videos yet</p>
            <p className="mt-1.5 text-xs leading-relaxed text-surface-400 max-w-[260px] mx-auto">
              Create child projects from the Projects page — they'll appear here to plan and track.
            </p>
            <button
              onClick={() => setMainView('projects')}
              className="mt-5 inline-flex items-center gap-2 rounded-xl bg-primary-600 px-4 py-2 text-sm font-semibold text-white hover:bg-primary-700 transition-colors cursor-pointer"
            >
              Go to Projects <ArrowRight size={14} />
            </button>
          </div>
        </div>
      ) : viewMode === 'timeline' ? (
        /* ─────────────── TIMELINE VIEW ─────────────── */
        <WeeklyTimeline
          children={visibleChildren}
          parents={parentProjects}
          today={today}
          weekOffset={weekOffset}
          onWeekChange={(d) => setWeekOffset((w) => w + d)}
          onSelect={setSelectedChildId}
          selectedId={selectedChildId}
        />
      ) : (
        /* ─────────────── KANBAN VIEW ─────────────── */
        <div className="flex min-h-0 flex-1 gap-0">

          {/* ── Backlog Panel (collapsible) ── */}
          <AnimatePresence initial={false}>
            {backlogOpen && (
              <motion.aside
                key="backlog-panel"
                initial={{ width: 0, opacity: 0 }}
                animate={{ width: 280, opacity: 1 }}
                exit={{ width: 0, opacity: 0 }}
                transition={{ duration: 0.22, ease: 'easeInOut' }}
                className="shrink-0 overflow-hidden border-r border-surface-200 bg-white/60 flex flex-col"
              >
                <div className="border-b border-surface-200 px-4 py-3">
                  <div className="flex items-center justify-between gap-2">
                    <div className="flex items-center gap-2">
                      <span className="h-2 w-2 rounded-full bg-slate-400" />
                      <h2 className="text-sm font-bold text-surface-800">Backlog</h2>
                      <span className="rounded-full border border-slate-200 bg-slate-100 px-2 py-0.5 text-[10px] font-bold text-slate-600">
                        {backlogCards.length}
                      </span>
                    </div>
                    <button
                      onClick={() => setBacklogOpen(false)}
                      className="rounded-lg p-1.5 text-surface-400 hover:bg-surface-100 hover:text-surface-700 transition-colors cursor-pointer"
                      title="Collapse backlog"
                    >
                      <PanelLeftClose size={14} />
                    </button>
                  </div>
                  <p className="mt-1 text-[11px] leading-relaxed text-surface-400">
                    Ideas & videos not yet ready to edit.
                  </p>
                </div>

                <div
                  className={cn(
                    'flex-1 overflow-y-scroll p-3 space-y-2',
                    dragTarget === 'backlog' && 'ring-2 ring-inset ring-primary-200 bg-primary-50/30'
                  )}
                  onDragOver={(e) => { e.preventDefault(); setDragTarget('backlog') }}
                  onDragLeave={() => setDragTarget((cur) => cur === 'backlog' ? null : cur)}
                  onDrop={(e) => handleDrop(e, 'backlog')}
                >
                  {loading ? (
                    [1, 2, 3].map((n) => <SkeletonCard key={n} />)
                  ) : backlogCards.length === 0 ? (
                    <EmptyLane stage="backlog" />
                  ) : (
                    <AnimatePresence mode="popLayout">
                      {backlogCards.map((child) => (
                        <PlannerCard
                          key={child.id}
                          child={child}
                          parent={parentProjects.find((p) => p.id === child.parentId)}
                          today={today}
                          selected={selectedChildId === child.id}
                          celebrating={celebratingId === child.id}
                          onSelect={() => setSelectedChildId(child.id === selectedChildId ? null : child.id)}
                          onDragStart={() => { setDraggedChildId(child.id); draggedChildIdRef.current = child.id }}
                          onDragEnd={() => { setDraggedChildId(null); draggedChildIdRef.current = null; setDragTarget(null) }}
                        />
                      ))}
                    </AnimatePresence>
                  )}
                </div>
              </motion.aside>
            )}
          </AnimatePresence>

          {/* Backlog collapsed toggle */}
          {!backlogOpen && (
            <button
              onClick={() => setBacklogOpen(true)}
              className="shrink-0 flex flex-col items-center justify-center gap-2 border-r border-surface-200 bg-white/60 px-2 hover:bg-surface-50 transition-colors cursor-pointer"
              title="Expand backlog"
            >
              <PanelLeftOpen size={14} className="text-surface-400" />
              <span className="text-[10px] font-bold uppercase tracking-widest text-surface-400 [writing-mode:vertical-rl] rotate-180">
                Backlog ({backlogCards.length})
              </span>
            </button>
          )}

          {/* ── 3-column Board ── */}
          <div className="flex min-h-0 flex-1 flex-col">
            <div className="min-h-0 flex-1 overflow-x-auto px-4 py-4">
              <div className="grid h-full min-w-[660px] grid-cols-3 gap-3">
                {BOARD_STAGES.map((stage) => {
                  const meta  = STAGE_META[stage]
                  const cards = laneMap[stage]
                  const Icon  = meta.icon

                  return (
                    <div
                      key={stage}
                      className={cn(
                        'flex min-h-full flex-col rounded-2xl border transition-all',
                        meta.surface,
                        meta.border,
                        dragTarget === stage && 'ring-2 ring-primary-300'
                      )}
                      onDragOver={(e) => { e.preventDefault(); setDragTarget(stage) }}
                      onDragLeave={() => setDragTarget((cur) => cur === stage ? null : cur)}
                      onDrop={(e) => handleDrop(e, stage)}
                    >
                      {/* Column header */}
                      <div className="border-b border-white/60 px-4 py-3.5">
                        <div className="flex items-center justify-between gap-2">
                          <div className="flex items-center gap-2">
                            <span className={cn('h-2 w-2 rounded-full', meta.accent)} />
                            <span className="text-sm font-bold text-surface-800">{meta.title}</span>
                            <Icon size={13} className="text-surface-400" />
                          </div>
                          <span className={cn('rounded-full border px-2 py-0.5 text-[10px] font-bold', meta.badge)}>
                            {cards.length}
                          </span>
                        </div>
                      </div>

                      {/* Cards */}
                      <div className="flex-1 space-y-2 overflow-y-scroll p-3">
                        {loading ? (
                          [1, 2].map((n) => <SkeletonCard key={n} />)
                        ) : cards.length === 0 ? (
                          <div className={cn('text-sm', meta.badge.split(' ').find((c) => c.startsWith('text-')))}>
                            <EmptyLane stage={stage} />
                          </div>
                        ) : (
                          <AnimatePresence mode="popLayout">
                            {cards.map((child) => (
                              <PlannerCard
                                key={child.id}
                                child={child}
                                parent={parentProjects.find((p) => p.id === child.parentId)}
                                today={today}
                                selected={selectedChildId === child.id}
                                celebrating={celebratingId === child.id}
                                onSelect={() => setSelectedChildId(child.id === selectedChildId ? null : child.id)}
                                onDragStart={() => { setDraggedChildId(child.id); draggedChildIdRef.current = child.id }}
                                onDragEnd={() => { setDraggedChildId(null); draggedChildIdRef.current = null; setDragTarget(null) }}
                              />
                            ))}
                          </AnimatePresence>
                        )}
                      </div>
                    </div>
                  )
                })}
              </div>
            </div>

            {/* Zero filter result banner */}
            {childProjects.length > 0 && visibleChildren.length === 0 && !loading && (
              <div className="mx-4 mb-4 rounded-xl border border-dashed border-surface-200 bg-white/70 px-4 py-3 text-center text-sm text-surface-500">
                No videos match the current filters. <button onClick={() => { setSearch(''); setParentFilter('all'); setPriorityFilter('all'); setDeadlineFilter('all') }} className="underline hover:text-surface-800 cursor-pointer">Clear filters</button>
              </div>
            )}
          </div>
        </div>
      )}

      {/* ── Slide-in Inspector ── */}
      <AnimatePresence>
        {selectedChild && (
          <>
            {/* Backdrop */}
            <motion.div
              key="inspector-backdrop"
              initial={{ opacity: 0 }}
              animate={{ opacity: 1 }}
              exit={{ opacity: 0 }}
              transition={{ duration: 0.18 }}
              className="absolute inset-0 z-30 bg-black/20 backdrop-blur-[1px]"
              onClick={() => setSelectedChildId(null)}
            />

            {/* Panel */}
            <motion.aside
              key="inspector-panel"
              initial={{ x: '100%' }}
              animate={{ x: 0 }}
              exit={{ x: '100%' }}
              transition={{ duration: 0.22, ease: [0.32, 0.72, 0, 1] }}
              className="absolute right-0 top-0 bottom-0 z-40 flex w-[360px] flex-col border-l border-surface-200 bg-white shadow-xl"
            >
              {/* Inspector header */}
              <div className="border-b border-surface-200 px-5 py-4 shrink-0">
                <div className="flex items-start justify-between gap-3">
                  <div className="min-w-0">
                    <p className="text-[10px] font-bold uppercase tracking-[0.2em] text-surface-400">
                      {parentProjects.find((p) => p.id === selectedChild.parentId)?.name ?? 'Unknown channel'}
                    </p>
                    <h2 className="mt-1 text-base font-bold leading-snug text-surface-900">
                      {selectedChild.title || selectedChild.name}
                    </h2>
                    <div className="mt-2 flex flex-wrap gap-1.5">
                      <span className={cn('rounded-full border px-2 py-0.5 text-[10px] font-semibold', priorityTone(selectedChild.priority))}>
                        {selectedChild.priority}
                      </span>
                      <span className={cn(
                        'rounded-full border px-2 py-0.5 text-[10px] font-medium',
                        isOverdue(selectedChild, today) ? 'border-rose-200 bg-rose-50 text-rose-600'
                        : isDueToday(selectedChild, today) ? 'border-amber-200 bg-amber-50 text-amber-600'
                        : 'border-surface-200 bg-surface-50 text-surface-500'
                      )}>
                        {dueLabel(selectedChild, today)}
                      </span>
                    </div>
                  </div>
                  <button
                    onClick={() => setSelectedChildId(null)}
                    className="shrink-0 rounded-lg p-1.5 text-surface-400 hover:bg-surface-100 hover:text-surface-700 transition-colors cursor-pointer"
                  >
                    <X size={15} />
                  </button>
                </div>
              </div>

              {/* Inspector body */}
              <div className="min-h-0 flex-1 overflow-y-auto px-5 py-5 space-y-5">
                {/* Stage */}
                <div>
                  <label className="label mb-2 block">Stage</label>
                  <div className="grid grid-cols-2 gap-2">
                    {INSPECTOR_STAGES.map((stage) => (
                      <button
                        key={stage}
                        onClick={() => setDrawerStage(stage)}
                        className={cn(
                          'rounded-xl border px-3 py-2 text-left text-sm font-medium transition-colors cursor-pointer',
                          drawerStage === stage
                            ? 'border-primary-300 bg-primary-50 text-primary-700 font-semibold'
                            : 'border-surface-200 bg-white text-surface-600 hover:border-primary-200'
                        )}
                      >
                        {STAGE_META[stage].title}
                      </button>
                    ))}
                  </div>
                </div>

                {/* Priority */}
                <div>
                  <label className="label mb-2 block">Priority</label>
                  <div className="grid grid-cols-2 gap-2">
                    {PRIORITY_OPTIONS.map((o) => (
                      <button
                        key={o.value}
                        onClick={() => setDrawerPriority(o.value)}
                        className={cn(
                          'rounded-xl border px-3 py-2 text-left text-sm font-medium transition-colors cursor-pointer',
                          drawerPriority === o.value
                            ? 'border-primary-300 bg-primary-50 text-primary-700 font-semibold'
                            : `bg-white hover:border-primary-200 ${o.tone}`
                        )}
                      >
                        {o.label}
                      </button>
                    ))}
                  </div>
                </div>

                {/* Deadline */}
                <div>
                  <label className="label mb-2 block">Deadline</label>
                  <input
                    type="date"
                    className="input w-full"
                    value={drawerDeadline}
                    onChange={(e) => setDrawerDeadline(e.target.value)}
                  />
                </div>

                {/* Planning note */}
                <div>
                  <label className="label mb-2 block">Planning note</label>
                  <textarea
                    rows={6}
                    className="textarea w-full"
                    value={drawerNote}
                    onChange={(e) => setDrawerNote(e.target.value)}
                    placeholder="What's the status? What does the editor need to know? What's blocking?"
                  />
                </div>

                {/* Snapshot */}
                <div className="rounded-2xl border border-surface-200 bg-surface-50 p-4">
                  <p className="text-[10px] font-bold uppercase tracking-[0.16em] text-surface-400 mb-3">Snapshot</p>
                  <div className="space-y-2 text-xs text-surface-600">
                    {[
                      ['Status', selectedChild.status],
                      ['Created', selectedChild.createdAt],
                      ['Folder', selectedChild.folderPath || '—'],
                    ].map(([label, value]) => (
                      <div key={label} className="flex items-center justify-between gap-3">
                        <span className="opacity-70">{label}</span>
                        <span className="font-medium text-surface-800 truncate max-w-[180px] text-right">{value}</span>
                      </div>
                    ))}
                  </div>
                </div>
              </div>

              {/* Inspector footer */}
              <div className="shrink-0 border-t border-surface-200 px-5 py-4">
                <div className="flex items-center gap-2">
                  <button
                    className="btn-secondary flex-1"
                    onClick={() => openChildWorkspace(selectedChild)}
                  >
                    <ArrowRight size={13} />
                    Open Workspace
                  </button>
                  <button
                    className="btn-primary flex-1"
                    onClick={handleSaveInspector}
                    disabled={saving}
                  >
                    {saving ? <Loader2 size={13} className="animate-spin" /> : null}
                    {saving ? 'Saving…' : 'Save Plan'}
                  </button>
                </div>
              </div>
            </motion.aside>
          </>
        )}
      </AnimatePresence>
    </div>
  )
}
