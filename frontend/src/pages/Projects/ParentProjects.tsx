import { useState } from 'react'
import { motion, AnimatePresence } from 'framer-motion'
import { useAppStore, type ParentProject } from '@/store/app.store'
import { Plus, FolderOpen, Trash2, ChevronRight, User, Building, Tag } from 'lucide-react'
import { cn } from '@/lib/utils'

interface FormState {
  name: string; author: string; publisher: string; copyright: string; keywordsRaw: string
}

const EMPTY: FormState = { name: '', author: '', publisher: '', copyright: '', keywordsRaw: '' }

export default function ParentProjects() {
  const { parentProjects, addParent, deleteParent, selectParent, setProjectSubView } = useAppStore()
  const [showForm, setShowForm] = useState(false)
  const [form, setForm] = useState<FormState>(EMPTY)
  const [deleting, setDeleting] = useState<string | null>(null)

  const handleAdd = () => {
    if (!form.name.trim()) return
    addParent(form)
    setForm(EMPTY)
    setShowForm(false)
  }

  const handleOpen = (p: ParentProject) => {
    selectParent(p.id)
    setProjectSubView('children')
  }

  return (
    <div className="flex flex-col h-full">
      {/* Header */}
      <div className="flex items-center justify-between px-6 py-4 border-b border-surface-200 bg-white">
        <div>
          <h1 className="page-title">Parent Projects</h1>
          <p className="page-sub mt-0.5">{parentProjects.length} project{parentProjects.length !== 1 ? 's' : ''}</p>
        </div>
        <button className="btn-primary" onClick={() => setShowForm(true)}>
          <Plus size={14} />
          New Project
        </button>
      </div>

      {/* Content */}
      <div className="flex-1 overflow-y-auto p-6">
        {/* Add Form */}
        <AnimatePresence>
          {showForm && (
            <motion.div
              initial={{ opacity: 0, y: -8 }}
              animate={{ opacity: 1, y: 0 }}
              exit={{ opacity: 0, y: -8 }}
              transition={{ duration: 0.18 }}
              className="card p-5 mb-5"
            >
              <h3 className="text-sm font-semibold text-surface-800 mb-4">New Parent Project</h3>
              <div className="grid grid-cols-2 gap-3 mb-3">
                <div>
                  <label className="label">Project Name *</label>
                  <input className="input" placeholder="e.g. Review Gadget 2025"
                    value={form.name} onChange={e => setForm(f => ({ ...f, name: e.target.value }))} />
                </div>
                <div>
                  <label className="label">Author</label>
                  <input className="input" placeholder="Your name"
                    value={form.author} onChange={e => setForm(f => ({ ...f, author: e.target.value }))} />
                </div>
                <div>
                  <label className="label">Publisher</label>
                  <input className="input" placeholder="Channel / Publisher name"
                    value={form.publisher} onChange={e => setForm(f => ({ ...f, publisher: e.target.value }))} />
                </div>
                <div>
                  <label className="label">Copyright</label>
                  <input className="input" placeholder="e.g. 2025 Steve"
                    value={form.copyright} onChange={e => setForm(f => ({ ...f, copyright: e.target.value }))} />
                </div>
                <div className="col-span-2">
                  <label className="label">Keywords</label>
                  <input className="input" placeholder="tag1, tag2, tag3"
                    value={form.keywordsRaw} onChange={e => setForm(f => ({ ...f, keywordsRaw: e.target.value }))} />
                </div>
              </div>
              <div className="flex gap-2 justify-end">
                <button className="btn-secondary" onClick={() => { setShowForm(false); setForm(EMPTY) }}>Cancel</button>
                <button className="btn-primary" onClick={handleAdd} disabled={!form.name.trim()}>Create Project</button>
              </div>
            </motion.div>
          )}
        </AnimatePresence>

        {/* Empty state */}
        {parentProjects.length === 0 && (
          <div className="flex flex-col items-center justify-center h-48 text-surface-400">
            <FolderOpen size={36} className="mb-3 opacity-40" />
            <p className="text-sm">No projects yet. Create your first one.</p>
          </div>
        )}

        {/* Project grid */}
        <div className="grid grid-cols-1 gap-3">
          <AnimatePresence>
            {parentProjects.map((p, i) => (
              <motion.div
                key={p.id}
                initial={{ opacity: 0, y: 8 }}
                animate={{ opacity: 1, y: 0 }}
                exit={{ opacity: 0, scale: 0.97 }}
                transition={{ duration: 0.18, delay: i * 0.04 }}
                className={cn(
                  'card p-4 flex items-center gap-4 cursor-pointer',
                  'hover:shadow-card-hover hover:border-primary-200 transition-all duration-150',
                  deleting === p.id && 'opacity-50 pointer-events-none',
                )}
                onClick={() => handleOpen(p)}
              >
                {/* Icon */}
                <div className="w-10 h-10 rounded-lg bg-primary-50 flex items-center justify-center shrink-0">
                  <FolderOpen size={18} className="text-primary-500" />
                </div>

                {/* Info */}
                <div className="flex-1 min-w-0">
                  <div className="flex items-center gap-2">
                    <p className="text-sm font-semibold text-surface-900 truncate">{p.name}</p>
                    <span className="badge-neutral ml-1">{p.childCount} children</span>
                  </div>
                  <div className="flex items-center gap-3 mt-1">
                    {p.author && (
                      <span className="flex items-center gap-1 text-xs text-surface-500">
                        <User size={11} /> {p.author}
                      </span>
                    )}
                    {p.publisher && (
                      <span className="flex items-center gap-1 text-xs text-surface-500">
                        <Building size={11} /> {p.publisher}
                      </span>
                    )}
                    {p.keywordsRaw && (
                      <span className="flex items-center gap-1 text-xs text-surface-400">
                        <Tag size={11} /> {p.keywordsRaw}
                      </span>
                    )}
                  </div>
                </div>

                {/* Actions */}
                <div className="flex items-center gap-1 ml-2" onClick={e => e.stopPropagation()}>
                  <button
                    className="btn-icon text-surface-400 hover:text-red-500 hover:bg-red-50"
                    title="Delete"
                    onClick={() => {
                      setDeleting(p.id)
                      setTimeout(() => { deleteParent(p.id); setDeleting(null) }, 300)
                    }}
                  >
                    <Trash2 size={14} />
                  </button>
                  <ChevronRight size={16} className="text-surface-300" />
                </div>
              </motion.div>
            ))}
          </AnimatePresence>
        </div>
      </div>
    </div>
  )
}
