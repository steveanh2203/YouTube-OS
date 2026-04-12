import { usePanelContext } from '@/contexts/PanelContext'
import { useAppStore, type ProjectSubView } from '@/store/app.store'
import { RefreshCw, ChevronLeft } from 'lucide-react'

const SUB_LABELS: Record<string, string> = {
  parents:      'All Projects',
  children:     'Child Projects',
  'ai-gen':     'AI Gen',
  'ai-audio':   'AI Audio',
  'livestream': 'Livestream',
  'srt-gen':    'SRT Generator',
  'raw-seo':    'Raw SEO',
  'roxy-upload':'Roxy Upload',
  'youtube-reply': 'YouTube Reply',
  'animation':  'Animation',
  'fast-edit':  'Fast Edit',
  'cut-automate': 'Cut Automate',
}

const VIEW_LABELS: Record<string, string> = {
  projects:   'Projects',
  planner:    'Video Planner',
  render:     'Render Dashboard',
  automate:   'Automate',
  'reply-center': 'Reply Center',
  analytics:  'Analytics',
  competitors: 'Competitors',
  settings: 'Settings',
}

const BACK_MAP: Partial<Record<ProjectSubView, ProjectSubView>> = {
  children:     'parents',
  'ai-gen':     'children',
  'ai-audio':   'children',
  'livestream': 'children',
  'srt-gen':    'children',
  'raw-seo':    'children',
  'roxy-upload':'children',
  'youtube-reply': 'children',
  'animation':  'children',
  'fast-edit':  'children',
  'cut-automate': 'children',
}

export default function PanelTopBar() {
  const { mainView, projectSubView, automateSubView, setProjectSubView } = usePanelContext()
  const { isLoading, loadingText } = useAppStore()

  const title = VIEW_LABELS[mainView] ?? mainView
  const sub = mainView === 'projects'
    ? SUB_LABELS[projectSubView]
    : mainView === 'automate'
      ? automateSubView === 'long' ? 'Long Video' : 'Short Video'
      : undefined
  const backTarget = mainView === 'projects' ? BACK_MAP[projectSubView] : undefined

  return (
    <header
      className="flex items-center h-11 px-4 border-b border-surface-200 bg-white shrink-0 gap-3"
      data-tauri-drag-region
    >
      {/* Back button */}
      {backTarget && (
        <button
          className="btn-icon text-surface-400 hover:text-surface-700 hover:bg-surface-100 shrink-0"
          onClick={() => setProjectSubView(backTarget)}
          title="Go back"
        >
          <ChevronLeft size={16} />
        </button>
      )}

      {/* Breadcrumb */}
      <div className="flex items-center gap-1.5 flex-1" data-tauri-drag-region>
        {backTarget ? (
          <button
            className="text-sm text-surface-400 hover:text-primary-600 transition-colors"
            onClick={() => setProjectSubView(backTarget)}
          >
            {SUB_LABELS[backTarget]}
          </button>
        ) : (
          <span className="text-sm font-semibold text-surface-900">{title}</span>
        )}
        {sub && backTarget && (
          <>
            <span className="text-surface-300">/</span>
            <span className="text-sm font-semibold text-surface-900">{sub}</span>
          </>
        )}
        {sub && !backTarget && (
          <>
            <span className="text-surface-300">/</span>
            <span className="text-sm text-surface-500">{sub}</span>
          </>
        )}
      </div>

      {/* Loading indicator */}
      {isLoading && (
        <div className="flex items-center gap-2 text-xs text-surface-500">
          <RefreshCw size={12} className="animate-spin" />
          <span>{loadingText || 'Loading...'}</span>
        </div>
      )}
    </header>
  )
}
