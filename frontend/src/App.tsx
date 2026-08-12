/* Hallmark · pre-emit critique: P5 H5 E5 S5 R5 V3 */
import { Component, useEffect, type ReactNode } from 'react'
import ToastContainer from '@/components/ui/ToastContainer'
import TaskToastStack from '@/components/ui/TaskToastStack'
import WorkspacePanel from '@/components/layout/WorkspacePanel'
import CommunityScheduler from '@/components/community/CommunityScheduler'
import { useAppStore } from '@/store/app.store'

class ErrorBoundary extends Component<{ children: ReactNode }, { error: Error | null }> {
  state = { error: null }
  static getDerivedStateFromError(error: Error) { return { error } }
  render() {
    if (this.state.error) {
      const err = this.state.error as Error
      return (
        <div style={{ padding: 32, fontFamily: 'monospace', color: 'red', whiteSpace: 'pre-wrap' }}>
          <b>Runtime Error:</b>{'\n'}{err.message}{'\n\n'}{err.stack}
        </div>
      )
    }
    return this.props.children
  }
}

export default function App() {
  const themeMode = useAppStore((state) => state.themeMode)

  useEffect(() => {
    const root = document.documentElement
    root.classList.toggle('dark', themeMode === 'dark')
    root.classList.toggle('light', themeMode === 'light')
    root.style.colorScheme = themeMode
  }, [themeMode])

  return (
    <ErrorBoundary>
      <div className={`${themeMode} theme flex h-dvh flex-col overflow-hidden bg-background text-foreground`}>
        <CommunityScheduler />

        <div className="flex min-h-0 flex-1 overflow-hidden bg-background">
          <WorkspacePanel />
        </div>

        {/* Global toast notifications */}
        <ToastContainer />

        {/* Background task notifications */}
        <TaskToastStack />
      </div>
    </ErrorBoundary>
  )
}
