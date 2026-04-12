import { useState, useEffect, useCallback, useRef } from 'react'
import { motion, AnimatePresence } from 'framer-motion'
import { useAppStore, type ChildProject, type ChildStatus, type PlannerPriority, type PlannerStage } from '@/store/app.store'
import { usePanelContext } from '@/contexts/PanelContext'
import { Plus, FileVideo, Trash2, Files, ChevronDown, Loader, Wifi, MessageSquareText } from 'lucide-react'
import { cn } from '@/lib/utils'
import DocumentsTab from './DocumentsTab'
import CommunityTab from './CommunityTab'

const API = 'http://127.0.0.1:8765'

// ---------------------------------------------------------------------------
// Status config — colors & labels
// ---------------------------------------------------------------------------

const STATUS_OPTIONS: { value: ChildStatus; label: string; dotCls: string; bgCls: string }[] = [
  { value: 'draft',         label: 'Draft',         dotCls: 'bg-gray-400',   bgCls: 'bg-gray-100 text-gray-700' },
  { value: 'resource_prep', label: 'Resource Prep', dotCls: 'bg-amber-400',  bgCls: 'bg-amber-50 text-amber-700' },
  { value: 'editing',       label: 'Editing',       dotCls: 'bg-blue-400',   bgCls: 'bg-blue-50 text-blue-700' },
  { value: 'published',     label: 'Published',     dotCls: 'bg-green-500',  bgCls: 'bg-green-50 text-green-700' },
]

function statusConfig(s: ChildStatus) {
  return STATUS_OPTIONS.find(o => o.value === s) ?? STATUS_OPTIONS[0]
}

// ---------------------------------------------------------------------------
// Helper: map API response to frontend ChildProject
// ---------------------------------------------------------------------------

function mapChild(raw: Record<string, unknown>, parentId: string): ChildProject {
  return {
    id: String(raw.id),
    parentId,
    name: (raw.display_name as string) ?? `Video ${raw.video_number}`,
    status: (raw.status as ChildStatus) ?? 'draft',
    title: (raw.title as string) ?? '',
    description: (raw.description as string) ?? '',
    seedingComments: (raw.seeding_comments as string) ?? '',
    folderPath: (raw.base_folder_path as string) ?? '',
    planningStage: (raw.planning_stage as PlannerStage) ?? ((raw.status as ChildStatus) === 'published' ? 'published' : (raw.status as ChildStatus) === 'editing' ? 'editing' : (raw.status as ChildStatus) === 'resource_prep' ? 'ready' : 'backlog'),
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

// ---------------------------------------------------------------------------
// Status Dropdown component
// ---------------------------------------------------------------------------

function StatusDropdown({ child, onUpdate }: { child: ChildProject; onUpdate: (status: ChildStatus) => void }) {
  const [open, setOpen] = useState(false)
  const [pos, setPos] = useState({ top: 0, left: 0 })
  const btnRef = useRef<HTMLButtonElement>(null)
  const ref = useRef<HTMLDivElement>(null)
  const cfg = statusConfig(child.status)

  useEffect(() => {
    const handler = (e: MouseEvent) => {
      if (
        ref.current && !ref.current.contains(e.target as Node) &&
        btnRef.current && !btnRef.current.contains(e.target as Node)
      ) setOpen(false)
    }
    document.addEventListener('mousedown', handler)
    return () => document.removeEventListener('mousedown', handler)
  }, [])

  const handleOpen = (e: React.MouseEvent) => {
    e.stopPropagation()
    if (!open && btnRef.current) {
      const rect = btnRef.current.getBoundingClientRect()
      setPos({ top: rect.bottom + 4, left: rect.left })
    }
    setOpen(o => !o)
  }

  return (
    <div className="relative inline-block">
      <button
        ref={btnRef}
        className={cn('flex items-center gap-1.5 px-2 py-1 rounded-md text-xs font-medium transition-colors', cfg.bgCls)}
        onClick={handleOpen}
      >
        <span className={cn('w-2 h-2 rounded-full', cfg.dotCls)} />
        {cfg.label}
        <ChevronDown size={10} className={cn('transition-transform', open && 'rotate-180')} />
      </button>

      <AnimatePresence>
        {open && (
          <motion.div
            ref={ref}
            initial={{ opacity: 0, y: -4 }} animate={{ opacity: 1, y: 0 }}
            exit={{ opacity: 0, y: -4 }} transition={{ duration: 0.1 }}
            style={{ position: 'fixed', top: pos.top, left: pos.left, zIndex: 9999 }}
            className="bg-white rounded-lg shadow-lg border border-surface-200 py-1 min-w-[148px]"
          >
            {STATUS_OPTIONS.map(opt => (
              <button
                key={opt.value}
                className={cn(
                  'w-full flex items-center gap-2 px-3 py-1.5 text-xs hover:bg-surface-50 transition-colors',
                  child.status === opt.value && 'font-semibold',
                )}
                onClick={(e) => {
                  e.stopPropagation()
                  onUpdate(opt.value)
                  setOpen(false)
                }}
              >
                <span className={cn('w-2 h-2 rounded-full shrink-0', opt.dotCls)} />
                {opt.label}
              </button>
            ))}
          </motion.div>
        )}
      </AnimatePresence>
    </div>
  )
}

// ---------------------------------------------------------------------------
// Main component
// ---------------------------------------------------------------------------

export default function ChildProjects() {
  const { setChildren, updateChild: storeUpdateChild } = useAppStore()
  const parentProjects = useAppStore(s => s.parentProjects)
  const childProjects  = useAppStore(s => s.childProjects)
  const { selectedParentId, selectChild, setProjectSubView } = usePanelContext()

  const parent = parentProjects.find(p => p.id === selectedParentId)
  const children = childProjects.filter(c => c.parentId === selectedParentId)

  const [showForm, setShowForm] = useState(false)
  const [videoNumber, setVideoNumber] = useState('')
  const [creating, setCreating] = useState(false)
  const [selected, setSelected] = useState<Set<string>>(new Set())
  const [activeTab, setActiveTab] = useState<'children' | 'documents' | 'community'>('children')
  const [loading, setLoading] = useState(false)

  // Delete confirm
  const [confirmDelete, setConfirmDelete] = useState<
    { type: 'single'; child: ChildProject } | { type: 'bulk'; ids: string[] } | null
  >(null)

  // ---------------------------------------------------------------------------
  // Fetch children from API
  // ---------------------------------------------------------------------------

  const fetchChildren = useCallback(async () => {
    if (!selectedParentId) return
    setLoading(true)
    try {
      const res = await fetch(`${API}/api/child-projects/?parent_id=${selectedParentId}`)
      if (res.ok) {
        const data: Record<string, unknown>[] = await res.json()
        const mapped = data.map(d => mapChild(d, selectedParentId))
        // Replace children for this parent in store
        const otherChildren = childProjects.filter(c => c.parentId !== selectedParentId)
        setChildren([...otherChildren, ...mapped])
      }
    } finally {
      setLoading(false)
    }
  }, [childProjects, selectedParentId, setChildren])

  useEffect(() => { fetchChildren() }, [fetchChildren])

  // ---------------------------------------------------------------------------
  // Create
  // ---------------------------------------------------------------------------

  const handleCreate = async () => {
    const num = parseInt(videoNumber)
    if (isNaN(num) || !selectedParentId) return
    setCreating(true)
    try {
      const res = await fetch(`${API}/api/child-projects/?parent_id=${selectedParentId}`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ video_number: num }),
      })
      if (res.ok) {
        await fetchChildren()
        setVideoNumber('')
        setShowForm(false)
      }
    } finally {
      setCreating(false)
    }
  }

  // ---------------------------------------------------------------------------
  // Delete
  // ---------------------------------------------------------------------------

  const handleDelete = async (childId: string) => {
    await fetch(`${API}/api/child-projects/${childId}`, { method: 'DELETE' })
    await fetchChildren()
    setConfirmDelete(null)
  }

  const handleBulkDelete = async (ids: string[]) => {
    await Promise.all(ids.map(id => fetch(`${API}/api/child-projects/${id}`, { method: 'DELETE' })))
    setSelected(new Set())
    await fetchChildren()
    setConfirmDelete(null)
  }

  // ---------------------------------------------------------------------------
  // Update status
  // ---------------------------------------------------------------------------

  const handleStatusChange = async (child: ChildProject, newStatus: ChildStatus) => {
    // Optimistic update
    storeUpdateChild(child.id, { status: newStatus })
    await fetch(`${API}/api/child-projects/${child.id}`, {
      method: 'PATCH',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ status: newStatus }),
    })
  }

  // ---------------------------------------------------------------------------
  // Selection
  // ---------------------------------------------------------------------------

  const toggleSelect = (id: string) => {
    setSelected(prev => {
      const next = new Set(prev)
      if (next.has(id)) next.delete(id)
      else next.add(id)
      return next
    })
  }

  const handleOpenTools = (child: ChildProject) => {
    selectChild(child.id)
    setProjectSubView('resource-prep')
  }

  return (
    <div className="flex flex-col h-full">
      {/* Header */}
      <div className="flex items-center justify-between px-6 py-4 border-b border-surface-200 bg-white">
        <div>
          <h1 className="page-title">{parent?.name ?? 'Child Projects'}</h1>
          <p className="page-sub mt-0.5">{children.length} child project{children.length !== 1 ? 's' : ''}</p>
        </div>
        <div className="flex items-center gap-2">
          {activeTab === 'children' && selected.size > 0 && (
            <button
              className="btn-danger"
              onClick={() => setConfirmDelete({ type: 'bulk', ids: Array.from(selected) })}
            >
              <Trash2 size={14} />
              Delete {selected.size} selected
            </button>
          )}
          {activeTab === 'children' && (
            <button className="btn-primary" onClick={() => setShowForm(true)}>
              <Plus size={14} />
              New Child
            </button>
          )}
        </div>
      </div>

      {/* Tab Bar */}
      <div className="flex items-center gap-1 px-6 pt-3 pb-0 bg-white border-b border-surface-200">
        <button
          className={cn(
            'px-4 py-2 text-xs font-semibold rounded-t-md border-b-2 transition-colors duration-150',
            activeTab === 'children'
              ? 'border-primary-600 text-primary-700 bg-primary-50/60'
              : 'border-transparent text-surface-500 hover:text-surface-700 hover:bg-surface-50',
          )}
          onClick={() => setActiveTab('children')}
        >
          <span className="flex items-center gap-1.5">
            <FileVideo size={13} />
            Children
          </span>
        </button>
        <button
          className={cn(
            'px-4 py-2 text-xs font-semibold rounded-t-md border-b-2 transition-colors duration-150',
            activeTab === 'documents'
              ? 'border-primary-600 text-primary-700 bg-primary-50/60'
              : 'border-transparent text-surface-500 hover:text-surface-700 hover:bg-surface-50',
          )}
          onClick={() => setActiveTab('documents')}
        >
          <span className="flex items-center gap-1.5">
            <Files size={13} />
            Documents
          </span>
        </button>
        <button
          className={cn(
            'px-4 py-2 text-xs font-semibold rounded-t-md border-b-2 transition-colors duration-150',
            activeTab === 'community'
              ? 'border-primary-600 text-primary-700 bg-primary-50/60'
              : 'border-transparent text-surface-500 hover:text-surface-700 hover:bg-surface-50',
          )}
          onClick={() => setActiveTab('community')}
        >
          <span className="flex items-center gap-1.5">
            <MessageSquareText size={13} />
            Community
          </span>
        </button>
      </div>

      {/* Tab Content */}
      {activeTab === 'documents' ? (
        <DocumentsTab parentId={selectedParentId!} />
      ) : activeTab === 'community' ? (
        <CommunityTab parentId={selectedParentId!} children={children} />
      ) : (
        <div className="flex-1 overflow-y-auto p-6">
          {/* Add Form — simplified: only video number */}
          <AnimatePresence>
            {showForm && (
              <motion.div
                initial={{ opacity: 0, y: -8 }} animate={{ opacity: 1, y: 0 }}
                exit={{ opacity: 0, y: -8 }} transition={{ duration: 0.18 }}
                className="card p-5 mb-5"
              >
                <h3 className="text-sm font-semibold text-surface-800 mb-4">New Child Project</h3>
                <div className="flex items-end gap-3">
                  <div className="flex-1 max-w-[200px]">
                    <label className="label">Video Number *</label>
                    <input
                      className="input"
                      type="number"
                      min="1"
                      placeholder="e.g. 1"
                      autoFocus
                      value={videoNumber}
                      onChange={e => setVideoNumber(e.target.value)}
                      onKeyDown={e => { if (e.key === 'Enter') handleCreate(); if (e.key === 'Escape') setShowForm(false) }}
                    />
                  </div>
                  <p className="text-xs text-surface-400 pb-2">
                    Will be displayed as <span className="font-semibold text-surface-600">Video {videoNumber || '...'}</span>
                  </p>
                  {parent?.roxyProfileName && (
                    <p className="pb-2 text-xs text-surface-500">
                      Roxy profile mặc định: <span className="font-semibold text-surface-700">{parent.roxyProfileName}</span>
                    </p>
                  )}
                  <div className="ml-auto flex gap-2">
                    <button className="btn-secondary" onClick={() => { setShowForm(false); setVideoNumber('') }}>
                      Cancel
                    </button>
                    <button
                      className="btn-primary"
                      onClick={handleCreate}
                      disabled={creating || !videoNumber.trim() || isNaN(parseInt(videoNumber))}
                    >
                      {creating ? <Loader size={14} className="animate-spin" /> : 'Create Child'}
                    </button>
                  </div>
                </div>
              </motion.div>
            )}
          </AnimatePresence>

          {/* Loading */}
          {loading && children.length === 0 && (
            <div className="flex items-center justify-center py-12 text-surface-400">
              <Loader size={20} className="animate-spin" />
            </div>
          )}

          {/* Empty state */}
          {children.length === 0 && !showForm && !loading && (
            <div className="flex flex-col items-center justify-center h-48 text-surface-400">
              <FileVideo size={36} className="mb-3 opacity-40" />
              <p className="text-sm">No child projects. Add your first video project.</p>
            </div>
          )}

          {/* Table */}
          {children.length > 0 && (
            <div className="card overflow-hidden">
              <table className="w-full text-sm">
                <thead>
                  <tr className="border-b border-surface-200 bg-surface-50">
                    <th className="w-8 px-4 py-2.5">
                      <input type="checkbox" className="rounded"
                        checked={selected.size === children.length && children.length > 0}
                        onChange={e => setSelected(e.target.checked ? new Set(children.map(c => c.id)) : new Set())} />
                    </th>
                    <th className="px-4 py-2.5 text-left text-xs font-semibold text-surface-500">Name</th>
                    <th className="px-4 py-2.5 text-left text-xs font-semibold text-surface-500 w-40">Status</th>
                    <th className="px-4 py-2.5 text-left text-xs font-semibold text-surface-500 w-28">Created</th>
                    <th className="px-4 py-2.5 w-12" />
                  </tr>
                </thead>
                <tbody>
                  <AnimatePresence>
                    {children.map((c, i) => (
                      <motion.tr
                        key={c.id}
                        initial={{ opacity: 0 }}
                        animate={{ opacity: 1 }}
                        exit={{ opacity: 0 }}
                        transition={{ delay: i * 0.03 }}
                        className={cn(
                          'border-b border-surface-100 hover:bg-surface-50 transition-colors duration-100',
                          selected.has(c.id) && 'bg-primary-50/50',
                        )}
                      >
                        <td className="px-4 py-2.5">
                          <input type="checkbox" className="rounded"
                            checked={selected.has(c.id)} onChange={() => toggleSelect(c.id)} />
                        </td>
                        <td
                          className="px-4 py-2.5 cursor-pointer"
                          onClick={() => handleOpenTools(c)}
                        >
                          <p className="font-medium text-surface-900">{c.name}</p>
                          {c.title && <p className="text-xs text-surface-400 truncate max-w-[240px]">{c.title}</p>}
                          {c.roxyProfileName && (
                            <p className="mt-1 flex items-center gap-1 text-xs text-surface-500">
                              <Wifi size={11} /> {c.roxyProfileName}
                            </p>
                          )}
                        </td>
                        <td className="px-4 py-2.5">
                          <StatusDropdown child={c} onUpdate={(s) => handleStatusChange(c, s)} />
                        </td>
                        <td className="px-4 py-2.5 text-xs text-surface-400">{c.createdAt}</td>
                        <td className="px-4 py-2.5">
                          <button
                            className="btn-icon text-surface-400 hover:text-red-500 hover:bg-red-50"
                            title="Delete"
                            onClick={() => setConfirmDelete({ type: 'single', child: c })}
                          >
                            <Trash2 size={13} />
                          </button>
                        </td>
                      </motion.tr>
                    ))}
                  </AnimatePresence>
                </tbody>
              </table>
            </div>
          )}
        </div>
      )}

      {/* Delete confirm modal */}
      <AnimatePresence>
        {confirmDelete && (
          <motion.div
            initial={{ opacity: 0 }} animate={{ opacity: 1 }} exit={{ opacity: 0 }}
            className="fixed inset-0 z-50 flex items-center justify-center bg-black/30"
          >
            <motion.div
              initial={{ scale: 0.95, opacity: 0 }} animate={{ scale: 1, opacity: 1 }}
              exit={{ scale: 0.95, opacity: 0 }} transition={{ duration: 0.15 }}
              className="bg-white rounded-xl shadow-xl p-6 w-[380px] mx-4"
            >
              <h3 className="text-sm font-semibold text-surface-900 mb-2">
                {confirmDelete.type === 'bulk'
                  ? `Delete ${confirmDelete.ids.length} project${confirmDelete.ids.length !== 1 ? 's' : ''}?`
                  : `Delete "${confirmDelete.child.name}"?`}
              </h3>
              <p className="text-xs text-surface-500 mb-5">
                {confirmDelete.type === 'bulk'
                  ? `${confirmDelete.ids.length} selected project${confirmDelete.ids.length !== 1 ? 's' : ''} will be permanently deleted.`
                  : 'This child project and all its data will be permanently deleted.'}
              </p>
              <div className="flex gap-2 justify-end">
                <button className="btn-secondary text-xs" onClick={() => setConfirmDelete(null)}>
                  Cancel
                </button>
                <button
                  className="btn-danger text-xs"
                  onClick={() =>
                    confirmDelete.type === 'bulk'
                      ? handleBulkDelete(confirmDelete.ids)
                      : handleDelete(confirmDelete.child.id)
                  }
                >
                  Delete
                </button>
              </div>
            </motion.div>
          </motion.div>
        )}
      </AnimatePresence>
    </div>
  )
}
