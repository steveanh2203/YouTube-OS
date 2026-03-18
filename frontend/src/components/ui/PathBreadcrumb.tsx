import { FolderOpen, ChevronRight, Folder, HardDrive } from 'lucide-react'
import { cn } from '@/lib/utils'

// ─── helpers ──────────────────────────────────────────────────────────────────

/**
 * Split a filesystem path into labelled segments.
 * e.g. "/Users/steveanh/Desktop/foo" →
 *   [{ label: "/", path: "/" }, { label: "Users", path: "/Users" }, …]
 *
 * Works on both POSIX (/foo/bar) and Windows-style (C:\foo\bar).
 */
function splitPath(raw: string): { label: string; path: string }[] {
  if (!raw.trim()) return []

  // Normalise: replace backslashes, collapse repeated slashes
  const normalised = raw.replace(/\\/g, '/').replace(/\/+/g, '/')

  const isAbsolute = normalised.startsWith('/')
  const parts = normalised.split('/').filter(Boolean) // drop empty strings

  const segments: { label: string; path: string }[] = []

  if (isAbsolute) {
    segments.push({ label: '/', path: '/' })
    parts.forEach((part, i) => {
      segments.push({
        label: part,
        path: '/' + parts.slice(0, i + 1).join('/'),
      })
    })
  } else {
    // Relative or Windows drive (e.g. "C:" is parts[0])
    parts.forEach((part, i) => {
      segments.push({
        label: part,
        path: parts.slice(0, i + 1).join('/'),
      })
    })
  }

  return segments
}

// ─── component ────────────────────────────────────────────────────────────────

interface PathBreadcrumbProps {
  /** Displayed label above the bar */
  label?: string
  /** Current directory path string */
  value: string
  /** Called when the user edits the path directly or clicks a segment */
  onChange: (v: string) => void
  /** Called when the folder-picker button is clicked */
  onPickDirectory: () => void
  placeholder?: string
  className?: string
}

export function PathBreadcrumb({
  label,
  value,
  onChange,
  onPickDirectory,
  placeholder = '/path/to/folder/',
  className,
}: PathBreadcrumbProps) {
  const segments = splitPath(value)
  const isEmpty = value.trim() === ''

  return (
    <div className={cn('space-y-1', className)}>
      {label && <span className="label">{label}</span>}

      {/* Outer container: same visual style as .input but flex row */}
      <div
        className={cn(
          'flex items-center gap-0 rounded-lg border border-surface-300 bg-white',
          'focus-within:border-primary-400 focus-within:ring-2 focus-within:ring-primary-100',
          'transition-shadow overflow-hidden min-h-[38px]',
        )}
      >
        {/* Breadcrumb / empty-state area — takes all available space */}
        <div className="flex items-center flex-1 min-w-0 px-2.5 py-1.5 overflow-x-auto scrollbar-none gap-0.5">
          {isEmpty ? (
            <span className="text-xs text-surface-400 font-mono select-none truncate">{placeholder}</span>
          ) : (
            segments.map((seg, i) => {
              const isFirst = i === 0
              const isLast = i === segments.length - 1

              return (
                <span key={seg.path} className="flex items-center gap-0.5 shrink-0">
                  {/* Separator (chevron) — shown before all except the first segment */}
                  {!isFirst && (
                    <ChevronRight size={11} className="text-surface-300 mx-0.5 shrink-0" />
                  )}

                  {/* Segment button */}
                  <button
                    type="button"
                    title={seg.path}
                    onClick={() => onChange(seg.path)}
                    className={cn(
                      'flex items-center gap-1 px-1.5 py-0.5 rounded-md text-xs font-mono',
                      'transition-colors duration-100',
                      isLast
                        ? 'bg-primary-50 text-primary-700 font-semibold cursor-default'
                        : 'text-surface-500 hover:bg-surface-100 hover:text-surface-800 cursor-pointer',
                      isFirst && seg.label === '/'
                        ? 'pr-0.5'
                        : '',
                    )}
                  >
                    {/* Icon: drive icon for root/drive, folder for others */}
                    {isFirst ? (
                      seg.label === '/' ? (
                        <HardDrive size={11} className="shrink-0" />
                      ) : (
                        <HardDrive size={11} className="shrink-0" />
                      )
                    ) : isLast ? (
                      <Folder size={11} className="shrink-0" />
                    ) : null}

                    {/* Hide the "/" root label itself — the icon is enough */}
                    {!(isFirst && seg.label === '/') && (
                      <span className="max-w-[140px] truncate">{seg.label}</span>
                    )}
                  </button>
                </span>
              )
            })
          )}
        </div>

        {/* Divider */}
        <div className="w-px self-stretch bg-surface-200 shrink-0" />

        {/* Folder picker button */}
        <button
          type="button"
          onClick={onPickDirectory}
          className={cn(
            'flex items-center gap-1.5 px-3 text-xs font-medium shrink-0 self-stretch',
            'text-surface-500 hover:bg-surface-50 hover:text-surface-800',
            'transition-colors duration-100',
          )}
          title="Choose folder"
        >
          <FolderOpen size={13} />
          <span>Browse</span>
        </button>
      </div>
    </div>
  )
}
