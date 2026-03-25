import { cn } from '@/lib/utils'
import { useAppStore, type MainView, type ProjectSubView } from '@/store/app.store'
import { usePanelContext } from '@/contexts/PanelContext'
import {
  FolderOpen, LayoutDashboard, BarChart2, ChevronLeft, ChevronRight,
  Scissors, Cpu, Mic, Radio, ChevronRight as Chevron, Target,
  FileText, Tag, Upload, Film, ClipboardList, CalendarRange, Video,
} from 'lucide-react'

export default function Sidebar() {
  const {
    mainView, setMainView,
    projectSubView, setProjectSubView,
    sidebarCollapsed, setSidebarCollapsed,
    selectedParentId, selectParent,
    selectedChildId, selectChild,
  } = usePanelContext()

  const { parentProjects, splitView } = useAppStore()

  const collapsed = sidebarCollapsed
  const compact = splitView && !collapsed

  const mainNav = [
    { id: 'planner',      label: 'Planner',      icon: CalendarRange },
    { id: 'projects',     label: 'Projects',     icon: FolderOpen },
    { id: 'render',       label: 'Render',        icon: LayoutDashboard },
    { id: 'analytics',    label: 'Analytics',     icon: BarChart2 },
    { id: 'competitors',  label: 'Competitors',   icon: Target },
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
    { id: 'sora-gen',    label: 'Sora Gen',     icon: Video },
    // { id: 'animation', label: 'Animation', icon: Sparkles }, // hidden
  ] as { id: ProjectSubView; label: string; icon: React.ElementType }[]

  return (
    <aside
      className={cn(
        'flex flex-col bg-slate-900 transition-all duration-200 shrink-0',
        collapsed ? 'w-14' : compact ? 'w-44' : 'w-52',
      )}
    >
      {/* Logo + Collapse */}
      {collapsed ? (
        <div className="flex flex-col items-center py-3 border-b border-white/10 gap-2">
          <div className="shrink-0 w-7 h-7 rounded-md bg-primary-500 flex items-center justify-center shadow-sm">
            <Scissors size={15} color="white" strokeWidth={2.5} />
          </div>
          <button
            className="btn-icon text-slate-400 hover:text-white hover:bg-white/10"
            onClick={() => setSidebarCollapsed(false)}
            title="Expand"
          >
            <ChevronRight size={14} />
          </button>
        </div>
      ) : (
        <div
          className="flex items-center gap-2.5 px-3 py-3 border-b border-white/10"
          data-tauri-drag-region
        >
          <div className="shrink-0 w-7 h-7 rounded-md bg-primary-500 flex items-center justify-center shadow-sm">
            <Scissors size={15} color="white" strokeWidth={2.5} />
          </div>
          <span className={cn('font-semibold text-white tracking-tight truncate flex-1', compact ? 'text-[13px]' : 'text-sm')}>
            MasterOS
          </span>
          <button
            className="btn-icon text-slate-400 hover:text-white hover:bg-white/10"
            onClick={() => setSidebarCollapsed(true)}
            title="Collapse"
          >
            <ChevronLeft size={14} />
          </button>
        </div>
      )}

      {/* Main Nav */}
      <nav className={cn('flex flex-col gap-0.5 p-2 flex-1 overflow-y-auto overflow-x-hidden', compact && 'p-1.5')}>
        {mainNav.map(({ id, label, icon: Icon }) => (
          <button
            key={id}
            className={cn('nav-item w-full', compact && 'gap-2 px-2.5 py-1.5 text-[13px]', mainView === id && 'active')}
            onClick={() => setMainView(id)}
            title={collapsed ? label : undefined}
          >
            <Icon size={16} className="shrink-0" />
            {!collapsed && <span>{label}</span>}
          </button>
        ))}

        {/* Projects sub-nav — only when on projects view and not collapsed */}
        {mainView === 'projects' && !collapsed && (
          <div className="mt-2">
            <p className="section-title">Workspace</p>

            {/* Parent list */}
            {projectSubView !== 'parents' && (
              <button
                className="nav-item w-full text-slate-400 text-xs"
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
                  compact ? 'px-2.5 py-1.5 text-[13px]' : 'px-3 py-1.5 text-sm',
                  'hover:bg-white/10 transition-colors duration-150',
                  selectedParentId === p.id ? 'text-white font-medium bg-primary-500/80' : 'text-slate-300',
                )}
                onClick={() => {
                  selectParent(p.id)
                  setProjectSubView('children')
                }}
              >
                <FolderOpen size={13} className="shrink-0" />
                <span className="truncate">{p.name}</span>
                <span className="ml-auto text-xs bg-white/10 text-slate-300 px-1.5 py-0.5 rounded">{p.childCount}</span>
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
                    className={cn('nav-item w-full', compact && 'gap-2 px-2.5 py-1.5 text-[13px]', projectSubView === id && 'active')}
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

      {/* Bottom info */}
      {!collapsed && !compact && (
        <div className="px-3 py-2 border-t border-white/10">
          <p className="text-xs text-slate-500">v0.1.0 — Light</p>
        </div>
      )}
    </aside>
  )
}
