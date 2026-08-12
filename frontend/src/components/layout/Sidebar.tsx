import { cn } from '@/lib/utils'
import { useAppStore, type MainView, type ProjectSubView } from '@/store/app.store'
import { usePanelContext } from '@/contexts/PanelContext'
import {
  FolderOpen, LayoutDashboard, Radar, ChevronLeft, ChevronRight,
  Scissors, Cpu, Mic, Radio, ChevronRight as Chevron, Target,
  FileText, Tag, Upload, Film, ClipboardList, Video, MessageSquareReply, Settings,
  Moon, Sun,
} from 'lucide-react'

export default function Sidebar() {
  const {
    mainView, setMainView,
    projectSubView, setProjectSubView,
    sidebarCollapsed, setSidebarCollapsed,
    selectedParentId, selectParent,
    selectedChildId, selectChild,
  } = usePanelContext()

  const { parentProjects, themeMode, setThemeMode } = useAppStore()

  const collapsed = sidebarCollapsed

  const mainNav = [
    { id: 'projects',     label: 'Projects',     icon: FolderOpen },
    { id: 'render',       label: 'FFmpeg Studio', icon: LayoutDashboard },
    { id: 'reply-center', label: 'Reply Center',  icon: MessageSquareReply },
    { id: 'analytics',    label: 'Niche Research', icon: Radar },
    { id: 'competitors',  label: 'Competitors',   icon: Target },
    { id: 'settings',     label: 'Settings',      icon: Settings },
  ] as { id: MainView; label: string; icon: React.ElementType }[]

  const toolNav = [
    { id: 'resource-prep', label: 'Resource Prep', icon: ClipboardList },
    { id: 'ai-gen',      label: 'AI Gen',      icon: Cpu },
    { id: 'ai-audio',    label: 'AI Audio',    icon: Mic },
    { id: 'livestream',  label: 'Livestream',  icon: Radio },
    { id: 'srt-gen',     label: 'SRT Gen',     icon: FileText },
    { id: 'raw-seo',     label: 'Raw SEO',     icon: Tag },
    { id: 'roxy-upload', label: 'Roxy Upload', icon: Upload },
    { id: 'fast-edit',     label: 'Fast Edit',     icon: Film },
    { id: 'cut-automate', label: 'Cut Automate', icon: Scissors },
    { id: 'sora-gen',          label: 'Sora Gen',        icon: Video },
    // { id: 'animation', label: 'Animation', icon: Sparkles }, // hidden
  ] as { id: ProjectSubView; label: string; icon: React.ElementType }[]

  return (
    <aside
      className={cn(
        'flex shrink-0 flex-col border-r border-sidebar-border bg-sidebar text-sidebar-foreground transition-[width] duration-200',
        collapsed ? 'w-14' : 'w-52',
      )}
    >
      {/* Logo + Collapse */}
      {collapsed ? (
        <div className="flex flex-col items-center gap-2 border-b border-sidebar-border py-2.5">
          <div className="flex size-7 shrink-0 items-center justify-center rounded-lg bg-sidebar-primary text-sidebar-primary-foreground shadow-sm">
            <Scissors size={15} strokeWidth={2.5} />
          </div>
          <button
            className="btn-icon text-sidebar-foreground/45 hover:bg-sidebar-accent hover:text-sidebar-foreground"
            onClick={() => setSidebarCollapsed(false)}
            title="Expand"
          >
            <ChevronRight size={14} />
          </button>
        </div>
      ) : (
        <div className="flex items-center gap-2.5 border-b border-sidebar-border px-3 py-2.5">
          <div className="flex size-7 shrink-0 items-center justify-center rounded-lg bg-sidebar-primary text-sidebar-primary-foreground shadow-sm">
            <Scissors size={15} strokeWidth={2.5} />
          </div>
          <span className="flex-1 truncate font-heading text-sm font-semibold tracking-[-0.025em] text-sidebar-foreground">
            MasterOS
          </span>
          <button
            className="btn-icon text-sidebar-foreground/45 hover:bg-sidebar-accent hover:text-sidebar-foreground"
            onClick={() => setSidebarCollapsed(true)}
            title="Collapse"
          >
            <ChevronLeft size={14} />
          </button>
        </div>
      )}

      {/* Main Nav */}
      <nav className="flex flex-1 flex-col gap-0.5 overflow-x-hidden overflow-y-auto p-2">
        {mainNav.map(({ id, label, icon: Icon }) => {
          return (
            <button
              key={id}
              className={cn('nav-item w-full', mainView === id && 'active')}
              onClick={() => setMainView(id)}
              title={collapsed ? label : undefined}
            >
              <Icon size={16} className="shrink-0" />
              {!collapsed && <span>{label}</span>}
            </button>
          )
        })}

        {/* Projects sub-nav — only when on projects view and not collapsed */}
        {mainView === 'projects' && !collapsed && (
          <div className="mt-2">
            <p className="section-title">Workspace</p>

            {/* Parent list */}
            {projectSubView !== 'parents' && (
              <button
                className="nav-item w-full text-xs"
                onClick={() => { setProjectSubView('parents'); selectParent(null); selectChild(null) }}
              >
                <Chevron size={12} className="rotate-180" />
                All Projects
              </button>
            )}

            {projectSubView === 'parents' && parentProjects.map((p) => (
              <button
                key={p.id}
                className={cn(
                  'w-full flex items-center gap-2 rounded-md cursor-pointer',
                  'px-3 py-1.5 text-sm',
                  'transition-colors duration-150 hover:bg-sidebar-accent',
                  selectedParentId === p.id ? 'bg-sidebar-accent font-medium text-sidebar-foreground' : 'text-sidebar-foreground/65',
                )}
                onClick={() => {
                  selectParent(p.id)
                  setProjectSubView('children')
                }}
              >
                <FolderOpen size={13} className="shrink-0" />
                <span className="truncate">{p.name}</span>
                <span className="ml-auto rounded-md border border-sidebar-border bg-sidebar-accent px-1.5 py-0.5 text-xs text-sidebar-foreground/60">{p.childCount}</span>
              </button>
            ))}

            {/* Child tools — show when a child is selected */}
            {selectedChildId && (
              <>
                <div className="divider my-2" />
                <p className="section-title">Child Tools</p>
                {toolNav.map(({ id, label, icon: Icon }) => (
                  <button
                    key={id}
                    className={cn('nav-item w-full', projectSubView === id && 'active')}
                    onClick={() => setProjectSubView(id)}
                  >
                    <Icon size={14} className="shrink-0" />
                    <span>{label}</span>
                  </button>
                ))}
              </>
            )}
          </div>
        )}
      </nav>

      {/* Theme + bottom info */}
      <div className="border-t border-sidebar-border p-2">
        <button
          type="button"
          className={cn('nav-item', collapsed && 'justify-center px-0')}
          onClick={() => setThemeMode(themeMode === 'dark' ? 'light' : 'dark')}
          aria-label={themeMode === 'dark' ? 'Switch to light theme' : 'Switch to dark theme'}
          title={collapsed ? (themeMode === 'dark' ? 'Light theme' : 'Dark theme') : undefined}
        >
          {themeMode === 'dark' ? <Sun size={16} /> : <Moon size={16} />}
          {!collapsed && <span>{themeMode === 'dark' ? 'Light theme' : 'Dark theme'}</span>}
        </button>
        {!collapsed && (
          <p className="mt-1 px-3 text-[10px] font-medium uppercase tracking-[0.12em] text-sidebar-foreground/30">
            Web · v0.1.0
          </p>
        )}
      </div>
    </aside>
  )
}
