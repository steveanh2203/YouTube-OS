import { createContext, useContext } from 'react'
import { useAppStore, type MainView, type ProjectSubView } from '@/store/app.store'

// ─── Context shape ────────────────────────────────────────────────────────────

export interface PanelContextValue {
  mainView: MainView
  projectSubView: ProjectSubView
  selectedParentId: string | null
  selectedChildId: string | null
  sidebarCollapsed: boolean

  setMainView: (view: MainView) => void
  setProjectSubView: (view: ProjectSubView) => void
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

// ─── Helper: expose the single workspace through the existing context API ────

export function usePanelContextValue(): PanelContextValue {
  const store = useAppStore()

  return {
    mainView:         store.mainView,
    projectSubView:   store.projectSubView,
    selectedParentId: store.selectedParentId,
    selectedChildId:  store.selectedChildId,
    sidebarCollapsed: store.sidebarCollapsed,
    setMainView:      store.setMainView,
    setProjectSubView:store.setProjectSubView,
    selectParent:     store.selectParent,
    selectChild:      store.selectChild,
    setSidebarCollapsed: store.setSidebarCollapsed,
  }
}
