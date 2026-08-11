import { useState, useEffect } from 'react'
import { pickMediaFile } from '@/lib/browserPickers'
import { useAppStore } from '@/store/app.store'
import { seoApi, roxyApi } from '@/lib/api'
import { loadRoxyConfig, normalizeRoxyHost, saveRoxyConfig, type RoxyConfig } from '@/lib/roxy'
import {
  Zap, Plus, Play, Loader, CheckCircle, XCircle,
  ChevronDown, ChevronUp, Settings, Film, Trash2,
} from 'lucide-react'
import { cn, unknownErrorMessage } from '@/lib/utils'

// ─── Types ────────────────────────────────────────────────────────────────────

type JobStatus = 'pending' | 'seo' | 'upload' | 'done' | 'error'

interface AutomateJob {
  id: string
  videoPath: string
  videoName: string
  parentId: string
  childId: string
  status: JobStatus
  errorMsg?: string
}

// ─── Add Video Dialog ─────────────────────────────────────────────────────────

function AddVideoDialog({
  videoPath,
  onConfirm,
  onCancel,
}: {
  videoPath: string
  onConfirm: (parentId: string, childId: string) => void
  onCancel: () => void
}) {
  const { parentProjects, childProjects } = useAppStore()
  const [parentId, setParentId] = useState(parentProjects[0]?.id ?? '')
  const kids = childProjects.filter(c => c.parentId === parentId)
  const [childId, setChildId] = useState(kids[0]?.id ?? '')

  return (
    <div className="fixed inset-0 bg-black/40 flex items-center justify-center z-50">
      <div className="bg-white rounded-2xl shadow-2xl w-[440px] p-6 space-y-4">
        <h2 className="text-sm font-semibold text-surface-900">Map video to project</h2>

        <div className="flex items-center gap-2 bg-surface-50 rounded-lg px-3 py-2">
          <Film size={13} className="text-surface-400 shrink-0" />
          <span className="text-xs font-mono text-surface-600 truncate">
            {videoPath.split('/').pop()}
          </span>
        </div>

        <div>
          <label className="label mb-1">Parent project</label>
          <select className="input text-sm w-full" value={parentId}
            onChange={e => {
              const nextParentId = e.target.value
              setParentId(nextParentId)
              setChildId(childProjects.find(child => child.parentId === nextParentId)?.id ?? '')
            }}>
            {parentProjects.length === 0 && <option value="">- No parent projects yet -</option>}
            {parentProjects.map(p => (
              <option key={p.id} value={p.id}>{p.name}</option>
            ))}
          </select>
        </div>

        <div>
          <label className="label mb-1">Child project</label>
          <select className="input text-sm w-full" value={childId}
            onChange={e => setChildId(e.target.value)}>
            {kids.length === 0 && <option value="">- No child projects yet -</option>}
            {kids.map(c => (
              <option key={c.id} value={c.id}>{c.name}</option>
            ))}
          </select>
        </div>

        <div className="flex justify-end gap-2 pt-1">
          <button className="btn-secondary" onClick={onCancel}>Cancel</button>
          <button
            className="btn-primary"
            disabled={!childId || !parentId}
            onClick={() => onConfirm(parentId, childId)}
          >
            Confirm
          </button>
        </div>
      </div>
    </div>
  )
}

// ─── Status Badge ─────────────────────────────────────────────────────────────

function JobBadge({ status, errorMsg }: { status: JobStatus; errorMsg?: string }) {
  const base = 'inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-xs font-medium'
  if (status === 'pending') return (
    <span className={cn(base, 'bg-surface-100 text-surface-500')}>Queued</span>
  )
  if (status === 'seo') return (
    <span className={cn(base, 'bg-blue-100 text-blue-700')}>
      <Loader size={10} className="animate-spin" /> SEO...
    </span>
  )
  if (status === 'upload') return (
    <span className={cn(base, 'bg-purple-100 text-purple-700')}>
      <Loader size={10} className="animate-spin" /> Upload...
    </span>
  )
  if (status === 'done') return (
    <span className={cn(base, 'bg-green-100 text-green-700')}>
      <CheckCircle size={10} /> Done
    </span>
  )
  if (status === 'error') return (
    <span className={cn(base, 'bg-red-100 text-red-600 cursor-help')} title={errorMsg}>
      <XCircle size={10} /> Error
    </span>
  )
  return null
}

// ─── Main Component ───────────────────────────────────────────────────────────

export default function AutomateV2() {
  const { parentProjects, childProjects } = useAppStore()

  const [jobs, setJobs]                   = useState<AutomateJob[]>([])
  const [pendingPath, setPendingPath]     = useState<string | null>(null)
  const [running, setRunning]             = useState(false)
  const [roxy, setRoxy]                   = useState<RoxyConfig>(loadRoxyConfig)
  const [showConfig, setShowConfig]       = useState(false)

  useEffect(() => {
    saveRoxyConfig(roxy)
  }, [roxy])

  const updateJob = (id: string, patch: Partial<AutomateJob>) =>
    setJobs(prev => prev.map(j => j.id === id ? { ...j, ...patch } : j))

  const handleAddVideo = async () => {
    try {
      const selected = await pickMediaFile('video/*,.mp4,.mov,.avi,.mkv,.webm')
      if (selected) setPendingPath(selected.reference)
    } catch { /* cancelled */ }
  }

  const confirmAdd = (parentId: string, childId: string) => {
    if (!pendingPath) return
    setJobs(prev => [...prev, {
      id: `j${Date.now()}`,
      videoPath: pendingPath,
      videoName: pendingPath.split('/').pop() ?? pendingPath,
      parentId,
      childId,
      status: 'pending',
    }])
    setPendingPath(null)
  }

  const runJob = async (job: AutomateJob) => {
    const child  = childProjects.find(c => c.id === job.childId)
    const parent = parentProjects.find(p => p.id === job.parentId)

    if (!child || !parent) {
      updateJob(job.id, { status: 'error', errorMsg: 'Project not found' })
      return
    }

    // ── Step 1: Raw SEO ──────────────────────────────────────────────────────
    updateJob(job.id, { status: 'seo', errorMsg: undefined })
    try {
      await seoApi.apply({
        video_path: job.videoPath,
        title: child.title,
        description: child.description,
        keywords_raw: parent.keywordsRaw,
        rename_to_title: false,
      })
    } catch (error: unknown) {
      updateJob(job.id, { status: 'error', errorMsg: `SEO: ${unknownErrorMessage(error, 'unknown')}` })
      return
    }

    // ── Step 2: Roxy Upload ──────────────────────────────────────────────────
    if (!roxy.workspaceId || !roxy.profileId || !roxy.apiToken) {
      updateJob(job.id, { status: 'error', errorMsg: 'Roxy is not fully configured (token / workspaceId / profileId)' })
      return
    }

    updateJob(job.id, { status: 'upload' })
    try {
      const res = await roxyApi.upload({
        api_host: normalizeRoxyHost(roxy.apiHost),
        api_token: roxy.apiToken,
        workspace_id: roxy.workspaceId,
        profile_id: roxy.profileId,
        video_path: job.videoPath,
        close_after: false,
      })
      if (res.ok) {
        updateJob(job.id, { status: 'done' })
      } else {
        updateJob(job.id, { status: 'error', errorMsg: res.message })
      }
    } catch (error: unknown) {
      updateJob(job.id, { status: 'error', errorMsg: `Upload: ${unknownErrorMessage(error, 'unknown')}` })
    }
  }

  const handleRunAll = async () => {
    const queue = jobs.filter(j => j.status === 'pending' || j.status === 'error')
    if (queue.length === 0) return
    setRunning(true)
    for (const job of queue) await runJob(job)
    setRunning(false)
  }

  const pendingCount = jobs.filter(j => j.status === 'pending').length
  const doneCount    = jobs.filter(j => j.status === 'done').length
  const errorCount   = jobs.filter(j => j.status === 'error').length
  const roxyReady    = !!roxy.apiToken && !!roxy.workspaceId && !!roxy.profileId

  return (
    <div className="flex flex-col h-full">

      {/* ── Roxy config bar ──────────────────────────────────────────────────── */}
      <div className="border-b border-surface-100">
        <button
          className="w-full flex items-center justify-between px-6 py-2.5 text-xs text-surface-500 hover:text-surface-700 hover:bg-surface-50 transition-colors"
          onClick={() => setShowConfig(p => !p)}
        >
          <span className="flex items-center gap-1.5">
            <Settings size={11} />
            Roxy Config
            {roxyReady
              ? <span className="text-green-600 font-medium ml-1">· ready</span>
              : <span className="text-amber-500 font-medium ml-1">· not configured</span>
            }
          </span>
          {showConfig ? <ChevronUp size={11} /> : <ChevronDown size={11} />}
        </button>

        {showConfig && (
          <div className="px-6 pb-4 pt-1 grid grid-cols-4 gap-3 bg-surface-50">
            <div className="col-span-2">
              <label className="label">API Host</label>
              <input className="input text-xs font-mono" value={roxy.apiHost}
                onChange={e => setRoxy(r => ({ ...r, apiHost: e.target.value }))}
                onBlur={e => setRoxy(r => ({ ...r, apiHost: normalizeRoxyHost(e.target.value) }))} />
            </div>
            <div className="col-span-2">
              <label className="label">API Token</label>
              <input className="input text-xs font-mono" type="password"
                placeholder="Roxy token" value={roxy.apiToken}
                onChange={e => setRoxy(r => ({ ...r, apiToken: e.target.value }))} />
            </div>
            <div>
              <label className="label">Workspace ID</label>
              <input className="input text-xs" type="number"
                value={roxy.workspaceId ?? ''}
                onChange={e => setRoxy(r => ({ ...r, workspaceId: parseInt(e.target.value) || null }))} />
            </div>
            <div className="col-span-3">
              <label className="label">Profile ID (dir_id)</label>
              <input className="input text-xs font-mono" placeholder="profile dir_id"
                value={roxy.profileId}
                onChange={e => setRoxy(r => ({ ...r, profileId: e.target.value }))} />
            </div>
          </div>
        )}
      </div>

      {/* ── Toolbar ──────────────────────────────────────────────────────────── */}
      <div className="flex items-center justify-between px-6 py-3 border-b border-surface-100 bg-white">
        <div className="flex items-center gap-3 text-xs text-surface-400">
          <span>{jobs.length} video{jobs.length !== 1 ? 's' : ''}</span>
          {doneCount > 0 && <span className="text-green-600 font-medium">{doneCount} done</span>}
          {errorCount > 0 && <span className="text-red-500 font-medium">{errorCount} errors</span>}
        </div>
        <div className="flex items-center gap-2">
          <button className="btn-secondary text-xs" onClick={handleAddVideo} disabled={running}>
            <Plus size={13} /> Add Video
          </button>
          <button
            className="btn-primary text-xs"
            disabled={running || pendingCount === 0}
            onClick={handleRunAll}
          >
            {running
              ? <><Loader size={13} className="animate-spin" /> Running...</>
              : <><Zap size={13} /> Automate ({pendingCount})</>
            }
          </button>
        </div>
      </div>

      {/* ── Table ────────────────────────────────────────────────────────────── */}
      <div className="flex-1 overflow-y-auto">
        {jobs.length === 0 ? (
          <div className="flex flex-col items-center justify-center h-48 text-surface-400">
            <Zap size={28} className="mb-3 opacity-20" />
            <p className="text-sm font-medium">Queue is empty</p>
            <p className="text-xs mt-1">Click "Add Video" to add a video to the queue</p>
          </div>
        ) : (
          <table className="w-full text-sm">
            <thead>
              <tr className="border-b border-surface-100 bg-surface-50">
                <th className="text-left px-6 py-2.5 text-xs font-semibold text-surface-500">Video</th>
                <th className="text-left px-4 py-2.5 text-xs font-semibold text-surface-500">Child Project</th>
                <th className="text-left px-4 py-2.5 text-xs font-semibold text-surface-500">Parent</th>
                <th className="text-left px-4 py-2.5 text-xs font-semibold text-surface-500">Status</th>
                <th className="px-4 py-2.5 w-16" />
              </tr>
            </thead>
            <tbody>
              {jobs.map(job => {
                const child  = childProjects.find(c => c.id === job.childId)
                const parent = parentProjects.find(p => p.id === job.parentId)
                const active = job.status === 'seo' || job.status === 'upload'
                return (
                  <tr key={job.id} className="border-b border-surface-50 hover:bg-surface-50 transition-colors">
                    <td className="px-6 py-3">
                      <div className="flex items-center gap-2">
                        <Film size={13} className="text-surface-300 shrink-0" />
                        <div className="min-w-0">
                          <p className="text-xs font-mono text-surface-700 truncate max-w-[200px]" title={job.videoPath}>
                            {job.videoName}
                          </p>
                          <p className="text-xs text-surface-400 truncate max-w-[200px]">
                            {job.videoPath.split('/').slice(0, -1).join('/').split('/').slice(-2).join('/')}
                          </p>
                        </div>
                      </div>
                    </td>
                    <td className="px-4 py-3 text-xs text-surface-700">{child?.name ?? '—'}</td>
                    <td className="px-4 py-3 text-xs text-surface-400">{parent?.name ?? '—'}</td>
                    <td className="px-4 py-3">
                      <JobBadge status={job.status} errorMsg={job.errorMsg} />
                    </td>
                    <td className="px-4 py-3">
                      <div className="flex items-center justify-end gap-1">
                        {!active && (job.status === 'pending' || job.status === 'error') && !running && (
                          <button
                            className="p-1.5 rounded text-primary-600 hover:bg-primary-50 transition-colors"
                            title="Run this job"
                            onClick={() => runJob(job)}
                          >
                            <Play size={12} />
                          </button>
                        )}
                        {!active && (
                          <button
                            className="p-1.5 rounded text-surface-300 hover:text-red-500 hover:bg-red-50 transition-colors"
                            title="Delete"
                            onClick={() => setJobs(p => p.filter(j => j.id !== job.id))}
                          >
                            <Trash2 size={12} />
                          </button>
                        )}
                      </div>
                    </td>
                  </tr>
                )
              })}
            </tbody>
          </table>
        )}
      </div>

      {/* ── Add Video Dialog ─────────────────────────────────────────────────── */}
      {pendingPath && (
        <AddVideoDialog
          videoPath={pendingPath}
          onConfirm={confirmAdd}
          onCancel={() => setPendingPath(null)}
        />
      )}
    </div>
  )
}
