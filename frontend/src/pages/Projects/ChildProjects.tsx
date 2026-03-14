import { useState } from 'react'
import { motion, AnimatePresence } from 'framer-motion'
import { useAppStore, type ChildProject } from '@/store/app.store'
import { Plus, FileVideo, Trash2, Wrench, FolderOpen, CheckCircle, Clock, Loader, XCircle } from 'lucide-react'
import { cn } from '@/lib/utils'

const STATUS_CONFIG = {
  Draft:      { label: 'Draft',      cls: 'badge-neutral', icon: Clock },
  processing: { label: 'Processing', cls: 'badge-info',    icon: Loader },
  done:       { label: 'Done',       cls: 'badge-success', icon: CheckCircle },
  failed:     { label: 'Failed',     cls: 'badge-error',   icon: XCircle },
}

interface FormState {
  name: string; title: string; description: string; seedingCommentsRaw: string; folderPath: string
}
const EMPTY: FormState = { name: '', title: '', description: '', seedingCommentsRaw: '', folderPath: '' }

export default function ChildProjects() {
  const {
    selectedParentId, parentProjects, childProjects,
    addChild, deleteChild, selectChild, setProjectSubView,
  } = useAppStore()

  const parent = parentProjects.find(p => p.id === selectedParentId)
  const children = childProjects.filter(c => c.parentId === selectedParentId)

  const [showForm, setShowForm] = useState(false)
  const [form, setForm] = useState<FormState>(EMPTY)
  const [selected, setSelected] = useState<Set<string>>(new Set())

  const toggleSelect = (id: string) => {
    setSelected(prev => {
      const next = new Set(prev)
      next.has(id) ? next.delete(id) : next.add(id)
      return next
    })
  }

  const handleAdd = () => {
    if (!form.title.trim() || !selectedParentId) return
    addChild({ ...form, parentId: selectedParentId, status: 'Draft', aiAudioNotes: '', livestreamNotes: '' })
    setForm(EMPTY)
    setShowForm(false)
  }

  const handleOpenTools = (child: ChildProject) => {
    selectChild(child.id)
    setProjectSubView('ai-gen')
  }

  const bulkDelete = () => {
    selected.forEach(id => deleteChild(id))
    setSelected(new Set())
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
          {selected.size > 0 && (
            <button className="btn-danger" onClick={bulkDelete}>
              <Trash2 size={14} />
              Delete {selected.size} selected
            </button>
          )}
          <button className="btn-primary" onClick={() => setShowForm(true)}>
            <Plus size={14} />
            New Child
          </button>
        </div>
      </div>

      {/* Content */}
      <div className="flex-1 overflow-y-auto p-6">
        {/* Add Form */}
        <AnimatePresence>
          {showForm && (
            <motion.div
              initial={{ opacity: 0, y: -8 }} animate={{ opacity: 1, y: 0 }}
              exit={{ opacity: 0, y: -8 }} transition={{ duration: 0.18 }}
              className="card p-5 mb-5"
            >
              <h3 className="text-sm font-semibold text-surface-800 mb-4">New Child Project</h3>
              <div className="grid grid-cols-2 gap-3 mb-3">
                <div>
                  <label className="label">Name *</label>
                  <input className="input" placeholder="e.g. Video 01"
                    value={form.name} onChange={e => setForm(f => ({ ...f, name: e.target.value }))} />
                </div>
                <div>
                  <label className="label">Title *</label>
                  <input className="input" placeholder="YouTube video title"
                    value={form.title} onChange={e => setForm(f => ({ ...f, title: e.target.value }))} />
                </div>
                <div className="col-span-2">
                  <label className="label">Description</label>
                  <textarea className="textarea" rows={2} placeholder="Video description"
                    value={form.description} onChange={e => setForm(f => ({ ...f, description: e.target.value }))} />
                </div>
                <div>
                  <label className="label">Seed Comments</label>
                  <textarea className="textarea" rows={2} placeholder="One comment per line"
                    value={form.seedingCommentsRaw} onChange={e => setForm(f => ({ ...f, seedingCommentsRaw: e.target.value }))} />
                </div>
                <div>
                  <label className="label">Folder Path</label>
                  <input className="input" placeholder="/Projects/MyVideo"
                    value={form.folderPath} onChange={e => setForm(f => ({ ...f, folderPath: e.target.value }))} />
                </div>
              </div>
              <div className="flex gap-2 justify-end">
                <button className="btn-secondary" onClick={() => { setShowForm(false); setForm(EMPTY) }}>Cancel</button>
                <button className="btn-primary" onClick={handleAdd} disabled={!form.name.trim() || !form.title.trim()}>Create Child</button>
              </div>
            </motion.div>
          )}
        </AnimatePresence>

        {/* Empty state */}
        {children.length === 0 && (
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
                  <th className="px-4 py-2.5 text-left text-xs font-semibold text-surface-500">Title</th>
                  <th className="px-4 py-2.5 text-left text-xs font-semibold text-surface-500">Folder</th>
                  <th className="px-4 py-2.5 text-left text-xs font-semibold text-surface-500">Status</th>
                  <th className="px-4 py-2.5 text-left text-xs font-semibold text-surface-500">Created</th>
                  <th className="px-4 py-2.5" />
                </tr>
              </thead>
              <tbody>
                <AnimatePresence>
                  {children.map((c, i) => {
                    const { label, cls, icon: StatusIcon } = STATUS_CONFIG[c.status]
                    return (
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
                        <td className="px-4 py-2.5">
                          <p className="font-medium text-surface-900 truncate max-w-[180px]">{c.title}</p>
                          {c.description && <p className="text-xs text-surface-400 truncate max-w-[180px]">{c.description}</p>}
                        </td>
                        <td className="px-4 py-2.5">
                          <span className="flex items-center gap-1 text-xs text-surface-500">
                            <FolderOpen size={11} />
                            <span className="truncate max-w-[120px]">{c.folderPath || '—'}</span>
                          </span>
                        </td>
                        <td className="px-4 py-2.5">
                          <span className={cn('badge flex items-center gap-1', cls)}>
                            <StatusIcon size={10} className={c.status === 'processing' ? 'animate-spin' : ''} />
                            {label}
                          </span>
                        </td>
                        <td className="px-4 py-2.5 text-xs text-surface-400">{c.createdAt}</td>
                        <td className="px-4 py-2.5">
                          <div className="flex items-center gap-1 justify-end">
                            <button
                              className="btn-icon text-surface-400 hover:text-primary-600 hover:bg-primary-50"
                              title="Open Tools"
                              onClick={() => handleOpenTools(c)}
                            >
                              <Wrench size={13} />
                            </button>
                            <button
                              className="btn-icon text-surface-400 hover:text-red-500 hover:bg-red-50"
                              title="Delete"
                              onClick={() => deleteChild(c.id)}
                            >
                              <Trash2 size={13} />
                            </button>
                          </div>
                        </td>
                      </motion.tr>
                    )
                  })}
                </AnimatePresence>
              </tbody>
            </table>
          </div>
        )}
      </div>
    </div>
  )
}
