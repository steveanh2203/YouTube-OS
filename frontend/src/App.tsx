import { useEffect, useState, useRef, Component, type ReactNode } from 'react'
import { useAppStore } from '@/store/app.store'
import ToastContainer from '@/components/ui/ToastContainer'
import TaskToastStack from '@/components/ui/TaskToastStack'
import TopBar from '@/components/layout/TopBar'
import WorkspacePanel from '@/components/layout/WorkspacePanel'
import SplitDivider from '@/components/layout/SplitDivider'
import { getMinPanelWidth } from '@/components/layout/splitLayout'
import CommunityScheduler from '@/components/community/CommunityScheduler'

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
  const { splitView, activePanelId, setActivePanelId } = useAppStore()

  // Left panel width in pixels (null = 50/50 default via flex)
  const [leftWidth, setLeftWidth] = useState<number | null>(null)
  const containerRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    if (!splitView || !containerRef.current) return

    const clampLeftWidth = () => {
      if (!containerRef.current || leftWidth == null) return
      const total = containerRef.current.getBoundingClientRect().width
      const minPanelWidth = getMinPanelWidth(total)
      const clamped = Math.max(minPanelWidth, Math.min(leftWidth, total - minPanelWidth))
      if (clamped !== leftWidth) {
        setLeftWidth(clamped)
      }
    }

    clampLeftWidth()
    const observer = new ResizeObserver(clampLeftWidth)
    observer.observe(containerRef.current)
    return () => observer.disconnect()
  }, [splitView, leftWidth])

  return (
    <ErrorBoundary>
      <div className="flex flex-col h-full bg-surface-100">
        {/* Global top bar — split toggle lives here */}
        <TopBar />
        <CommunityScheduler />

        {/* Workspace: 1 or 2 panels */}
        <div ref={containerRef} className="flex flex-1 min-h-0 overflow-hidden">

          {/* Left (or only) panel */}
          <WorkspacePanel
            panelId="left"
            isActive={activePanelId === 'left'}
            onActivate={() => setActivePanelId('left')}
            style={splitView && leftWidth != null ? { width: leftWidth, flex: 'none' } : { flex: 1 }}
          />

          {/* Divider only shows in split mode, but right panel stays mounted to preserve tool session state */}
          {splitView && (
            <SplitDivider onResize={setLeftWidth} containerRef={containerRef} />
          )}
          <WorkspacePanel
            panelId="right"
            isActive={splitView && activePanelId === 'right'}
            onActivate={() => splitView && setActivePanelId('right')}
            style={splitView ? { flex: 1 } : { width: 0, flex: 'none', display: 'none' }}
          />
        </div>

        {/* Global toast notifications */}
        <ToastContainer />

        {/* Background task notifications */}
        <TaskToastStack />
      </div>
    </ErrorBoundary>
  )
}
