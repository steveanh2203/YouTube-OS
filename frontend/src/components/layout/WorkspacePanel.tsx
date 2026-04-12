import { useEffect, useState, type CSSProperties, type ReactNode } from 'react'
import { AnimatePresence, motion } from 'framer-motion'
import { PanelContext, usePanelContextValue, usePanelContext } from '@/contexts/PanelContext'
import { useAppStore } from '@/store/app.store'
import Sidebar from '@/components/layout/Sidebar'
import PanelTopBar from '@/components/layout/PanelTopBar'

// Pages
import ParentProjects from '@/pages/Projects/ParentProjects'
import ChildProjects from '@/pages/Projects/ChildProjects'
import AIGen from '@/pages/Projects/tools/AIGen'
import AIAudio from '@/pages/Projects/tools/AIAudio'
import Livestream from '@/pages/Projects/tools/Livestream'
import SRTGen from '@/pages/Projects/tools/SRTGen'
import RawSEO from '@/pages/Projects/tools/RawSEO'
import RoxyUpload from '@/pages/Projects/tools/RoxyUpload'
import AnimationTool from '@/pages/Projects/tools/Animation'
import FastEdit from '@/pages/Projects/tools/FastEdit'
import CutAutomate from '@/pages/Projects/tools/CutAutomate'
import ResourcePrep from '@/pages/Projects/tools/ResourcePrep'
import SoraGen from '@/pages/Projects/tools/SoraGen'
import AudioVisualizer from '@/pages/Projects/tools/AudioVisualizer'
import RenderDashboard from '@/pages/RenderDashboard'
import AutomatePage from '@/pages/Automate'
import Analytics from '@/pages/Analytics'
import CompetitorsPage from '@/pages/Competitors'
import ProductionPlanner from '@/pages/ProductionPlanner'
import ReplyCenter from '@/pages/ReplyCenter'
import SettingsPage from '@/pages/Settings'

// ─── Keep-alive tool views ────────────────────────────────────────────────────

const TOOL_VIEWS = ['resource-prep', 'ai-gen', 'ai-audio', 'livestream', 'srt-gen', 'raw-seo', 'roxy-upload', 'animation', 'fast-edit', 'cut-automate', 'sora-gen', 'audio-visualizer'] as const
type ToolView = typeof TOOL_VIEWS[number]

function isToolView(v: string): v is ToolView {
  return (TOOL_VIEWS as readonly string[]).includes(v)
}

const PAGE_VARIANTS = {
  initial: { opacity: 0, y: 6 },
  animate: { opacity: 1, y: 0 },
  exit:    { opacity: 0, y: -4 },
}

// ─── Inner panel content — reads from PanelContext ────────────────────────────

function PanelInner() {
  const { panelId, mainView, projectSubView } = usePanelContext()

  const [mountedTools, setMountedTools] = useState<Set<ToolView>>(new Set())

  useEffect(() => {
    if (mainView === 'projects' && isToolView(projectSubView)) {
      // eslint-disable-next-line react-hooks/set-state-in-effect
      setMountedTools(prev => {
        if (prev.has(projectSubView)) return prev
        const next = new Set(prev)
        next.add(projectSubView)
        return next
      })
    }
  }, [mainView, projectSubView])

  const showingTool = mainView === 'projects' && isToolView(projectSubView)

  let nonToolPage: ReactNode = null
  if (!showingTool) {
    if (mainView === 'planner')             nonToolPage = <ProductionPlanner />
    else if (mainView === 'render')         nonToolPage = <RenderDashboard />
    else if (mainView === 'automate')       nonToolPage = <AutomatePage />
    else if (mainView === 'reply-center')   nonToolPage = <ReplyCenter />
    else if (mainView === 'analytics')      nonToolPage = <Analytics />
    else if (mainView === 'competitors')    nonToolPage = <CompetitorsPage />
    else if (mainView === 'settings')       nonToolPage = <SettingsPage />
    else if (projectSubView === 'children') nonToolPage = <ChildProjects />
    else                                    nonToolPage = <ParentProjects />
  }

  const pageKey = `${panelId}-${mainView}-${projectSubView}`

  return (
    <div className="flex flex-1 min-h-0 overflow-hidden">
      <Sidebar />

      <div className="flex flex-col flex-1 min-w-0">
        <PanelTopBar />

        <main className="flex-1 overflow-hidden relative">

          {/* Non-tool pages (animated) */}
          <AnimatePresence mode="wait">
            {!showingTool && (
              <motion.div
                key={pageKey}
                variants={PAGE_VARIANTS}
                initial="initial"
                animate="animate"
                exit="exit"
                transition={{ duration: 0.18, ease: 'easeOut' }}
                className="h-full"
              >
                {nonToolPage}
              </motion.div>
            )}
          </AnimatePresence>

          {/* Tool pages (keep-alive) */}
          <div className={showingTool ? 'h-full' : 'hidden'}>
            {mountedTools.has('resource-prep') && <div className={projectSubView === 'resource-prep' ? 'h-full' : 'hidden'}><ResourcePrep /></div>}
            {mountedTools.has('ai-gen')      && <div className={projectSubView === 'ai-gen'      ? 'h-full' : 'hidden'}><AIGen /></div>}
            {mountedTools.has('ai-audio')    && <div className={projectSubView === 'ai-audio'    ? 'h-full' : 'hidden'}><AIAudio /></div>}
            {mountedTools.has('livestream')  && <div className={projectSubView === 'livestream'  ? 'h-full' : 'hidden'}><Livestream /></div>}
            {mountedTools.has('srt-gen')     && <div className={projectSubView === 'srt-gen'     ? 'h-full' : 'hidden'}><SRTGen /></div>}
            {mountedTools.has('raw-seo')     && <div className={projectSubView === 'raw-seo'     ? 'h-full' : 'hidden'}><RawSEO /></div>}
            {mountedTools.has('roxy-upload') && <div className={projectSubView === 'roxy-upload' ? 'h-full' : 'hidden'}><RoxyUpload /></div>}
            {mountedTools.has('animation')   && <div className={projectSubView === 'animation'   ? 'h-full' : 'hidden'}><AnimationTool /></div>}
            {mountedTools.has('fast-edit')     && <div className={projectSubView === 'fast-edit'     ? 'h-full' : 'hidden'}><FastEdit /></div>}
            {mountedTools.has('cut-automate') && <div className={projectSubView === 'cut-automate' ? 'h-full' : 'hidden'}><CutAutomate /></div>}
            {mountedTools.has('sora-gen')          && <div className={projectSubView === 'sora-gen'          ? 'h-full' : 'hidden'}><SoraGen /></div>}
            {mountedTools.has('audio-visualizer') && <div className={projectSubView === 'audio-visualizer' ? 'h-full' : 'hidden'}><AudioVisualizer /></div>}
          </div>

        </main>
      </div>
    </div>
  )
}

// ─── WorkspacePanel ───────────────────────────────────────────────────────────

interface WorkspacePanelProps {
  panelId: string
  style?: CSSProperties
  onActivate?: () => void
  isActive?: boolean
}

export default function WorkspacePanel({ panelId, style, onActivate, isActive }: WorkspacePanelProps) {
  const contextValue = usePanelContextValue(panelId)
  const splitView = useAppStore((s) => s.splitView)

  return (
    <PanelContext.Provider value={contextValue}>
      <div
        className={[
          'flex flex-col flex-1 min-w-0 overflow-hidden',
          splitView && isActive ? 'ring-1 ring-inset ring-primary-400' : '',
        ].join(' ')}
        style={style}
        onMouseDown={onActivate}
      >
        <PanelInner />
      </div>
    </PanelContext.Provider>
  )
}
