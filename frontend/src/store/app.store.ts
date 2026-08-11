import { create } from 'zustand'
import { persist } from 'zustand/middleware'

// ─── Enums (mirrors Python backend) ────────────────────────────────────────

export type ChildStatus   = 'draft' | 'resource_prep' | 'editing' | 'published'
export type PlannerStage = 'backlog' | 'ready' | 'editing' | 'published'
export type PlannerPriority = 'low' | 'medium' | 'high' | 'urgent'

// ─── Core Interfaces (mirrors Python models) ────────────────────────────────

/** mirrors the Python backend parent-project schema */
export interface ParentProject {
  id: string
  name: string
  author: string
  publisher: string
  copyright: string
  keywordsRaw: string      // comma/newline separated, e.g. "gadget, review, tech"
  roxyWorkspaceId: number | null
  roxyProfileId: string
  roxyProfileName: string
  childCount: number
  createdAt: string
}

/** mirrors ChildProjectRecord in models.py */
export interface ChildProject {
  id: string
  parentId: string
  name: string             // display name, e.g. "Video 01"
  status: ChildStatus
  title: string            // YouTube video title
  description: string      // YouTube video description
  seedingComments: string  // newline-separated seed comments
  folderPath: string       // absolute path to child project folder
  /** Legacy API aliases kept until every feature uses the normalized web model. */
  base_folder_path?: string
  video_number?: number
  planningStage: PlannerStage
  priority: PlannerPriority
  deadline: string | null
  planningNote: string
  roxyWorkspaceId: number | null
  roxyProfileId: string
  roxyProfileName: string
  // Resource-prep checkmarks
  prepTitleDone: boolean
  prepDescDone: boolean
  prepSeedDone: boolean
  prepFolderDone: boolean
  createdAt: string
}

/** per-project analytics snapshot */
export interface AnalyticsEntry {
  id: string
  projectName: string
  exportedAt: string
  durationS: number
  fileSizeMb: number
  fps: number
  resolution: string       // e.g. "1920×1080"
  seoApplied: boolean
  uploadedAt: string | null
  roxyProfileId: string | null
}

/** YouTube competitor link */
export interface Competitor {
  id: string
  videoId: string           // extracted YouTube video ID — dedup key
  url: string               // original pasted URL
  title: string             // from oEmbed
  channel: string           // from oEmbed (author_name)
  thumbnail: string         // from oEmbed
  purpose: CompetitorPurpose
  notes: string
  childProjectId: string | null   // which child project it belongs to
  parentProjectId: string | null  // denormalized for easy filtering
  addedAt: string
}

// ─── Navigation ─────────────────────────────────────────────────────────────

export type MainView      = 'projects' | 'planner' | 'render' | 'automate' | 'reply-center' | 'analytics' | 'competitors' | 'settings'
export type ProjectSubView = 'parents' | 'children' | 'resource-prep' | 'ai-gen' | 'ai-audio' | 'livestream' | 'srt-gen' | 'raw-seo' | 'roxy-upload' | 'youtube-reply' | 'fast-edit' | 'cut-automate' | 'sora-gen' | 'audio-visualizer'
export type AutomateSubView = 'short' | 'long'
export type CompetitorPurpose = 'rewrite' | 'reference' | 'trending' | 'script'

// ─── Panel State ─────────────────────────────────────────────────────────────

export interface PanelState {
  id: string                         // 'left' | 'right'
  mainView: MainView
  projectSubView: ProjectSubView
  automateSubView: AutomateSubView
  selectedParentId: string | null
  selectedChildId: string | null
  sidebarCollapsed: boolean
}

const DEFAULT_PANEL = (id: string): PanelState => ({
  id,
  mainView: 'projects',
  projectSubView: 'parents',
  automateSubView: 'short',
  selectedParentId: null,
  selectedChildId: null,
  sidebarCollapsed: false,
})

// ─── Store State ─────────────────────────────────────────────────────────────

interface AppState {
  // Navigation (legacy single-panel — kept for backward compat)
  mainView: MainView
  projectSubView: ProjectSubView
  automateSubView: AutomateSubView
  selectedParentId: string | null
  selectedChildId: string | null

  // Split View
  splitView: boolean
  panels: [PanelState, PanelState]
  activePanelId: string

  // Data — mock, will be replaced by FastAPI calls
  parentProjects: ParentProject[]
  childProjects: ChildProject[]
  analyticsEntries: AnalyticsEntry[]
  competitors: Competitor[]

  // UI
  sidebarCollapsed: boolean
  isLoading: boolean
  loadingText: string
  requestedParentEditorId: string | null

  // Navigation actions (legacy — drives single-panel / backward compat)
  setMainView: (view: MainView) => void
  setProjectSubView: (view: ProjectSubView) => void
  setAutomateSubView: (view: AutomateSubView) => void
  selectParent: (id: string | null) => void
  selectChild: (id: string | null) => void
  setSidebarCollapsed: (v: boolean) => void
  setLoading: (v: boolean, text?: string) => void
  requestParentEditor: (id: string | null) => void

  // Split View actions
  setSplitView: (v: boolean) => void
  setActivePanelId: (id: string) => void
  setPanelMainView: (panelId: string, view: MainView) => void
  setPanelSubView: (panelId: string, view: ProjectSubView) => void
  setPanelAutomateSubView: (panelId: string, view: AutomateSubView) => void
  selectPanelParent: (panelId: string, id: string | null) => void
  selectPanelChild: (panelId: string, id: string | null) => void
  setPanelSidebarCollapsed: (panelId: string, v: boolean) => void

  // Parent CRUD
  setParentProjects: (projects: ParentProject[]) => void
  addParentDirect: (p: ParentProject) => void
  addParent: (p: Omit<ParentProject, 'id' | 'childCount' | 'createdAt'>) => void
  updateParent: (id: string, patch: Partial<ParentProject>) => void
  deleteParent: (id: string) => void

  // Child CRUD
  addChild: (c: Omit<ChildProject, 'id' | 'createdAt'>) => void
  addChildDirect: (c: ChildProject) => void
  setChildren: (children: ChildProject[]) => void
  updateChild: (id: string, patch: Partial<ChildProject>) => void
  deleteChild: (id: string) => void

  // Competitor actions
  setCompetitors: (items: Competitor[]) => void
  addCompetitor: (c: Omit<Competitor, 'id' | 'addedAt'>) => { ok: boolean; duplicate?: Competitor }
  deleteCompetitor: (id: string) => void
  updateCompetitor: (id: string, patch: Partial<Competitor>) => void
}

// ─── Mock Data ───────────────────────────────────────────────────────────────

// No demo data — all data will come from FastAPI backend
const MOCK_PARENTS: ParentProject[]     = []
const MOCK_CHILDREN: ChildProject[]     = []
const MOCK_ANALYTICS: AnalyticsEntry[]  = []
const MOCK_COMPETITORS: Competitor[]    = []

function applyChildCountsToParents(parents: ParentProject[], children: ChildProject[]): ParentProject[] {
  const counts = children.reduce<Record<string, number>>((acc, child) => {
    acc[child.parentId] = (acc[child.parentId] ?? 0) + 1
    return acc
  }, {})

  return parents.map((parent) => ({
    ...parent,
    childCount: counts[parent.id] ?? parent.childCount ?? 0,
  }))
}

// ─── Store ────────────────────────────────────────────────────────────────────

export const useAppStore = create<AppState>()(persist((set) => ({
  mainView: 'projects',
  projectSubView: 'parents',
  automateSubView: 'short',
  selectedParentId: null,
  selectedChildId: null,

  // Split View
  splitView: false,
  panels: [DEFAULT_PANEL('left'), DEFAULT_PANEL('right')],
  activePanelId: 'left',

  parentProjects: MOCK_PARENTS,
  childProjects: MOCK_CHILDREN,
  analyticsEntries: MOCK_ANALYTICS,
  competitors: MOCK_COMPETITORS,
  sidebarCollapsed: false,
  isLoading: false,
  loadingText: '',
  requestedParentEditorId: null,

  setMainView: (view) => set({ mainView: view }),
  setProjectSubView: (view) => set({ projectSubView: view }),
  setAutomateSubView: (view) => set({ automateSubView: view }),
  selectParent: (id) => set({ selectedParentId: id }),
  selectChild: (id) => set({ selectedChildId: id }),
  setSidebarCollapsed: (v) => set({ sidebarCollapsed: v }),
  setLoading: (v, text = '') => set({ isLoading: v, loadingText: text }),
  requestParentEditor: (id) => set({ requestedParentEditorId: id }),

  // Split View actions
  setSplitView: (v) => set({ splitView: v }),
  setActivePanelId: (id) => set({ activePanelId: id }),
  setPanelMainView: (panelId, view) => set((s) => ({
    panels: s.panels.map((p) => p.id === panelId ? { ...p, mainView: view } : p) as [PanelState, PanelState],
  })),
  setPanelSubView: (panelId, view) => set((s) => ({
    panels: s.panels.map((p) => p.id === panelId ? { ...p, projectSubView: view } : p) as [PanelState, PanelState],
  })),
  setPanelAutomateSubView: (panelId, view) => set((s) => ({
    panels: s.panels.map((p) => p.id === panelId ? { ...p, automateSubView: view } : p) as [PanelState, PanelState],
  })),
  selectPanelParent: (panelId, id) => set((s) => ({
    panels: s.panels.map((p) => p.id === panelId ? { ...p, selectedParentId: id } : p) as [PanelState, PanelState],
  })),
  selectPanelChild: (panelId, id) => set((s) => ({
    panels: s.panels.map((p) => p.id === panelId ? { ...p, selectedChildId: id } : p) as [PanelState, PanelState],
  })),
  setPanelSidebarCollapsed: (panelId, v) => set((s) => ({
    panels: s.panels.map((p) => p.id === panelId ? { ...p, sidebarCollapsed: v } : p) as [PanelState, PanelState],
  })),

  // Parent CRUD
  setParentProjects: (projects) => set((s) => ({
    parentProjects: applyChildCountsToParents(projects, s.childProjects),
  })),
  addParentDirect: (p) => set((s) => ({
    parentProjects: [...s.parentProjects, p],
  })),
  addParent: (p) => set((s) => ({
    parentProjects: [...s.parentProjects, {
      ...p, id: `p${Date.now()}`, childCount: 0, createdAt: new Date().toISOString().slice(0, 10),
    }],
  })),
  updateParent: (id, patch) => set((s) => ({
    parentProjects: s.parentProjects.map((p) => p.id === id ? { ...p, ...patch } : p),
  })),
  deleteParent: (id) => set((s) => ({
    parentProjects: s.parentProjects.filter((p) => p.id !== id),
    childProjects: s.childProjects.filter((c) => c.parentId !== id),
  })),

  // Child CRUD
  addChild: (c) => set((s) => ({
    childProjects: [...s.childProjects, { ...c, id: `c${Date.now()}`, createdAt: new Date().toISOString().slice(0, 10) }],
    parentProjects: s.parentProjects.map((p) =>
      p.id === c.parentId ? { ...p, childCount: p.childCount + 1 } : p
    ),
  })),
  addChildDirect: (c) => set((s) => ({
    childProjects: [...s.childProjects, c],
  })),
  setChildren: (children) => set((s) => ({
    childProjects: children,
    parentProjects: applyChildCountsToParents(s.parentProjects, children),
  })),
  updateChild: (id, patch) => set((s) => ({
    childProjects: s.childProjects.map((c) => c.id === id ? { ...c, ...patch } : c),
  })),
  deleteChild: (id) => set((s) => {
    const child = s.childProjects.find((c) => c.id === id)
    return {
      childProjects: s.childProjects.filter((c) => c.id !== id),
      parentProjects: s.parentProjects.map((p) =>
        p.id === child?.parentId ? { ...p, childCount: Math.max(0, p.childCount - 1) } : p
      ),
    }
  }),

  // Competitor actions
  setCompetitors: (items) => set({ competitors: items }),
  addCompetitor: (c) => {
    let result: { ok: boolean; duplicate?: Competitor } = { ok: false }
    set((s) => {
      const nextUrl = c.url.trim().toLowerCase()
      const nextVideoId = c.videoId.trim()
      const dup = s.competitors.find((x) => {
        const sameUrl = x.url.trim().toLowerCase() === nextUrl
        const sameVideoId = nextVideoId && x.videoId.trim() === nextVideoId
        return sameUrl || sameVideoId
      })
      if (dup) { result = { ok: false, duplicate: dup }; return {} }
      const entry: Competitor = { ...c, id: `comp${Date.now()}`, addedAt: new Date().toISOString() }
      result = { ok: true }
      return { competitors: [...s.competitors, entry] }
    })
    return result
  },
  deleteCompetitor: (id) => set((s) => ({
    competitors: s.competitors.filter((c) => c.id !== id),
  })),
  updateCompetitor: (id, patch) => set((s) => ({
    competitors: s.competitors.map((c) => c.id === id ? { ...c, ...patch } : c),
  })),
}), {
  name: 'autocapcut-store',
  // Only persist data that should survive app restarts
  partialize: (s) => ({
    parentProjects:  s.parentProjects,
    childProjects:   s.childProjects,
    competitors:     s.competitors,
    analyticsEntries: s.analyticsEntries,
  }),
}))
