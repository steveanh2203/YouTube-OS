import { useCallback, useEffect, useRef, useState } from 'react'
import { FileAudio, FileImage, Film, Loader, Upload, X } from 'lucide-react'
import { mediaApi, type MediaAsset, type MediaKind } from '@/lib/media'
import { cn } from '@/lib/utils'

interface MediaPickerProps {
  kind: MediaKind
  accept: string
  label: string
  selectedName?: string | null
  className?: string
  onSelect: (asset: MediaAsset) => void
}

const KIND_ICON = {
  audio: FileAudio,
  image: FileImage,
  subtitle: FileAudio,
  text: FileAudio,
  video: Film,
} satisfies Record<MediaKind, typeof FileAudio>

const formatBytes = (bytes: number) => {
  if (bytes < 1024) return `${bytes} B`
  if (bytes < 1024 * 1024) return `${Math.round(bytes / 1024)} KB`
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`
}

export function MediaPicker({ kind, accept, label, selectedName, className, onSelect }: MediaPickerProps) {
  const [open, setOpen] = useState(false)
  const [assets, setAssets] = useState<MediaAsset[]>([])
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')
  const inputRef = useRef<HTMLInputElement>(null)
  const Icon = KIND_ICON[kind]

  const loadAssets = useCallback(async () => {
    setLoading(true)
    setError('')
    try {
      setAssets(await mediaApi.list(kind))
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : 'Could not load Media Library.')
    } finally {
      setLoading(false)
    }
  }, [kind])

  useEffect(() => {
    if (open) void loadAssets()
  }, [loadAssets, open])

  const upload = async (file: File) => {
    setLoading(true)
    setError('')
    try {
      const asset = await mediaApi.upload(file)
      onSelect(asset)
      setOpen(false)
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : 'Upload failed.')
    } finally {
      setLoading(false)
      if (inputRef.current) inputRef.current.value = ''
    }
  }

  return (
    <>
      <button className={cn('btn-secondary', className)} onClick={() => setOpen(true)} type="button">
        <Icon size={13} />
        {selectedName ? `Change ${label}` : label}
      </button>

      {open && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-surface-950/45 p-4" role="dialog" aria-modal="true">
          <div className="flex max-h-[70vh] w-full max-w-xl flex-col overflow-hidden rounded-xl border border-surface-200 bg-surface-0 shadow-xl">
            <div className="flex items-center justify-between border-b border-surface-200 px-5 py-4">
              <div>
                <h2 className="text-sm font-semibold text-surface-900">Choose {label}</h2>
                <p className="mt-0.5 text-xs text-surface-500">Upload a file or reuse one from this server.</p>
              </div>
              <button className="btn-icon" onClick={() => setOpen(false)} type="button" aria-label="Close media picker">
                <X size={16} />
              </button>
            </div>

            <div className="border-b border-surface-100 p-4">
              <input
                ref={inputRef}
                type="file"
                accept={accept}
                className="hidden"
                onChange={(event) => {
                  const file = event.currentTarget.files?.[0]
                  if (file) void upload(file)
                }}
              />
              <button className="btn-primary w-full" onClick={() => inputRef.current?.click()} disabled={loading} type="button">
                {loading ? <Loader size={14} className="animate-spin" /> : <Upload size={14} />}
                Upload {label}
              </button>
              {error && <p className="mt-2 text-xs text-red-300">{error}</p>}
            </div>

            <div className="min-h-40 flex-1 overflow-y-auto p-2">
              {loading && assets.length === 0 ? (
                <div className="flex h-36 items-center justify-center text-surface-400"><Loader size={18} className="animate-spin" /></div>
              ) : assets.length === 0 ? (
                <div className="flex h-36 flex-col items-center justify-center gap-2 text-surface-400">
                  <Icon size={24} />
                  <p className="text-xs">No {kind} files in Media Library yet.</p>
                </div>
              ) : assets.map((asset) => (
                <button
                  key={asset.id}
                  type="button"
                  className="flex w-full items-center gap-3 rounded-lg px-3 py-2.5 text-left hover:bg-surface-50 focus:outline-none focus:ring-2 focus:ring-primary-300"
                  onClick={() => { onSelect(asset); setOpen(false) }}
                >
                  <span className="flex h-9 w-9 shrink-0 items-center justify-center rounded-lg bg-primary-50 text-primary-600"><Icon size={16} /></span>
                  <span className="min-w-0 flex-1">
                    <span className="block truncate text-sm font-medium text-surface-800">{asset.original_name}</span>
                    <span className="block text-xs text-surface-400">{formatBytes(asset.size_bytes)}</span>
                  </span>
                </button>
              ))}
            </div>
          </div>
        </div>
      )}
    </>
  )
}
