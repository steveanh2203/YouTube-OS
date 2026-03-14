import { useAppStore } from '@/store/app.store'
import { RefreshCw, Settings } from 'lucide-react'

const VIEW_LABELS: Record<string, string> = {
  projects:   'Projects',
  render:     'Render Dashboard',
  analytics:  'Analytics',
}

const SUB_LABELS: Record<string, string> = {
  parents:    'All Projects',
  children:   'Child Projects',
  'ai-gen':   'AI Gen',
  'ai-audio': 'AI Audio',
  livestream: 'Livestream',
}

export default function TopBar() {
  const { mainView, projectSubView, isLoading, loadingText } = useAppStore()

  const title = VIEW_LABELS[mainView] ?? mainView
  const sub = mainView === 'projects' ? SUB_LABELS[projectSubView] : undefined

  return (
    <header
      className="flex items-center h-11 px-4 border-b border-surface-200 bg-white shrink-0 gap-3"
      data-tauri-drag-region
    >
      {/* Breadcrumb */}
      <div className="flex items-center gap-1.5 flex-1" data-tauri-drag-region>
        <span className="text-sm font-semibold text-surface-900">{title}</span>
        {sub && (
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

      {/* Settings */}
      <div className="flex items-center gap-1 ml-2">
        <button
          className="btn-icon text-surface-400 hover:text-surface-700 hover:bg-surface-100"
          title="Settings"
        >
          <Settings size={14} />
        </button>
      </div>
    </header>
  )
}
