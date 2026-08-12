import { useState, useEffect, useCallback } from 'react'
import { motion, AnimatePresence } from 'framer-motion'
import { useAppStore, type ChildProject, type ChildStatus, type Competitor as StoreCompetitor, type PlannerPriority, type PlannerStage } from '@/store/app.store'
import { usePanelContext } from '@/contexts/PanelContext'
import {
  Check, FolderOpen, Loader, Search, FolderPlus, X, AlertTriangle,
  ClipboardList, CheckCircle2, Circle, Type, AlignLeft, MessageSquare,
  ChevronRight, Pencil, Plus, XCircle,
} from 'lucide-react'
import { cn } from '@/lib/utils'
import { pickWorkspaceDirectory } from '@/lib/browserPickers'
import { toast } from '@/store/toast.store'
import { resolveYoutubeMetadata } from '@/pages/Competitors/utils'

const API = ''

// ---------------------------------------------------------------------------
// Helper: map API response → ChildProject
// ---------------------------------------------------------------------------

function mapChild(raw: Record<string, unknown>, parentId: string): ChildProject {
  return {
    id: String(raw.id),
    parentId,
    name: (raw.display_name as string) ?? `Video ${raw.video_number}`,
    status: (raw.status as ChildStatus) ?? 'draft',
    title: (raw.title as string) ?? '',
    description: (raw.description as string) ?? '',
    seedingComments: (raw.seeding_comments as string) ?? '',
    folderPath: (raw.base_folder_path as string) ?? '',
    planningStage: (raw.planning_stage as PlannerStage) ?? ((raw.status as ChildStatus) === 'published' ? 'published' : (raw.status as ChildStatus) === 'editing' ? 'editing' : (raw.status as ChildStatus) === 'resource_prep' ? 'ready' : 'backlog'),
    priority: (raw.priority as PlannerPriority) ?? 'medium',
    deadline: raw.deadline ? String(raw.deadline) : null,
    planningNote: (raw.planning_note as string) ?? '',
    roxyWorkspaceId: (raw.roxy_workspace_id as number) ?? null,
    roxyProfileId: (raw.roxy_profile_id as string) ?? '',
    roxyProfileName: (raw.roxy_profile_name as string) ?? '',
    prepTitleDone: (raw.prep_title_done as boolean) ?? false,
    prepDescDone: (raw.prep_desc_done as boolean) ?? false,
    prepSeedDone: (raw.prep_seed_done as boolean) ?? false,
    prepFolderDone: (raw.prep_folder_done as boolean) ?? false,
    createdAt: raw.created_at ? String(raw.created_at).slice(0, 10) : '',
  }
}

interface CompetitorItem {
  id: string
  url: string
  title: string
  videoId: string
  childProjectId: string | null
  parentProjectId: string | null
  thumbnailUrl: string
  normalizedUrl: string
  channel: string
  purpose: string
  notes: string
  createdAt: string
}

function mapCompetitor(raw: Record<string, unknown>): CompetitorItem {
  return {
    id: String(raw.id),
    url: (raw.url as string) ?? '',
    title: (raw.title as string) ?? '',
    videoId: (raw.video_id as string) ?? '',
    childProjectId: raw.child_project_id != null ? String(raw.child_project_id) : null,
    parentProjectId: raw.parent_project_id != null ? String(raw.parent_project_id) : null,
    thumbnailUrl: (raw.thumbnail_url as string) ?? '',
    normalizedUrl: (raw.normalized_url as string) ?? '',
    channel: (raw.channel as string) ?? '',
    purpose: (raw.purpose as string) ?? '',
    notes: (raw.notes as string) ?? '',
    createdAt: raw.created_at ? String(raw.created_at) : '',
  }
}

function mapStoreCompetitor(raw: Record<string, unknown>): StoreCompetitor {
  return {
    id: String(raw.id),
    videoId: (raw.video_id as string) ?? '',
    url: (raw.url as string) ?? '',
    title: (raw.title as string) ?? '',
    channel: (raw.channel as string) ?? '',
    thumbnail: (raw.thumbnail_url as string) ?? '',
    purpose: ((raw.purpose as string) ?? 'reference') as StoreCompetitor['purpose'],
    notes: (raw.notes as string) ?? '',
    childProjectId: raw.child_project_id != null ? String(raw.child_project_id) : null,
    parentProjectId: raw.parent_project_id != null ? String(raw.parent_project_id) : null,
    addedAt: raw.created_at ? String(raw.created_at) : new Date().toISOString(),
  }
}

// ---------------------------------------------------------------------------
// Step config
// ---------------------------------------------------------------------------

const STEPS = [
  { key: 'title',  label: 'Video Title',       icon: Type },
  { key: 'desc',   label: 'Description',        icon: AlignLeft },
  { key: 'seed',   label: 'Seeding Comments',   icon: MessageSquare },
  { key: 'folder', label: 'Resource Folder',    icon: FolderOpen },
] as const

type StepKey = typeof STEPS[number]['key']

// ---------------------------------------------------------------------------
// Sidebar step item
// ---------------------------------------------------------------------------

function StepItem({
  icon: Icon,
  label,
  done,
  active,
  index,
  onClick,
}: {
  icon: React.ElementType
  label: string
  done: boolean
  active: boolean
  index: number
  onClick: () => void
}) {
  return (
    <button
      className={cn(
        'w-full flex items-center gap-3 px-3 py-2.5 rounded-lg text-left transition-[background-color,border-color,color,box-shadow,opacity,transform] duration-150 cursor-pointer',
        active
          ? 'bg-primary-50 text-primary-700'
          : 'text-surface-600 hover:bg-surface-50 hover:text-surface-800',
      )}
      onClick={onClick}
    >
      {/* Circle indicator */}
      <div className={cn(
        'w-6 h-6 rounded-full flex items-center justify-center shrink-0 transition-[background-color,border-color,color,box-shadow,opacity,transform] duration-200 text-xs font-bold',
        done
          ? 'bg-green-500 text-white'
          : active
            ? 'bg-primary-500 text-white'
            : 'bg-surface-200 text-surface-500',
      )}>
        {done ? <Check size={11} strokeWidth={3} /> : index + 1}
      </div>

      <div className="flex-1 min-w-0">
        <div className="flex items-center gap-1.5">
          <Icon size={13} className="shrink-0" />
          <span className="text-xs font-medium truncate">{label}</span>
        </div>
        {done && (
          <p className="text-[10px] text-green-300 mt-0.5">Completed</p>
        )}
      </div>

      {active && <ChevronRight size={13} className="shrink-0 text-primary-400" />}
    </button>
  )
}

// ---------------------------------------------------------------------------
// Main component
// ---------------------------------------------------------------------------

export default function ResourcePrep() {
  const { selectedChildId, selectedParentId, setMainView } = usePanelContext()
  const { updateChild: storeUpdateChild, childProjects, parentProjects, setCompetitors: setStoreCompetitors } = useAppStore()

  const child = childProjects.find(c => c.id === selectedChildId)
  const parent = parentProjects.find(p => p.id === (child?.parentId ?? selectedParentId))

  // Active step
  const [activeStep, setActiveStep] = useState<StepKey>('title')

  // Local form state — syncs from child
  const [title, setTitle] = useState('')
  const [description, setDescription] = useState('')
  const [seedingComments, setSeedingComments] = useState('')
  const [folderPath, setFolderPath] = useState('')
  const [prepTitleDone, setPrepTitleDone] = useState(false)
  const [prepDescDone, setPrepDescDone] = useState(false)
  const [prepSeedDone, setPrepSeedDone] = useState(false)
  const [prepFolderDone, setPrepFolderDone] = useState(false)
  const [competitors, setLocalCompetitors] = useState<CompetitorItem[]>([])
  const [competitorUrl, setCompetitorUrl] = useState('')
  const [competitorTitle, setCompetitorTitle] = useState('')
  const [competitorThumbnail, setCompetitorThumbnail] = useState('')
  const [competitorChecking, setCompetitorChecking] = useState(false)
  const [competitorSaving, setCompetitorSaving] = useState(false)
  const [competitorLoading, setCompetitorLoading] = useState(false)
  const [competitorMetadataLoading, setCompetitorMetadataLoading] = useState(false)
  const [competitorDuplicate, setCompetitorDuplicate] = useState<CompetitorItem | null>(null)
  const [competitorNotice, setCompetitorNotice] = useState('')
  const [competitorSaved, setCompetitorSaved] = useState(false)
  const [autoThumbnailUrl, setAutoThumbnailUrl] = useState('')
  const [autoCompetitorTitle, setAutoCompetitorTitle] = useState('')
  const [autoCompetitorChannel, setAutoCompetitorChannel] = useState('')
  const [selectedCompetitorId, setSelectedCompetitorId] = useState<string | null>(null)
  const [competitorMode, setCompetitorMode] = useState<'view' | 'add' | 'edit'>('add')

  const [saving, setSaving] = useState(false)
  const [scanning, setScanning] = useState(false)

  // Folder setup
  const [showFolderSetup, setShowFolderSetup] = useState(false)
  const [subfolders, setSubfolders] = useState<{ name: string }[]>([])
  const [folderCount, setFolderCount] = useState(1)
  const [creatingFolders, setCreatingFolders] = useState(false)
  const [createFolderResult, setCreateFolderResult] = useState<'success' | 'error' | null>(null)
  const [showScanConfirm, setShowScanConfirm] = useState(false)
  const [scanMessage, setScanMessage] = useState('')

  // Sync from store child → local state
  useEffect(() => {
    if (!child) return
    setTitle(child.title)
    setDescription(child.description)
    setSeedingComments(child.seedingComments)
    setFolderPath(child.folderPath)
    setPrepTitleDone(child.prepTitleDone)
    setPrepDescDone(child.prepDescDone)
    setPrepSeedDone(child.prepSeedDone)
    setPrepFolderDone(child.prepFolderDone)
  }, [child])

  const resetCompetitorForm = useCallback(() => {
    setCompetitorUrl('')
    setCompetitorTitle('')
    setCompetitorThumbnail('')
    setAutoThumbnailUrl('')
    setAutoCompetitorTitle('')
    setAutoCompetitorChannel('')
    setCompetitorDuplicate(null)
    setCompetitorSaved(false)
  }, [])

  const applyCompetitorForm = useCallback((item: CompetitorItem | null) => {
    if (!item) {
      resetCompetitorForm()
      return
    }

    setCompetitorUrl(item.url)
    setCompetitorTitle(item.title)
    setCompetitorThumbnail(item.thumbnailUrl)
    setAutoThumbnailUrl(item.thumbnailUrl)
    setAutoCompetitorTitle(item.title)
    setAutoCompetitorChannel(item.channel)
    setCompetitorDuplicate(null)
    setCompetitorSaved(false)
  }, [resetCompetitorForm])

  const selectedCompetitor = competitors.find((item) => item.id === selectedCompetitorId) ?? null
  const isEditingCompetitor = competitorMode === 'add' || competitorMode === 'edit'
  const blockingDuplicate = competitorDuplicate && competitorDuplicate.id !== selectedCompetitorId
    ? competitorDuplicate
    : null

  const loadCompetitors = useCallback(async (preferredId?: string | null, forceAdd = false) => {
    if (!selectedChildId) return
    const childProjectId = Number(selectedChildId)
    if (!Number.isFinite(childProjectId)) return

    setCompetitorLoading(true)
    try {
      const [childRes, allRes] = await Promise.all([
        fetch(`${API}/api/competitors/?child_project_id=${childProjectId}`),
        fetch(`${API}/api/competitors/`),
      ])
      if (!childRes.ok) {
        throw new Error(`Failed to load competitors (${childRes.status})`)
      }

      const childData: Record<string, unknown>[] = await childRes.json()
      const childItems = childData.map(mapCompetitor)
      setLocalCompetitors(childItems)

      if (forceAdd || childItems.length === 0) {
        setSelectedCompetitorId(null)
        setCompetitorMode('add')
        resetCompetitorForm()
      } else {
        const next = childItems.find((item) => item.id === preferredId) ?? childItems[0]
        setSelectedCompetitorId(next.id)
        setCompetitorMode('view')
        applyCompetitorForm(next)
      }

      if (allRes.ok) {
        const allData: Record<string, unknown>[] = await allRes.json()
        setStoreCompetitors(allData.map(mapStoreCompetitor))
      }
    } catch {
      setLocalCompetitors([])
      setSelectedCompetitorId(null)
      setCompetitorMode('add')
      resetCompetitorForm()
    } finally {
      setCompetitorLoading(false)
    }
  }, [applyCompetitorForm, resetCompetitorForm, selectedChildId, setStoreCompetitors])

  // ---------------------------------------------------------------------------
  // Save field to backend
  // ---------------------------------------------------------------------------

  const saveField = useCallback(async (patch: Record<string, unknown>) => {
    if (!selectedChildId) return
    setSaving(true)
    try {
      const res = await fetch(`${API}/api/child-projects/${selectedChildId}`, {
        method: 'PATCH',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(patch),
      })
      if (res.ok) {
        const data = await res.json()
        const mapped = mapChild(data, selectedParentId ?? '')
        storeUpdateChild(selectedChildId, mapped)
        setPrepTitleDone(mapped.prepTitleDone)
        setPrepDescDone(mapped.prepDescDone)
        setPrepSeedDone(mapped.prepSeedDone)
        setPrepFolderDone(mapped.prepFolderDone)
      }
    } finally {
      setSaving(false)
    }
  }, [selectedChildId, selectedParentId, storeUpdateChild])

  const checkCompetitorUrl = useCallback(async (url: string) => {
    const value = url.trim()
    if (!value) {
      setCompetitorDuplicate(null)
      setCompetitorNotice('')
      return null
    }

    setCompetitorChecking(true)
    try {
      const res = await fetch(`${API}/api/competitors/check?url=${encodeURIComponent(value)}`)
      if (!res.ok) {
        throw new Error(`Failed to check competitor URL (${res.status})`)
      }
      const data = await res.json()
      const duplicate = data?.duplicate ? mapCompetitor(data.duplicate as Record<string, unknown>) : null
      setCompetitorDuplicate(duplicate)
      setCompetitorNotice(duplicate ? 'This URL is already saved.' : 'This URL is available.')
      setCompetitorSaved(false)
      return duplicate
    } catch {
      setCompetitorDuplicate(null)
      setCompetitorNotice('')
      return null
    } finally {
      setCompetitorChecking(false)
    }
  }, [])

  useEffect(() => {
    if (!selectedChildId) {
      setLocalCompetitors([])
      setSelectedCompetitorId(null)
      setCompetitorMode('add')
      resetCompetitorForm()
      setCompetitorNotice('')
      return
    }

    setSelectedCompetitorId(null)
    setCompetitorMode('add')
    resetCompetitorForm()
    setCompetitorNotice('')
    void loadCompetitors()
  }, [loadCompetitors, resetCompetitorForm, selectedChildId])

  useEffect(() => {
    if (!isEditingCompetitor) return
    const value = competitorUrl.trim()
    if (!value) {
      setCompetitorDuplicate(null)
      setCompetitorNotice('')
      return
    }

    const timer = window.setTimeout(() => {
      void checkCompetitorUrl(value)
    }, 450)

    return () => window.clearTimeout(timer)
  }, [checkCompetitorUrl, competitorUrl, isEditingCompetitor])

  useEffect(() => {
    if (!isEditingCompetitor) return
    const value = competitorUrl.trim()
    const shouldAutofillThumbnail = !competitorThumbnail.trim() || competitorThumbnail === autoThumbnailUrl
    const shouldAutofillTitle = !competitorTitle.trim() || competitorTitle === autoCompetitorTitle
    if (!value || (!shouldAutofillThumbnail && !shouldAutofillTitle)) return

    let cancelled = false
    setCompetitorMetadataLoading(true)

    void resolveYoutubeMetadata(value)
      .then(({ title, channelName, thumbnailUrl }) => {
        if (cancelled) return
        if (shouldAutofillTitle && title) {
          setAutoCompetitorTitle(title)
          setCompetitorTitle(title)
        }
        setAutoCompetitorChannel(channelName)
        if (shouldAutofillThumbnail) {
          setAutoThumbnailUrl(thumbnailUrl)
          setCompetitorThumbnail(thumbnailUrl)
        }
      })
      .finally(() => {
        if (!cancelled) setCompetitorMetadataLoading(false)
      })

    return () => {
      cancelled = true
    }
  }, [autoCompetitorTitle, autoThumbnailUrl, competitorThumbnail, competitorTitle, competitorUrl, isEditingCompetitor])

  const handleSaveCompetitor = async () => {
    if (!selectedChildId || !selectedParentId) {
      toast.error('No project selected', 'Select a parent project and child project first.')
      return
    }

    const url = competitorUrl.trim()
    const titleValue = competitorTitle.trim()
    const thumbnailValue = competitorThumbnail.trim()

    if (!url || !titleValue) {
      toast.warning('Missing details', 'Enter both the video URL and title.')
      return
    }

    const duplicate = await checkCompetitorUrl(url)
    if (duplicate && duplicate.id !== selectedCompetitorId) {
      toast.warning('Duplicate URL', 'This URL has already been saved.')
      return
    }

    const childProjectId = Number(selectedChildId)
    const parentProjectId = Number(selectedParentId)
    if (!Number.isFinite(childProjectId) || !Number.isFinite(parentProjectId)) {
      toast.error('Invalid project ID', 'The selected project ID is invalid.')
      return
    }

    setCompetitorSaving(true)
    try {
      const endpoint = competitorMode === 'edit' && selectedCompetitorId
        ? `${API}/api/competitors/${selectedCompetitorId}`
        : `${API}/api/competitors/`

      const method = competitorMode === 'edit' && selectedCompetitorId ? 'PATCH' : 'POST'
      const res = await fetch(endpoint, {
        method,
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          child_project_id: childProjectId,
          parent_project_id: parentProjectId,
          url,
          title: titleValue,
          thumbnail_url: thumbnailValue,
          channel: autoCompetitorChannel,
          purpose: 'reference',
          notes: '',
        }),
      })

      if (res.status === 409) {
        const data = await res.json().catch(() => null)
        const duplicate = data?.detail?.duplicate ? mapCompetitor(data.detail.duplicate as Record<string, unknown>) : null
        setCompetitorDuplicate(duplicate)
        setCompetitorNotice('This URL is already saved.')
        toast.warning('Duplicate URL', 'This URL has already been saved.')
        return
      }

      if (!res.ok) {
        const err = await res.json().catch(() => ({}))
        throw new Error(err?.detail ?? `Failed to save competitor (${res.status})`)
      }

      const saved = await res.json().catch(() => null)
      const savedId = saved?.id != null ? String(saved.id) : selectedCompetitorId
      setCompetitorDuplicate(null)
      setCompetitorNotice('Saved successfully.')
      setCompetitorSaved(true)
      setCompetitorMode('view')
      toast.success('Saved', method === 'PATCH' ? 'Competitor updated.' : 'Reference video saved.')
      await loadCompetitors(savedId)
    } catch (err) {
      const msg = err instanceof Error ? err.message : String(err)
      toast.error('Failed', msg)
    } finally {
      setCompetitorSaving(false)
    }
  }

  const handleAddCompetitor = () => {
    setSelectedCompetitorId(null)
    setCompetitorMode('add')
    resetCompetitorForm()
    setCompetitorNotice('Add a new competitor for this child project.')
  }

  const handleEditCompetitor = () => {
    if (!selectedCompetitor) return
    setCompetitorMode('edit')
    applyCompetitorForm(selectedCompetitor)
    setCompetitorNotice('Edit this competitor and save your changes.')
  }

  const handleCancelCompetitorEdit = () => {
    if (selectedCompetitor) {
      setCompetitorMode('view')
      applyCompetitorForm(selectedCompetitor)
      setCompetitorNotice('')
      return
    }
    handleAddCompetitor()
    setCompetitorNotice('')
  }

  // ---------------------------------------------------------------------------
  // Folder actions
  // ---------------------------------------------------------------------------

  const handlePickFolder = async () => {
    const result = await pickWorkspaceDirectory(child?.name ?? 'Video workspace')
    if (result && typeof result === 'string') {
      setFolderPath(result)
      await saveField({ base_folder_path: result })
      setSubfolders([])
      setShowFolderSetup(false)
    }
  }

  const handleScan = async () => {
    if (!folderPath || !selectedParentId) return
    setScanning(true)
    try {
      const tplRes = await fetch(`${API}/api/parent-projects/${selectedParentId}/folder-templates`)
      const tplData: { folder_name: string }[] = await tplRes.json()

      const scanRes = await fetch(`${API}/api/child-projects/scan?parent_id=${selectedParentId}`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ parent_folder_path: folderPath }),
      })
      const scanData = await scanRes.json()
      const diskFolders: string[] = scanData.unmapped_folders ?? []
      const totalOnDisk: number = scanData.total_on_disk ?? 0

      if (tplData.length > 0) {
        const templateNames = tplData.map(t => t.folder_name)
        const missing = templateNames.filter(n => !diskFolders.includes(n))
        if (missing.length > 0 || totalOnDisk === 0) {
          setSubfolders(tplData.map(t => ({ name: t.folder_name })))
          setFolderCount(tplData.length)
          setScanMessage(`Found ${tplData.length} folders in template but ${missing.length} are missing on disk.`)
          setShowScanConfirm(true)
        } else {
          setSubfolders(tplData.map(t => ({ name: t.folder_name })))
          setFolderCount(tplData.length)
          setShowFolderSetup(true)
        }
      } else {
        if (totalOnDisk === 0) {
          setScanMessage('No subfolders found. Would you like to create subfolders?')
        } else {
          setScanMessage(`Found ${totalOnDisk} folders on disk but no template saved.`)
          setSubfolders(diskFolders.map(n => ({ name: n })))
          setFolderCount(diskFolders.length)
        }
        setShowScanConfirm(true)
      }
    } finally {
      setScanning(false)
    }
  }

  const handleConfirmYes = () => {
    setShowScanConfirm(false)
    if (subfolders.length === 0) {
      setSubfolders(Array.from({ length: folderCount }, () => ({ name: '' })))
    }
    setShowFolderSetup(true)
  }

  const handleFolderCountChange = (count: number) => {
    const safeCount = Math.max(1, Math.min(20, count))
    setFolderCount(safeCount)
    setSubfolders(prev =>
      Array.from({ length: safeCount }, (_, i) => ({ name: prev[i]?.name ?? '' }))
    )
  }

  const handleCreateFolders = async () => {
    if (subfolders.some(f => !f.name.trim())) {
      toast.warning('Incomplete folders', 'Fill in all folder names before creating.')
      return
    }
    if (!selectedParentId) {
      toast.error('No project selected', 'Select a parent project first.')
      return
    }

    setCreatingFolders(true)
    setCreateFolderResult(null)
    try {
      // 1. Save folder templates to DB
      const tplRes = await fetch(
        `${API}/api/parent-projects/${selectedParentId}/folder-templates`,
        {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(subfolders.map((f, i) => ({ folder_name: f.name.trim(), sort_order: i }))),
        }
      )
      if (!tplRes.ok) {
        const err = await tplRes.json().catch(() => ({}))
        throw new Error(err?.detail ?? `Template save failed (${tplRes.status})`)
      }

      // 2. Create subfolders on disk (only if we have a folder path)
      if (folderPath.trim()) {
        const diskRes = await fetch(`${API}/api/child-projects/create-folders`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            base_path: folderPath.trim(),
            folder_names: subfolders.map(f => f.name.trim()),
          }),
        })
        if (!diskRes.ok) {
          const err = await diskRes.json().catch(() => ({}))
          throw new Error(err?.detail ?? `Folder creation failed (${diskRes.status})`)
        }
        const diskData = await diskRes.json()
        const created: string[] = diskData.created ?? []
        const existed: string[] = diskData.already_exists ?? []
        toast.success(
          'Folders created',
          `${created.length} created, ${existed.length} already existed.`,
        )
      } else {
        toast.success('Template saved', `${subfolders.length} folder names saved as template.`)
      }

      setCreateFolderResult('success')
      // Close the panel after short delay so user sees the success state
      setTimeout(() => {
        setShowFolderSetup(false)
        setCreateFolderResult(null)
      }, 1400)
    } catch (err) {
      const msg = err instanceof Error ? err.message : String(err)
      toast.error('Failed', msg)
      setCreateFolderResult('error')
      setTimeout(() => setCreateFolderResult(null), 3000)
    } finally {
      setCreatingFolders(false)
    }
  }

  // ---------------------------------------------------------------------------
  // Derived
  // ---------------------------------------------------------------------------

  const doneMap: Record<StepKey, boolean> = {
    title:  prepTitleDone,
    desc:   prepDescDone,
    seed:   prepSeedDone,
    folder: prepFolderDone,
  }
  const doneCount = Object.values(doneMap).filter(Boolean).length
  const allDone = doneCount === 4

  // ---------------------------------------------------------------------------
  // No child guard
  // ---------------------------------------------------------------------------

  if (!child) {
    return (
      <div className="flex flex-col items-center justify-center h-full text-surface-400 gap-3">
        <ClipboardList size={36} className="opacity-30" />
        <p className="text-sm">Select a child project to prepare resources.</p>
      </div>
    )
  }

  // ---------------------------------------------------------------------------
  // Render
  // ---------------------------------------------------------------------------

  return (
    <div className="flex flex-col h-full overflow-hidden bg-surface-50">

      {/* ── Scan Confirm Modal ─────────────────────────────────────────────── */}
      <AnimatePresence>
        {showScanConfirm && (
          <motion.div
            initial={{ opacity: 0 }}
            animate={{ opacity: 1 }}
            exit={{ opacity: 0 }}
            className="fixed inset-0 z-50 flex items-center justify-center bg-black/30"
          >
            <motion.div
              initial={{ scale: 0.95, opacity: 0 }}
              animate={{ scale: 1, opacity: 1 }}
              exit={{ scale: 0.95, opacity: 0 }}
              transition={{ duration: 0.15 }}
              className="bg-surface-0 rounded-xl shadow-popover p-6 w-[400px] mx-4"
            >
              <div className="flex items-start gap-3 mb-4">
                <div className="w-8 h-8 rounded-full bg-amber-500/10 flex items-center justify-center shrink-0">
                  <AlertTriangle size={15} className="text-amber-300" />
                </div>
                <div>
                  <h3 className="text-sm font-semibold text-surface-900 mb-1">Subfolder Setup</h3>
                  <p className="text-xs text-surface-500 leading-relaxed">{scanMessage}</p>
                </div>
              </div>
              <div className="flex gap-2 justify-end">
                <button className="btn-secondary text-xs" onClick={() => setShowScanConfirm(false)}>Cancel</button>
                <button className="btn-primary text-xs" onClick={handleConfirmYes}>Yes, proceed</button>
              </div>
            </motion.div>
          </motion.div>
        )}
      </AnimatePresence>

      {/* ── Top header bar ────────────────────────────────────────────────── */}
      <div className="flex items-center justify-between px-5 py-3 border-b border-surface-200 bg-surface-0 shrink-0">
        <div className="flex items-center gap-2.5 min-w-0">
          <div className="w-7 h-7 rounded-md bg-primary-100 flex items-center justify-center shrink-0">
            <ClipboardList size={14} className="text-primary-600" />
          </div>
          <div className="min-w-0">
            <p className="text-[10px] font-medium text-surface-400 truncate">
              {parent?.name ?? 'Parent Project'} / {child.name} / Resource Prep
            </p>
            <h1 className="text-sm font-semibold text-surface-900 truncate">{child.name}</h1>
          </div>
        </div>

        <div className="flex items-center gap-2 shrink-0">
          {saving && (
            <span className="flex items-center gap-1 text-[11px] text-surface-400">
              <Loader size={11} className="animate-spin" />
              Saving
            </span>
          )}
          {/* Progress pill */}
          <div className={cn(
            'flex items-center gap-1.5 px-2.5 py-1 rounded-full text-xs font-semibold transition-[background-color,border-color,color,box-shadow,opacity,transform] duration-300',
            allDone
              ? 'bg-green-500/10 text-green-300'
              : 'bg-surface-100 text-surface-600',
          )}>
            {allDone && <Check size={11} strokeWidth={3} />}
            <span>{doneCount}/4</span>
            {allDone && <span className="ml-0.5">All ready</span>}
          </div>
        </div>
      </div>

      {/* ── Progress bar ──────────────────────────────────────────────────── */}
      <div className="h-0.5 bg-surface-100 shrink-0">
        <motion.div
          className="h-full bg-green-500"
          animate={{ width: `${(doneCount / 4) * 100}%` }}
          transition={{ duration: 0.4, ease: 'easeOut' }}
        />
      </div>

      {/* ── Main layout: sidebar + content ────────────────────────────────── */}
      <div className="flex flex-1 min-h-0 overflow-hidden">

        {/* Sidebar */}
        <div className="w-52 shrink-0 border-r border-surface-200 bg-surface-0 flex flex-col overflow-y-auto">
          <div className="p-3">
            <p className="text-[10px] font-semibold text-surface-400 uppercase tracking-wider px-1 mb-2">
              Steps
            </p>
            <div className="space-y-0.5">
              {STEPS.map((step, i) => (
                <StepItem
                  key={step.key}
                  icon={step.icon}
                  label={step.label}
                  done={doneMap[step.key]}
                  active={activeStep === step.key}
                  index={i}
                  onClick={() => setActiveStep(step.key)}
                />
              ))}
            </div>
          </div>

          {/* All done celebration */}
          <AnimatePresence>
            {allDone && (
              <motion.div
                initial={{ opacity: 0, y: 8 }}
                animate={{ opacity: 1, y: 0 }}
                exit={{ opacity: 0 }}
                className="mx-3 mb-3 mt-auto p-3 rounded-lg bg-green-500/10 border border-green-500/20"
              >
                <div className="flex items-center gap-2 mb-1">
                  <CheckCircle2 size={14} className="text-green-300 shrink-0" />
                  <span className="text-xs font-semibold text-green-300">Ready to edit!</span>
                </div>
                <p className="text-[11px] text-green-300 leading-relaxed">
                  All resources prepared. Status will auto-advance to Resource Prep.
                </p>
              </motion.div>
            )}
          </AnimatePresence>
        </div>

        {/* Content pane */}
        <div className="flex-1 overflow-y-auto">
          <AnimatePresence mode="wait">
            <motion.div
              key={activeStep}
              initial={{ opacity: 0, y: 6 }}
              animate={{ opacity: 1, y: 0 }}
              exit={{ opacity: 0, y: -4 }}
              transition={{ duration: 0.15, ease: 'easeOut' }}
              className="p-5"
            >
              {activeStep === 'title' && (
                <StepPane
                  label="Video Title"
                  icon={Type}
                  done={prepTitleDone}
                  onToggle={() => {
                    const next = !prepTitleDone
                    setPrepTitleDone(next)
                    saveField({ prep_title_done: next, title })
                  }}
                  hint="The YouTube video title for this video."
                >
                  <div className="space-y-4">
                    <input
                      className="input w-full"
                      placeholder="Enter your YouTube video title…"
                      value={title}
                      onChange={e => setTitle(e.target.value)}
                      onBlur={() => saveField({ title })}
                    />

                    <div className="rounded-xl border border-surface-200 bg-surface-0 overflow-hidden shadow-sm">
                      <div className="flex items-center justify-between gap-3 border-b border-surface-200 bg-surface-50/80 px-4 py-3">
                        <div>
                          <p className="text-sm font-semibold text-surface-900">Reference Video</p>
                          <p className="text-[11px] text-surface-500">Keep multiple competitors for this child project. Reopen later to review or edit them.</p>
                        </div>
                        <div className="rounded-full bg-surface-0 px-2.5 py-1 text-[11px] font-medium text-surface-500 shrink-0">
                          {competitorLoading ? 'Loading...' : `${competitors.length} saved`}
                        </div>
                      </div>

                      <div className="border-b border-surface-200 px-4 py-3">
                        <div className="flex flex-col gap-3">
                          <div className="flex flex-col gap-3 lg:flex-row lg:items-center lg:justify-between">
                            <div>
                              <p className="text-[11px] font-semibold uppercase tracking-wide text-surface-500">
                                Saved competitors
                              </p>
                              <p className="mt-1 text-[11px] text-surface-400">
                                Compact table for this child project. Click a row to review or edit.
                              </p>
                            </div>

                            <div className="flex flex-wrap items-center gap-2">
                              {selectedCompetitor && competitorMode === 'view' && (
                                <button
                                  type="button"
                                  className="btn-secondary px-3 py-2 text-xs"
                                  onClick={handleEditCompetitor}
                                >
                                  <Pencil size={13} />
                                  Edit competitor
                                </button>
                              )}

                              <button
                                type="button"
                                className="btn-secondary px-3 py-2 text-xs"
                                onClick={handleAddCompetitor}
                              >
                                <Plus size={13} />
                                Add competitor
                              </button>
                            </div>
                          </div>

                          {competitors.length > 0 ? (
                            <div className="overflow-hidden rounded-xl border border-surface-200 bg-surface-0">
                              <div className="grid grid-cols-[64px_minmax(0,1.6fr)_minmax(0,1fr)_110px] gap-3 border-b border-surface-200 bg-surface-50 px-3 py-2 text-[11px] font-semibold uppercase tracking-wide text-surface-500">
                                <span>#</span>
                                <span>Title</span>
                                <span>Channel</span>
                                <span>Added</span>
                              </div>

                              <div className="max-h-56 overflow-y-auto">
                                {competitors.map((item, index) => {
                                  const isSelected = selectedCompetitorId === item.id && competitorMode !== 'add'

                                  return (
                                    <button
                                      key={item.id}
                                      type="button"
                                      className={cn(
                                        'grid w-full grid-cols-[64px_minmax(0,1.6fr)_minmax(0,1fr)_110px] gap-3 border-b border-surface-100 px-3 py-3 text-left transition-colors last:border-b-0',
                                        isSelected
                                          ? 'bg-primary-50 text-primary-700'
                                          : 'bg-surface-0 text-surface-700 hover:bg-surface-50',
                                      )}
                                      onClick={() => {
                                        setSelectedCompetitorId(item.id)
                                        setCompetitorMode('view')
                                        applyCompetitorForm(item)
                                        setCompetitorNotice('')
                                      }}
                                    >
                                      <span className="text-xs font-semibold text-surface-500">
                                        {String(index + 1).padStart(2, '0')}
                                      </span>
                                      <span className="truncate text-xs font-medium">
                                        {item.title || `Competitor ${index + 1}`}
                                      </span>
                                      <span className="truncate text-xs text-surface-500">
                                        {item.channel || 'No channel'}
                                      </span>
                                      <span className="text-xs text-surface-500">
                                        {item.createdAt ? String(item.createdAt).slice(0, 10) : '—'}
                                      </span>
                                    </button>
                                  )
                                })}
                              </div>
                            </div>
                          ) : (
                            <div className="rounded-xl border border-dashed border-surface-200 bg-surface-50 px-3 py-4 text-xs text-surface-400">
                              No competitors saved for this child project yet.
                            </div>
                          )}
                        </div>
                      </div>

                      <div className="grid gap-5 p-4 xl:grid-cols-[minmax(0,1fr)_320px]">
                        <div className="space-y-4">
                          <div className="space-y-1.5">
                            <label className="text-[11px] font-medium text-surface-500 uppercase tracking-wide">
                              Video URL
                            </label>
                            <input
                              className={cn(
                                'input w-full',
                                competitorDuplicate ? 'border-amber-300 focus:border-amber-400' : '',
                              )}
                              placeholder="Paste a YouTube video URL"
                              value={competitorUrl}
                              readOnly={!isEditingCompetitor}
                              onChange={e => {
                                setCompetitorUrl(e.target.value)
                                setCompetitorSaved(false)
                              }}
                              onBlur={() => {
                                if (isEditingCompetitor) void checkCompetitorUrl(competitorUrl)
                              }}
                            />
                          </div>

                          <div className="space-y-1.5">
                            <label className="text-[11px] font-medium text-surface-500 uppercase tracking-wide">
                              Source Title
                            </label>
                            <input
                              className="input w-full"
                              placeholder="Enter the competitor video title"
                              value={competitorTitle}
                              readOnly={!isEditingCompetitor}
                              onChange={e => {
                                setCompetitorTitle(e.target.value)
                                if (e.target.value !== autoCompetitorTitle) {
                                  setAutoCompetitorTitle('')
                                }
                                setCompetitorSaved(false)
                              }}
                            />
                          </div>

                          <div className="space-y-1.5">
                            <label className="text-[11px] font-medium text-surface-500 uppercase tracking-wide">
                              Thumbnail URL
                            </label>
                            <input
                              className="input w-full"
                              placeholder="Auto-filled from YouTube"
                              value={competitorThumbnail}
                              readOnly={!isEditingCompetitor}
                              onChange={e => {
                                setCompetitorThumbnail(e.target.value)
                                if (e.target.value !== autoThumbnailUrl) {
                                  setAutoThumbnailUrl('')
                                }
                                setCompetitorSaved(false)
                              }}
                            />
                            <p className="text-[11px] text-surface-400">
                              Auto-filled from the YouTube link. You can still override it manually.
                            </p>
                          </div>
                        </div>

                        <div className="space-y-3">
                          <div className="rounded-xl border border-surface-200 bg-surface-50/70 p-3">
                            <div className="flex items-center justify-between gap-2 mb-2">
                              <p className="text-[11px] font-semibold uppercase tracking-wide text-surface-500">
                                Thumbnail Preview
                              </p>
                              <span className="text-[10px] text-surface-400">
                                {competitorThumbnail ? 'Ready' : 'Waiting'}
                              </span>
                            </div>

                            <div className="rounded-lg border border-surface-200 bg-surface-0 overflow-hidden">
                              {competitorThumbnail ? (
                                <div className="p-2">
                                  <img
                                    src={competitorThumbnail}
                                    alt="Thumbnail preview"
                                    className="w-full aspect-video object-cover rounded-md bg-surface-100"
                                    onError={(e) => {
                                      const img = e.currentTarget
                                      img.style.display = 'none'
                                      const fallback = img.nextElementSibling as HTMLDivElement | null
                                      if (fallback) fallback.style.display = 'flex'
                                    }}
                                    onLoad={(e) => {
                                      const img = e.currentTarget
                                      img.style.display = 'block'
                                      const fallback = img.nextElementSibling as HTMLDivElement | null
                                      if (fallback) fallback.style.display = 'none'
                                    }}
                                  />
                                  <div className="hidden aspect-video items-center justify-center rounded-md bg-surface-50 text-[11px] text-surface-400">
                                    Thumbnail preview unavailable
                                  </div>
                                </div>
                              ) : (
                                <div className="aspect-video flex items-center justify-center bg-surface-50 text-[11px] text-surface-400">
                                  {competitorMetadataLoading ? 'Loading video details...' : 'Paste a link to preview the thumbnail'}
                                </div>
                              )}
                            </div>

                            <div className="mt-3 space-y-1">
                              <p className="text-xs font-medium text-surface-800 truncate">
                                {competitorTitle.trim() || 'No title yet'}
                              </p>
                              <p className="text-[11px] text-surface-400 line-clamp-2 break-all">
                                {competitorUrl.trim() || 'The YouTube URL will appear here after you paste it.'}
                              </p>
                              <p className="text-[11px] text-surface-500">
                                {autoCompetitorChannel.trim() || selectedCompetitor?.channel || 'Channel will appear here'}
                              </p>
                            </div>
                          </div>
                        </div>
                      </div>

                      <div className="flex flex-col gap-3 border-t border-surface-200 px-4 py-3 md:flex-row md:items-center md:justify-between">
                        <p
                          className={cn(
                            'text-[11px] leading-relaxed',
                            blockingDuplicate ? 'text-amber-300' : competitorSaved ? 'text-green-300' : 'text-surface-400',
                          )}
                        >
                          {competitorChecking
                            ? 'Checking this URL...'
                            : blockingDuplicate
                              ? `Already saved: ${blockingDuplicate.title || blockingDuplicate.url}`
                              : competitorMode === 'view' && selectedCompetitor
                                ? 'This saved competitor belongs to the current child project. Use Edit if you want to change it.'
                              : competitorSaved
                                ? 'Saved successfully. Open the competitor table to review the full list.'
                                : competitorMetadataLoading
                                  ? 'Fetching title and thumbnail from YouTube...'
                                  : competitorNotice || 'Save a reference here, then open the full table when you need it.'
                          }
                        </p>

                        <div className="flex flex-wrap items-center justify-end gap-2">
                          {(competitorSaved || competitors.length > 0) && (
                            <button
                              type="button"
                              className="btn-secondary px-3 py-2 text-xs shrink-0"
                              onClick={() => setMainView('competitors')}
                            >
                              View all competitors
                            </button>
                          )}

                          {isEditingCompetitor && competitors.length > 0 && (
                            <button
                              type="button"
                              className="btn-secondary px-3 py-2 text-xs shrink-0"
                              onClick={handleCancelCompetitorEdit}
                            >
                              Cancel
                            </button>
                          )}

                          <button
                            type="button"
                            className="btn-primary px-3 py-2 text-xs shrink-0"
                            onClick={handleSaveCompetitor}
                            disabled={
                              competitorSaving
                              || competitorChecking
                              || !isEditingCompetitor
                              || !competitorUrl.trim()
                              || !competitorTitle.trim()
                              || !!blockingDuplicate
                            }
                          >
                          {competitorSaving ? 'Saving...' : competitorMode === 'edit' ? 'Update competitor' : 'Save reference'}
                        </button>
                        </div>
                      </div>
                    </div>
                  </div>
                </StepPane>
              )}

              {activeStep === 'desc' && (
                <StepPane
                  label="Video Description"
                  icon={AlignLeft}
                  done={prepDescDone}
                  onToggle={() => {
                    const next = !prepDescDone
                    setPrepDescDone(next)
                    saveField({ prep_desc_done: next, description })
                  }}
                  hint="The YouTube video description. Paste or write your full description here."
                >
                  <textarea
                    className="textarea w-full"
                    rows={8}
                    placeholder="YouTube video description…"
                    value={description}
                    onChange={e => setDescription(e.target.value)}
                    onBlur={() => saveField({ description })}
                  />
                </StepPane>
              )}

              {activeStep === 'seed' && (
                <StepPane
                  label="Seeding Comments"
                  icon={MessageSquare}
                  done={prepSeedDone}
                  onToggle={() => {
                    const next = !prepSeedDone
                    setPrepSeedDone(next)
                    saveField({ prep_seed_done: next, seeding_comments: seedingComments })
                  }}
                  hint="Comments to seed on the video after publishing. One comment per line."
                >
                  <textarea
                    className="textarea w-full"
                    rows={8}
                    placeholder="One comment per line…"
                    value={seedingComments}
                    onChange={e => setSeedingComments(e.target.value)}
                    onBlur={() => saveField({ seeding_comments: seedingComments })}
                  />
                </StepPane>
              )}

              {activeStep === 'folder' && (
                <StepPane
                  label="Resource Folder"
                  icon={FolderOpen}
                  done={prepFolderDone}
                  onToggle={() => {
                    const next = !prepFolderDone
                    setPrepFolderDone(next)
                    saveField({ prep_folder_done: next, base_folder_path: folderPath })
                  }}
                  hint="The base folder on disk that holds all assets for this video."
                >
                  <div className="flex gap-2 mb-3">
                    <input
                      className="input flex-1"
                      placeholder="/Projects/MyVideo"
                      value={folderPath}
                      onChange={e => setFolderPath(e.target.value)}
                      onBlur={() => saveField({ base_folder_path: folderPath })}
                    />
                    <button
                      type="button"
                      className="btn-secondary px-3 gap-1.5"
                      onClick={handlePickFolder}
                      title="Choose folder"
                    >
                      <FolderOpen size={13} />
                      <span className="text-xs">Browse</span>
                    </button>
                    <button
                      type="button"
                      className="btn-secondary px-3 gap-1.5"
                      disabled={!folderPath || scanning}
                      onClick={handleScan}
                      title="Scan subfolders"
                    >
                      {scanning
                        ? <Loader size={13} className="animate-spin" />
                        : <Search size={13} />}
                      <span className="text-xs">Scan</span>
                    </button>
                  </div>

                  {/* Folder setup panel */}
                  <AnimatePresence>
                    {showFolderSetup && (
                      <motion.div
                        initial={{ opacity: 0, height: 0 }}
                        animate={{ opacity: 1, height: 'auto' }}
                        exit={{ opacity: 0, height: 0 }}
                        transition={{ duration: 0.2 }}
                        className="overflow-hidden"
                      >
                        <div className="border border-surface-200 rounded-lg p-4 bg-surface-0">
                          <div className="flex items-center justify-between mb-3">
                            <p className="text-xs font-semibold text-surface-700 flex items-center gap-1.5">
                              <FolderPlus size={13} />
                              Configure Subfolders
                            </p>
                            <button
                              className="text-surface-300 hover:text-surface-500 transition-colors cursor-pointer"
                              onClick={() => setShowFolderSetup(false)}
                            >
                              <X size={14} />
                            </button>
                          </div>

                          <div className="flex items-center gap-2 mb-3">
                            <label className="text-xs text-surface-500">Count:</label>
                            <input
                              className="input w-20 text-center"
                              type="number" min="1" max="20"
                              value={folderCount}
                              onChange={e => handleFolderCountChange(parseInt(e.target.value) || 1)}
                            />
                            <span className="text-xs text-surface-400">
                              {subfolders.length} folder{subfolders.length !== 1 ? 's' : ''}
                            </span>
                          </div>

                          {subfolders.length > 0 && (
                            <div className="grid grid-cols-2 gap-2 mb-3">
                              {subfolders.map((folder, i) => (
                                <div key={i} className="flex items-center gap-1.5">
                                  <span className="text-[11px] text-surface-400 w-5 text-right shrink-0">{i + 1}.</span>
                                  <input
                                    className="input flex-1 text-xs"
                                    placeholder={`Folder ${i + 1}`}
                                    value={folder.name}
                                    onChange={e => {
                                      const updated = [...subfolders]
                                      updated[i] = { name: e.target.value }
                                      setSubfolders(updated)
                                    }}
                                  />
                                  <button
                                    type="button"
                                    className="text-surface-300 hover:text-red-400 transition-colors shrink-0 cursor-pointer"
                                    onClick={() => {
                                      const updated = subfolders.filter((_, j) => j !== i)
                                      setSubfolders(updated)
                                      setFolderCount(updated.length)
                                    }}
                                  >
                                    <X size={12} />
                                  </button>
                                </div>
                              ))}
                            </div>
                          )}

                          <button
                            type="button"
                            className={cn(
                              'w-full flex items-center justify-center gap-2 px-4 py-2.5 rounded-lg text-sm font-semibold',
                              'transition-[background-color,border-color,color,box-shadow,opacity,transform] duration-200 cursor-pointer',
                              'disabled:cursor-not-allowed disabled:opacity-60',
                              createFolderResult === 'success'
                                ? 'bg-green-500 text-white shadow-sm'
                                : createFolderResult === 'error'
                                  ? 'bg-red-500 text-white shadow-sm'
                                  : 'bg-primary-500 hover:bg-primary-600 active:bg-primary-700 text-white shadow-sm',
                            )}
                            disabled={creatingFolders || createFolderResult === 'success' || subfolders.some(f => !f.name.trim())}
                            onClick={handleCreateFolders}
                          >
                            {creatingFolders ? (
                              <><Loader size={14} className="animate-spin" /> Creating folders…</>
                            ) : createFolderResult === 'success' ? (
                              <><Check size={14} strokeWidth={3} /> Done! Folders created</>
                            ) : createFolderResult === 'error' ? (
                              <><XCircle size={14} /> Failed — try again</>
                            ) : (
                              <><FolderPlus size={14} /> Create folders & save template</>
                            )}
                          </button>
                        </div>
                      </motion.div>
                    )}
                  </AnimatePresence>
                </StepPane>
              )}
            </motion.div>
          </AnimatePresence>
        </div>
      </div>
    </div>
  )
}

// ---------------------------------------------------------------------------
// StepPane — content area for each step
// ---------------------------------------------------------------------------

interface StepPaneProps {
  label: string
  icon: React.ElementType
  done: boolean
  onToggle: () => void
  hint: string
  children: React.ReactNode
}

function StepPane({ label, icon: Icon, done, onToggle, hint, children }: StepPaneProps) {
  return (
    <div>
      {/* Pane header */}
      <div className="flex items-start justify-between mb-4">
        <div className="flex items-center gap-2.5">
          <div className={cn(
            'w-8 h-8 rounded-lg flex items-center justify-center shrink-0 transition-[background-color,border-color,color,box-shadow,opacity,transform] duration-200',
            done ? 'bg-green-500/10' : 'bg-primary-50',
          )}>
            <Icon size={15} className={done ? 'text-green-300' : 'text-primary-500'} />
          </div>
          <div>
            <h2 className="text-sm font-semibold text-surface-900">{label}</h2>
            <p className="text-[11px] text-surface-400 mt-0.5">{hint}</p>
          </div>
        </div>

        {/* Mark done toggle */}
        <button
          className={cn(
            'flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs font-medium transition-[background-color,border-color,color,box-shadow,opacity,transform] duration-150 cursor-pointer border shrink-0',
            done
              ? 'bg-green-500/10 text-green-300 border-green-500/20 hover:bg-green-500/10'
              : 'bg-surface-0 text-surface-600 border-surface-200 hover:bg-surface-50 hover:text-surface-800',
          )}
          onClick={onToggle}
          title={done ? 'Mark as incomplete' : 'Mark as complete'}
        >
          {done
            ? <><CheckCircle2 size={13} /> Done</>
            : <><Circle size={13} /> Mark done</>}
        </button>
      </div>

      {/* Input area */}
      <div className={cn(
        'rounded-lg border p-4 transition-[background-color,border-color,color,box-shadow,opacity,transform] duration-200',
        done ? 'border-green-500/20 bg-green-500/10' : 'border-surface-200 bg-surface-0',
      )}>
        {children}
      </div>
    </div>
  )
}
