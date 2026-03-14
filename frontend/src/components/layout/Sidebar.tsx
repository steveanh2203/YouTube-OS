import { cn } from '@/lib/utils'
import { useAppStore, type MainView } from '@/store/app.store'
import {
  FolderOpen, LayoutDashboard, BarChart2, ChevronLeft, ChevronRight,
  Scissors, Cpu, Mic, Radio, ChevronRight as Chevron,
} from 'lucide-react'

export default function Sidebar() {
  const {
    mainView, setMainView,
    projectSubView, setProjectSubView,
    sidebarCollapsed, setSidebarCollapsed,
    parentProjects, selectedParentId, selectParent,
    selectedChildId, selectChild,
  } = useAppStore()

  const collapsed = sidebarCollapsed

  const mainNav = [
    { id: 'projects',  label: 'Projects',   icon: FolderOpen },
    { id: 'render',    label: 'Render',      icon: LayoutDashboard },
    { id: 'analytics', label: 'Analytics',   icon: BarChart2 },
  ] as { id: MainView; label: string; icon: React.ElementType }[]

  const toolNav = [
    { id: 'ai-gen',    label: 'AI Gen',      icon: Cpu },
    { id: 'ai-audio',  label: 'AI Audio',    icon: Mic },
    { id: 'livestream', label: 'Livestream', icon: Radio },
  ]

  return (
    <aside
      className={cn(
        'flex flex-col bg-white border-r border-surface-200 transition-all duration-200 shrink-0',
        collapsed ? 'w-14' : 'w-52',
      )}
    >
      {/* Logo + Collapse */}
      <div
        className="flex items-center gap-2.5 px-3 py-3 border-b border-surface-200"
        data-tauri-drag-region
      >
        <div className="shrink-0 w-7 h-7 rounded-md bg-primary-500 flex items-center justify-center">
          <Scissors size={15} color="white" strokeWidth={2.5} />
        </div>
        {!collapsed && (
          <span className="font-semibold text-surface-900 text-sm tracking-tight truncate flex-1">
            AutoCapCut
          </span>
        )}
        <button
          className="btn-icon text-surface-400 hover:text-surface-700 hover:bg-surface-100 ml-auto"
          onClick={() => setSidebarCollapsed(!collapsed)}
          title={collapsed ? 'Expand' : 'Collapse'}
        >
          {collapsed ? <ChevronRight size={14} /> : <ChevronLeft size={14} />}
        </button>
      </div>

      {/* Main Nav */}
      <nav className="flex flex-col gap-0.5 p-2 flex-1 overflow-y-auto overflow-x-hidden">
        {mainNav.map(({ id, label, icon: Icon }) => (
          <button
            key={id}
            className={cn('nav-item w-full', mainView === id && 'active')}
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
                className="nav-item w-full text-surface-500 text-xs"
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
                  'w-full flex items-center gap-2 px-3 py-1.5 rounded-md text-sm cursor-pointer',
                  'hover:bg-surface-100 transition-colors duration-150',
                  selectedParentId === p.id ? 'text-primary-700 font-medium bg-primary-50' : 'text-surface-600',
                )}
                onClick={() => {
                  selectParent(p.id)
                  setProjectSubView('children')
                }}
              >
                <FolderOpen size={13} className="shrink-0" />
                <span className="truncate">{p.name}</span>
                <span className="ml-auto badge-neutral text-xs">{p.childCount}</span>
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
                    onClick={() => setProjectSubView(id as any)}
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
      {!collapsed && (
        <div className="px-3 py-2 border-t border-surface-200">
          <p className="text-xs text-surface-400">v0.1.0 — Light</p>
        </div>
      )}
    </aside>
  )
}
