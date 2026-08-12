import { usePanelContext } from '@/contexts/PanelContext'
import { useAppStore, type ProjectSubView } from '@/store/app.store'
import { RefreshCw, ChevronLeft } from 'lucide-react'
import { Button } from '@/components/ui/button'

const SUB_LABELS: Record<string, string> = {
  parents:      'All Projects',
  children:     'Child Projects',
  'resource-prep': 'Resource Prep',
  'ai-gen':     'AI Gen',
  'ai-audio':   'AI Audio',
  'livestream': 'Livestream',
  'srt-gen':    'SRT Generator',
  'raw-seo':    'Raw SEO',
  'roxy-upload':'Roxy Upload',
  'youtube-reply': 'YouTube Reply',
  'fast-edit':  'Fast Edit',
  'cut-automate': 'Cut Automate',
  'sora-gen': 'Sora Gen',
}

const VIEW_LABELS: Record<string, string> = {
  projects:   'Projects',
  render:     'FFmpeg Studio',
  'reply-center': 'Reply Center',
  analytics:  'Niche Research',
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
  'fast-edit':  'children',
  'cut-automate': 'children',
  'resource-prep': 'children',
  'sora-gen': 'children',
}

export default function PanelTopBar() {
  const { mainView, projectSubView, setProjectSubView } = usePanelContext()
  const { isLoading, loadingText } = useAppStore()

  const title = VIEW_LABELS[mainView] ?? mainView
  const sub = mainView === 'projects'
    ? SUB_LABELS[projectSubView]
    : undefined
  const backTarget = mainView === 'projects' ? BACK_MAP[projectSubView] : undefined

  return (
    <header className="flex h-11 shrink-0 items-center gap-2 border-b border-border bg-background/95 px-3">
      {/* Back button */}
      {backTarget && (
        <Button
          variant="ghost"
          size="icon-sm"
          className="shrink-0 text-muted-foreground"
          onClick={() => setProjectSubView(backTarget)}
          aria-label="Go back"
        >
          <ChevronLeft />
        </Button>
      )}

      {/* Breadcrumb */}
      <div className="flex min-w-0 flex-1 items-center gap-1.5">
        {backTarget ? (
          <button
            className="whitespace-nowrap text-sm text-muted-foreground transition-colors hover:text-primary"
            onClick={() => setProjectSubView(backTarget)}
          >
            {SUB_LABELS[backTarget]}
          </button>
        ) : (
          <span className="truncate font-heading text-sm font-semibold text-foreground">{title}</span>
        )}
        {sub && backTarget && (
          <>
            <span className="text-border">/</span>
            <span className="truncate text-sm font-medium text-foreground">{sub}</span>
          </>
        )}
        {sub && !backTarget && (
          <>
            <span className="text-border">/</span>
            <span className="truncate text-sm text-muted-foreground">{sub}</span>
          </>
        )}
      </div>

      {/* Loading indicator */}
      {isLoading && (
        <div className="flex items-center gap-2 text-xs text-muted-foreground" aria-live="polite">
          <RefreshCw size={12} className="animate-spin" />
          <span>{loadingText || 'Loading...'}</span>
        </div>
      )}
    </header>
  )
}
