import { useState, useRef } from 'react'
import { useAppStore } from '@/store/app.store'
import { usePanelContext } from '@/contexts/PanelContext'
import { Upload, Loader, CheckCircle, XCircle, RefreshCw, FolderOpen, Wifi } from 'lucide-react'
import { roxyApi, type RoxyWorkspaceInfo, type RoxyProfileInfo } from '@/lib/api'
import { cn } from '@/lib/utils'
import { taskStore } from '@/store/task.store'

type Step = 'config' | 'profile' | 'upload' | 'done'

export default function RoxyUpload() {
  const { childProjects } = useAppStore()
  const { selectedChildId } = usePanelContext()
  const child = childProjects.find(c => c.id === selectedChildId)

  // Config
  const [apiHost,    setApiHost]    = useState('http://127.0.0.1:1080')
  const [apiToken,   setApiToken]   = useState('')

  // Workspaces + Profiles
  const [workspaces,   setWorkspaces]   = useState<RoxyWorkspaceInfo[]>([])
  const [profiles,     setProfiles]     = useState<RoxyProfileInfo[]>([])
  const [workspaceId,  setWorkspaceId]  = useState<number | null>(null)
  const [profileId,    setProfileId]    = useState('')

  // Upload
  const [videoPath,    setVideoPath]    = useState('')
  const [closeAfter,   setCloseAfter]   = useState(false)

  // State
  const [step,         setStep]         = useState<Step>('config')
  const [loading,      setLoading]      = useState(false)
  const [error,        setError]        = useState<string | null>(null)
  const [successMsg,   setSuccessMsg]   = useState('')
  const fileInputRef = useRef<HTMLInputElement>(null)

  // ── Step 1: Connect & load workspaces ──────────────────────────────────────
  const handleConnect = async () => {
    if (!apiHost.trim() || !apiToken.trim()) return
    setLoading(true); setError(null)
    try {
      const res = await roxyApi.workspaces(apiHost.trim(), apiToken.trim())
      if (!res.ok || res.workspaces.length === 0) {
        setError(res.message || 'No workspaces found'); return
      }
      setWorkspaces(res.workspaces)
      setWorkspaceId(res.workspaces[0].workspace_id)
      setStep('profile')
    } catch (err: any) {
      setError(err?.message ?? 'Could not connect to the Roxy API')
    } finally {
      setLoading(false)
    }
  }

  // ── Step 2: Load profiles ──────────────────────────────────────────────────
  const handleLoadProfiles = async () => {
    if (!workspaceId) return
    setLoading(true); setError(null)
    try {
      const res = await roxyApi.profiles(apiHost.trim(), apiToken.trim(), workspaceId)
      if (!res.ok) { setError(res.message); return }
      setProfiles(res.profiles)
      if (res.profiles.length > 0) setProfileId(res.profiles[0].dir_id)
      setStep('upload')
    } catch (err: any) {
      setError(err?.message ?? 'Could not load profiles')
    } finally {
      setLoading(false)
    }
  }

  // ── Step 3: Upload ─────────────────────────────────────────────────────────
  const handleUpload = async () => {
    if (!videoPath.trim() || !profileId || !workspaceId) return
    setLoading(true); setError(null)
    const taskId = taskStore.add({ toolId: 'roxy-upload', toolLabel: 'Roxy Upload', label: 'Upload video YouTube' })
    try {
      const res = await roxyApi.upload({
        api_host: apiHost.trim(),
        api_token: apiToken.trim(),
        workspace_id: workspaceId,
        profile_id: profileId,
        video_path: videoPath.trim(),
        close_after: closeAfter,
      })
      if (res.ok) {
        setSuccessMsg(res.message)
        setStep('done')
        taskStore.complete(taskId, 'done', res.message)
      } else {
        setError(res.message)
        taskStore.complete(taskId, 'error', res.message)
      }
    } catch (err: any) {
      const msg = err?.message ?? 'Upload failed'
      setError(msg)
      taskStore.complete(taskId, 'error', msg)
    } finally {
      setLoading(false)
    }
  }

  const handleFilePick = (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0]
    if (!file) return
    const path = (file as any).path ?? ''
    setVideoPath(path || file.name)
  }

  const reset = () => {
    setStep('config'); setError(null); setSuccessMsg('')
    setWorkspaces([]); setProfiles([]); setWorkspaceId(null); setProfileId('')
  }

  // ── Step indicator ─────────────────────────────────────────────────────────
  const STEPS = [
    { key: 'config',  label: '1. Connect' },
    { key: 'profile', label: '2. Workspace' },
    { key: 'upload',  label: '3. Upload' },
    { key: 'done',    label: '4. Xong' },
  ]

  return (
    <div className="flex flex-col h-full">
      {/* Header */}
      <div className="flex items-center gap-3 px-6 py-4 border-b border-surface-200 bg-white">
        <div className="w-8 h-8 rounded-lg bg-red-100 flex items-center justify-center">
          <Upload size={16} className="text-red-600" />
        </div>
        <div>
          <h1 className="page-title">Roxy Upload</h1>
          <p className="page-sub">{child?.name ?? 'No child selected'} · Upload to YouTube through Roxy Browser</p>
        </div>
      </div>

      <div className="flex-1 overflow-y-auto p-6 space-y-5">
        {/* Step progress */}
        <div className="flex items-center gap-1">
          {STEPS.map((s, i) => (
            <div key={s.key} className="flex items-center gap-1">
              <div className={cn(
                'px-3 py-1 rounded-full text-xs font-medium transition-colors',
                step === s.key ? 'bg-primary-600 text-white' :
                STEPS.findIndex(x => x.key === step) > i ? 'bg-green-100 text-green-700' :
                'bg-surface-100 text-surface-400'
              )}>
                {s.label}
              </div>
              {i < STEPS.length - 1 && <div className="w-4 h-px bg-surface-200" />}
            </div>
          ))}
        </div>

        {/* Error banner */}
        {error && (
          <div className="flex items-start gap-2 rounded-lg px-4 py-3 bg-red-50 border border-red-200 text-sm text-red-700">
            <XCircle size={15} className="shrink-0 mt-0.5" />
            <p className="whitespace-pre-wrap">{error}</p>
          </div>
        )}

        {/* ── STEP 1: Config ── */}
        {(step === 'config' || step === 'profile' || step === 'upload') && (
          <div className="card p-4 space-y-3">
            <p className="text-sm font-semibold text-surface-700">Roxy API</p>
            <div className="grid grid-cols-2 gap-3">
              <div>
                <label className="label">API Host</label>
                <input className="input font-mono text-xs" value={apiHost}
                  onChange={e => setApiHost(e.target.value)}
                  disabled={step !== 'config'} />
              </div>
              <div>
                <label className="label">API Token</label>
                <input className="input font-mono text-xs" type="password"
                  placeholder="Enter the Roxy token"
                  value={apiToken} onChange={e => setApiToken(e.target.value)}
                  disabled={step !== 'config'} />
              </div>
            </div>
            {step === 'config' && (
              <div className="flex justify-end">
                <button className="btn-primary" onClick={handleConnect}
                  disabled={loading || !apiHost.trim() || !apiToken.trim()}>
                  {loading ? <Loader size={14} className="animate-spin" /> : <Wifi size={14} />}
                  {loading ? 'Connecting...' : 'Connect Roxy'}
                </button>
              </div>
            )}
          </div>
        )}

        {/* ── STEP 2: Workspace + Profile ── */}
        {(step === 'profile' || step === 'upload') && (
          <div className="card p-4 space-y-3">
            <p className="text-sm font-semibold text-surface-700">Choose a workspace and profile</p>

            <div>
              <label className="label">Workspace</label>
              <select className="input text-sm"
                value={workspaceId ?? ''}
                onChange={e => { setWorkspaceId(Number(e.target.value)); setProfiles([]); setProfileId(''); setStep('profile') }}
                disabled={step === 'upload' && profiles.length > 0}>
                {workspaces.map(w => (
                  <option key={w.workspace_id} value={w.workspace_id}>
                    {w.workspace_name || `Workspace #${w.workspace_id}`}
                  </option>
                ))}
              </select>
            </div>

            {step === 'profile' && (
              <div className="flex justify-end gap-2">
                <button className="btn-secondary" onClick={reset}>Back</button>
                <button className="btn-primary" onClick={handleLoadProfiles}
                  disabled={loading || !workspaceId}>
                  {loading ? <Loader size={14} className="animate-spin" /> : <RefreshCw size={14} />}
                  {loading ? 'Loading...' : 'Load Profiles'}
                </button>
              </div>
            )}

            {profiles.length > 0 && (
              <div>
                <label className="label">Profile ({profiles.length} profiles)</label>
                <select className="input text-sm" value={profileId}
                  onChange={e => setProfileId(e.target.value)}>
                  {profiles.map(p => (
                    <option key={p.dir_id} value={p.dir_id}>{p.display_name}</option>
                  ))}
                </select>
              </div>
            )}
          </div>
        )}

        {/* ── STEP 3: Upload ── */}
        {step === 'upload' && profiles.length > 0 && (
          <div className="card p-4 space-y-3">
            <p className="text-sm font-semibold text-surface-700">Video file</p>

            <div>
              <label className="label">Video path</label>
              <div className="flex gap-2">
                <input className="input font-mono text-xs flex-1"
                  placeholder="/path/to/video.mp4"
                  value={videoPath} onChange={e => setVideoPath(e.target.value)} />
                <button className="btn-secondary shrink-0"
                  onClick={() => fileInputRef.current?.click()}>
                  <FolderOpen size={14} /> Choose file
                </button>
                <input ref={fileInputRef} type="file"
                  accept=".mp4,.mov,.avi,.mkv,.webm" className="hidden"
                  onChange={handleFilePick} />
              </div>
            </div>

            <label className="flex items-center gap-2 text-sm text-surface-700 cursor-pointer select-none">
              <input type="checkbox" className="rounded border-surface-300"
                checked={closeAfter} onChange={e => setCloseAfter(e.target.checked)} />
              Close the Roxy profile after upload completes
            </label>

            <div className="flex justify-end gap-2">
              <button className="btn-secondary" onClick={() => { setStep('profile'); setProfiles([]); setProfileId('') }}>
                Change profile
              </button>
              <button className="btn-primary" onClick={handleUpload}
                disabled={loading || !videoPath.trim() || !profileId}>
                {loading ? <Loader size={14} className="animate-spin" /> : <Upload size={14} />}
                {loading ? 'Uploading...' : 'Start upload'}
              </button>
            </div>

            {loading && (
              <p className="text-xs text-surface-400 text-center">
                Opening the Roxy profile and navigating to YouTube Studio... (this may take 15-30s)
              </p>
            )}
          </div>
        )}

        {/* ── STEP 4: Done ── */}
        {step === 'done' && (
          <div className="card p-5 border-green-200 bg-green-50">
            <div className="flex items-start gap-3">
              <CheckCircle size={20} className="text-green-600 shrink-0 mt-0.5" />
              <div className="flex-1">
                <p className="text-sm font-semibold text-green-700 mb-1">Upload started!</p>
                <p className="text-sm text-green-600 whitespace-pre-wrap">{successMsg}</p>
              </div>
            </div>
            <div className="flex justify-end mt-4">
              <button className="btn-secondary" onClick={reset}>
                <RefreshCw size={14} /> Upload another video
              </button>
            </div>
          </div>
        )}
      </div>
    </div>
  )
}
