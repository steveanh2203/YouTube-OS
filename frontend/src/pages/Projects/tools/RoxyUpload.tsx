import { useEffect, useState } from 'react'
import { motion, AnimatePresence } from 'framer-motion'
import { pickMediaFile } from '@/lib/browserPickers'
import {
  Briefcase,
  CheckCheck,
  CheckCircle,
  ChevronDown,
  FileVideo,
  FolderOpen,
  KeyRound,
  ListChecks,
  Loader,
  PlayCircle,
  RefreshCw,
  Server,
  Settings2,
  Upload,
  User,
  Wifi,
  XCircle,
  ShieldAlert,
  type LucideIcon,
} from 'lucide-react'

import { usePanelContext } from '@/contexts/PanelContext'
import { useRoxyAutoConnect } from '@/hooks/useRoxyAutoConnect'
import { roxyApi } from '@/lib/api'
import { normalizeRoxyHost } from '@/lib/roxy'
import { cn } from '@/lib/utils'
import { useAppStore } from '@/store/app.store'
import { taskStore } from '@/store/task.store'

type Step = 'config' | 'profile' | 'upload' | 'done'

type StepMeta = {
  key: Step
  label: string
  hint: string
  icon: LucideIcon
}

const STEPS: StepMeta[] = [
  { key: 'config', label: 'Connect API', hint: 'Nhập host và token để kết nối Roxy.', icon: Server },
  { key: 'profile', label: 'Choose Profile', hint: 'Chọn workspace và profile upload.', icon: ListChecks },
  { key: 'upload', label: 'Open Upload', hint: 'Chọn video và mở flow upload trên Roxy.', icon: PlayCircle },
  { key: 'done', label: 'Ready', hint: 'Roxy đã mở luồng upload thành công.', icon: CheckCheck },
]

const STEP_INDEX: Record<Step, number> = {
  config: 0,
  profile: 1,
  upload: 2,
  done: 3,
}

function getErrorMessage(error: unknown, fallback: string): string {
  return error instanceof Error ? error.message : fallback
}

export default function RoxyUpload() {
  const { childProjects, parentProjects } = useAppStore()
  const { selectedChildId } = usePanelContext()
  const child = childProjects.find(c => c.id === selectedChildId)
  const parent = parentProjects.find(item => item.id === child?.parentId)
  const preferredWorkspaceId = child?.roxyWorkspaceId ?? parent?.roxyWorkspaceId ?? undefined
  const preferredProfileId = child?.roxyProfileId || parent?.roxyProfileId || undefined
  const {
    activeProfile,
    activeWorkspace,
    apiHost,
    apiToken,
    connect: connectRoxy,
    error,
    handleWorkspaceChange: changeWorkspace,
    hasConfig,
    loading,
    normalizedHost,
    profileId,
    profiles,
    setApiHost,
    setApiToken,
    setProfileId,
    setShowReminder,
    showReminder,
    workspaceId,
    workspaces,
  } = useRoxyAutoConnect({
    preferredWorkspaceId,
    preferredProfileId,
  })
  const [videoPath, setVideoPath] = useState('')
  const [closeAfter, setCloseAfter] = useState(false)
  const [step, setStep] = useState<Step>('config')
  const [uploading, setUploading] = useState(false)
  const [uploadError, setUploadError] = useState<string | null>(null)
  const [successMsg, setSuccessMsg] = useState('')
  const stepIndex = STEP_INDEX[step]
  const busy = loading || uploading
  const errorMessage = uploadError || error
  const canContinueProfile = !!workspaceId && !!profileId && profiles.length > 0
  const canUpload = !!videoPath.trim() && !!profileId && !!workspaceId
  const loadingLabel =
    step === 'config'
      ? 'Connecting to Roxy API and loading profiles...'
      : step === 'profile'
        ? 'Loading available profiles...'
        : 'Opening Roxy profile and YouTube Studio...'

  const summaryCards = [
    {
      label: 'API Host',
      value: normalizedHost,
      state: hasConfig ? 'Configured' : 'Missing',
      tone: hasConfig ? 'bg-primary-500 text-surface-950' : 'bg-surface-0 text-surface-700',
    },
    {
      label: 'Workspace',
      value: activeWorkspace?.workspace_name || 'Chưa chọn',
      state: activeWorkspace ? 'Selected' : 'Pending',
      tone: activeWorkspace ? 'bg-amber-500 text-amber-950' : 'bg-surface-0 text-surface-700',
    },
    {
      label: 'Profile',
      value: activeProfile?.display_name || 'Chưa chọn',
      state: activeProfile ? 'Ready' : 'Pending',
      tone: activeProfile ? 'bg-sky-500 text-sky-950' : 'bg-surface-0 text-surface-700',
    },
    {
      label: 'Video',
      value: videoPath.trim() ? (videoPath.split('/').pop() ?? videoPath) : 'Chưa có file',
      state: videoPath.trim() ? 'Attached' : 'Pending',
      tone: videoPath.trim() ? 'bg-emerald-500 text-emerald-950' : 'bg-surface-0 text-surface-700',
    },
  ]

  const handleConnect = async () => {
    if (!hasConfig) return
    setUploadError(null)
    const ok = await connectRoxy()
    if (ok) setStep('profile')
  }

  const handleUpload = async () => {
    if (!canUpload || !workspaceId) return
    setUploading(true)
    setUploadError(null)
    const taskId = taskStore.add({ toolId: 'roxy-upload', toolLabel: 'Roxy Upload', label: 'Upload video YouTube' })

    try {
      const res = await roxyApi.upload({
        api_host: normalizedHost,
        api_token: apiToken.trim(),
        workspace_id: workspaceId,
        profile_id: profileId,
        video_path: videoPath.trim(),
        close_after: closeAfter,
      })

      setApiHost(normalizedHost)
      if (res.ok) {
        setSuccessMsg(res.message)
        setStep('done')
        taskStore.complete(taskId, 'done', res.message)
        return
      }

      setUploadError(res.message)
      taskStore.complete(taskId, 'error', res.message)
    } catch (err: unknown) {
      const message = getErrorMessage(err, 'Upload failed')
      setUploadError(message)
      taskStore.complete(taskId, 'error', message)
    } finally {
      setUploading(false)
    }
  }

  const handleChooseFile = async () => {
    try {
      const selected = await pickMediaFile('video/*,.mp4,.mov,.avi,.mkv,.webm')
      if (selected) {
        setVideoPath(selected.reference)
        setUploadError(null)
        return
      }
    } catch (cause) {
      setUploadError(cause instanceof Error ? cause.message : 'Video upload failed.')
    }
  }

  const reset = () => {
    setStep(profiles.length > 0 ? 'profile' : 'config')
    setUploadError(null)
    setSuccessMsg('')
    setVideoPath('')
    setCloseAfter(false)
  }

  useEffect(() => {
    if (step !== 'config' || workspaces.length === 0) return
    const timer = window.setTimeout(() => setStep('profile'), 0)
    return () => window.clearTimeout(timer)
  }, [step, workspaces.length])

  const renderStepCard = () => {
    if (step === 'config') {
      return (
        <div className="card overflow-hidden">
          <div className="border-b border-surface-200 bg-surface-50 px-5 py-4 md:px-6">
            <p className="text-base font-semibold text-surface-800">Connect to Roxy API</p>
            <p className="mt-1 text-sm text-surface-500">Nhập đúng host local API và token trước khi nạp workspace.</p>
          </div>

          <div className="space-y-4 px-5 py-5 md:px-6 md:py-6">
            <div className="grid gap-4 md:grid-cols-2">
              <div className="rounded-2xl border border-surface-200 bg-surface-50/70 p-4">
                <div className="mb-3 flex items-center gap-2">
                  <div className="flex h-9 w-9 items-center justify-center rounded-xl bg-surface-0 text-surface-600 shadow-sm">
                    <Server size={17} />
                  </div>
                  <div>
                    <p className="text-sm font-semibold text-surface-800">API Host</p>
                    <p className="text-xs text-surface-500">Địa chỉ local API của Roxy</p>
                  </div>
                </div>
                <label className="label">API Host</label>
                <input
                  className="input bg-surface-0 font-mono text-xs"
                  value={apiHost}
                  onChange={e => setApiHost(e.target.value)}
                  onBlur={e => setApiHost(normalizeRoxyHost(e.target.value))}
                />
                <p className="mt-2 text-xs text-surface-400">Mặc định nên là `http://127.0.0.1:50000`.</p>
              </div>

              <div className="rounded-2xl border border-surface-200 bg-surface-50/70 p-4">
                <div className="mb-3 flex items-center gap-2">
                  <div className="flex h-9 w-9 items-center justify-center rounded-xl bg-surface-0 text-surface-600 shadow-sm">
                    <KeyRound size={17} />
                  </div>
                  <div>
                    <p className="text-sm font-semibold text-surface-800">API Token</p>
                    <p className="text-xs text-surface-500">Token dùng để gọi Roxy API</p>
                  </div>
                </div>
                <label className="label">API Token</label>
                <input
                  className="input bg-surface-0 font-mono text-xs"
                  type="password"
                  placeholder="Paste token của Roxy"
                  value={apiToken}
                  onChange={e => setApiToken(e.target.value)}
                />
                <p className="mt-2 text-xs text-surface-400">Token đúng vẫn fail nếu host/port chưa có service chạy.</p>
              </div>
            </div>

            <div className="flex flex-col gap-3 border-t border-surface-100 pt-4 md:flex-row md:items-center md:justify-between">
              <p className="text-xs text-surface-400">Sau khi connect thành công, app sẽ tự lấy danh sách workspace của tài khoản.</p>
              <button className="btn-primary justify-center" onClick={handleConnect} disabled={busy || !hasConfig}>
                {busy ? <Loader size={14} className="animate-spin" /> : <Wifi size={14} />}
                {busy ? 'Connecting...' : 'Connect to Roxy API'}
              </button>
            </div>
          </div>
        </div>
      )
    }

    if (step === 'profile') {
      return (
        <div className="card overflow-hidden">
          <div className="border-b border-surface-200 bg-surface-50 px-5 py-4 md:px-6">
            <p className="text-base font-semibold text-surface-800">Choose workspace and upload profile</p>
            <p className="mt-1 text-sm text-surface-500">Connect xong là app tự load sẵn profile cho ngươi.</p>
          </div>

          <div className="space-y-4 px-5 py-5 md:px-6 md:py-6">
            <div className="grid gap-3 md:grid-cols-[minmax(0,1fr)_minmax(0,1fr)]">
              <div className="rounded-2xl border border-surface-200 bg-surface-50/80 p-4">
                <div className="mb-3 flex items-center justify-between gap-3">
                  <div className="flex items-center gap-2">
                    <div className="flex h-9 w-9 items-center justify-center rounded-xl bg-surface-0 text-surface-600 shadow-sm">
                      <Briefcase size={17} />
                    </div>
                    <div>
                      <p className="text-sm font-semibold text-surface-800">Workspace</p>
                      <p className="text-xs text-surface-500">{workspaces.length} workspace</p>
                    </div>
                  </div>
                  <span className="rounded-full bg-surface-0 px-2.5 py-1 text-[11px] font-semibold text-surface-500 shadow-sm">
                    auto loaded
                  </span>
                </div>

                <div className="relative">
                  <select
                    className="input h-11 appearance-none bg-surface-0 pr-10 text-sm shadow-sm"
                    value={workspaceId ?? ''}
                    onChange={e => changeWorkspace(Number(e.target.value))}
                    disabled={busy}
                  >
                    {workspaces.map(w => (
                      <option key={w.workspace_id} value={w.workspace_id}>
                        {w.workspace_name || `Workspace #${w.workspace_id}`}
                      </option>
                    ))}
                  </select>
                  <ChevronDown size={16} className="pointer-events-none absolute right-3 top-1/2 -translate-y-1/2 text-surface-400" />
                </div>
              </div>

              <div className="rounded-2xl border border-surface-200 bg-surface-50/80 p-4">
                <div className="mb-3 flex items-center justify-between gap-3">
                  <div className="flex items-center gap-2">
                    <div className="flex h-9 w-9 items-center justify-center rounded-xl bg-surface-0 text-surface-600 shadow-sm">
                      <User size={17} />
                    </div>
                    <div>
                      <p className="text-sm font-semibold text-surface-800">Profile</p>
                      <p className="text-xs text-surface-500">{profiles.length} profile</p>
                    </div>
                  </div>
                  {busy && <Loader size={14} className="animate-spin text-surface-400" />}
                </div>

                <div className="relative">
                  <select
                    className="input h-11 appearance-none bg-surface-0 pr-10 text-sm shadow-sm"
                    value={profileId}
                    onChange={e => setProfileId(e.target.value)}
                    disabled={profiles.length === 0 || busy}
                  >
                    {profiles.length === 0 && <option value="">Đang load profile...</option>}
                    {profiles.map(p => (
                      <option key={p.dir_id} value={p.dir_id}>{p.display_name}</option>
                    ))}
                  </select>
                  <ChevronDown size={16} className="pointer-events-none absolute right-3 top-1/2 -translate-y-1/2 text-surface-400" />
                </div>
              </div>
            </div>

            <div className="flex flex-col gap-3 border-t border-surface-100 pt-4 md:flex-row md:items-center md:justify-between">
              <p className="text-xs text-surface-400">Đổi workspace là app tự nạp lại profile, không cần bấm thêm gì nữa.</p>
              <div className="flex flex-col gap-2 sm:flex-row">
                <button className="btn-secondary justify-center" onClick={() => setStep('config')}>Back</button>
                <button className="btn-primary justify-center" onClick={() => setStep('upload')} disabled={!canContinueProfile || busy}>
                  Continue
                </button>
              </div>
            </div>
          </div>
        </div>
      )
    }

    if (step === 'upload') {
      return (
        <div className="card overflow-hidden">
          <div className="border-b border-surface-200 bg-surface-50 px-5 py-4 md:px-6">
            <div className="flex flex-col gap-3 md:flex-row md:items-start md:justify-between">
              <div>
                <p className="text-base font-semibold text-surface-800">Video to upload</p>
                <p className="mt-1 text-sm text-surface-500">Thêm video rồi mở trực tiếp luồng upload trên profile đã chọn.</p>
              </div>
              <div className="grid gap-2 sm:grid-cols-2">
                <div className="rounded-xl border border-surface-200 bg-surface-0 px-3 py-2">
                  <p className="text-[11px] font-medium uppercase tracking-[0.14em] text-surface-400">Workspace</p>
                  <p className="mt-1 text-sm font-medium text-surface-700">{activeWorkspace?.workspace_name || 'N/A'}</p>
                </div>
                <div className="rounded-xl border border-surface-200 bg-surface-0 px-3 py-2">
                  <p className="text-[11px] font-medium uppercase tracking-[0.14em] text-surface-400">Profile</p>
                  <p className="mt-1 text-sm font-medium text-surface-700">{activeProfile?.display_name || 'N/A'}</p>
                </div>
              </div>
            </div>
          </div>

          <div className="space-y-5 px-5 py-5 md:px-6 md:py-6">
            <div className="rounded-xl border border-dashed border-primary-200 bg-primary-50 p-4 md:p-5">
              <div className="mb-4 flex items-center gap-3">
                <div className="flex h-11 w-11 items-center justify-center rounded-2xl bg-surface-0 text-primary-600 shadow-sm">
                  <FileVideo size={18} />
                </div>
                <div>
                  <p className="text-sm font-semibold text-surface-800">Video from Media Library</p>
                  <p className="text-xs text-surface-500">Hỗ trợ mp4, mov, avi, mkv, webm.</p>
                </div>
              </div>

              <div className="flex flex-col gap-3 lg:flex-row">
                <input
                  className="input min-w-0 flex-1 bg-surface-0 font-mono text-xs"
                  placeholder="Choose a video from Media Library"
                  value={videoPath}
                  readOnly
                />
                <button className="btn-secondary shrink-0 justify-center px-4 lg:min-w-[160px]" onClick={handleChooseFile}>
                  <FolderOpen size={14} /> Choose file
                </button>
              </div>

              <div className="mt-3 flex flex-wrap items-center gap-2 text-xs">
                <span className="rounded-full border border-surface-200 bg-surface-0 px-3 py-1 text-surface-500">Formats: mp4, mov, avi, mkv, webm</span>
                {videoPath.trim() && (
                  <span className="rounded-full border border-primary-100 bg-primary-50 px-3 py-1 text-primary-700">File ready</span>
                )}
              </div>
            </div>

            <div className="flex flex-col gap-4 rounded-2xl border border-surface-200 bg-surface-0 p-4 md:flex-row md:items-center md:justify-between">
              <label className="flex items-start gap-3 text-sm text-surface-700 cursor-pointer select-none">
                <input type="checkbox" className="mt-0.5 h-4 w-4 rounded border-surface-300" checked={closeAfter} onChange={e => setCloseAfter(e.target.checked)} />
                <span>
                  <span className="block font-medium text-surface-700">Close profile after upload</span>
                  <span className="mt-0.5 block text-xs text-surface-400">Bật nếu muốn Roxy tự đóng profile sau khi mở luồng upload.</span>
                </span>
              </label>

              <div className="flex flex-col gap-2 sm:flex-row">
                <button className="btn-secondary justify-center" onClick={() => setStep('profile')}>
                  Back to profile selection
                </button>
                <button className="btn-primary justify-center px-5" onClick={handleUpload} disabled={busy || !canUpload}>
                  {busy ? <Loader size={14} className="animate-spin" /> : <Upload size={14} />}
                  {busy ? 'Uploading...' : 'Open upload in Roxy'}
                </button>
              </div>
            </div>
          </div>
        </div>
      )
    }

    return (
      <div className="card overflow-hidden border-green-500/20">
        <div className="border-t border-emerald-500/20 bg-emerald-500/10 px-5 py-4 md:px-6">
          <div className="flex items-center gap-3">
            <div className="flex h-11 w-11 items-center justify-center rounded-2xl bg-green-500 text-white shadow-sm">
              <CheckCircle size={20} />
            </div>
            <div>
              <p className="text-base font-semibold text-green-300">Upload flow is ready</p>
              <p className="mt-1 text-sm text-green-300">Roxy đã mở luồng upload. Anh có thể tiếp tục thao tác trong browser.</p>
            </div>
          </div>
        </div>

        <div className="space-y-5 px-5 py-5 md:px-6 md:py-6">
          <div className="rounded-2xl border border-green-500/20 bg-green-500/10 px-4 py-4 text-sm text-green-300">
            <p className="font-semibold">Upload started!</p>
            <p className="mt-1 whitespace-pre-wrap text-green-300">{successMsg}</p>
          </div>

          <div className="grid gap-3 md:grid-cols-3">
            <div className="rounded-xl border border-surface-200 bg-surface-0 px-4 py-3">
              <p className="text-[11px] font-medium uppercase tracking-[0.14em] text-surface-400">Workspace</p>
              <p className="mt-1 text-sm font-semibold text-surface-700">{activeWorkspace?.workspace_name || 'N/A'}</p>
            </div>
            <div className="rounded-xl border border-surface-200 bg-surface-0 px-4 py-3">
              <p className="text-[11px] font-medium uppercase tracking-[0.14em] text-surface-400">Profile</p>
              <p className="mt-1 text-sm font-semibold text-surface-700">{activeProfile?.display_name || 'N/A'}</p>
            </div>
            <div className="rounded-xl border border-surface-200 bg-surface-0 px-4 py-3">
              <p className="text-[11px] font-medium uppercase tracking-[0.14em] text-surface-400">Video</p>
              <p className="mt-1 truncate text-sm font-semibold text-surface-700">{videoPath.split('/').pop() ?? videoPath}</p>
            </div>
          </div>

          <div className="flex flex-col gap-2 sm:flex-row sm:justify-end">
            <button className="btn-secondary justify-center" onClick={() => setStep('upload')}>
              Review upload setup
            </button>
            <button className="btn-primary justify-center" onClick={reset}>
              <RefreshCw size={14} /> Upload another video
            </button>
          </div>
        </div>
      </div>
    )
  }

  return (
    <>
    <div className="flex h-full flex-col">
      <div className="flex items-center gap-3 border-b border-surface-200 bg-surface-0 px-6 py-4">
        <div className="flex h-9 w-9 items-center justify-center rounded-xl bg-red-500/10 text-red-300">
          <Upload size={17} />
        </div>
        <div>
          <h1 className="page-title">Roxy Upload</h1>
          <p className="page-sub">{child?.name ?? 'No child selected'} · Upload to YouTube through Roxy Browser</p>
        </div>
      </div>

      <div className="flex-1 overflow-y-auto">
        <div className="mx-auto flex max-w-7xl flex-col gap-6 p-6">
          <div className="grid gap-4 xl:grid-cols-4">
            {summaryCards.map(card => (
              <div key={card.label} className={cn('stat-card border-surface-200 shadow-card', card.tone)}>
                <span className={cn('stat-label', card.tone.includes('gradient') ? 'text-white/80' : 'text-surface-400')}>{card.label}</span>
                <span className={cn('truncate text-sm font-semibold', card.tone.includes('gradient') ? 'text-white' : 'text-surface-800')}>
                  {card.value}
                </span>
                <span className={cn('text-xs', card.tone.includes('gradient') ? 'text-white/80' : 'text-surface-400')}>{card.state}</span>
              </div>
            ))}
          </div>

          {errorMessage && (
            <div className="flex items-start gap-3 rounded-2xl border border-red-500/20 bg-red-500/10 px-4 py-3 text-sm text-red-300 shadow-sm">
              <XCircle size={16} className="mt-0.5 shrink-0" />
              <div>
                <p className="font-semibold">Có lỗi khi chạy Roxy Upload</p>
                <p className="mt-1 whitespace-pre-wrap text-red-300">{errorMessage}</p>
              </div>
            </div>
          )}

          {busy && (
            <div className="flex items-center gap-3 rounded-2xl border border-primary-100 bg-primary-50 px-4 py-3 text-sm text-primary-700 shadow-sm">
              <Loader size={16} className="shrink-0 animate-spin" />
              <div>
                <p className="font-semibold">{loadingLabel}</p>
                <p className="text-primary-600">Giữ nguyên màn hình này, app đang xử lý theo bước hiện tại.</p>
              </div>
            </div>
          )}

          {(child?.roxyProfileName || parent?.roxyProfileName) && (
            <div className="rounded-2xl border border-sky-500/20 bg-sky-500/10 px-4 py-3 text-sm text-sky-300 shadow-sm">
              Profile mặc định của project này: <span className="font-semibold">{child?.roxyProfileName || parent?.roxyProfileName}</span>
            </div>
          )}

          <div className="space-y-6">
              <div className="card p-5 md:p-6">
                <div className="flex flex-col gap-4 lg:flex-row lg:items-center lg:justify-between">
                  <div>
                    <p className="text-base font-semibold text-surface-800">Upload flow</p>
                    <p className="mt-1 text-sm text-surface-500">Thiết lập một lần rồi đi tuần tự từ kết nối API tới mở upload trên Roxy.</p>
                  </div>
                  <div className="inline-flex items-center gap-2 self-start rounded-full border border-surface-200 bg-surface-50 px-3 py-1 text-xs font-medium text-surface-600">
                    <Settings2 size={13} />
                    Step {stepIndex + 1} / {STEPS.length}
                  </div>
                </div>

                <div className="mt-5 grid gap-3 md:grid-cols-2 xl:grid-cols-4">
                  {STEPS.map((item, index) => {
                    const Icon = item.icon
                    const isActive = item.key === step
                    const isDone = stepIndex > index
                    const isAvailable =
                      item.key === 'config'
                      || (item.key === 'profile' && workspaces.length > 0)
                      || (item.key === 'upload' && profiles.length > 0)
                      || (item.key === 'done' && !!successMsg)

                    return (
                      <button
                        key={item.key}
                        type="button"
                        disabled={!isAvailable}
                        onClick={() => isAvailable && setStep(item.key)}
                        className={cn(
                          'rounded-2xl border p-4 text-left transition-[background-color,border-color,color,box-shadow,opacity,transform]',
                          isActive
                            ? 'border-primary-300 bg-primary-50 shadow-sm'
                            : isDone
                              ? 'border-green-500/20 bg-green-500/10'
                              : 'border-surface-200 bg-surface-0 hover:border-surface-300',
                          !isAvailable && 'cursor-not-allowed opacity-60 hover:border-surface-200',
                        )}
                      >
                        <div className="flex items-start justify-between gap-3">
                          <div className={cn(
                            'flex h-10 w-10 items-center justify-center rounded-xl',
                            isActive ? 'bg-primary-500 text-white' : isDone ? 'bg-green-500 text-white' : 'bg-surface-100 text-surface-500',
                          )}>
                            <Icon size={18} />
                          </div>
                          <span className={cn(
                            'rounded-full px-2 py-0.5 text-[11px] font-semibold',
                            isActive ? 'bg-primary-100 text-primary-700' : isDone ? 'bg-green-500/10 text-green-300' : 'bg-surface-100 text-surface-500',
                          )}>
                            {isDone ? 'Done' : isActive ? 'Current' : `Step ${index + 1}`}
                          </span>
                        </div>
                        <p className="mt-4 text-sm font-semibold text-surface-800">{item.label}</p>
                        <p className="mt-1 text-xs leading-relaxed text-surface-500">{item.hint}</p>
                      </button>
                    )
                  })}
                </div>
              </div>

              {renderStepCard()}
            </div>
          </div>
        </div>
      </div>

      <AnimatePresence>
        {showReminder && (
          <motion.div
            initial={{ opacity: 0 }}
            animate={{ opacity: 1 }}
            exit={{ opacity: 0 }}
            className="fixed inset-0 z-50 flex items-center justify-center bg-black/30 px-4"
          >
            <motion.div
              initial={{ scale: 0.96, opacity: 0 }}
              animate={{ scale: 1, opacity: 1 }}
              exit={{ scale: 0.96, opacity: 0 }}
              transition={{ duration: 0.16 }}
              className="w-full max-w-md rounded-xl bg-surface-0 p-5 shadow-xl"
            >
              <div className="mb-3 flex items-center gap-3">
                <div className="flex h-10 w-10 items-center justify-center rounded-xl bg-amber-500/10 text-amber-300">
                  <ShieldAlert size={18} />
                </div>
                <div>
                  <p className="text-sm font-semibold text-surface-900">Bật ứng dụng Roxy</p>
                  <p className="text-xs text-surface-500">App đang tự retry realtime để kết nối lại.</p>
                </div>
              </div>
              <p className="mb-4 whitespace-pre-wrap text-sm text-surface-600">
                {errorMessage || 'Chưa kết nối được tới local API của Roxy.'}
              </p>
              <div className="flex justify-end gap-2">
                <button className="btn-secondary" onClick={() => setShowReminder(false)}>
                  Ẩn tạm
                </button>
                <button
                  className="btn-primary"
                  onClick={() => void connectRoxy({ silent: true })}
                  disabled={!apiToken.trim() || busy}
                >
                  {busy ? <Loader size={14} className="animate-spin" /> : <Wifi size={14} />}
                  Thử lại ngay
                </button>
              </div>
            </motion.div>
          </motion.div>
        )}
      </AnimatePresence>
    </>
  )
}
