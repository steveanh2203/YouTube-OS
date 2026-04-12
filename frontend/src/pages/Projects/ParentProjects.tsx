import { useCallback, useEffect, useRef, useState } from 'react'
import { motion, AnimatePresence } from 'framer-motion'
import { useAppStore } from '@/store/app.store'
import { usePanelContext } from '@/contexts/PanelContext'
import { Plus, FolderOpen, Trash2, ChevronRight, User, Building, Tag, Loader, Pencil, Briefcase, Wifi, ShieldAlert, ChevronsUpDown } from 'lucide-react'
import { useRoxyAutoConnect } from '@/hooks/useRoxyAutoConnect'
import { useExtensionSocket } from '@/hooks/useExtensionSocket'
import { cn } from '@/lib/utils'
import { youtubeReplyApi, type YouTubeConfigMapResponse } from '@/lib/api'
import { fromApiYouTubeConfig, sanitizeYouTubeReplyConfig, toApiYouTubeConfig, type YouTubeReplyConfig } from '@/lib/youtubeReply'
import { toast } from '@/store/toast.store'

const API = 'http://127.0.0.1:8765'

interface FormState {
  name: string; author: string; publisher: string; copyright: string; keywordsRaw: string
}

const EMPTY: FormState = { name: '', author: '', publisher: '', copyright: '', keywordsRaw: '' }

// Shape returned by FastAPI POST/GET /api/parent-projects/
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
  updated_at: string
}

interface ApiChildProjectCountRow {
  id: number
  parent_project_id?: number | null
}

const toProjectState = (p: ApiParentProject, childCount = 0) => ({
  id: String(p.id),
  name: p.name,
  author: p.author ?? '',
  publisher: p.publisher ?? '',
  copyright: p.copyright ?? '',
  keywordsRaw: p.keywords_raw ?? '',
  roxyWorkspaceId: p.roxy_workspace_id ?? null,
  roxyProfileId: p.roxy_profile_id ?? '',
  roxyProfileName: p.roxy_profile_name ?? '',
  childCount,
  createdAt: p.created_at.slice(0, 10),
})

export default function ParentProjects() {
  const { setParentProjects, deleteParent, updateParent, requestedParentEditorId, requestParentEditor } = useAppStore()
  const parentProjects = useAppStore(s => s.parentProjects)
  const { selectParent, setMainView, setProjectSubView } = usePanelContext()

  const [showForm, setShowForm] = useState(false)
  const [form, setForm] = useState<FormState>(EMPTY)
  const [editingId, setEditingId] = useState<string | null>(null)
  const [deleting, setDeleting] = useState<string | null>(null)
  const [saving, setSaving] = useState(false)
  const [fetchError, setFetchError] = useState<string | null>(null)
  const [youtubeConfig, setYouTubeConfig] = useState<YouTubeReplyConfig>(sanitizeYouTubeReplyConfig())
  const [youtubeConfigsByParent, setYouTubeConfigsByParent] = useState<Record<string, YouTubeReplyConfig>>({})
  const retryTimerRef = useRef<number | null>(null)
  const fetchFailuresRef = useRef(0)
  const {
    activeProfile,
    apiToken,
    connect: connectRoxy,
    error: roxyError,
    handleWorkspaceChange,
    hasConfig: hasRoxyConfig,
    loading: roxyLoading,
    profileId,
    profiles,
    setProfileId,
    setShowReminder,
    showReminder,
    workspaceId,
    workspaces,
  } = useRoxyAutoConnect({ enabled: showForm })

  const loadYouTubeConfigs = useCallback(async () => {
    try {
      const res: YouTubeConfigMapResponse = await youtubeReplyApi.configs()
      const next = Object.fromEntries(
        Object.entries(res.items).map(([parentId, config]) => [parentId, fromApiYouTubeConfig(config)]),
      )
      setYouTubeConfigsByParent(next)
      if (editingId) {
        setYouTubeConfig(next[editingId] ?? sanitizeYouTubeReplyConfig())
      }
    } catch {
      setYouTubeConfigsByParent({})
      if (editingId) {
        setYouTubeConfig(sanitizeYouTubeReplyConfig())
      }
    }
  }, [editingId])

  // ── Fetch from backend on mount ──────────────────────────────────────────
  useEffect(() => {
    let cancelled = false

    const clearRetry = () => {
      if (retryTimerRef.current !== null) {
        window.clearTimeout(retryTimerRef.current)
        retryTimerRef.current = null
      }
    }

    async function load() {
      try {
        const [parentsRes, childrenRes] = await Promise.all([
          fetch(`${API}/api/parent-projects/`),
          fetch(`${API}/api/child-projects/`),
        ])
        if (!parentsRes.ok) throw new Error(`HTTP ${parentsRes.status}`)
        if (!childrenRes.ok) throw new Error(`HTTP ${childrenRes.status}`)

        const data: ApiParentProject[] = await parentsRes.json()
        const childRows: ApiChildProjectCountRow[] = await childrenRes.json()
        const counts = childRows.reduce<Record<string, number>>((acc, child) => {
          const parentId = child.parent_project_id
          if (parentId == null) return acc
          const key = String(parentId)
          acc[key] = (acc[key] ?? 0) + 1
          return acc
        }, {})

        if (cancelled) return
        clearRetry()
        fetchFailuresRef.current = 0
        setFetchError(null)
        setParentProjects(data.map((project) => toProjectState(project, counts[String(project.id)] ?? 0)))
      } catch {
        if (cancelled) return
        fetchFailuresRef.current += 1
        if (fetchFailuresRef.current >= 3) {
          setFetchError('Cannot connect to backend')
        }
        clearRetry()
        retryTimerRef.current = window.setTimeout(load, 3000)
      }
    }

    load()

    return () => {
      cancelled = true
      clearRetry()
    }
  }, [setParentProjects])

  useEffect(() => {
    void loadYouTubeConfigs()
  }, [loadYouTubeConfigs])

  useExtensionSocket('account_connect', () => {
    void loadYouTubeConfigs()
    toast.success('YTB Connect', 'Kênh vừa được extension connect vào desktop app.')
  })

  const closeForm = () => {
    setShowForm(false)
    setEditingId(null)
    setForm(EMPTY)
    setYouTubeConfig(sanitizeYouTubeReplyConfig())
    setShowReminder(false)
    requestParentEditor(null)
  }

  const openCreateForm = () => {
    setEditingId(null)
    setForm(EMPTY)
    setYouTubeConfig(sanitizeYouTubeReplyConfig())
    setShowForm(true)
  }

  const openEditForm = useCallback((id: string) => {
    const project = parentProjects.find(item => item.id === id)
    if (!project) return
    setEditingId(id)
    setForm({
      name: project.name,
      author: project.author,
      publisher: project.publisher,
      copyright: project.copyright,
      keywordsRaw: project.keywordsRaw,
    })
    setYouTubeConfig(youtubeConfigsByParent[project.id] ?? sanitizeYouTubeReplyConfig())
    setShowForm(true)
    if (project.roxyWorkspaceId) {
      void connectRoxy({
        preferredWorkspaceId: project.roxyWorkspaceId,
        preferredProfileId: project.roxyProfileId,
        silent: true,
      })
    }
  }, [connectRoxy, parentProjects, youtubeConfigsByParent])

  useEffect(() => {
    if (!requestedParentEditorId) return
    openEditForm(requestedParentEditorId)
  }, [openEditForm, requestedParentEditorId])

  // ── Create / Update via API ───────────────────────────────────────────────
  const handleSubmit = async () => {
    if (!form.name.trim()) return
    setSaving(true)
    try {
      const isEditing = Boolean(editingId)
      const res = await fetch(
        isEditing ? `${API}/api/parent-projects/${editingId}` : `${API}/api/parent-projects/`,
        {
          method: isEditing ? 'PATCH' : 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            name: form.name.trim(),
            author: form.author.trim() || null,
            publisher: form.publisher.trim() || null,
            copyright: form.copyright.trim() || null,
            keywords_raw: form.keywordsRaw.trim() || null,
            roxy_workspace_id: workspaceId || null,
            roxy_profile_id: profileId || null,
            roxy_profile_name: activeProfile?.display_name || null,
          }),
        }
      )
      if (!res.ok) throw new Error(`HTTP ${res.status}`)
      const saved: ApiParentProject = await res.json()
      const existingProject = editingId ? parentProjects.find(item => item.id === editingId) : null
      const nextProject = toProjectState(saved, existingProject?.childCount ?? 0)

      if (isEditing) {
        updateParent(nextProject.id, nextProject)
      } else {
        useAppStore.getState().addParentDirect(nextProject)
      }

      const savedConfigRes = await youtubeReplyApi.saveConfig(nextProject.id, toApiYouTubeConfig(youtubeConfig))
      const savedConfig = fromApiYouTubeConfig(savedConfigRes.config)
      setYouTubeConfigsByParent(prev => ({ ...prev, [nextProject.id]: savedConfig }))

      closeForm()
      toast.success('Parent Project', `${nextProject.name} saved.`)
    } catch (error: unknown) {
      const message = error instanceof Error ? error.message : 'Cannot save parent project.'
      toast.error('Save failed', message)
    } finally {
      setSaving(false)
    }
  }

  // ── Delete via API ────────────────────────────────────────────────────────
  const handleDelete = async (id: string) => {
    setDeleting(id)
    try {
      await fetch(`${API}/api/parent-projects/${id}`, { method: 'DELETE' })
      deleteParent(id)
      setYouTubeConfigsByParent(prev => {
        const next = { ...prev }
        delete next[id]
        return next
      })
    } finally {
      setDeleting(null)
    }
  }

  const handleOpen = (id: string) => {
    selectParent(id)
    setProjectSubView('children')
  }

  const roxyStatusText = !hasRoxyConfig
    ? 'Chưa có token Roxy. Qua màn Roxy Upload nhập token 1 lần, sau đó form này sẽ tự hiện profile.'
    : roxyLoading
      ? 'Đang tự kết nối Roxy và nạp profile...'
      : activeProfile
        ? `Profile đang chọn: ${activeProfile.display_name}`
        : (roxyError || 'Chưa chọn profile Roxy')

  const updateYouTubeConfig = <K extends keyof YouTubeReplyConfig>(key: K, value: YouTubeReplyConfig[K]) => {
    setYouTubeConfig(prev => ({ ...prev, [key]: value }))
  }

  return (
    <div className="flex flex-col h-full">
      {/* Header */}
      <div className="flex items-center justify-between px-6 py-4 border-b border-surface-200 bg-white">
        <div>
          <h1 className="page-title">Parent Projects</h1>
          <p className="page-sub mt-0.5">{parentProjects.length} project{parentProjects.length !== 1 ? 's' : ''}</p>
        </div>
        <button className="btn-primary" onClick={openCreateForm}>
          <Plus size={14} />
          New Project
        </button>
      </div>

      {/* Content */}
      <div className="flex-1 overflow-y-auto p-6">
        {/* Backend error banner */}
        {fetchError && (
          <div className="mb-4 px-4 py-2.5 rounded-lg bg-red-50 border border-red-200 text-xs text-red-700">
            {fetchError} — showing cached data
          </div>
        )}

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
              <h3 className="text-sm font-semibold text-surface-800 mb-4">
                {editingId ? 'Edit Parent Project' : 'New Parent Project'}
              </h3>
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
                <div className="col-span-2 rounded-2xl border border-surface-200 bg-gradient-to-r from-surface-50 to-primary-50/40 p-4">
                  <div className="mb-3 flex items-center justify-between gap-3">
                    <div>
                      <p className="text-sm font-semibold text-surface-800">Roxy Connection</p>
                      <p className="mt-1 text-xs text-surface-500">Chọn workspace và profile mặc định cho parent project.</p>
                    </div>
                    <span className={cn(
                      'badge',
                      hasRoxyConfig && activeProfile
                        ? 'bg-green-50 text-green-700'
                        : roxyLoading
                          ? 'bg-amber-50 text-amber-700'
                          : 'bg-surface-100 text-surface-600',
                    )}>
                      {roxyLoading ? 'Syncing' : activeProfile ? 'Connected' : 'Pending'}
                    </span>
                  </div>

                  <div className="grid gap-4 md:grid-cols-2">
                    <div>
                      <label className="label !mb-2">Roxy Workspace</label>
                      <div className="rounded-xl border border-surface-200 bg-white px-4 py-3 shadow-sm transition-all duration-150 focus-within:border-primary-400 focus-within:ring-2 focus-within:ring-primary-100">
                        <div className="mb-2 flex items-center justify-between gap-3">
                          <div className="flex items-center gap-2 text-surface-500">
                            <Briefcase size={15} />
                            <span className="text-xs font-medium">Workspace</span>
                          </div>
                          <Briefcase size={15} className="text-surface-300" />
                        </div>
                        <div className="relative">
                          <select
                            className="w-full appearance-none bg-transparent pr-8 text-base font-medium text-surface-900 outline-none disabled:cursor-not-allowed disabled:text-surface-400"
                            value={workspaceId ?? ''}
                            onChange={e => handleWorkspaceChange(Number(e.target.value))}
                            disabled={!hasRoxyConfig || roxyLoading || workspaces.length === 0}
                          >
                            {workspaces.length === 0 && <option value="">Chưa tải workspace</option>}
                            {workspaces.map(item => (
                              <option key={item.workspace_id} value={item.workspace_id}>
                                {item.workspace_name || `Workspace #${item.workspace_id}`}
                              </option>
                            ))}
                          </select>
                          <ChevronsUpDown size={16} className="pointer-events-none absolute right-0 top-1/2 -translate-y-1/2 text-surface-400" />
                        </div>
                      </div>
                    </div>

                    <div>
                      <label className="label !mb-2">Roxy Profile</label>
                      <div className="rounded-xl border border-surface-200 bg-white px-4 py-3 shadow-sm transition-all duration-150 focus-within:border-primary-400 focus-within:ring-2 focus-within:ring-primary-100">
                        <div className="mb-2 flex items-center justify-between gap-3">
                          <div className="flex items-center gap-2 text-surface-500">
                            <Wifi size={15} />
                            <span className="text-xs font-medium">Profile</span>
                          </div>
                          <Wifi size={15} className="text-surface-300" />
                        </div>
                        <div className="relative">
                          <select
                            className="w-full appearance-none bg-transparent pr-8 text-base font-medium text-surface-900 outline-none disabled:cursor-not-allowed disabled:text-surface-400"
                            value={profileId}
                            onChange={e => setProfileId(e.target.value)}
                            disabled={!hasRoxyConfig || roxyLoading || profiles.length === 0}
                          >
                            {profiles.length === 0 && <option value="">Chưa tải profile</option>}
                            {profiles.map(item => (
                              <option key={item.dir_id} value={item.dir_id}>
                                {item.display_name}
                              </option>
                            ))}
                          </select>
                          <ChevronsUpDown size={16} className="pointer-events-none absolute right-0 top-1/2 -translate-y-1/2 text-surface-400" />
                        </div>
                      </div>
                    </div>
                  </div>
                </div>
                <div className="col-span-2 rounded-2xl border border-surface-200 bg-gradient-to-r from-white to-red-50/50 p-4">
                  <div className="mb-3 flex items-center justify-between gap-3">
                    <div>
                      <p className="text-sm font-semibold text-surface-800">YouTube Pairing</p>
                      <p className="mt-1 text-xs text-surface-500">Không nhập token tay ở đây nữa. Pair kênh qua Settings + extension là đủ.</p>
                    </div>
                    <span className={cn(
                      'badge',
                      youtubeConfig.connected
                        ? 'bg-green-50 text-green-700'
                        : 'bg-surface-100 text-surface-600',
                    )}>
                      {youtubeConfig.connected ? 'Connected' : 'Pending'}
                    </span>
                  </div>

                  <div className="grid gap-4 md:grid-cols-2">
                    <div>
                      <label className="label">Channel ID</label>
                      <input
                        className="input font-mono text-xs"
                        placeholder="Will appear after pairing"
                        value={youtubeConfig.channelId}
                        readOnly
                      />
                    </div>
                    <div>
                      <label className="label">Channel Name</label>
                      <input
                        className="input"
                        placeholder="Will appear after pairing"
                        value={youtubeConfig.channelName}
                        readOnly
                      />
                    </div>
                  </div>

                  <div className="mt-4 flex flex-wrap items-center justify-between gap-3 rounded-xl border border-surface-200 bg-white px-4 py-3">
                    <div className="min-w-0">
                      <p className="text-xs font-medium text-surface-700">
                        {youtubeConfig.connected
                          ? `Connected to ${youtubeConfig.channelName || youtubeConfig.channelId}`
                          : 'This project has not been paired to a YouTube channel yet.'}
                      </p>
                      <p className="mt-1 text-xs text-surface-500">
                        {youtubeConfig.verifiedAt
                          ? `Verified at ${new Date(youtubeConfig.verifiedAt).toLocaleString()}`
                          : 'Go to Settings, verify OAuth, then use YTB Connect to pair the right channel.'}
                      </p>
                    </div>

                    <button
                      className="btn-secondary"
                      onClick={() => setMainView('settings')}
                    >
                      Open Settings
                    </button>
                  </div>
                </div>
                <div className="col-span-2 rounded-2xl border border-surface-200 bg-gradient-to-r from-white to-sky-50/60 p-4">
                  <div className="mb-3 flex items-center justify-between gap-3">
                    <div>
                      <p className="text-sm font-semibold text-surface-800">Brand Channel</p>
                      <p className="mt-1 text-xs text-surface-500">Đây là ADN riêng của từng project. Auto reply sẽ bám đúng profile này.</p>
                    </div>
                    <span className="badge bg-sky-50 text-sky-700">
                      Per project
                    </span>
                  </div>

                  <div className="grid gap-4 md:grid-cols-2">
                    <div>
                      <label className="label">Channel DNA</label>
                      <textarea
                        className="textarea text-sm"
                        rows={3}
                        placeholder="Calm, trustworthy, practical, reassuring..."
                        value={youtubeConfig.channelDna}
                        onChange={e => updateYouTubeConfig('channelDna', e.target.value)}
                      />
                    </div>
                    <div>
                      <label className="label">Audience profile</label>
                      <textarea
                        className="textarea text-sm"
                        rows={3}
                        placeholder="Older adults, caregivers, beginners, busy parents..."
                        value={youtubeConfig.audienceProfile}
                        onChange={e => updateYouTubeConfig('audienceProfile', e.target.value)}
                      />
                    </div>
                    <div>
                      <label className="label">Target market</label>
                      <textarea
                        className="textarea text-sm"
                        rows={3}
                        placeholder="US seniors, Korean expats in the US, Vietnam market..."
                        value={youtubeConfig.targetMarket}
                        onChange={e => updateYouTubeConfig('targetMarket', e.target.value)}
                      />
                    </div>
                  </div>
                </div>
              </div>
              <div className="mb-4 rounded-xl border border-surface-200 bg-surface-50 px-4 py-3 text-xs text-surface-500">
                {roxyStatusText}
              </div>
              <div className="flex gap-2 justify-end">
                <button className="btn-secondary" onClick={closeForm}>Cancel</button>
                <button
                  className="btn-primary"
                  onClick={handleSubmit}
                  disabled={!form.name.trim() || saving}
                >
                  {saving ? (
                    <><Loader size={13} className="animate-spin" /> {editingId ? 'Saving...' : 'Creating...'}</>
                  ) : (
                    editingId ? 'Save Changes' : 'Create Project'
                  )}
                </button>
              </div>
            </motion.div>
          )}
        </AnimatePresence>

        {/* Empty state */}
        {parentProjects.length === 0 && !showForm && (
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
                onClick={() => handleOpen(p.id)}
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
                    {p.roxyProfileName && (
                      <span className="flex items-center gap-1 text-xs text-surface-500">
                        <Wifi size={11} /> {p.roxyProfileName}
                      </span>
                    )}
                  </div>
                </div>

                {/* Actions */}
                <div className="flex items-center gap-1 ml-2" onClick={e => e.stopPropagation()}>
                  <button
                    className="btn-icon text-surface-400 hover:text-primary-500 hover:bg-primary-50"
                    title="Edit"
                    onClick={() => openEditForm(p.id)}
                  >
                    <Pencil size={14} />
                  </button>
                  <button
                    className="btn-icon text-surface-400 hover:text-red-500 hover:bg-red-50"
                    title="Delete"
                    onClick={() => handleDelete(p.id)}
                  >
                    {deleting === p.id ? <Loader size={14} className="animate-spin" /> : <Trash2 size={14} />}
                  </button>
                  <ChevronRight size={16} className="text-surface-300" />
                </div>
              </motion.div>
            ))}
          </AnimatePresence>
        </div>
      </div>

      <AnimatePresence>
        {showForm && showReminder && (
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
              className="w-full max-w-md rounded-xl bg-white p-5 shadow-xl"
            >
              <div className="mb-3 flex items-center gap-3">
                <div className="flex h-10 w-10 items-center justify-center rounded-xl bg-amber-100 text-amber-600">
                  <ShieldAlert size={18} />
                </div>
                <div>
                  <p className="text-sm font-semibold text-surface-900">Bật ứng dụng Roxy</p>
                  <p className="text-xs text-surface-500">App đang tự retry để lấy profile.</p>
                </div>
              </div>
              <p className="mb-4 whitespace-pre-wrap text-sm text-surface-600">
                {roxyError || 'Chưa kết nối được tới local API của Roxy.'}
              </p>
              <div className="flex justify-end gap-2">
                <button className="btn-secondary" onClick={() => setShowReminder(false)}>
                  Ẩn tạm
                </button>
                <button
                  className="btn-primary"
                  onClick={() => void connectRoxy({ silent: true })}
                  disabled={!apiToken.trim() || roxyLoading}
                >
                  {roxyLoading ? <Loader size={14} className="animate-spin" /> : <Wifi size={14} />}
                  Thử lại ngay
                </button>
              </div>
            </motion.div>
          </motion.div>
        )}
      </AnimatePresence>
    </div>
  )
}
