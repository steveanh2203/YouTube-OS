import { create } from 'zustand'
import { useAppStore, type MainView, type ProjectSubView } from '@/store/app.store'

// ─── Types ────────────────────────────────────────────────────────────────────

export type TaskStatus = 'running' | 'done' | 'error' | 'cancelled'

export interface BackgroundTask {
  id: string
  toolId: ProjectSubView
  toolLabel: string       // e.g. "Cut Automate"
  label: string           // e.g. "Bước 03 — Tách Ảnh"
  status: TaskStatus
  startedAt: number
  completedAt?: number
  message?: string        // result message on completion
  progress?: number
  canCancel?: boolean
  jobId?: string
  panelId?: string
  targetMainView?: MainView
  targetProjectSubView?: ProjectSubView
  targetParentId?: string | null
  targetChildId?: string | null
}

// ─── Store ────────────────────────────────────────────────────────────────────

interface TaskStore {
  tasks: BackgroundTask[]

  /** Register a new running task, returns its id */
  addTask: (task: Omit<BackgroundTask, 'id' | 'status' | 'startedAt'>) => string

  /** Mark a task as done or error */
  completeTask: (id: string, status: 'done' | 'error' | 'cancelled', message?: string) => void

  /** Patch a task while it is running */
  updateTask: (id: string, patch: Partial<Omit<BackgroundTask, 'id'>>) => void

  /** Remove a task from the list (e.g. after toast is dismissed) */
  removeTask: (id: string) => void

  /** Clear all finished tasks */
  clearFinished: () => void
}

function uid() { return Math.random().toString(36).slice(2, 9) }

export const useTaskStore = create<TaskStore>((set) => ({
  tasks: [],

  addTask: (task) => {
    const id = uid()
    const appState = useAppStore.getState()
    const activePanel = appState.panels.find((panel) => panel.id === appState.activePanelId) ?? appState.panels[0]

    set(s => ({
      tasks: [...s.tasks, {
        ...task,
        id,
        status: 'running',
        startedAt: Date.now(),
        progress: task.progress ?? 0,
        panelId: activePanel?.id,
        targetMainView: activePanel?.mainView,
        targetProjectSubView: activePanel?.projectSubView,
        targetParentId: activePanel?.selectedParentId ?? null,
        targetChildId: activePanel?.selectedChildId ?? null,
      }],
    }))
    return id
  },

  completeTask: (id, status, message) => {
    set(s => ({
      tasks: s.tasks.map(t =>
        t.id === id
          ? { ...t, status, message, completedAt: Date.now(), progress: 100 }
          : t
      ),
    }))
  },

  updateTask: (id, patch) => {
    set(s => ({
      tasks: s.tasks.map(t =>
        t.id === id
          ? { ...t, ...patch }
          : t
      ),
    }))
  },

  removeTask: (id) => {
    set(s => ({ tasks: s.tasks.filter(t => t.id !== id) }))
  },

  clearFinished: () => {
    set(s => ({ tasks: s.tasks.filter(t => t.status === 'running') }))
  },
}))

// Convenience helpers — call anywhere without hooks
export const taskStore = {
  add: (task: Omit<BackgroundTask, 'id' | 'status' | 'startedAt'>) =>
    useTaskStore.getState().addTask(task),
  complete: (id: string, status: 'done' | 'error' | 'cancelled', message?: string) =>
    useTaskStore.getState().completeTask(id, status, message),
  update: (id: string, patch: Partial<Omit<BackgroundTask, 'id'>>) =>
    useTaskStore.getState().updateTask(id, patch),
  remove: (id: string) =>
    useTaskStore.getState().removeTask(id),
}
