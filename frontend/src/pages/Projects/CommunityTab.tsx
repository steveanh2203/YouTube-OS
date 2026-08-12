import { useCallback, useEffect, useMemo, useState } from 'react'
import { pickMediaFile } from '@/lib/browserPickers'
import type { ChildProject } from '@/store/app.store'
import { communityApi, type CommunityLogEntry, type CommunityPostItem } from '@/lib/api'
import { loadRoxyConfig } from '@/lib/roxy'
import { taskStore } from '@/store/task.store'
import { cn } from '@/lib/utils'
import {
  AlertCircle,
  CheckCircle2,
  Clock3,
  History,
  ImagePlus,
  Loader2,
  Plus,
  RefreshCw,
  Send,
} from 'lucide-react'


type StatusFilter = 'all' | 'draft' | 'queued' | 'posting' | 'published' | 'failed'

interface CommunityFormState {
  body: string
  imagePath: string
  channelUrl: string
  childProjectId: string
  postMode: 'now' | 'schedule'
  scheduleAt: string
}


const DEFAULT_FORM: CommunityFormState = {
  body: '',
  imagePath: '',
  channelUrl: '',
  childProjectId: '',
  postMode: 'now',
  scheduleAt: '',
}


function formatDateTime(value?: string | null) {
  if (!value) return '—'
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return value
  return date.toLocaleString()
}


function toDateTimeLocal(value?: string | null) {
  if (!value) return ''
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return ''
  const local = new Date(date.getTime() - date.getTimezoneOffset() * 60000)
  return local.toISOString().slice(0, 16)
}


function fromPost(post: CommunityPostItem | null): CommunityFormState {
  if (!post) return DEFAULT_FORM
  return {
    body: post.body ?? '',
    imagePath: post.image_path ?? '',
    channelUrl: post.channel_url ?? '',
    childProjectId: post.child_project_id ? String(post.child_project_id) : '',
    postMode: post.post_mode === 'schedule' ? 'schedule' : 'now',
    scheduleAt: toDateTimeLocal(post.schedule_at),
  }
}


function statusTone(status: string) {
  if (status === 'published') return 'bg-emerald-500/10 text-emerald-300 border-emerald-500/20'
  if (status === 'queued') return 'bg-blue-500/10 text-blue-300 border-blue-500/20'
  if (status === 'posting') return 'bg-amber-500/10 text-amber-300 border-amber-500/20'
  if (status === 'failed') return 'bg-rose-500/10 text-rose-300 border-rose-500/20'
  return 'bg-surface-50 text-surface-600 border-surface-200'
}


export default function CommunityTab({ parentId, children }: { parentId: string; children: ChildProject[] }) {
  const numericParentId = Number(parentId)
  const [posts, setPosts] = useState<CommunityPostItem[]>([])
  const [logs, setLogs] = useState<CommunityLogEntry[]>([])
  const [selectedId, setSelectedId] = useState<number | null>(null)
  const [form, setForm] = useState<CommunityFormState>(DEFAULT_FORM)
  const [loading, setLoading] = useState(true)
  const [busy, setBusy] = useState(false)
  const [filter, setFilter] = useState<StatusFilter>('all')
  const [search, setSearch] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [status, setStatus] = useState('Ready.')

  const selectedPost = useMemo(
    () => posts.find((item) => item.id === selectedId) ?? null,
    [posts, selectedId],
  )

  const refreshPosts = useCallback(async () => {
    setLoading(true)
    try {
      const res = await communityApi.list({
        parent_project_id: numericParentId,
        status: filter === 'all' ? '' : filter,
        q: search,
      })
      setPosts(res.posts)
      setStatus(res.message)
      setError(null)
      if (!res.posts.length) {
        setSelectedId(null)
        setForm(DEFAULT_FORM)
        setLogs([])
        return
      }
      setSelectedId((current) => (current && res.posts.some((item) => item.id === current) ? current : res.posts[0].id))
    } catch (err: unknown) {
      const message = err instanceof Error ? err.message : 'Could not load Community posts.'
      setError(message)
      setStatus(message)
    } finally {
      setLoading(false)
    }
  }, [filter, numericParentId, search])

  useEffect(() => {
    refreshPosts()
  }, [refreshPosts])

  useEffect(() => {
    setForm(fromPost(selectedPost))
  }, [selectedPost])

  useEffect(() => {
    if (!selectedPost) {
      setLogs([])
      return
    }
    let cancelled = false
    communityApi.logs(selectedPost.id)
      .then((res) => {
        if (!cancelled) setLogs(res.logs)
      })
      .catch(() => {
        if (!cancelled) setLogs([])
      })
    return () => { cancelled = true }
  }, [selectedPost])

  const persistDraft = useCallback(async () => {
    const payload = {
      child_project_id: form.childProjectId ? Number(form.childProjectId) : null,
      body: form.body,
      image_path: form.imagePath || null,
      channel_url: form.channelUrl || null,
      post_mode: form.postMode,
      schedule_at: form.postMode === 'schedule' && form.scheduleAt
        ? new Date(form.scheduleAt).toISOString()
        : null,
    } as const

    if (!selectedPost) {
      const created = await communityApi.create({
        parent_project_id: numericParentId,
        ...payload,
      })
      if (created.post) {
        setSelectedId(created.post.id)
      }
      return created
    }
    return communityApi.update(selectedPost.id, payload)
  }, [form, numericParentId, selectedPost])

  const handleSave = async () => {
    setBusy(true)
    setError(null)
    const taskId = taskStore.add({ toolId: 'children', toolLabel: 'Community', label: 'Save community draft' })
    try {
      const res = await persistDraft()
      setStatus(res.message)
      taskStore.complete(taskId, 'done', res.message)
      await refreshPosts()
    } catch (err: unknown) {
      const message = err instanceof Error ? err.message : 'Could not save draft.'
      setError(message)
      setStatus(message)
      taskStore.complete(taskId, 'error', message)
    } finally {
      setBusy(false)
    }
  }

  const handleCreate = async () => {
    setBusy(true)
    setError(null)
    const taskId = taskStore.add({ toolId: 'children', toolLabel: 'Community', label: 'Create community draft' })
    try {
      const res = await communityApi.create({ parent_project_id: numericParentId })
      if (res.post) setSelectedId(res.post.id)
      setStatus(res.message)
      taskStore.complete(taskId, 'done', res.message)
      await refreshPosts()
    } catch (err: unknown) {
      const message = err instanceof Error ? err.message : 'Could not create draft.'
      setError(message)
      setStatus(message)
      taskStore.complete(taskId, 'error', message)
    } finally {
      setBusy(false)
    }
  }

  const handleQueue = async () => {
    if (!selectedPost && !form.body.trim()) return
    setBusy(true)
    setError(null)
    const taskId = taskStore.add({ toolId: 'children', toolLabel: 'Community', label: 'Queue community post' })
    try {
      const saved = await persistDraft()
      const postId = saved.post?.id ?? selectedPost?.id
      if (!postId) throw new Error('Missing community post id after save.')
      const queued = await communityApi.queue(postId)
      setStatus(queued.message)
      taskStore.complete(taskId, 'done', queued.message)
      await refreshPosts()
    } catch (err: unknown) {
      const message = err instanceof Error ? err.message : 'Could not queue post.'
      setError(message)
      setStatus(message)
      taskStore.complete(taskId, 'error', message)
    } finally {
      setBusy(false)
    }
  }

  const handlePublishNow = async () => {
    setBusy(true)
    setError(null)
    const roxy = loadRoxyConfig()
    if (!roxy.apiToken.trim() || !roxy.apiHost.trim()) {
      const message = 'Roxy API host/token is missing. Open Roxy config first.'
      setError(message)
      setStatus(message)
      setBusy(false)
      return
    }

    const taskId = taskStore.add({ toolId: 'children', toolLabel: 'Community', label: 'Publish community post now' })
    try {
      const saved = await persistDraft()
      const postId = saved.post?.id ?? selectedPost?.id
      if (!postId) throw new Error('Missing community post id after save.')
      const published = await communityApi.publishNow(postId, {
        api_host: roxy.apiHost,
        api_token: roxy.apiToken,
      })
      if (!published.ok) {
        throw new Error(published.message)
      }
      setStatus(published.message)
      taskStore.complete(taskId, 'done', published.message)
      await refreshPosts()
    } catch (err: unknown) {
      const message = err instanceof Error ? err.message : 'Could not publish post.'
      setError(message)
      setStatus(message)
      taskStore.complete(taskId, 'error', message)
      await refreshPosts()
    } finally {
      setBusy(false)
    }
  }

  const handleRetry = async () => {
    if (!selectedPost) return
    setBusy(true)
    setError(null)
    const taskId = taskStore.add({ toolId: 'children', toolLabel: 'Community', label: 'Retry community post' })
    try {
      const res = await communityApi.retry(selectedPost.id)
      setStatus(res.message)
      taskStore.complete(taskId, 'done', res.message)
      await refreshPosts()
    } catch (err: unknown) {
      const message = err instanceof Error ? err.message : 'Could not retry post.'
      setError(message)
      setStatus(message)
      taskStore.complete(taskId, 'error', message)
    } finally {
      setBusy(false)
    }
  }

  const handlePickImage = async () => {
    const selected = await pickMediaFile('image/*,.png,.jpg,.jpeg,.webp')
    if (selected) {
      setForm((prev) => ({ ...prev, imagePath: selected.reference }))
    }
  }

  return (
    <div className="flex h-full min-h-0">
      <div className="w-[360px] shrink-0 border-r border-surface-200 bg-surface-0">
        <div className="border-b border-surface-200 px-5 py-4">
          <div className="flex items-center justify-between">
            <div>
              <h2 className="text-sm font-semibold text-surface-900">Community</h2>
              <p className="mt-1 text-xs text-surface-500">Plan, queue, and auto-publish channel posts.</p>
            </div>
            <button className="btn-primary h-9 px-3 text-xs" onClick={handleCreate} disabled={busy}>
              <Plus size={14} />
              New Post
            </button>
          </div>

          <div className="mt-4 grid gap-2">
            <input
              className="input text-sm"
              placeholder="Search body or channel URL..."
              value={search}
              onChange={(event) => setSearch(event.target.value)}
            />
            <div className="flex gap-2">
              <select
                className="input h-9 text-xs"
                value={filter}
                onChange={(event) => setFilter(event.target.value as StatusFilter)}
              >
                <option value="all">All status</option>
                <option value="draft">Draft</option>
                <option value="queued">Queued</option>
                <option value="posting">Posting</option>
                <option value="published">Published</option>
                <option value="failed">Failed</option>
              </select>
              <button className="btn-secondary h-9 px-3 text-xs" onClick={refreshPosts} disabled={loading}>
                {loading ? <Loader2 size={14} className="animate-spin" /> : <RefreshCw size={14} />}
                Refresh
              </button>
            </div>
          </div>
        </div>

        <div className="h-[calc(100%-126px)] overflow-y-auto p-3">
          {posts.length === 0 && !loading ? (
            <div className="rounded-2xl border border-dashed border-surface-200 bg-surface-50 px-4 py-6 text-center">
              <p className="text-sm font-medium text-surface-700">No community posts yet.</p>
              <p className="mt-1 text-xs text-surface-500">Create the first draft for this channel.</p>
            </div>
          ) : (
            <div className="space-y-2">
              {posts.map((post) => (
                <button
                  key={post.id}
                  onClick={() => setSelectedId(post.id)}
                  className={cn(
                    'w-full rounded-2xl border px-4 py-3 text-left transition-colors',
                    selectedId === post.id
                      ? 'border-primary-300 bg-primary-50/70'
                      : 'border-surface-200 bg-surface-0 hover:border-surface-300 hover:bg-surface-50',
                  )}
                >
                  <div className="flex items-start justify-between gap-3">
                    <div className="min-w-0">
                      <p className="line-clamp-2 text-sm font-medium text-surface-900">
                        {post.body.trim() || 'Untitled community draft'}
                      </p>
                      <p className="mt-1 text-xs text-surface-500">
                        {post.post_mode === 'schedule'
                          ? `Scheduled · ${formatDateTime(post.schedule_at)}`
                          : `Updated · ${formatDateTime(post.updated_at)}`}
                      </p>
                    </div>
                    <span className={cn('rounded-full border px-2 py-1 text-[11px] font-semibold uppercase', statusTone(post.status))}>
                      {post.status}
                    </span>
                  </div>
                </button>
              ))}
            </div>
          )}
        </div>
      </div>

      <div className="flex min-w-0 flex-1 flex-col bg-surface-50">
        <div className="border-b border-surface-200 bg-surface-0 px-6 py-4">
          <div className="flex items-center justify-between gap-3">
            <div>
              <h2 className="text-sm font-semibold text-surface-900">
                {selectedPost ? `Post #${selectedPost.id}` : 'Community Composer'}
              </h2>
              <p className="mt-1 text-xs text-surface-500">{status}</p>
            </div>
            {selectedPost && (
              <span className={cn('rounded-full border px-2 py-1 text-[11px] font-semibold uppercase', statusTone(selectedPost.status))}>
                {selectedPost.status}
              </span>
            )}
          </div>
        </div>

        <div className="grid min-h-0 flex-1 gap-0 xl:grid-cols-[minmax(0,1fr)_320px]">
          <div className="min-h-0 overflow-y-auto p-6">
            <div className="card space-y-4 p-5">
              <div>
                <label className="label">Channel URL *</label>
                <input
                  className="input"
                  placeholder="https://www.youtube.com/@your-channel"
                  value={form.channelUrl}
                  onChange={(event) => setForm((prev) => ({ ...prev, channelUrl: event.target.value }))}
                />
              </div>

              <div>
                <label className="label">Source child</label>
                <select
                  className="input"
                  value={form.childProjectId}
                  onChange={(event) => setForm((prev) => ({ ...prev, childProjectId: event.target.value }))}
                >
                  <option value="">None</option>
                  {children.map((child) => (
                    <option key={child.id} value={child.id}>
                      {child.name}
                    </option>
                  ))}
                </select>
              </div>

              <div>
                <label className="label">Body *</label>
                <textarea
                  className="textarea min-h-[220px]"
                  placeholder="Write the Community post body..."
                  value={form.body}
                  onChange={(event) => setForm((prev) => ({ ...prev, body: event.target.value }))}
                />
              </div>

              <div>
                <label className="label">Image</label>
                <div className="flex gap-2">
                  <input
                    className="input flex-1 font-mono text-xs"
                    placeholder="Choose an image from Media Library"
                    value={form.imagePath}
                    readOnly
                  />
                  <button className="btn-secondary h-10 px-3 text-xs" onClick={handlePickImage} type="button">
                    <ImagePlus size={14} />
                    Choose
                  </button>
                </div>
              </div>

              <div className="grid gap-4 md:grid-cols-2">
                <div>
                  <label className="label">Post mode</label>
                  <select
                    className="input"
                    value={form.postMode}
                    onChange={(event) => setForm((prev) => ({ ...prev, postMode: event.target.value as 'now' | 'schedule' }))}
                  >
                    <option value="now">Post now</option>
                    <option value="schedule">Schedule in app</option>
                  </select>
                </div>
                <div>
                  <label className="label">Schedule at</label>
                  <input
                    className="input"
                    type="datetime-local"
                    disabled={form.postMode !== 'schedule'}
                    value={form.scheduleAt}
                    onChange={(event) => setForm((prev) => ({ ...prev, scheduleAt: event.target.value }))}
                  />
                </div>
              </div>

              <div className="flex flex-wrap gap-2 pt-1">
                <button className="btn-secondary h-10 px-4 text-sm" onClick={handleSave} disabled={busy}>
                  {busy ? <Loader2 size={14} className="animate-spin" /> : <RefreshCw size={14} />}
                  Save Draft
                </button>
                <button className="btn-secondary h-10 px-4 text-sm" onClick={handleQueue} disabled={busy}>
                  <Clock3 size={14} />
                  Queue
                </button>
                <button className="btn-primary h-10 px-4 text-sm" onClick={handlePublishNow} disabled={busy}>
                  <Send size={14} />
                  Publish Now
                </button>
                {selectedPost?.status === 'failed' && (
                  <button className="btn-secondary h-10 px-4 text-sm" onClick={handleRetry} disabled={busy}>
                    <History size={14} />
                    Retry
                  </button>
                )}
              </div>

              {error && (
                <div className="rounded-2xl border border-rose-500/20 bg-rose-500/10 px-4 py-3 text-sm text-rose-300">
                  <div className="flex items-start gap-2">
                    <AlertCircle size={16} className="mt-0.5 shrink-0" />
                    <span>{error}</span>
                  </div>
                </div>
              )}
            </div>
          </div>

          <aside className="min-h-0 overflow-y-auto border-l border-surface-200 bg-surface-0 p-5">
            <div className="space-y-5">
              <section>
                <h3 className="text-sm font-semibold text-surface-900">Run summary</h3>
                <div className="mt-3 space-y-2 text-sm text-surface-600">
                  <div className="flex items-center justify-between">
                    <span>Status</span>
                    <span className="font-medium text-surface-900">{selectedPost?.status ?? 'draft'}</span>
                  </div>
                  <div className="flex items-center justify-between">
                    <span>Attempts</span>
                    <span className="font-medium text-surface-900">{selectedPost?.attempt_count ?? 0}</span>
                  </div>
                  <div className="flex items-center justify-between">
                    <span>Published</span>
                    <span className="font-medium text-surface-900">{formatDateTime(selectedPost?.published_at)}</span>
                  </div>
                </div>
                {selectedPost?.youtube_post_url && (
                  <a
                    href={selectedPost.youtube_post_url}
                    target="_blank"
                    rel="noreferrer"
                    className="mt-3 inline-flex items-center gap-2 text-xs font-semibold text-primary-700"
                  >
                    <CheckCircle2 size={14} />
                    Open published post
                  </a>
                )}
              </section>

              <section>
                <h3 className="text-sm font-semibold text-surface-900">Logs</h3>
                <div className="mt-3 rounded-2xl border border-surface-200 bg-surface-950 p-3 text-xs text-slate-200">
                  {logs.length === 0 ? (
                    <p className="text-slate-400">No logs yet.</p>
                  ) : (
                    <div className="space-y-2">
                      {logs.map((entry, index) => (
                        <div key={`${entry.ts}-${index}`}>
                          <p className="font-medium text-slate-100">[{entry.ts}] {entry.level}</p>
                          <p className="mt-0.5 whitespace-pre-wrap text-slate-300">{entry.message}</p>
                        </div>
                      ))}
                    </div>
                  )}
                </div>
              </section>
            </div>
          </aside>
        </div>
      </div>
    </div>
  )
}
