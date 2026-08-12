import { useCallback, useEffect, useRef, useState } from 'react'
import { motion, AnimatePresence } from 'framer-motion'
import {
  FolderPlus, Folder, FolderOpen, Trash2, Upload, File, ExternalLink,
  ChevronRight, ChevronDown, Plus, X, Loader, CheckSquare, Square, Minus,
} from 'lucide-react'
import { cn } from '@/lib/utils'

const API = ''

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

interface DocFolder {
  id: number
  parent_project_id: number
  name: string
  created_at: string
}

interface Doc {
  id: number
  parent_project_id: number
  folder_id: number | null
  file_name: string
  file_path: string
  file_size: number
  mime_type: string
  created_at: string
}

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function formatBytes(bytes: number) {
  if (bytes < 1024) return `${bytes} B`
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`
}

function fileIcon(mimeType: string) {
  if (mimeType.startsWith('image/')) return '🖼'
  if (mimeType.includes('pdf')) return '📄'
  if (mimeType.includes('word') || mimeType.includes('document')) return '📝'
  if (mimeType.includes('sheet') || mimeType.includes('excel')) return '📊'
  if (mimeType.includes('presentation') || mimeType.includes('powerpoint')) return '📋'
  if (mimeType.startsWith('video/')) return '🎥'
  if (mimeType.startsWith('audio/')) return '🎵'
  if (mimeType.includes('zip') || mimeType.includes('compressed')) return '🗜'
  return '📎'
}

// ---------------------------------------------------------------------------
// Main component
// ---------------------------------------------------------------------------

export default function DocumentsTab({ parentId }: { parentId: string }) {
  const numericParentId = Number(parentId)
  const [folders, setFolders] = useState<DocFolder[]>([])
  const [docs, setDocs] = useState<Doc[]>([])
  const [loading, setLoading] = useState(true)
  const [expandedFolders, setExpandedFolders] = useState<Set<number>>(new Set())
  const [activeFolderId, setActiveFolderId] = useState<number | null | 'root'>('root')

  // New folder form
  const [showNewFolder, setShowNewFolder] = useState(false)
  const [newFolderName, setNewFolderName] = useState('')
  const [creatingFolder, setCreatingFolder] = useState(false)

  // Upload state
  const [uploading, setUploading] = useState(false)

  // Drag hover state — for full-page overlay
  const [dragHover, setDragHover] = useState(false)

  // Hidden file input ref for click-to-browse
  const fileInputRef = useRef<HTMLInputElement>(null)

  // Multi-select
  const [selectedIds, setSelectedIds] = useState<Set<number>>(new Set())

  // Reset selection when active folder changes
  useEffect(() => { setSelectedIds(new Set()) }, [activeFolderId])

  // Delete confirm
  const [confirmDelete, setConfirmDelete] = useState<
    { type: 'folder'; item: DocFolder } | { type: 'doc'; item: Doc } | { type: 'bulk'; ids: number[] } | null
  >(null)

  /** Upload via browser File objects (picker or drag and drop). */
  const handleUploadFiles = async (files: File[]) => {
    setUploading(true)
    try {
      const folderId = activeFolderId !== 'root' ? activeFolderId : null
      const uploaded: Doc[] = []
      for (const file of files) {
        const fd = new FormData()
        fd.append('file', file)
        const url = folderId != null
          ? `${API}/api/parent-projects/${numericParentId}/docs/upload?folder_id=${folderId}`
          : `${API}/api/parent-projects/${numericParentId}/docs/upload`
        const res = await fetch(url, { method: 'POST', body: fd })
        if (res.ok) uploaded.push(await res.json())
      }
      setDocs(prev => [...prev, ...uploaded])
    } finally {
      setUploading(false)
    }
  }

  // ---------------------------------------------------------------------------
  // Fetch
  // ---------------------------------------------------------------------------

  const fetchAll = useCallback(async () => {
    setLoading(true)
    try {
      const [fRes, dRes] = await Promise.all([
        fetch(`${API}/api/parent-projects/${numericParentId}/doc-folders`),
        fetch(`${API}/api/parent-projects/${numericParentId}/docs`),
      ])
      const fData: DocFolder[] = await fRes.json()
      const dData: Doc[] = await dRes.json()
      setFolders(fData)
      setDocs(dData)
    } finally {
      setLoading(false)
    }
  }, [numericParentId])

  useEffect(() => { fetchAll() }, [fetchAll])

  // ---------------------------------------------------------------------------
  // Folder actions
  // ---------------------------------------------------------------------------

  const handleCreateFolder = async () => {
    if (!newFolderName.trim()) return
    setCreatingFolder(true)
    try {
      const res = await fetch(`${API}/api/parent-projects/${numericParentId}/doc-folders`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ name: newFolderName.trim() }),
      })
      const created: DocFolder = await res.json()
      setFolders(prev => [...prev, created])
      setExpandedFolders(prev => new Set([...prev, created.id]))
      setActiveFolderId(created.id)
      setNewFolderName('')
      setShowNewFolder(false)
    } finally {
      setCreatingFolder(false)
    }
  }

  const handleDeleteFolder = async (folder: DocFolder) => {
    await fetch(`${API}/api/parent-projects/${numericParentId}/doc-folders/${folder.id}`, {
      method: 'DELETE',
    })
    setFolders(prev => prev.filter(f => f.id !== folder.id))
    setDocs(prev => prev.filter(d => d.folder_id !== folder.id))
    if (activeFolderId === folder.id) setActiveFolderId('root')
    setConfirmDelete(null)
  }

  // ---------------------------------------------------------------------------
  // Doc actions
  // ---------------------------------------------------------------------------

  const handleDeleteDoc = async (doc: Doc) => {
    await fetch(`${API}/api/parent-projects/${numericParentId}/docs/${doc.id}`, {
      method: 'DELETE',
    })
    setDocs(prev => prev.filter(d => d.id !== doc.id))
    setConfirmDelete(null)
  }

  const handleOpenDoc = (doc: Doc) => {
    window.open(
      `${API}/api/parent-projects/${numericParentId}/docs/${doc.id}/download`,
      '_blank',
      'noopener,noreferrer',
    )
  }

  const handleBulkDelete = async (ids: number[]) => {
    await Promise.all(
      ids.map(id =>
        fetch(`${API}/api/parent-projects/${numericParentId}/docs/${id}`, { method: 'DELETE' }),
      ),
    )
    setDocs(prev => prev.filter(d => !ids.includes(d.id)))
    setSelectedIds(new Set())
    setConfirmDelete(null)
  }

  // Selection helpers
  const toggleSelect = (id: number) => {
    setSelectedIds(prev => {
      const next = new Set(prev)
      if (next.has(id)) next.delete(id)
      else next.add(id)
      return next
    })
  }

  const toggleSelectAll = () => {
    if (selectedIds.size === displayDocs.length) {
      setSelectedIds(new Set())
    } else {
      setSelectedIds(new Set(displayDocs.map(d => d.id)))
    }
  }

  // ---------------------------------------------------------------------------
  // Derived lists
  // ---------------------------------------------------------------------------

  const rootDocs = docs.filter(d => d.folder_id == null)
  const docsInFolder = (folderId: number) => docs.filter(d => d.folder_id === folderId)
  const displayDocs = activeFolderId === 'root' ? rootDocs : docsInFolder(activeFolderId as number)

  // ---------------------------------------------------------------------------
  // Render
  // ---------------------------------------------------------------------------

  if (loading) {
    return (
      <div className="flex-1 flex items-center justify-center text-surface-400">
        <Loader size={20} className="animate-spin" />
      </div>
    )
  }

  return (
    <div className="flex flex-1 overflow-hidden">
      {/* ------------------------------------------------------------------ */}
      {/* Sidebar — folders                                                   */}
      {/* ------------------------------------------------------------------ */}
      <div className="w-56 border-r border-surface-200 flex flex-col bg-surface-50/60">
        {/* Sidebar header */}
        <div className="flex items-center justify-between px-3 py-2.5 border-b border-surface-200">
          <span className="text-xs font-semibold text-surface-600">Folders</span>
          <button
            className="btn-icon text-surface-400 hover:text-primary-600 hover:bg-primary-50"
            title="New folder"
            onClick={() => { setShowNewFolder(true); setNewFolderName('') }}
          >
            <FolderPlus size={13} />
          </button>
        </div>

        {/* New folder input */}
        <AnimatePresence>
          {showNewFolder && (
            <motion.div
              initial={{ opacity: 0, height: 0 }} animate={{ opacity: 1, height: 'auto' }}
              exit={{ opacity: 0, height: 0 }} transition={{ duration: 0.15 }}
              className="px-3 py-2 border-b border-surface-200 bg-surface-0"
            >
              <div className="flex gap-1">
                <input
                  autoFocus
                  className="input flex-1 text-xs py-1"
                  placeholder="Folder name"
                  value={newFolderName}
                  onChange={e => setNewFolderName(e.target.value)}
                  onKeyDown={e => {
                    if (e.key === 'Enter') handleCreateFolder()
                    if (e.key === 'Escape') { setShowNewFolder(false); setNewFolderName('') }
                  }}
                />
                <button
                  className="btn-icon text-surface-400 hover:text-green-300"
                  disabled={creatingFolder || !newFolderName.trim()}
                  onClick={handleCreateFolder}
                >
                  {creatingFolder ? <Loader size={12} className="animate-spin" /> : <Plus size={12} />}
                </button>
                <button
                  className="btn-icon text-surface-400 hover:text-red-400"
                  onClick={() => { setShowNewFolder(false); setNewFolderName('') }}
                >
                  <X size={12} />
                </button>
              </div>
            </motion.div>
          )}
        </AnimatePresence>

        {/* Folder list */}
        <div className="flex-1 overflow-y-auto py-1">
          {/* Root item */}
          <button
            className={cn(
              'w-full flex items-center gap-1.5 px-3 py-1.5 text-xs text-left transition-colors duration-100',
              activeFolderId === 'root'
                ? 'bg-primary-50 text-primary-700 font-semibold'
                : 'text-surface-600 hover:bg-surface-100',
            )}
            onClick={() => setActiveFolderId('root')}
          >
            <FolderOpen size={13} className="shrink-0" />
            <span className="truncate">All / Uncategorized</span>
            {rootDocs.length > 0 && (
              <span className="ml-auto text-[10px] text-surface-400">{rootDocs.length}</span>
            )}
          </button>

          {/* Named folders */}
          {folders.map(folder => {
            const count = docsInFolder(folder.id).length
            const active = activeFolderId === folder.id
            return (
              <div key={folder.id}>
                <div
                  className={cn(
                    'group flex items-center gap-1 px-2 py-1.5 text-xs cursor-pointer transition-colors duration-100 select-none',
                    active ? 'bg-primary-50 text-primary-700 font-semibold' : 'text-surface-600 hover:bg-surface-100',
                  )}
                  onClick={() => { setActiveFolderId(folder.id); setExpandedFolders(prev => { const n = new Set(prev); if (n.has(folder.id)) n.delete(folder.id); else n.add(folder.id); return n }) }}
                >
                  <button
                    className="shrink-0 text-surface-400 hover:text-surface-600 p-0.5"
                    onClick={e => { e.stopPropagation(); setExpandedFolders(prev => { const n = new Set(prev); if (n.has(folder.id)) n.delete(folder.id); else n.add(folder.id); return n }) }}
                  >
                    {expandedFolders.has(folder.id) ? <ChevronDown size={11} /> : <ChevronRight size={11} />}
                  </button>
                  {active ? <FolderOpen size={13} className="shrink-0" /> : <Folder size={13} className="shrink-0" />}
                  <span className="truncate flex-1">{folder.name}</span>
                  {count > 0 && <span className="text-[10px] text-surface-400">{count}</span>}
                  <button
                    className="opacity-0 group-hover:opacity-100 shrink-0 text-surface-300 hover:text-red-400 transition-[background-color,border-color,color,box-shadow,opacity,transform] duration-100 p-0.5"
                    title="Delete folder"
                    onClick={e => { e.stopPropagation(); setConfirmDelete({ type: 'folder', item: folder }) }}
                  >
                    <Trash2 size={11} />
                  </button>
                </div>
              </div>
            )
          })}

          {folders.length === 0 && (
            <p className="px-3 py-3 text-[11px] text-surface-400">No folders yet</p>
          )}
        </div>
      </div>

      {/* ------------------------------------------------------------------ */}
      {/* Main content                                                        */}
      {/* ------------------------------------------------------------------ */}
      <div
        className="relative flex-1 flex flex-col overflow-hidden"
        onDragEnter={(event) => { event.preventDefault(); setDragHover(true) }}
        onDragOver={(event) => { event.preventDefault(); setDragHover(true) }}
        onDragLeave={(event) => {
          if (!event.currentTarget.contains(event.relatedTarget as Node | null)) setDragHover(false)
        }}
        onDrop={(event) => {
          event.preventDefault()
          setDragHover(false)
          void handleUploadFiles(Array.from(event.dataTransfer.files))
        }}
      >
        {/* Drag hover overlay — only covers the main content, not the sidebar */}
        <AnimatePresence>
          {dragHover && (
            <motion.div
              initial={{ opacity: 0 }}
              animate={{ opacity: 1 }}
              exit={{ opacity: 0 }}
              transition={{ duration: 0.15 }}
              className="absolute inset-0 z-40 flex items-center justify-center bg-primary-50/80 backdrop-blur-sm border-2 border-dashed border-primary-400 rounded-lg pointer-events-none"
            >
              <motion.div
                initial={{ scale: 0.9, opacity: 0 }}
                animate={{ scale: 1, opacity: 1 }}
                exit={{ scale: 0.9, opacity: 0 }}
                transition={{ duration: 0.2, delay: 0.05 }}
                className="flex flex-col items-center gap-3"
              >
                <motion.div
                  animate={{ y: [0, -6, 0] }}
                  transition={{ repeat: Infinity, duration: 1.5, ease: 'easeInOut' }}
                >
                  <Upload size={36} className="text-primary-500" />
                </motion.div>
                <p className="text-sm font-semibold text-primary-700">
                  Drop files to upload
                </p>
                <p className="text-xs text-primary-500">
                  {activeFolderId === 'root'
                    ? 'Files will be added to Uncategorized'
                    : `Files will be added to "${folders.find(f => f.id === activeFolderId)?.name ?? ''}"`}
                </p>
              </motion.div>
            </motion.div>
          )}
        </AnimatePresence>

        {/* Toolbar */}
        <div className="flex items-center justify-between px-4 py-2.5 border-b border-surface-200 bg-surface-0">
          <span className="text-xs text-surface-500">
            {activeFolderId === 'root' ? 'Uncategorized files' : folders.find(f => f.id === activeFolderId)?.name ?? ''}
            <span className="ml-2 text-surface-400">({displayDocs.length} file{displayDocs.length !== 1 ? 's' : ''})</span>
          </span>

          {/* Minimal upload area — single line */}
          <div className="flex items-center gap-2">
            {uploading ? (
              <div className="flex items-center gap-1.5 text-xs text-surface-400">
                <Loader size={12} className="animate-spin" />
                <span>Uploading...</span>
              </div>
            ) : (
              <button
                className="flex items-center gap-1.5 text-xs text-surface-400 hover:text-primary-600 transition-colors duration-100"
                onClick={() => fileInputRef.current?.click()}
              >
                <Upload size={12} />
                <span>Add files</span>
              </button>
            )}
            <input
              ref={fileInputRef}
              type="file"
              multiple
              className="hidden"
              onChange={e => {
                const files = Array.from(e.target.files ?? [])
                if (files.length > 0) handleUploadFiles(files)
                e.target.value = ''
              }}
            />
          </div>
        </div>

        {/* File list */}
        <div className="flex-1 overflow-y-auto p-4">
          {displayDocs.length > 0 && (
            <div className="card overflow-hidden">
              <table className="w-full text-xs">
                <thead>
                  <tr className="border-b border-surface-200 bg-surface-50">
                     <th className="px-2 py-2 w-8">
                       <button
                         className="flex items-center justify-center text-surface-400 hover:text-primary-600 transition-colors"
                         onClick={toggleSelectAll}
                         title={selectedIds.size === displayDocs.length ? 'Deselect all' : 'Select all'}
                       >
                         {selectedIds.size === 0 ? (
                           <Square size={14} />
                         ) : selectedIds.size === displayDocs.length ? (
                           <CheckSquare size={14} className="text-primary-600" />
                         ) : (
                           <Minus size={14} className="text-primary-600" />
                         )}
                       </button>
                     </th>
                     <th className="px-4 py-2 text-left font-semibold text-surface-500">File</th>
                     <th className="px-4 py-2 text-left font-semibold text-surface-500 w-24">Size</th>
                    <th className="px-4 py-2 text-left font-semibold text-surface-500 w-32">Added</th>
                    <th className="px-4 py-2 w-16" />
                  </tr>
                </thead>
                <tbody>
                  <AnimatePresence>
                    {displayDocs.map((doc, i) => (
                      <motion.tr
                        key={doc.id}
                        initial={{ opacity: 0 }} animate={{ opacity: 1 }} exit={{ opacity: 0 }}
                        transition={{ delay: i * 0.02 }}
                        className={cn(
                          'border-b border-surface-100 hover:bg-surface-50 transition-colors duration-100 group',
                          selectedIds.has(doc.id) && 'bg-primary-50/50',
                        )}
                       >
                         <td className="px-2 py-2 w-8">
                           <button
                             className="flex items-center justify-center text-surface-400 hover:text-primary-600 transition-colors"
                             onClick={() => toggleSelect(doc.id)}
                           >
                             {selectedIds.has(doc.id)
                               ? <CheckSquare size={14} className="text-primary-600" />
                               : <Square size={14} />}
                           </button>
                         </td>
                         <td className="px-4 py-2">
                          <div className="flex items-center gap-2">
                            <span className="text-base leading-none">{fileIcon(doc.mime_type)}</span>
                            <span className="font-medium text-surface-800 truncate max-w-[260px]">{doc.file_name}</span>
                          </div>
                        </td>
                        <td className="px-4 py-2 text-surface-400">{formatBytes(doc.file_size)}</td>
                        <td className="px-4 py-2 text-surface-400">
                          {new Date(doc.created_at).toLocaleDateString()}
                        </td>
                        <td className="px-4 py-2">
                          <div className="flex items-center gap-1 justify-end opacity-0 group-hover:opacity-100 transition-opacity duration-100">
                            <button
                              className="btn-icon text-surface-400 hover:text-primary-600 hover:bg-primary-50"
                              title="Open file"
                              onClick={() => handleOpenDoc(doc)}
                            >
                              <ExternalLink size={12} />
                            </button>
                            <button
                              className="btn-icon text-surface-400 hover:text-red-500 hover:bg-red-500/10"
                              title="Delete"
                              onClick={() => setConfirmDelete({ type: 'doc', item: doc })}
                            >
                              <Trash2 size={12} />
                            </button>
                          </div>
                        </td>
                      </motion.tr>
                    ))}
                  </AnimatePresence>
                </tbody>
              </table>
            </div>
          )}

          {displayDocs.length === 0 && !uploading && (
            <div className="flex flex-col items-center justify-center py-12 text-surface-400">
              <File size={28} className="mb-2 opacity-40" />
              <p className="text-xs">No files here yet.</p>
              <p className="text-[11px] mt-1 text-surface-300">
                Drag files from Finder or{' '}
                <button
                  className="text-primary-500 hover:text-primary-600 underline underline-offset-2"
                  onClick={() => fileInputRef.current?.click()}
                >
                  click to browse
                </button>
              </p>
            </div>
          )}
        </div>

        {/* Floating bulk-action bar */}
        <AnimatePresence>
          {selectedIds.size > 0 && (
            <motion.div
              initial={{ opacity: 0, y: 12 }}
              animate={{ opacity: 1, y: 0 }}
              exit={{ opacity: 0, y: 12 }}
              transition={{ duration: 0.15 }}
              className="absolute bottom-4 left-1/2 -translate-x-1/2 z-30 flex items-center gap-3 bg-primary-500 text-surface-950 rounded-lg shadow-xl px-4 py-2.5"
            >
              <span className="text-xs font-medium">
                {selectedIds.size} file{selectedIds.size !== 1 ? 's' : ''} selected
              </span>
              <div className="w-px h-4 bg-surface-600" />
              <button
                className="text-xs text-surface-300 hover:text-white transition-colors"
                onClick={() => setSelectedIds(new Set())}
              >
                Deselect
              </button>
              <button
                className="flex items-center gap-1 text-xs text-red-400 hover:text-red-300 transition-colors"
                onClick={() => setConfirmDelete({ type: 'bulk', ids: Array.from(selectedIds) })}
              >
                <Trash2 size={12} />
                Delete
              </button>
            </motion.div>
          )}
        </AnimatePresence>
      </div>

      {/* ------------------------------------------------------------------ */}
      {/* Delete confirm modal                                                */}
      {/* ------------------------------------------------------------------ */}
      <AnimatePresence>
        {confirmDelete && (
          <motion.div
            initial={{ opacity: 0 }} animate={{ opacity: 1 }} exit={{ opacity: 0 }}
            className="fixed inset-0 z-50 flex items-center justify-center bg-black/30"
          >
            <motion.div
              initial={{ scale: 0.95, opacity: 0 }} animate={{ scale: 1, opacity: 1 }}
              exit={{ scale: 0.95, opacity: 0 }} transition={{ duration: 0.15 }}
              className="bg-surface-0 rounded-xl shadow-xl p-6 w-[380px] mx-4"
            >
              <h3 className="text-sm font-semibold text-surface-900 mb-2">
                {confirmDelete.type === 'folder'
                  ? 'Delete folder?'
                  : confirmDelete.type === 'bulk'
                    ? `Delete ${confirmDelete.ids.length} file${confirmDelete.ids.length !== 1 ? 's' : ''}?`
                    : 'Delete file?'}
              </h3>
              <p className="text-xs text-surface-500 mb-5">
                {confirmDelete.type === 'folder'
                  ? `"${(confirmDelete.item as DocFolder).name}" and all its files will be permanently deleted.`
                  : confirmDelete.type === 'bulk'
                    ? `${confirmDelete.ids.length} selected file${confirmDelete.ids.length !== 1 ? 's' : ''} will be permanently deleted.`
                    : `"${(confirmDelete.item as Doc).file_name}" will be permanently deleted.`}
              </p>
              <div className="flex gap-2 justify-end">
                <button className="btn-secondary text-xs" onClick={() => setConfirmDelete(null)}>
                  Cancel
                </button>
                <button
                  className="btn-danger text-xs"
                  onClick={() =>
                    confirmDelete.type === 'folder'
                      ? handleDeleteFolder(confirmDelete.item as DocFolder)
                      : confirmDelete.type === 'bulk'
                        ? handleBulkDelete(confirmDelete.ids)
                        : handleDeleteDoc(confirmDelete.item as Doc)
                  }
                >
                  Delete
                </button>
              </div>
            </motion.div>
          </motion.div>
        )}
      </AnimatePresence>
    </div>
  )
}
