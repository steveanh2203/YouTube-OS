import { useState } from 'react'
import { useAppStore } from '@/store/app.store'
import { usePanelContext } from '@/contexts/PanelContext'
import { Tag, Play, Loader, CheckCircle, XCircle } from 'lucide-react'
import { seoApi, type SEOFileResult } from '@/lib/api'
import { cn } from '@/lib/utils'
import { taskStore } from '@/store/task.store'

export default function RawSEO() {
  const { childProjects, parentProjects } = useAppStore()
  const { selectedChildId } = usePanelContext()
  const child  = childProjects.find(c => c.id === selectedChildId)
  const parent = parentProjects.find(p => p.id === child?.parentId)

  const [folderPath,    setFolderPath]    = useState(child?.folderPath ?? '')
  const [title,         setTitle]         = useState(child?.title ?? '')
  const [description,   setDescription]   = useState(child?.description ?? '')
  const [keywordsRaw,   setKeywordsRaw]   = useState(parent?.keywordsRaw ?? '')
  const [renameToTitle, setRenameToTitle] = useState(false)
  const [loading,       setLoading]       = useState(false)
  const [result, setResult] = useState<{ ok: boolean; message: string; results: SEOFileResult[] } | null>(null)

  const handleApply = async () => {
    if (!folderPath.trim()) return
    setLoading(true)
    setResult(null)
    const taskId = taskStore.add({ toolId: 'raw-seo', toolLabel: 'Raw SEO', label: 'Apply SEO metadata' })
    try {
      const res = await seoApi.apply({
        folder_path: folderPath.trim(),
        title, description, keywords_raw: keywordsRaw, rename_to_title: renameToTitle,
      })
      setResult({ ok: res.ok, message: res.message, results: res.results })
      taskStore.complete(taskId, res.ok ? 'done' : 'error', res.message)
    } catch (err: any) {
      const msg = err?.message ?? 'API error'
      setResult({ ok: false, message: msg, results: [] })
      taskStore.complete(taskId, 'error', msg)
    } finally {
      setLoading(false)
    }
  }

  return (
    <div className="flex flex-col h-full">
      <div className="flex items-center gap-3 px-6 py-4 border-b border-surface-200 bg-white">
        <div className="w-8 h-8 rounded-lg bg-blue-100 flex items-center justify-center">
          <Tag size={16} className="text-blue-600" />
        </div>
        <div>
          <h1 className="page-title">Raw SEO</h1>
          <p className="page-sub">{child?.name ?? 'No child selected'} · Apply metadata to video files</p>
        </div>
      </div>

      <div className="flex-1 overflow-y-auto p-6 space-y-4">
        <div>
          <label className="label">Video folder</label>
          <input className="input font-mono text-xs" placeholder="/path/to/video/folder"
            value={folderPath} onChange={e => setFolderPath(e.target.value)} />
        </div>

        <div>
          <label className="label">Title</label>
          <input className="input" placeholder="YouTube video title"
            value={title} onChange={e => setTitle(e.target.value)} />
        </div>

        <div>
          <label className="label">Description</label>
          <textarea className="textarea" rows={4} placeholder="Video description"
            value={description} onChange={e => setDescription(e.target.value)} />
        </div>

        <div>
          <label className="label">Keywords (comma-separated or one per line)</label>
          <textarea className="textarea" rows={3} placeholder="gadget, review, unboxing"
            value={keywordsRaw} onChange={e => setKeywordsRaw(e.target.value)} />
        </div>

        <label className="flex items-center gap-2 text-sm text-surface-700 cursor-pointer select-none">
          <input type="checkbox" className="rounded border-surface-300"
            checked={renameToTitle} onChange={e => setRenameToTitle(e.target.checked)} />
          Rename files to match the title
        </label>

        <div className="flex justify-end pt-2">
          <button className="btn-primary" onClick={handleApply}
            disabled={loading || !folderPath.trim()}>
            {loading ? <Loader size={14} className="animate-spin" /> : <Play size={14} />}
            {loading ? 'Applying...' : 'Apply SEO'}
          </button>
        </div>

        {/* Results */}
        {result && (
          <div className={cn('card p-4', result.ok ? 'border-green-200 bg-green-50' : 'border-red-200 bg-red-50')}>
            <p className={cn('text-sm font-semibold mb-3', result.ok ? 'text-green-700' : 'text-red-700')}>
              {result.message}
            </p>
            {result.results.length > 0 && (
              <div className="space-y-1.5">
                {result.results.map((r, i) => (
                  <div key={i} className="flex items-start gap-2 text-xs bg-white rounded px-3 py-2 border border-surface-200">
                    {r.ok
                      ? <CheckCircle size={13} className="text-green-500 shrink-0 mt-0.5" />
                      : <XCircle size={13} className="text-red-500 shrink-0 mt-0.5" />}
                    <div className="min-w-0">
                      <p className="font-mono truncate text-surface-600">{r.file.split('/').pop()}</p>
                      <p className={cn('mt-0.5', r.ok ? 'text-green-600' : 'text-red-600')}>{r.message}</p>
                    </div>
                  </div>
                ))}
              </div>
            )}
          </div>
        )}
      </div>
    </div>
  )
}
