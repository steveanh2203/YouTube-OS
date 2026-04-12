import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import * as Dialog from '@radix-ui/react-dialog'
import { useAppStore } from '@/store/app.store'
import { usePanelContext } from '@/contexts/PanelContext'
import { useExtensionSocket } from '@/hooks/useExtensionSocket'
import { taskStore } from '@/store/task.store'
import { toast } from '@/store/toast.store'
import { cn } from '@/lib/utils'
import { youtubeReplyApi, type YouTubeCommentItem, type YouTubeCommentReplyItem, type YouTubeConfigMapResponse, type YouTubeQuotaSnapshot } from '@/lib/api'
import {
  buildEffectiveSystemPrompt,
  DEFAULT_MODEL,
  fromApiYouTubeAIReplySettings,
  fromApiYouTubeConfig,
  sanitizeYouTubeAIReplySettings,
  sanitizeYouTubeReplyConfig,
  toApiYouTubeConfig,
  type YouTubeAIReplySettings,
  type YouTubeReplyConfig,
} from '@/lib/youtubeReply'
import {
  Bot,
  CheckCircle,
  Link2,
  Loader,
  MessageSquareReply,
  RefreshCw,
  Search,
  Send,
  SkipForward,
  Sparkles,
  X,
} from 'lucide-react'

// ── Types ──────────────────────────────────────────────────────────────────────

type CommentFilter = 'all' | 'new' | 'ready' | 'review' | 'replied' | 'skipped'
type CommentActionState = 'drafted' | 'replied' | 'skipped'
type AutoReplyDecision = 'safe' | 'review'
type AutoReplySummary = {
  processed: number
  drafted: number
  replied: number
  skipped: number
  review: number
  failed: number
}

type ReplyCenterState = {
  comments: YouTubeCommentItem[]
  drafts: Record<string, string>
  actionById: Record<string, CommentActionState>
  nextPageToken: string | null
  selectedCommentId: string | null
  lastSyncedAt: string | null
}

const EMPTY_CENTER_STATE: ReplyCenterState = {
  comments: [],
  drafts: {},
  actionById: {},
  nextPageToken: null,
  selectedCommentId: null,
  lastSyncedAt: null,
}

// ── Utilities ──────────────────────────────────────────────────────────────────

function formatRelativeTime(value?: string | null): string {
  if (!value) return ''
  const date = new Date(value)
  if (isNaN(date.getTime())) return value
  const diff = Date.now() - date.getTime()
  const mins = Math.floor(diff / 60000)
  if (mins < 1) return 'just now'
  if (mins < 60) return `${mins}m ago`
  const hours = Math.floor(mins / 60)
  if (hours < 24) return `${hours}h ago`
  const days = Math.floor(hours / 24)
  if (days < 7) return `${days}d ago`
  return date.toLocaleDateString()
}

function formatSyncLabel(value?: string | null): string {
  if (!value) return 'Not synced'
  const date = new Date(value)
  if (isNaN(date.getTime())) return 'Not synced'
  const diff = Date.now() - date.getTime()
  const mins = Math.floor(diff / 60000)
  if (mins < 1) return 'Synced just now'
  if (mins < 60) return `Synced ${mins}m ago`
  const hours = Math.floor(mins / 60)
  return `Synced ${hours}h ago`
}

function getErrorMessage(error: unknown, fallback: string) {
  return error instanceof Error ? error.message : fallback
}

function formatQuotaPill(value?: YouTubeQuotaSnapshot | null): string {
  if (!value) return 'Quota --'
  return `Quota ${value.estimated_units_used}/${Math.round(value.daily_limit / 1000)}k`
}

function getCommentState(
  item: YouTubeCommentItem,
  draft: string,
  actionState?: CommentActionState,
): Exclude<CommentFilter, 'all' | 'review'> {
  if (actionState === 'replied' || item.reply_count > 0) return 'replied'
  if (actionState === 'skipped' || !item.can_auto_reply) return 'skipped'
  if (draft.trim()) return 'ready'
  return 'new'
}

function uniqueSeedLines(source: string[]) {
  const seen = new Set<string>()
  const lines: string[] = []
  for (const block of source) {
    for (const line of block.split('\n')) {
      const cleaned = line.trim()
      if (!cleaned) continue
      const key = cleaned.toLowerCase()
      if (seen.has(key)) continue
      seen.add(key)
      lines.push(cleaned)
      if (lines.length >= 8) return lines
    }
  }
  return lines
}

function inferSentiment(text: string): 'positive' | 'negative' | 'question' | 'neutral' {
  const normalized = text.toLowerCase()
  const positiveTerms = ['thank', 'thanks', 'love', 'great', 'helpful', 'amazing', 'good', 'awesome', 'perfect']
  const negativeTerms = ['hate', 'terrible', 'stupid', 'fake', 'awful', 'worst', 'scam', 'idiot', 'useless']
  if (normalized.includes('?')) return 'question'
  if (negativeTerms.some((t) => normalized.includes(t))) return 'negative'
  if (positiveTerms.some((t) => normalized.includes(t))) return 'positive'
  return 'neutral'
}

function getAutoReplyDecision(text: string): AutoReplyDecision {
  const normalized = text.toLowerCase()
  const sensitiveTerms = [
    'diagnose', 'diagnosis', 'medicine', 'medication', 'prescribe', 'prescription',
    'dosage', 'side effect', 'treatment', 'cure', 'disease', 'symptom', 'pain',
    'doctor', 'hospital', 'supplement', 'pill', 'blood pressure', 'diabetes',
  ]
  if (normalized.includes('?')) return 'review'
  if (inferSentiment(text) !== 'neutral' && inferSentiment(text) !== 'positive') return 'review'
  if (sensitiveTerms.some((t) => normalized.includes(t))) return 'review'
  if (normalized.length > 220) return 'review'
  return 'safe'
}

function emptyAutoReplySummary(): AutoReplySummary {
  return { processed: 0, drafted: 0, replied: 0, skipped: 0, review: 0, failed: 0 }
}

function createOptimisticReplyItem(
  replyText: string,
  authorName: string,
  replyId?: string,
): YouTubeCommentReplyItem {
  const now = new Date().toISOString()
  return {
    reply_id: replyId?.trim() || `local-reply-${Date.now()}`,
    author_display_name: authorName.trim() || 'Channel',
    author_channel_id: null,
    text: replyText.trim(),
    published_at: now,
    updated_at: now,
    is_from_channel_owner: true,
  }
}

function appendReplyThread(
  current: YouTubeCommentReplyItem[],
  next: YouTubeCommentReplyItem,
): YouTubeCommentReplyItem[] {
  if (current.some((item) => item.reply_id === next.reply_id)) return current
  return [...current, next]
}

// ── Avatar ─────────────────────────────────────────────────────────────────────

const AVATAR_HUES = ['indigo', 'teal', 'violet', 'amber', 'rose', 'sky'] as const
type AvatarHue = typeof AVATAR_HUES[number]

const AVATAR_CLS: Record<AvatarHue, string> = {
  indigo: 'bg-indigo-100 text-indigo-700',
  teal:   'bg-teal-100 text-teal-700',
  violet: 'bg-violet-100 text-violet-700',
  amber:  'bg-amber-100 text-amber-700',
  rose:   'bg-rose-100 text-rose-700',
  sky:    'bg-sky-100 text-sky-700',
}

function getAvatarHue(name: string): AvatarHue {
  const sum = name.split('').reduce((acc, c) => acc + c.charCodeAt(0), 0)
  return AVATAR_HUES[sum % AVATAR_HUES.length]
}

function AvatarCircle({ name, size = 'sm' }: { name: string; size?: 'sm' | 'md' }) {
  const initials = name.split(' ').slice(0, 2).map((w) => w[0]?.toUpperCase() ?? '').join('') || '?'
  return (
    <div className={cn(
      'flex shrink-0 items-center justify-center rounded-full font-semibold',
      size === 'sm' ? 'h-8 w-8 text-xs' : 'h-9 w-9 text-[13px]',
      AVATAR_CLS[getAvatarHue(name)],
    )}>
      {initials}
    </div>
  )
}

// ── Filter tabs config ─────────────────────────────────────────────────────────

const FILTER_TABS: { key: CommentFilter; label: string }[] = [
  { key: 'all',     label: 'All' },
  { key: 'new',     label: 'New' },
  { key: 'ready',   label: 'Drafted' },
  { key: 'review',  label: 'Review' },
  { key: 'replied', label: 'Replied' },
  { key: 'skipped', label: 'Skipped' },
]

// ── Main component ─────────────────────────────────────────────────────────────

export default function ReplyCenter() {
  const { parentProjects, childProjects, setPanelMainView } = useAppStore()
  const { panelId, selectedParentId, selectParent } = usePanelContext()

  // Core state
  const [configs, setConfigs] = useState<Record<string, YouTubeReplyConfig>>({})
  const [aiSettings, setAiSettings] = useState<YouTubeAIReplySettings>(sanitizeYouTubeAIReplySettings())
  const [centerStateByParent, setCenterStateByParent] = useState<Record<string, ReplyCenterState>>({})
  const [quota, setQuota] = useState<YouTubeQuotaSnapshot | null>(null)
  const [draftingId, setDraftingId] = useState<string | null>(null)
  const [postingId, setPostingId] = useState<string | null>(null)
  const [filter, setFilter] = useState<CommentFilter>('all')
  const [query, setQuery] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [replyHistoryCommentId, setReplyHistoryCommentId] = useState<string | null>(null)
  const [savingMode, setSavingMode] = useState(false)
  const [isSyncing, setIsSyncing] = useState(false)
  const [showAiAutoConfirm, setShowAiAutoConfirm] = useState(false)
  const [focusedIndex, setFocusedIndex] = useState(-1)
  const [autoRefreshEnabled] = useState(true)
  const [pollMinutes] = useState(2)

  const inboxRequestRef = useRef(false)
  const autoProcessingIdsRef = useRef(new Set<string>())
  const textareaRef = useRef<HTMLTextAreaElement>(null)

  // ── Load configs ──────────────────────────────────────────────────────────────

  const loadConfigs = useCallback(async () => {
    try {
      const res: YouTubeConfigMapResponse = await youtubeReplyApi.configs()
      setConfigs(
        Object.fromEntries(
          Object.entries(res.items).map(([id, cfg]) => [id, fromApiYouTubeConfig(cfg)]),
        ),
      )
    } catch (err: unknown) {
      setError(getErrorMessage(err, 'Cannot load channel connections.'))
    }
  }, [])

  useEffect(() => { void loadConfigs() }, [loadConfigs])

  useEffect(() => {
    void youtubeReplyApi.aiSettings()
      .then((res) => setAiSettings(fromApiYouTubeAIReplySettings(res.settings)))
      .catch((err: unknown) => setError(getErrorMessage(err, 'Cannot load AI reply settings.')))
  }, [])

  useEffect(() => {
    void youtubeReplyApi.quota()
      .then((res) => setQuota(res.quota))
      .catch(() => undefined)
  }, [])

  useExtensionSocket('account_connect', () => {
    void loadConfigs()
    toast.success('YTB Connect', 'Reply Center received a new channel connection.')
  })

  // ── Derived state ─────────────────────────────────────────────────────────────

  const activeParent = useMemo(
    () => parentProjects.find((p) => p.id === selectedParentId) ?? parentProjects[0] ?? null,
    [parentProjects, selectedParentId],
  )
  const activeParentId = activeParent?.id ?? null
  const activeConfig = sanitizeYouTubeReplyConfig(activeParentId ? configs[activeParentId] : undefined)
  const activeState = activeParentId ? (centerStateByParent[activeParentId] ?? EMPTY_CENTER_STATE) : EMPTY_CENTER_STATE

  const voiceContext = useMemo(() => {
    if (!activeParentId) return ''
    return uniqueSeedLines(
      childProjects.filter((c) => c.parentId === activeParentId).map((c) => c.seedingComments ?? ''),
    ).join('\n')
  }, [activeParentId, childProjects])

  const connectedProjectIds = useMemo(() => {
    const ids = new Set<string>()
    for (const [parentId, config] of Object.entries(configs)) {
      const s = sanitizeYouTubeReplyConfig(config)
      if (s.connected && (s.channelId || s.channelName)) ids.add(parentId)
    }
    return ids
  }, [configs])

  const projectOptions = useMemo(() => {
    if (!parentProjects.length) return []
    const connected = parentProjects.filter((p) => connectedProjectIds.has(p.id))
    const disconnected = parentProjects.filter((p) => !connectedProjectIds.has(p.id))
    return connected.length ? [...connected, ...disconnected] : parentProjects
  }, [connectedProjectIds, parentProjects])

  const draftModelCandidates = useMemo(() => {
    const ordered = [aiSettings.primaryModel, ...aiSettings.fallbackModels]
    const seen = new Set<string>()
    return ordered.map((m) => m.trim()).filter((m) => {
      if (!m || seen.has(m)) return false
      seen.add(m)
      return true
    })
  }, [aiSettings.fallbackModels, aiSettings.primaryModel])

  const filteredComments = useMemo(() => {
    const q = query.trim().toLowerCase()
    return activeState.comments.filter((item) => {
      const state = getCommentState(item, activeState.drafts[item.comment_id] ?? '', activeState.actionById[item.comment_id])
      if (filter === 'review') {
        if (state === 'replied' || state === 'skipped') return false
        if (getAutoReplyDecision(item.text) !== 'review') return false
      } else if (filter !== 'all' && state !== filter) {
        return false
      }
      if (!q) return true
      return [item.author_display_name, item.text, item.video_id ?? '', item.video_title ?? '']
        .join(' ').toLowerCase().includes(q)
    })
  }, [activeState, filter, query])

  const selectedCommentId = activeState.selectedCommentId
    ?? filteredComments[0]?.comment_id
    ?? activeState.comments[0]?.comment_id
    ?? null

  const selectedComment = useMemo(
    () => activeState.comments.find((c) => c.comment_id === selectedCommentId) ?? null,
    [activeState.comments, selectedCommentId],
  )
  const replyHistoryComment = useMemo(
    () => activeState.comments.find((c) => c.comment_id === replyHistoryCommentId) ?? null,
    [activeState.comments, replyHistoryCommentId],
  )
  const activeDraft = selectedCommentId ? (activeState.drafts[selectedCommentId] ?? '') : ''
  const selectedCommentState = selectedComment
    ? getCommentState(selectedComment, activeState.drafts[selectedComment.comment_id] ?? '', activeState.actionById[selectedComment.comment_id])
    : null
  const selectedDecision = selectedComment ? getAutoReplyDecision(selectedComment.text) : null

  const commentCounts = useMemo(() => {
    const c = { all: activeState.comments.length, new: 0, ready: 0, replied: 0, skipped: 0 }
    for (const item of activeState.comments) {
      const state = getCommentState(item, activeState.drafts[item.comment_id] ?? '', activeState.actionById[item.comment_id])
      c[state] += 1
    }
    return c
  }, [activeState])

  const reviewQueueCount = useMemo(() => activeState.comments.filter((item) => {
    const state = getCommentState(item, activeState.drafts[item.comment_id] ?? '', activeState.actionById[item.comment_id])
    if (state === 'replied' || state === 'skipped') return false
    return getAutoReplyDecision(item.text) === 'review'
  }).length, [activeState])

  const safeQueueCount = useMemo(() => activeState.comments.filter((item) => {
    const state = getCommentState(item, activeState.drafts[item.comment_id] ?? '', activeState.actionById[item.comment_id])
    if (state === 'replied' || state === 'skipped') return false
    return getAutoReplyDecision(item.text) === 'safe'
  }).length, [activeState])

  const filterCount = (key: CommentFilter): number => {
    if (key === 'review') return reviewQueueCount
    if (key === 'all') return commentCounts.all
    return commentCounts[key] ?? 0
  }

  const hasVerifiedConnection = Boolean(
    activeConfig.connected && (activeConfig.channelId.trim() || activeConfig.channelName.trim()),
  )
  const readyForInbox = Boolean(activeConfig.channelId.trim() && activeConfig.connected)
  const readyForDraft = Boolean(aiSettings.openRouterKey.trim())
  const aiAutoEnabled = activeConfig.autoReplyMode === 'safe-only'

  const syncDotCls = useMemo(() => {
    if (!activeState.lastSyncedAt) return 'bg-surface-300'
    const diff = Date.now() - new Date(activeState.lastSyncedAt).getTime()
    if (diff > 5 * 60 * 1000) return 'bg-amber-400'
    return 'bg-emerald-400'
  }, [activeState.lastSyncedAt])

  // Auto-resize textarea
  useEffect(() => {
    const el = textareaRef.current
    if (!el) return
    el.style.height = 'auto'
    el.style.height = `${Math.min(el.scrollHeight, 240)}px`
  }, [activeDraft])

  // ── Mutations ─────────────────────────────────────────────────────────────────

  const updateCenterState = (parentId: string, updater: (prev: ReplyCenterState) => ReplyCenterState) => {
    setCenterStateByParent((prev) => ({
      ...prev,
      [parentId]: updater(prev[parentId] ?? EMPTY_CENTER_STATE),
    }))
  }

  const selectComment = (commentId: string) => {
    if (!activeParentId) return
    updateCenterState(activeParentId, (prev) => ({ ...prev, selectedCommentId: commentId }))
  }

  const openReplyHistory = useCallback((commentId: string) => {
    setReplyHistoryCommentId(commentId)
  }, [])

  const updateDraft = useCallback((commentId: string, value: string) => {
    if (!activeParentId) return
    updateCenterState(activeParentId, (prev) => ({
      ...prev,
      drafts: { ...prev.drafts, [commentId]: value },
    }))
  }, [activeParentId])

  const markComment = useCallback((commentId: string, action: CommentActionState) => {
    if (!activeParentId) return
    updateCenterState(activeParentId, (prev) => ({
      ...prev,
      actionById: { ...prev.actionById, [commentId]: action },
    }))
  }, [activeParentId])

  // ── Project auto-select ───────────────────────────────────────────────────────

  useEffect(() => {
    const preferred = projectOptions[0]?.id ?? null
    if (!preferred) return
    if (!selectedParentId || !parentProjects.some((p) => p.id === selectedParentId)) {
      selectParent(preferred)
      return
    }
    if (connectedProjectIds.size > 0 && !connectedProjectIds.has(selectedParentId)) {
      selectParent(preferred)
    }
  }, [connectedProjectIds, parentProjects, projectOptions, selectParent, selectedParentId])

  useEffect(() => {
    setReplyHistoryCommentId(null)
  }, [activeParentId])

  // ── Draft generation ──────────────────────────────────────────────────────────

  const requestDraftForComment = useCallback(async (item: YouTubeCommentItem) => {
    const apiKey = aiSettings.openRouterKey.trim()
    if (!apiKey) return { ok: false, reply_text: '', should_skip: false, message: 'OpenRouter key is missing.' }

    let lastMessage = 'Cannot generate draft.'
    for (const model of draftModelCandidates.length ? draftModelCandidates : [DEFAULT_MODEL]) {
      const res = await youtubeReplyApi.draft({
        api_key: apiKey,
        model,
        comment_text: item.text,
        channel_name: activeConfig.channelName.trim() || activeParent?.name || '',
        video_title: item.video_title || item.video_id || '',
        seeding_comments: voiceContext,
        system_prompt: buildEffectiveSystemPrompt(activeConfig),
      })
      if (res.ok) return res
      lastMessage = res.message
      const n = res.message.toLowerCase()
      if (n.includes('auth failed') || n.includes('api key') || n.includes('401')) return res
    }
    return { ok: false, reply_text: '', should_skip: false, message: lastMessage }
  }, [activeConfig, activeParent?.name, aiSettings.openRouterKey, draftModelCandidates, voiceContext])

  const autoProcessComments = useCallback(async (
    items: YouTubeCommentItem[],
    options?: { force?: boolean },
  ): Promise<AutoReplySummary> => {
    const summary = emptyAutoReplySummary()
    if (!activeParentId) return summary
    const force = Boolean(options?.force)
    if (!force && activeConfig.autoReplyMode !== 'safe-only') return summary
    if (!aiSettings.openRouterKey.trim()) return summary

    for (const item of items) {
      if (!item.can_auto_reply || autoProcessingIdsRef.current.has(item.comment_id)) continue
      autoProcessingIdsRef.current.add(item.comment_id)
      summary.processed += 1
      try {
        const draftRes = await requestDraftForComment(item)
        if (!draftRes.ok) { summary.failed += 1; continue }
        if (draftRes.should_skip) {
          summary.skipped += 1
          markComment(item.comment_id, 'skipped')
          updateDraft(item.comment_id, '')
          continue
        }
        summary.drafted += 1
        updateDraft(item.comment_id, draftRes.reply_text)
        markComment(item.comment_id, 'drafted')

        if (getAutoReplyDecision(item.text) !== 'safe') { summary.review += 1; continue }

        const replyRes = await youtubeReplyApi.reply({
          parent_project_id: Number(activeParentId),
          parent_id: item.comment_id,
          video_id: item.video_id ?? '',
          reply_text: draftRes.reply_text,
        })
        if (!replyRes.ok) { summary.failed += 1; continue }
        const optimisticReply = createOptimisticReplyItem(
          draftRes.reply_text,
          activeConfig.channelName.trim() || activeParent?.name || 'Channel',
          replyRes.reply_id,
        )
        summary.replied += 1
        updateCenterState(activeParentId, (prev) => ({
          ...prev,
          comments: prev.comments.map((c) =>
            c.comment_id === item.comment_id
              ? {
                ...c,
                reply_count: c.reply_count + 1,
                can_auto_reply: false,
                replies: appendReplyThread(c.replies, optimisticReply),
              }
              : c,
          ),
          actionById: { ...prev.actionById, [item.comment_id]: 'replied' },
        }))
      } catch {
        summary.failed += 1
      } finally {
        autoProcessingIdsRef.current.delete(item.comment_id)
      }
    }
    return summary
  }, [activeConfig.autoReplyMode, activeConfig.channelName, activeParent?.name, activeParentId, aiSettings.openRouterKey, markComment, requestDraftForComment, updateDraft])

  // ── Load inbox ────────────────────────────────────────────────────────────────

  const loadInbox = useCallback(async (options?: {
    append?: boolean
    silent?: boolean
    reason?: 'manual' | 'initial' | 'poll'
    processAll?: boolean
    forceAutoReply?: boolean
  }) => {
    if (!activeParentId) return { ok: false, message: 'No active project.', autoReplySummary: emptyAutoReplySummary() }
    if (!activeConfig.channelId.trim()) return { ok: false, message: 'No connected channel.', autoReplySummary: emptyAutoReplySummary() }
    if (inboxRequestRef.current) return { ok: false, message: 'Already loading.', autoReplySummary: emptyAutoReplySummary() }

    const append = Boolean(options?.append)
    const silent = Boolean(options?.silent)
    const processAll = Boolean(options?.processAll)
    const forceAutoReply = Boolean(options?.forceAutoReply)
    inboxRequestRef.current = true
    setError(null)

    const taskId = silent ? '' : taskStore.add({
      toolId: 'youtube-reply',
      toolLabel: 'Reply Center',
      label: `Load comments for ${activeParent.name}`,
      targetMainView: 'reply-center',
      targetParentId: activeParent.id,
      targetChildId: null,
    })

    try {
      const res = await youtubeReplyApi.inbox({
        parent_project_id: Number(activeParentId),
        channel_id: activeConfig.channelId.trim(),
        max_results: 20,
        page_token: append ? (activeState.nextPageToken ?? '') : '',
      })

      if (!res.ok) {
        setError(res.message)
        if (!silent && taskId) taskStore.complete(taskId, 'error', res.message)
        return { ok: false, message: res.message, autoReplySummary: emptyAutoReplySummary() }
      }

      const current = activeState
      const merged = [...current.comments]
      const newItems: YouTubeCommentItem[] = []
      const byId = new Map(current.comments.map((c, i) => [c.comment_id, i]))

      for (const item of res.comments) {
        const idx = byId.get(item.comment_id)
        if (idx === undefined) { merged.push(item); newItems.push(item) }
        else merged[idx] = { ...merged[idx], ...item }
      }

      const toProcess = processAll
        ? merged.filter((c) => getCommentState(c, current.drafts[c.comment_id] ?? '', current.actionById[c.comment_id]) === 'new')
        : newItems

      updateCenterState(activeParentId, (prev) => ({
        ...prev,
        comments: merged,
        nextPageToken: res.next_page_token,
        selectedCommentId: prev.selectedCommentId ?? merged[0]?.comment_id ?? null,
        lastSyncedAt: new Date().toISOString(),
      }))

      let autoReplySummary = emptyAutoReplySummary()
      const shouldAutoReply = toProcess.length > 0 && (forceAutoReply || activeConfig.autoReplyMode === 'safe-only')
      if (shouldAutoReply) {
        autoReplySummary = await autoProcessComments(toProcess, { force: forceAutoReply })
      }

      void youtubeReplyApi.quota().then((quotaRes) => setQuota(quotaRes.quota)).catch(() => undefined)

      if (!silent && taskId) taskStore.complete(taskId, 'done', res.message)
      return { ok: true, message: res.message, autoReplySummary }
    } catch (err: unknown) {
      const message = getErrorMessage(err, 'Cannot load comments.')
      setError(message)
      if (!silent && taskId) taskStore.complete(taskId, 'error', message)
      return { ok: false, message, autoReplySummary: emptyAutoReplySummary() }
    } finally {
      inboxRequestRef.current = false
    }
  }, [activeConfig.autoReplyMode, activeConfig.channelId, activeParent, activeParentId, activeState, autoProcessComments])

  // Auto-load + poll
  useEffect(() => {
    if (!activeParentId || !readyForInbox) return
    if (activeState.comments.length > 0 || inboxRequestRef.current) return
    void loadInbox({ append: false, silent: true, reason: 'initial' })
  }, [activeParentId, activeState.comments.length, loadInbox, readyForInbox])

  useEffect(() => {
    if (!activeParentId || !readyForInbox || !autoRefreshEnabled) return
    const intervalMs = Math.max(1, pollMinutes) * 60_000
    const timer = window.setInterval(() => {
      void loadInbox({ append: false, silent: true, reason: 'poll' })
    }, intervalMs)
    return () => window.clearInterval(timer)
  }, [activeParentId, autoRefreshEnabled, loadInbox, pollMinutes, readyForInbox])

  // ── Mode toggle ───────────────────────────────────────────────────────────────

  const updateAutoReplyMode = useCallback(async (nextMode: YouTubeReplyConfig['autoReplyMode']) => {
    if (!activeParentId || nextMode === activeConfig.autoReplyMode) return
    const previousConfig = activeConfig
    const optimistic = sanitizeYouTubeReplyConfig({ ...activeConfig, autoReplyMode: nextMode })
    setSavingMode(true)
    setError(null)
    setConfigs((prev) => ({ ...prev, [activeParentId]: optimistic }))
    try {
      const res = await youtubeReplyApi.saveConfig(activeParentId, toApiYouTubeConfig(optimistic))
      setConfigs((prev) => ({ ...prev, [activeParentId]: fromApiYouTubeConfig(res.config) }))
      if (nextMode === 'safe-only' && readyForInbox) {
        void loadInbox({ append: false, silent: true, reason: 'manual', processAll: true, forceAutoReply: true })
      }
    } catch (err: unknown) {
      setConfigs((prev) => ({ ...prev, [activeParentId]: previousConfig }))
      setError(getErrorMessage(err, 'Cannot save auto reply mode.'))
    } finally {
      setSavingMode(false)
    }
  }, [activeConfig, activeParentId, loadInbox, readyForInbox])

  const handleAiAutoToggle = () => {
    if (aiAutoEnabled) {
      void updateAutoReplyMode('manual')
    } else {
      setShowAiAutoConfirm(true)
    }
  }

  // ── Draft + post ──────────────────────────────────────────────────────────────

  const draftReply = async (item: YouTubeCommentItem) => {
    if (!activeParentId || !aiSettings.openRouterKey.trim()) return
    setDraftingId(item.comment_id)
    setError(null)
    const taskId = taskStore.add({
      toolId: 'youtube-reply',
      toolLabel: 'Reply Center',
      label: `Draft reply for ${item.author_display_name}`,
      targetMainView: 'reply-center',
      targetParentId: activeParentId,
      targetChildId: null,
    })
    try {
      const res = await requestDraftForComment(item)
      if (!res.ok) {
        setError(res.message)
        taskStore.complete(taskId, 'error', res.message)
        return
      }
      if (res.should_skip) {
        markComment(item.comment_id, 'skipped')
        updateDraft(item.comment_id, '')
        taskStore.complete(taskId, 'done', 'Skipped.')
        return
      }
      updateDraft(item.comment_id, res.reply_text)
      markComment(item.comment_id, 'drafted')
      taskStore.complete(taskId, 'done', 'Draft generated.')
    } catch (err: unknown) {
      const message = getErrorMessage(err, 'Cannot generate draft.')
      setError(message)
      taskStore.complete(taskId, 'error', message)
    } finally {
      setDraftingId(null)
    }
  }

  const postReply = async (item: YouTubeCommentItem) => {
    if (!activeParentId) return
    const replyText = (activeState.drafts[item.comment_id] ?? '').trim()
    if (!replyText) return
    setPostingId(item.comment_id)
    setError(null)
    const taskId = taskStore.add({
      toolId: 'youtube-reply',
      toolLabel: 'Reply Center',
      label: `Reply to ${item.author_display_name}`,
      targetMainView: 'reply-center',
      targetParentId: activeParentId,
      targetChildId: null,
    })
    try {
      const res = await youtubeReplyApi.reply({
        parent_project_id: Number(activeParentId),
        parent_id: item.comment_id,
        video_id: item.video_id ?? '',
        reply_text: replyText,
      })
      if (!res.ok) {
        setError(res.message)
        taskStore.complete(taskId, 'error', res.message)
        return
      }
      const optimisticReply = createOptimisticReplyItem(
        replyText,
        activeConfig.channelName.trim() || activeParent?.name || 'Channel',
        res.reply_id,
      )
      updateCenterState(activeParentId, (prev) => ({
        ...prev,
        comments: prev.comments.map((c) =>
          c.comment_id === item.comment_id
            ? {
              ...c,
              reply_count: c.reply_count + 1,
              can_auto_reply: false,
              replies: appendReplyThread(c.replies, optimisticReply),
            }
            : c,
        ),
        actionById: { ...prev.actionById, [item.comment_id]: 'replied' },
      }))
      void youtubeReplyApi.quota().then((quotaRes) => setQuota(quotaRes.quota)).catch(() => undefined)
      toast.success('Reply sent', `Replied to ${item.author_display_name}.`)
      taskStore.complete(taskId, 'done', res.message)
    } catch (err: unknown) {
      const message = getErrorMessage(err, 'Cannot post reply.')
      setError(message)
      taskStore.complete(taskId, 'error', message)
    } finally {
      setPostingId(null)
    }
  }

  const openSettings = () => {
    setPanelMainView(panelId, 'settings')
    toast.info('Open Settings', 'Verify OAuth in Settings, then pair the channel from the extension.')
  }

  const handleManualSync = async () => {
    setIsSyncing(true)
    await loadInbox({ append: false, reason: 'manual' })
    setIsSyncing(false)
  }

  // ── Keyboard nav ──────────────────────────────────────────────────────────────

  const handleListKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === 'ArrowDown') {
      e.preventDefault()
      setFocusedIndex((prev) => Math.min(prev + 1, filteredComments.length - 1))
    } else if (e.key === 'ArrowUp') {
      e.preventDefault()
      setFocusedIndex((prev) => Math.max(prev - 1, 0))
    } else if (e.key === 'Enter' && focusedIndex >= 0) {
      e.preventDefault()
      const c = filteredComments[focusedIndex]
      if (c) selectComment(c.comment_id)
    }
  }

  const handleTextareaKeyDown = (e: React.KeyboardEvent<HTMLTextAreaElement>) => {
    if ((e.metaKey || e.ctrlKey) && e.key === 'Enter' && selectedComment) {
      e.preventDefault()
      void postReply(selectedComment)
    }
  }

  // ── Render ────────────────────────────────────────────────────────────────────

  const isDrafting = selectedComment ? draftingId === selectedComment.comment_id : false
  const isPosting  = selectedComment ? postingId  === selectedComment.comment_id : false
  const workspaceBusy = isDrafting || isPosting

  return (
    <div className="flex h-full flex-col overflow-hidden bg-surface-50">

      {/* AI Auto confirm dialog */}
      <Dialog.Root open={showAiAutoConfirm} onOpenChange={setShowAiAutoConfirm}>
        <Dialog.Portal>
          <Dialog.Overlay className="fixed inset-0 z-50 bg-black/25 backdrop-blur-[2px]" />
          <Dialog.Content className="fixed left-1/2 top-1/2 z-50 w-full max-w-sm -translate-x-1/2 -translate-y-1/2 rounded-xl border border-surface-200 bg-white p-6 shadow-popover animate-fade-in">
            <Dialog.Title className="text-base font-semibold text-surface-900">Enable AI Auto?</Dialog.Title>
            <Dialog.Description className="mt-2 text-sm leading-6 text-surface-600">
              Safe comments (praise, neutral) will be sent automatically. Questions and risky comments stay in your queue for review.
            </Dialog.Description>
            <div className="mt-5 flex justify-end gap-2">
              <Dialog.Close asChild>
                <button className="btn-secondary">Cancel</button>
              </Dialog.Close>
              <button
                className="btn-primary"
                onClick={() => { setShowAiAutoConfirm(false); void updateAutoReplyMode('safe-only') }}
              >
                <Bot size={14} />
                Enable AI Auto
              </button>
            </div>
          </Dialog.Content>
        </Dialog.Portal>
      </Dialog.Root>

      <Dialog.Root open={Boolean(replyHistoryCommentId)} onOpenChange={(open) => { if (!open) setReplyHistoryCommentId(null) }}>
        <Dialog.Portal>
          <Dialog.Overlay className="fixed inset-0 z-50 bg-black/25 backdrop-blur-[2px]" />
          <Dialog.Content className="fixed left-1/2 top-1/2 z-50 flex max-h-[76vh] w-[min(680px,calc(100vw-32px))] -translate-x-1/2 -translate-y-1/2 flex-col overflow-hidden rounded-2xl border border-surface-200 bg-white shadow-popover animate-fade-in">
            {replyHistoryComment && (
              <>
                <div className="flex items-start justify-between gap-4 border-b border-surface-200 px-4 py-3.5">
                  <div>
                    <Dialog.Title className="text-sm font-semibold text-surface-900">
                      Replied conversation
                    </Dialog.Title>
                    <Dialog.Description className="mt-1 text-[12px] leading-5 text-surface-500">
                      Review what was already sent to this viewer.
                    </Dialog.Description>
                  </div>
                  <Dialog.Close asChild>
                    <button className="btn-icon btn-ghost">
                      <X size={14} />
                    </button>
                  </Dialog.Close>
                </div>

                <div className="space-y-3 overflow-y-auto px-4 py-4">
                  <div className="card p-4">
                    <div className="flex items-start gap-3">
                      <AvatarCircle name={replyHistoryComment.author_display_name} size="md" />
                      <div className="min-w-0 flex-1">
                        <div className="flex items-center justify-between gap-2">
                          <p className="text-[13px] font-semibold text-surface-900">
                            {replyHistoryComment.author_display_name}
                          </p>
                          <span className="text-[11px] text-surface-400">
                            {formatRelativeTime(replyHistoryComment.published_at)}
                          </span>
                        </div>
                        {replyHistoryComment.video_title && (
                          <p className="mt-0.5 truncate text-[11px] text-surface-400">
                            {replyHistoryComment.video_title}
                          </p>
                        )}
                        <p className="mt-3 text-[13px] leading-6 text-surface-700">
                          {replyHistoryComment.text}
                        </p>
                      </div>
                    </div>
                  </div>

                  <div className="space-y-3">
                    <div className="flex items-center justify-between gap-2">
                      <p className="text-[11px] font-medium uppercase tracking-[0.12em] text-surface-400">
                        Sent replies
                      </p>
                      <span className="rounded-full bg-surface-100 px-2.5 py-1 text-[11px] font-medium text-surface-600">
                        {replyHistoryComment.replies.length} sent
                      </span>
                    </div>

                    {replyHistoryComment.replies.length === 0 ? (
                      <div className="rounded-2xl border border-dashed border-surface-200 bg-surface-50 px-4 py-5 text-[13px] text-surface-500">
                        No synced reply text yet for this thread.
                      </div>
                    ) : (
                      <div className="space-y-3">
                        {replyHistoryComment.replies.map((reply) => (
                          <div
                            key={reply.reply_id}
                            className={cn(
                              'rounded-2xl border px-4 py-3',
                              reply.is_from_channel_owner
                                ? 'border-primary-100 bg-primary-50/40'
                                : 'border-surface-200 bg-white',
                            )}
                          >
                            <div className="flex items-start gap-3">
                              <AvatarCircle name={reply.author_display_name} />
                              <div className="min-w-0 flex-1">
                                <div className="flex items-center justify-between gap-2">
                                  <div className="flex min-w-0 items-center gap-2">
                                    <p className="truncate text-[13px] font-semibold text-surface-900">
                                      {reply.author_display_name}
                                    </p>
                                    {reply.is_from_channel_owner && (
                                      <span className="rounded-full bg-white px-2 py-0.5 text-[10px] font-medium text-primary-700 ring-1 ring-primary-100">
                                        Sent
                                      </span>
                                    )}
                                  </div>
                                  <span className="text-[11px] text-surface-400">
                                    {formatRelativeTime(reply.published_at)}
                                  </span>
                                </div>
                                <p className="mt-2 text-[13px] leading-6 text-surface-700">
                                  {reply.text}
                                </p>
                              </div>
                            </div>
                          </div>
                        ))}
                      </div>
                    )}
                  </div>
                </div>

                <div className="flex justify-end border-t border-surface-200 px-4 py-3.5">
                  <Dialog.Close asChild>
                    <button className="btn-secondary">Close</button>
                  </Dialog.Close>
                </div>
              </>
            )}
          </Dialog.Content>
        </Dialog.Portal>
      </Dialog.Root>

      {/* ── 2-column layout ── */}
      <div className="flex min-h-0 flex-1 overflow-hidden">

        {/* ── LEFT: Inbox ── */}
        <div className="flex w-[38%] min-w-0 flex-col border-r border-surface-200 bg-white overflow-hidden">

          {/* Inbox header */}
          <div className="border-b border-surface-200 px-4 py-3">
            <div className="flex items-center gap-2">
              {/* Channel selector */}
              <div className="flex min-w-0 flex-1 items-center gap-2">
                <MessageSquareReply size={13} className="shrink-0 text-surface-400" />
                <select
                  className="min-w-0 flex-1 truncate bg-transparent text-sm font-semibold text-surface-900 outline-none cursor-pointer"
                  value={activeParentId ?? ''}
                  onChange={(e) => selectParent(e.target.value || null)}
                >
                  {projectOptions.length === 0 && <option value="">No project</option>}
                  {projectOptions.map((p) => {
                    const cfg = sanitizeYouTubeReplyConfig(configs[p.id])
                    return (
                      <option key={p.id} value={p.id}>
                        {p.name}{cfg.channelName ? ` · ${cfg.channelName}` : ''}
                      </option>
                    )
                  })}
                </select>
              </div>

              {/* AI Auto toggle */}
              <button
                className={cn(
                  'flex shrink-0 items-center gap-1.5 rounded-full px-2.5 py-1 text-[11px] font-semibold transition-colors duration-150 cursor-pointer',
                  aiAutoEnabled
                    ? 'bg-emerald-50 text-emerald-700 hover:bg-emerald-100'
                    : 'bg-surface-100 text-surface-500 hover:bg-surface-200',
                  (savingMode || !activeParentId) && 'cursor-not-allowed opacity-50',
                )}
                disabled={savingMode || !activeParentId}
                onClick={handleAiAutoToggle}
              >
                <Bot size={11} />
                AI Auto
              </button>

              <span className="inline-flex shrink-0 items-center rounded-full bg-surface-100 px-2.5 py-1 text-[11px] font-medium text-surface-600">
                {formatQuotaPill(quota)}
              </span>

              {/* Sync */}
              <button
                className="btn-icon btn-ghost"
                title="Sync inbox"
                disabled={!readyForInbox}
                onClick={() => void handleManualSync()}
              >
                <RefreshCw size={13} className={cn('transition-transform', isSyncing && 'animate-spin')} />
              </button>
            </div>
          </div>

          {/* Filter tabs */}
          <div className="border-b border-surface-200 px-3 py-2">
            <div className="flex items-center gap-1 overflow-x-auto pb-[3px]">
              {FILTER_TABS.map((tab) => {
                const count = filterCount(tab.key)
                const active = filter === tab.key
                return (
                  <button
                    key={tab.key}
                    className={cn(
                      'flex shrink-0 items-center gap-1 rounded-full px-2.5 py-1 text-[11px] font-medium transition-colors duration-150 cursor-pointer',
                      active
                        ? 'bg-primary-500 text-white'
                        : 'bg-surface-100 text-surface-600 hover:bg-surface-200',
                    )}
                    onClick={() => setFilter(tab.key)}
                  >
                    {tab.label}
                    {count > 0 && (
                      <span className={cn(
                        'rounded-full px-1.5 text-[10px] font-semibold',
                        active ? 'bg-white/25 text-white' : 'bg-surface-200 text-surface-600',
                      )}>
                        {count}
                      </span>
                    )}
                  </button>
                )
              })}
            </div>
          </div>

          {/* Search */}
          <div className="border-b border-surface-200 px-3 py-2">
            <div className="relative">
              <Search size={12} className="pointer-events-none absolute left-2.5 top-1/2 -translate-y-1/2 text-surface-400" />
              <input
                className="input py-1.5 pl-8 text-xs"
                placeholder="Search author or comment..."
                value={query}
                onChange={(e) => setQuery(e.target.value)}
              />
            </div>
          </div>

          {/* Comment list */}
          {!hasVerifiedConnection ? (
            <div className="flex flex-1 flex-col items-center justify-center px-6 py-10 text-center">
              <div className="mb-3 flex h-10 w-10 items-center justify-center rounded-xl bg-surface-100">
                <Link2 size={18} className="text-surface-400" />
              </div>
              <p className="text-sm font-semibold text-surface-900">No channel connected</p>
              <p className="mt-1 text-xs leading-5 text-surface-500">
                Connect your YouTube channel to start receiving comments.
              </p>
              <button className="btn-primary mt-4 text-xs" onClick={openSettings}>
                Open Settings
              </button>
            </div>
          ) : (
            <div
              className="flex-1 overflow-y-auto p-2 outline-none"
              tabIndex={0}
              onKeyDown={handleListKeyDown}
            >
              {filteredComments.length === 0 ? (
                <div className="flex min-h-[200px] flex-col items-center justify-center px-4 text-center">
                  <CheckCircle size={20} className="mb-2 text-emerald-300" />
                  <p className="text-sm font-semibold text-surface-900">All caught up</p>
                  <p className="mt-1 text-xs text-surface-500">No comments in this filter.</p>
                  {filter !== 'all' && (
                    <button className="btn-ghost mt-3 text-xs" onClick={() => setFilter('all')}>
                      View all
                    </button>
                  )}
                </div>
              ) : (
                <div className="space-y-1.5">
                  {filteredComments.map((item, idx) => {
                    const state = getCommentState(item, activeState.drafts[item.comment_id] ?? '', activeState.actionById[item.comment_id])
                    const sentiment = inferSentiment(item.text)
                    const isSelected = selectedComment?.comment_id === item.comment_id
                    const isFocused = focusedIndex === idx

                    const STATE_CLS: Record<typeof state, string> = {
                      new:     'bg-sky-50 text-sky-700',
                      ready:   'bg-violet-50 text-violet-700',
                      replied: 'bg-emerald-50 text-emerald-700',
                      skipped: 'bg-amber-50 text-amber-700',
                    }

                    return (
                      <button
                        key={item.comment_id}
                        className={cn(
                          'w-full rounded-xl border px-3 py-2.5 text-left transition-all duration-150 cursor-pointer',
                          isSelected
                            ? 'border-primary-300 bg-primary-50 shadow-card'
                            : isFocused
                            ? 'border-primary-200 bg-primary-50/50'
                            : 'border-surface-200 bg-white hover:border-surface-300 hover:bg-surface-50',
                          isFocused && 'ring-2 ring-primary-400 ring-offset-1',
                        )}
                        onClick={() => {
                          selectComment(item.comment_id)
                          setFocusedIndex(idx)
                          if (state === 'replied') openReplyHistory(item.comment_id)
                        }}
                      >
                        {/* Row 1: avatar + author + time */}
                        <div className="flex items-start gap-2.5">
                          <AvatarCircle name={item.author_display_name} />
                          <div className="min-w-0 flex-1">
                            <div className="flex items-center justify-between gap-1.5">
                              <p className="truncate text-[13px] font-semibold text-surface-900">
                                {item.author_display_name}
                              </p>
                              <span className="shrink-0 text-[11px] text-surface-400">
                                {formatRelativeTime(item.published_at)}
                              </span>
                            </div>
                            {/* Row 2: comment text */}
                            <p className="mt-0.5 line-clamp-2 text-[12px] leading-[18px] text-surface-600">
                              {item.text}
                            </p>
                            {/* Row 3: video title */}
                            {item.video_title && (
                              <p className="mt-1 truncate text-[11px] text-surface-400">
                                {item.video_title}
                              </p>
                            )}
                            {/* Row 4: badges (max 2) */}
                            <div className="mt-1.5 flex items-center gap-1.5">
                              <span className={cn('rounded-full px-2 py-0.5 text-[10px] font-medium', STATE_CLS[state])}>
                                {state}
                              </span>
                              {(sentiment === 'negative' || sentiment === 'question') && (
                                <span className={cn(
                                  'rounded-full px-2 py-0.5 text-[10px] font-medium',
                                  sentiment === 'question' ? 'bg-amber-50 text-amber-700' : 'bg-red-50 text-red-600',
                                )}>
                                  {sentiment}
                                </span>
                              )}
                            </div>
                          </div>
                        </div>
                      </button>
                    )
                  })}
                </div>
              )}
            </div>
          )}
        </div>

        {/* ── RIGHT: Reply Workspace ── */}
        <div className="flex min-w-0 flex-1 flex-col overflow-hidden">
          {!selectedComment ? (
            // Empty state
            <div className="flex flex-1 flex-col items-center justify-center px-8 text-center">
              <MessageSquareReply size={30} className="mb-3 text-surface-300" />
              <p className="text-sm font-semibold text-surface-900">Select a comment to reply</p>
              <p className="mt-1 text-xs leading-5 text-surface-500">
                {aiAutoEnabled
                  ? 'Or let AI Auto handle safe comments from the queue.'
                  : 'Pick a comment from the inbox to write or generate a reply.'}
              </p>
            </div>
          ) : (
            <div className={cn(
              'flex flex-1 flex-col overflow-y-auto p-5 transition-opacity duration-200',
              workspaceBusy && 'pointer-events-none opacity-60',
            )}>

              {/* Original comment */}
              <div className="card p-4">
                <div className="flex items-start gap-3">
                  <AvatarCircle name={selectedComment.author_display_name} size="md" />
                  <div className="min-w-0 flex-1">
                    <div className="flex items-center justify-between gap-2">
                      <p className="text-[13px] font-semibold text-surface-900">
                        {selectedComment.author_display_name}
                      </p>
                      <span className="text-[11px] text-surface-400">
                        {formatRelativeTime(selectedComment.published_at)}
                      </span>
                    </div>
                    {selectedComment.video_title && (
                      <p className="mt-0.5 text-[11px] text-surface-400 truncate">
                        {selectedComment.video_title}
                      </p>
                    )}
                  </div>
                </div>
                <p className="mt-3 text-[13px] leading-6 text-surface-700">{selectedComment.text}</p>

                {/* Decision chip in workspace */}
                {selectedDecision && (
                  <div className="mt-3 flex items-center gap-2">
                    <span className={cn(
                      'rounded-full px-2.5 py-1 text-[11px] font-medium',
                      selectedDecision === 'safe'
                        ? 'bg-emerald-50 text-emerald-700'
                        : 'bg-amber-50 text-amber-700',
                    )}>
                      {selectedDecision === 'safe' ? 'Auto-safe' : 'Needs review'}
                    </span>
                    {selectedCommentState && (
                      <span className={cn(
                        'rounded-full px-2.5 py-1 text-[11px] font-medium',
                        selectedCommentState === 'replied' ? 'bg-emerald-50 text-emerald-700'
                        : selectedCommentState === 'ready' ? 'bg-violet-50 text-violet-700'
                        : selectedCommentState === 'skipped' ? 'bg-amber-50 text-amber-700'
                        : 'bg-sky-50 text-sky-700',
                      )}>
                        {selectedCommentState}
                      </span>
                    )}
                  </div>
                )}
                {selectedCommentState === 'replied' && (
                  <button
                    className="mt-3 inline-flex items-center gap-2 rounded-full border border-surface-200 bg-surface-50 px-3 py-1.5 text-[11px] font-medium text-surface-600 transition-colors hover:border-primary-200 hover:bg-primary-50 hover:text-primary-700 cursor-pointer"
                    onClick={() => openReplyHistory(selectedComment.comment_id)}
                  >
                    <MessageSquareReply size={12} />
                    View replied thread
                  </button>
                )}
              </div>

              {/* Reply textarea */}
              <div className="mt-4">
                <label className="label">Your Reply</label>
                <textarea
                  ref={textareaRef}
                  className="textarea min-h-[96px] animate-fade-in"
                  value={activeDraft}
                  placeholder="Write a reply or generate with AI..."
                  onChange={(e) => updateDraft(selectedComment.comment_id, e.target.value)}
                  onKeyDown={handleTextareaKeyDown}
                />
                {activeDraft.trim() && (
                  <p className="mt-1 text-[11px] text-surface-400">
                    Cmd+Enter to send
                  </p>
                )}
              </div>

              {/* Error */}
              {error && (
                <p className="mt-3 rounded-lg bg-red-50 px-3 py-2 text-xs text-red-600">{error}</p>
              )}

              {/* Actions */}
              <div className="mt-4 flex flex-wrap items-center gap-2">
                <button
                  className="btn-primary"
                  disabled={!readyForDraft || isDrafting}
                  onClick={() => void draftReply(selectedComment)}
                >
                  {isDrafting
                    ? <Loader size={14} className="animate-spin" />
                    : <Sparkles size={14} />}
                  {isDrafting ? 'Generating…' : 'Generate Draft'}
                </button>

                <button
                  className="btn-secondary"
                  disabled={!readyForInbox || !activeDraft.trim() || isPosting}
                  onClick={() => void postReply(selectedComment)}
                >
                  {isPosting
                    ? <Loader size={14} className="animate-spin" />
                    : <Send size={14} />}
                  {isPosting ? 'Sending…' : 'Send Reply'}
                </button>

                <button
                  className="btn-ghost text-surface-500"
                  onClick={() => markComment(selectedComment.comment_id, 'skipped')}
                >
                  <SkipForward size={14} />
                  Skip
                </button>
              </div>

              {!readyForDraft && (
                <p className="mt-3 text-xs text-surface-500">
                  Add an OpenRouter key in{' '}
                  <button className="underline hover:text-surface-700 cursor-pointer" onClick={openSettings}>
                    Settings
                  </button>{' '}
                  to enable AI drafts.
                </p>
              )}
            </div>
          )}
        </div>
      </div>

      {/* ── Status bar (full-width) ── */}
      <div className="flex items-center gap-3 border-t border-surface-200 bg-surface-50 px-4 py-1.5 text-[11px] text-surface-500">
        <span className={cn('h-1.5 w-1.5 shrink-0 rounded-full', syncDotCls)} />
        <span>{formatSyncLabel(activeState.lastSyncedAt)}</span>
        {(safeQueueCount > 0 || reviewQueueCount > 0) && (
          <>
            <span className="text-surface-300">·</span>
            <span>{safeQueueCount} auto-safe · {reviewQueueCount} review</span>
          </>
        )}
        {activeConfig.channelName && (
          <>
            <span className="text-surface-300">·</span>
            <span className="truncate">{activeConfig.channelName}</span>
          </>
        )}
      </div>
    </div>
  )
}
