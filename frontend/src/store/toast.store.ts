import { create } from 'zustand'

export type ToastType = 'success' | 'error' | 'info' | 'warning'

export interface ToastItem {
  id: string
  type: ToastType
  title: string
  message?: string
  duration?: number   // ms, default 4000. 0 = persistent
}

interface ToastStore {
  toasts: ToastItem[]
  add: (toast: Omit<ToastItem, 'id'>) => string
  remove: (id: string) => void
  clear: () => void
}

function uid() { return Math.random().toString(36).slice(2, 9) }

export const useToastStore = create<ToastStore>((set) => ({
  toasts: [],

  add: (toast) => {
    const id = uid()
    set(state => ({ toasts: [...state.toasts, { ...toast, id }] }))
    const duration = toast.duration ?? 4000
    if (duration > 0) {
      setTimeout(() => {
        set(state => ({ toasts: state.toasts.filter(t => t.id !== id) }))
      }, duration)
    }
    return id
  },

  remove: (id) => {
    set(state => ({ toasts: state.toasts.filter(t => t.id !== id) }))
  },

  clear: () => set({ toasts: [] }),
}))

// Convenience helpers — call anywhere without hooks
export const toast = {
  success: (title: string, message?: string, duration?: number) =>
    useToastStore.getState().add({ type: 'success', title, message, duration }),
  error: (title: string, message?: string, duration?: number) =>
    useToastStore.getState().add({ type: 'error', title, message, duration }),
  info: (title: string, message?: string, duration?: number) =>
    useToastStore.getState().add({ type: 'info', title, message, duration }),
  warning: (title: string, message?: string, duration?: number) =>
    useToastStore.getState().add({ type: 'warning', title, message, duration }),
}
