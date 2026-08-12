import { lazy, Suspense, useEffect, useState, type ReactNode } from 'react'
import { AnimatePresence, motion } from 'framer-motion'
import { PanelContext, usePanelContextValue, usePanelContext } from '@/contexts/PanelContext'
import Sidebar from '@/components/layout/Sidebar'
import PanelTopBar from '@/components/layout/PanelTopBar'

// Route-level code splitting keeps optional production tools out of the initial bundle.
const ParentProjects = lazy(() => import('@/pages/Projects/ParentProjects'))
const ChildProjects = lazy(() => import('@/pages/Projects/ChildProjects'))
const AIGen = lazy(() => import('@/pages/Projects/tools/AIGen'))
const AIAudio = lazy(() => import('@/pages/Projects/tools/AIAudio'))
const Livestream = lazy(() => import('@/pages/Projects/tools/Livestream'))
const SRTGen = lazy(() => import('@/pages/Projects/tools/SRTGen'))
const RawSEO = lazy(() => import('@/pages/Projects/tools/RawSEO'))
const RoxyUpload = lazy(() => import('@/pages/Projects/tools/RoxyUpload'))
const FastEdit = lazy(() => import('@/pages/Projects/tools/FastEdit'))
const CutAutomate = lazy(() => import('@/pages/Projects/tools/CutAutomate'))
const ResourcePrep = lazy(() => import('@/pages/Projects/tools/ResourcePrep'))
const SoraGen = lazy(() => import('@/pages/Projects/tools/SoraGen'))
const RenderDashboard = lazy(() => import('@/pages/RenderDashboard'))
const Analytics = lazy(() => import('@/pages/Analytics'))
const CompetitorsPage = lazy(() => import('@/pages/Competitors'))
const ReplyCenter = lazy(() => import('@/pages/ReplyCenter'))
const SettingsPage = lazy(() => import('@/pages/Settings'))

// ─── Keep-alive tool views ────────────────────────────────────────────────────

const TOOL_VIEWS = ['resource-prep', 'ai-gen', 'ai-audio', 'livestream', 'srt-gen', 'raw-seo', 'roxy-upload', 'fast-edit', 'cut-automate', 'sora-gen'] as const
type ToolView = typeof TOOL_VIEWS[number]

function isToolView(v: string): v is ToolView {
  return (TOOL_VIEWS as readonly string[]).includes(v)
}

const PAGE_VARIANTS = {
  initial: { opacity: 0 },
  animate: { opacity: 1 },
  exit:    { opacity: 0 },
}

// ─── Inner panel content — reads from PanelContext ────────────────────────────

function PanelInner() {
  const { mainView, projectSubView } = usePanelContext()

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
    if (mainView === 'render')              nonToolPage = <RenderDashboard />
    else if (mainView === 'reply-center')   nonToolPage = <ReplyCenter />
    else if (mainView === 'analytics')      nonToolPage = <Analytics />
    else if (mainView === 'competitors')    nonToolPage = <CompetitorsPage />
    else if (mainView === 'settings')       nonToolPage = <SettingsPage />
    else if (projectSubView === 'children') nonToolPage = <ChildProjects />
    else                                    nonToolPage = <ParentProjects />
  }

  const pageKey = `${mainView}-${projectSubView}`

  return (
    <div className="flex min-h-0 flex-1 overflow-hidden bg-background">
      <Sidebar />

      <div className="flex min-w-0 flex-1 flex-col bg-background">
        <PanelTopBar />

        <main className="relative flex-1 overflow-hidden bg-background">
          <Suspense fallback={<div className="flex h-full items-center justify-center text-sm text-muted-foreground">Loading workspace…</div>}>

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
            {mountedTools.has('fast-edit')     && <div className={projectSubView === 'fast-edit'     ? 'h-full' : 'hidden'}><FastEdit /></div>}
            {mountedTools.has('cut-automate') && <div className={projectSubView === 'cut-automate' ? 'h-full' : 'hidden'}><CutAutomate /></div>}
            {mountedTools.has('sora-gen')          && <div className={projectSubView === 'sora-gen'          ? 'h-full' : 'hidden'}><SoraGen /></div>}
          </div>
          </Suspense>

        </main>
      </div>
    </div>
  )
}

// ─── WorkspacePanel ───────────────────────────────────────────────────────────

export default function WorkspacePanel() {
  const contextValue = usePanelContextValue()

  return (
    <PanelContext.Provider value={contextValue}>
      <div className="workspace-panel flex min-w-0 flex-1 flex-col overflow-hidden bg-background">
        <PanelInner />
      </div>
    </PanelContext.Provider>
  )
}
