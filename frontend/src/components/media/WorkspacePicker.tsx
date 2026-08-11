import { useCallback, useEffect, useState } from 'react'
import { Folder, FolderPlus, Loader, Upload, X } from 'lucide-react'
import { workspaceApi, type WorkspaceDirectory } from '@/lib/media'
import { cn } from '@/lib/utils'

interface WorkspacePickerDialogProps {
  suggestedName?: string
  onCancel: () => void
  onSelect: (directory: WorkspaceDirectory) => void
}

export function WorkspacePickerDialog({ suggestedName = 'New project', onCancel, onSelect }: WorkspacePickerDialogProps) {
  const [directories, setDirectories] = useState<WorkspaceDirectory[]>([])
  const [name, setName] = useState(suggestedName)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')

  const refresh = useCallback(async () => {
    setLoading(true)
    setError('')
    try {
      setDirectories(await workspaceApi.list())
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : 'Could not load workspace folders.')
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => { void refresh() }, [refresh])

  const create = async () => {
    if (!name.trim()) return
    setLoading(true)
    setError('')
    try {
      onSelect(await workspaceApi.create(name.trim()))
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : 'Could not create workspace folder.')
      setLoading(false)
    }
  }

  const importFolder = () => {
    const input = document.createElement('input')
    input.type = 'file'
    input.multiple = true
    input.setAttribute('webkitdirectory', '')
    input.onchange = async () => {
      const selected = Array.from(input.files ?? []) as Array<File & { webkitRelativePath?: string }>
      const supported = selected.filter((file) => /\.(aac|avif|csv|flac|gif|jpe?g|json|m4a|m4v|md|mkv|mov|mp3|mp4|ogg|opus|png|srt|txt|vtt|wav|webm|webp)$/i.test(file.name))
      if (supported.length === 0) {
        setError('The selected folder has no supported media files.')
        return
      }

      setLoading(true)
      setError('')
      try {
        const firstPath = supported[0].webkitRelativePath || supported[0].name
        const rootName = firstPath.split('/')[0] || suggestedName
        const directory = await workspaceApi.create(rootName)
        await workspaceApi.importFiles(directory.reference, supported.map((file) => {
          const clientPath = file.webkitRelativePath || file.name
          const parts = clientPath.split('/')
          return { file, relativePath: parts.length > 1 ? parts.slice(1).join('/') : file.name }
        }))
        onSelect(directory)
      } catch (cause) {
        setError(cause instanceof Error ? cause.message : 'Could not import folder.')
        setLoading(false)
      }
    }
    input.click()
  }

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-surface-950/45 p-4" role="dialog" aria-modal="true">
      <div className="flex max-h-[70vh] w-full max-w-lg flex-col overflow-hidden rounded-xl border border-surface-200 bg-white shadow-xl">
        <div className="flex items-center justify-between border-b border-surface-200 px-5 py-4">
          <div>
            <h2 className="text-sm font-semibold text-surface-900">Choose a server workspace</h2>
            <p className="mt-0.5 text-xs text-surface-500">Generated files stay inside the managed self-hosted workspace.</p>
          </div>
          <button className="btn-icon" type="button" onClick={onCancel} aria-label="Close workspace picker"><X size={16} /></button>
        </div>
        <div className="space-y-2 border-b border-surface-100 p-4">
          <div className="flex gap-2">
            <input className="input min-w-0 flex-1" value={name} onChange={(event) => setName(event.target.value)} placeholder="Folder name" />
            <button className="btn-primary shrink-0" type="button" disabled={loading || !name.trim()} onClick={() => void create()}>
              {loading ? <Loader size={14} className="animate-spin" /> : <FolderPlus size={14} />} Create
            </button>
          </div>
          <button className="btn-secondary w-full justify-center" type="button" disabled={loading} onClick={importFolder}>
            <Upload size={14} /> Import a folder from this device
          </button>
        </div>
        {error && <p className="px-4 pt-3 text-xs text-red-600">{error}</p>}
        <div className="min-h-40 flex-1 overflow-y-auto p-2">
          {directories.length === 0 && !loading ? (
            <div className="flex h-32 items-center justify-center text-xs text-surface-400">No workspace folders yet.</div>
          ) : directories.map((directory) => (
            <button
              key={directory.reference}
              className="flex w-full items-center gap-3 rounded-lg px-3 py-2.5 text-left hover:bg-surface-50 focus:outline-none focus:ring-2 focus:ring-primary-300"
              type="button"
              onClick={() => onSelect(directory)}
            >
              <span className="flex h-9 w-9 items-center justify-center rounded-lg bg-primary-50 text-primary-600"><Folder size={16} /></span>
              <span className="truncate text-sm font-medium text-surface-800">{directory.name}</span>
            </button>
          ))}
        </div>
      </div>
    </div>
  )
}

interface WorkspacePickerProps {
  label?: string
  selectedName?: string | null
  suggestedName?: string
  className?: string
  onSelect: (directory: WorkspaceDirectory) => void
}

export function WorkspacePicker({ label = 'Workspace Folder', selectedName, suggestedName, className, onSelect }: WorkspacePickerProps) {
  const [open, setOpen] = useState(false)
  return (
    <>
      <button className={cn('btn-secondary', className)} type="button" onClick={() => setOpen(true)}>
        <Folder size={14} /> {selectedName ? `Change ${label}` : label}
      </button>
      {open && (
        <WorkspacePickerDialog
          suggestedName={suggestedName}
          onCancel={() => setOpen(false)}
          onSelect={(directory) => { onSelect(directory); setOpen(false) }}
        />
      )}
    </>
  )
}
