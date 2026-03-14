import { useState } from 'react'
import { useAppStore } from '@/store/app.store'
import { Cpu, Save, Sparkles } from 'lucide-react'

export default function AIGen() {
  const { childProjects, selectedChildId } = useAppStore()
  const child = childProjects.find(c => c.id === selectedChildId)

  const [title, setTitle] = useState(child?.title ?? '')
  const [description, setDescription] = useState(child?.description ?? '')
  const [seedComments, setSeedComments] = useState(child?.seedingCommentsRaw ?? '')
  const [saved, setSaved] = useState(false)

  const handleSave = () => {
    setSaved(true)
    setTimeout(() => setSaved(false), 2000)
  }

  if (!child) return (
    <div className="flex items-center justify-center h-full text-surface-400 text-sm">
      Select a child project first.
    </div>
  )

  return (
    <div className="flex flex-col h-full">
      {/* Header */}
      <div className="flex items-center justify-between px-6 py-4 border-b border-surface-200 bg-white">
        <div className="flex items-center gap-3">
          <div className="w-8 h-8 rounded-lg bg-violet-50 flex items-center justify-center">
            <Cpu size={16} className="text-violet-500" />
          </div>
          <div>
            <h1 className="page-title">AI Gen</h1>
            <p className="page-sub mt-0.5">{child.title}</p>
          </div>
        </div>
        <button className="btn-primary" onClick={handleSave}>
          <Save size={14} />
          {saved ? 'Saved!' : 'Save'}
        </button>
      </div>

      {/* Content */}
      <div className="flex-1 overflow-y-auto p-6">
        <div className="max-w-2xl space-y-5">
          {/* Title */}
          <div className="card p-5">
            <div className="flex items-center gap-2 mb-3">
              <Sparkles size={14} className="text-primary-500" />
              <h2 className="text-sm font-semibold text-surface-800">Title</h2>
            </div>
            <label className="label">Video Title</label>
            <input className="input" value={title} onChange={e => setTitle(e.target.value)}
              placeholder="Catchy title for YouTube" />
            <p className="text-xs text-surface-400 mt-1.5">
              {title.length}/100 characters · AI will optimize for CTR
            </p>
          </div>

          {/* Description */}
          <div className="card p-5">
            <div className="flex items-center gap-2 mb-3">
              <Sparkles size={14} className="text-primary-500" />
              <h2 className="text-sm font-semibold text-surface-800">Description</h2>
            </div>
            <label className="label">Video Description</label>
            <textarea className="textarea" rows={6} value={description}
              onChange={e => setDescription(e.target.value)}
              placeholder="Full description with SEO keywords, chapters, links..." />
            <p className="text-xs text-surface-400 mt-1.5">{description.length}/5000 characters</p>
          </div>

          {/* Seed Comments */}
          <div className="card p-5">
            <div className="flex items-center gap-2 mb-3">
              <Sparkles size={14} className="text-primary-500" />
              <h2 className="text-sm font-semibold text-surface-800">Seed Comments</h2>
            </div>
            <label className="label">Comment seeding (separate by |)</label>
            <textarea className="textarea" rows={4} value={seedComments}
              onChange={e => setSeedComments(e.target.value)}
              placeholder="Great video!|So helpful, thanks!|Keep it up!" />
            <p className="text-xs text-surface-400 mt-1.5">
              {seedComments.split('\n').filter(Boolean).length} comments · Will be posted automatically after upload
            </p>
          </div>
        </div>
      </div>
    </div>
  )
}
