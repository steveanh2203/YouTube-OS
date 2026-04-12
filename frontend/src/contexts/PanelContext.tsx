import { createContext, useContext } from 'react'
import { useAppStore, type AutomateSubView, type MainView, type ProjectSubView, type PanelState } from '@/store/app.store'

// ─── Context shape ────────────────────────────────────────────────────────────

export interface PanelContextValue {
  panelId: string

  // Panel-scoped state
  mainView: MainView
  projectSubView: ProjectSubView
  automateSubView: AutomateSubView
  selectedParentId: string | null
  selectedChildId: string | null
  sidebarCollapsed: boolean

  // Panel-scoped actions
  setMainView: (view: MainView) => void
  setProjectSubView: (view: ProjectSubView) => void
  setAutomateSubView: (view: AutomateSubView) => void
  selectParent: (id: string | null) => void
  selectChild: (id: string | null) => void
  setSidebarCollapsed: (v: boolean) => void
}

export const PanelContext = createContext<PanelContextValue | null>(null)

// ─── Hook ─────────────────────────────────────────────────────────────────────

export function usePanelContext(): PanelContextValue {
  const ctx = useContext(PanelContext)
  if (!ctx) throw new Error('usePanelContext must be used inside a PanelContext.Provider')
  return ctx
}

// ─── Helper: build context value from store for a given panelId ───────────────

export function usePanelContextValue(panelId: string): PanelContextValue {
  const store = useAppStore()
  const panel: PanelState = store.panels.find((p) => p.id === panelId) ?? store.panels[0]

  return {
    panelId,
    mainView:         panel.mainView,
    projectSubView:   panel.projectSubView,
    automateSubView:  panel.automateSubView,
    selectedParentId: panel.selectedParentId,
    selectedChildId:  panel.selectedChildId,
    sidebarCollapsed: panel.sidebarCollapsed,
    setMainView:      (view) => store.setPanelMainView(panelId, view),
    setProjectSubView:(view) => store.setPanelSubView(panelId, view),
    setAutomateSubView:(view) => store.setPanelAutomateSubView(panelId, view),
    selectParent:     (id)   => store.selectPanelParent(panelId, id),
    selectChild:      (id)   => store.selectPanelChild(panelId, id),
    setSidebarCollapsed: (v) => store.setPanelSidebarCollapsed(panelId, v),
  }
}
