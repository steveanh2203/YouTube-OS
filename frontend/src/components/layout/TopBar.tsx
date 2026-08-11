import { useAppStore } from '@/store/app.store'
import { Columns2 } from 'lucide-react'
import { cn } from '@/lib/utils'

export default function TopBar() {
  const { splitView, setSplitView, isLoading, loadingText } = useAppStore()

  return (
    <header className="flex items-center h-10 px-4 border-b border-surface-200 bg-white shrink-0 gap-3">
      <div className="flex-1 text-xs text-surface-400 select-none">
        MasterOS
      </div>

      {/* Loading indicator */}
      {isLoading && (
        <div className="flex items-center gap-2 text-xs text-surface-500">
          <span className="animate-spin inline-block w-3 h-3 border border-surface-400 border-t-transparent rounded-full" />
          <span>{loadingText || 'Loading...'}</span>
        </div>
      )}

      {/* Split View toggle */}
      <button
        className={cn(
          'btn-icon transition-colors',
          splitView
            ? 'text-primary-600 bg-primary-50 hover:bg-primary-100'
            : 'text-surface-400 hover:text-surface-700 hover:bg-surface-100',
        )}
        title={splitView ? 'Close split view' : 'Open split view'}
        onClick={() => setSplitView(!splitView)}
      >
        <Columns2 size={15} />
      </button>
    </header>
  )
}
