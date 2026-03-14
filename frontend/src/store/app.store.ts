import { create } from 'zustand'

// ─── Enums (mirrors Python backend) ────────────────────────────────────────

export type ProjectSource = 'local' | 'cloud_cache'
export type ProjectStatus = 'pending' | 'processing' | 'done' | 'failed'
export type ChildStatus   = 'Draft' | 'processing' | 'done' | 'failed'
export type RenderStatus  = 'queued' | 'running' | 'done' | 'failed' | 'cancelled'

// ─── Core Interfaces (mirrors Python models) ────────────────────────────────

/** mirrors ParentProjectPreset in flet_app.py */
export interface ParentProject {
  id: string
  name: string
  author: string
  publisher: string
  copyright: string
  keywordsRaw: string      // comma/newline separated, e.g. "gadget, review, tech"
  childCount: number
  createdAt: string
}

/** mirrors ChildProjectRecord in flet_app.py */
export interface ChildProject {
  id: string
  parentId: string
  name: string             // e.g. "Video 01"
  status: ChildStatus
  title: string            // YouTube video title
  description: string      // YouTube video description
  seedingCommentsRaw: string  // newline-separated seed comments
  folderPath: string       // absolute path to child project folder
  aiAudioNotes: string     // notes for AI audio service
  livestreamNotes: string  // notes for Livestream service
}

/** mirrors ProjectItem in models.py (a CapCut draft project) */
export interface CapcutProject {
  id: string
  name: string
  path: string             // absolute path to draft folder
  source: ProjectSource    // 'local' | 'cloud_cache'
  status: ProjectStatus
  notes: string
  assignedProjectId: string | null   // linked ChildProject id
  isSelected: boolean
  selectionOrder: number | null
  metadata: ProjectMetadata
}

/** metadata extracted by project_loader.py / inspect_project() */
export interface ProjectMetadata {
  durationS: number        // duration in seconds
  fps: number
  width: number
  height: number
  videoSegments: number
  trackCount: number
  ffmpegReady: boolean
  modifiedAt: string       // ISO date string
}

/** one item in the render queue — maps to AutomationController run */
export interface RenderJob {
  id: string
  capcutProjectId: string
  projectName: string
  status: RenderStatus
  progress: number         // 0–100
  startedAt: string | null
  completedAt: string | null
  exportPath: string | null
  errorMessage: string | null
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

// ─── Navigation ─────────────────────────────────────────────────────────────

export type MainView      = 'projects' | 'render' | 'analytics'
export type ProjectSubView = 'parents' | 'children' | 'ai-gen' | 'ai-audio' | 'livestream'

// ─── Store State ─────────────────────────────────────────────────────────────

interface AppState {
  // Navigation
  mainView: MainView
  projectSubView: ProjectSubView
  selectedParentId: string | null
  selectedChildId: string | null

  // Data — mock, will be replaced by FastAPI calls
  parentProjects: ParentProject[]
  childProjects: ChildProject[]
  capcutProjects: CapcutProject[]
  renderJobs: RenderJob[]
  analyticsEntries: AnalyticsEntry[]

  // UI
  sidebarCollapsed: boolean
  isLoading: boolean
  loadingText: string

  // Navigation actions
  setMainView: (view: MainView) => void
  setProjectSubView: (view: ProjectSubView) => void
  selectParent: (id: string | null) => void
  selectChild: (id: string | null) => void
  setSidebarCollapsed: (v: boolean) => void
  setLoading: (v: boolean, text?: string) => void

  // Parent CRUD
  addParent: (p: Omit<ParentProject, 'id' | 'childCount' | 'createdAt'>) => void
  updateParent: (id: string, patch: Partial<ParentProject>) => void
  deleteParent: (id: string) => void

  // Child CRUD
  addChild: (c: Omit<ChildProject, 'id'>) => void
  updateChild: (id: string, patch: Partial<ChildProject>) => void
  deleteChild: (id: string) => void

  // CapCut project actions
  setCapcutStatus: (id: string, status: ProjectStatus) => void
  assignCapcutToChild: (capcutId: string, childId: string | null) => void
  toggleCapcutSelection: (id: string) => void

  // Render actions
  addRenderJob: (capcutProjectId: string) => void
  updateRenderJob: (id: string, patch: Partial<RenderJob>) => void
  cancelRenderJob: (id: string) => void
}

// ─── Mock Data ───────────────────────────────────────────────────────────────

// No demo data — all data will come from FastAPI backend
const MOCK_PARENTS: ParentProject[]     = []
const MOCK_CHILDREN: ChildProject[]     = []
const MOCK_CAPCUT: CapcutProject[]      = []
const MOCK_RENDER_JOBS: RenderJob[]     = []
const MOCK_ANALYTICS: AnalyticsEntry[]  = []

// ─── Store ────────────────────────────────────────────────────────────────────

export const useAppStore = create<AppState>((set) => ({
  mainView: 'projects',
  projectSubView: 'parents',
  selectedParentId: null,
  selectedChildId: null,
  parentProjects: MOCK_PARENTS,
  childProjects: MOCK_CHILDREN,
  capcutProjects: MOCK_CAPCUT,
  renderJobs: MOCK_RENDER_JOBS,
  analyticsEntries: MOCK_ANALYTICS,
  sidebarCollapsed: false,
  isLoading: false,
  loadingText: '',

  setMainView: (view) => set({ mainView: view }),
  setProjectSubView: (view) => set({ projectSubView: view }),
  selectParent: (id) => set({ selectedParentId: id }),
  selectChild: (id) => set({ selectedChildId: id }),
  setSidebarCollapsed: (v) => set({ sidebarCollapsed: v }),
  setLoading: (v, text = '') => set({ isLoading: v, loadingText: text }),

  // Parent CRUD
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
    childProjects: [...s.childProjects, { ...c, id: `c${Date.now()}` }],
    parentProjects: s.parentProjects.map((p) =>
      p.id === c.parentId ? { ...p, childCount: p.childCount + 1 } : p
    ),
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

  // CapCut project actions
  setCapcutStatus: (id, status) => set((s) => ({
    capcutProjects: s.capcutProjects.map((c) => c.id === id ? { ...c, status } : c),
  })),
  assignCapcutToChild: (capcutId, childId) => set((s) => ({
    capcutProjects: s.capcutProjects.map((c) =>
      c.id === capcutId ? { ...c, assignedProjectId: childId } : c
    ),
  })),
  toggleCapcutSelection: (id) => set((s) => {
    const proj = s.capcutProjects.find((c) => c.id === id)
    if (!proj) return {}
    const selectedCount = s.capcutProjects.filter((c) => c.isSelected).length
    return {
      capcutProjects: s.capcutProjects.map((c) =>
        c.id === id
          ? { ...c, isSelected: !c.isSelected, selectionOrder: !c.isSelected ? selectedCount + 1 : null }
          : c
      ),
    }
  }),

  // Render actions
  addRenderJob: (capcutProjectId) => set((s) => {
    const proj = s.capcutProjects.find((c) => c.id === capcutProjectId)
    if (!proj) return {}
    return {
      renderJobs: [...s.renderJobs, {
        id: `rj${Date.now()}`,
        capcutProjectId,
        projectName: proj.name,
        status: 'queued',
        progress: 0,
        startedAt: null,
        completedAt: null,
        exportPath: null,
        errorMessage: null,
      }],
    }
  }),
  updateRenderJob: (id, patch) => set((s) => ({
    renderJobs: s.renderJobs.map((j) => j.id === id ? { ...j, ...patch } : j),
  })),
  cancelRenderJob: (id) => set((s) => ({
    renderJobs: s.renderJobs.map((j) =>
      j.id === id && (j.status === 'queued' || j.status === 'running')
        ? { ...j, status: 'cancelled' }
        : j
    ),
  })),
}))
