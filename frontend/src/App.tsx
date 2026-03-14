import Sidebar from '@/components/layout/Sidebar'
import TopBar from '@/components/layout/TopBar'
import { useAppStore } from '@/store/app.store'
import { AnimatePresence, motion } from 'framer-motion'

// Pages
import ParentProjects from '@/pages/Projects/ParentProjects'
import ChildProjects from '@/pages/Projects/ChildProjects'
import AIGen from '@/pages/Projects/tools/AIGen'
import AIAudio from '@/pages/Projects/tools/AIAudio'
import Livestream from '@/pages/Projects/tools/Livestream'
import RenderDashboard from '@/pages/RenderDashboard'
import Analytics from '@/pages/Analytics'

const PAGE_VARIANTS = {
  initial: { opacity: 0, y: 6 },
  animate: { opacity: 1, y: 0 },
  exit:    { opacity: 0, y: -4 },
}

function CurrentPage() {
  const { mainView, projectSubView } = useAppStore()

  if (mainView === 'render')    return <RenderDashboard />
  if (mainView === 'analytics') return <Analytics />

  // Projects sub-views
  if (projectSubView === 'parents')   return <ParentProjects />
  if (projectSubView === 'children')  return <ChildProjects />
  if (projectSubView === 'ai-gen')    return <AIGen />
  if (projectSubView === 'ai-audio')  return <AIAudio />
  if (projectSubView === 'livestream') return <Livestream />

  return <ParentProjects />
}

export default function App() {
  const { mainView, projectSubView } = useAppStore()
  const pageKey = `${mainView}-${projectSubView}`

  return (
    <div className="flex h-full bg-surface-100">
      <Sidebar />

      <div className="flex flex-col flex-1 min-w-0">
        <TopBar />

        <main className="flex-1 overflow-hidden">
          <AnimatePresence mode="wait">
            <motion.div
              key={pageKey}
              variants={PAGE_VARIANTS}
              initial="initial"
              animate="animate"
              exit="exit"
              transition={{ duration: 0.18, ease: 'easeOut' }}
              className="h-full"
            >
              <CurrentPage />
            </motion.div>
          </AnimatePresence>
        </main>
      </div>
    </div>
  )
}
