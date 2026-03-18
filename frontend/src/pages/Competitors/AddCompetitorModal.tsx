import { useState, useEffect } from 'react'
import { motion, AnimatePresence } from 'framer-motion'
import { useAppStore, type CompetitorPurpose } from '@/store/app.store'
import { extractVideoId } from './index'
import { X, Loader2, AlertTriangle, CheckCircle2, Link } from 'lucide-react'
import { cn } from '@/lib/utils'

interface Props {
  open: boolean
  onClose: () => void
  childProjectId: string
  parentProjectId: string
}

const PURPOSES: { value: CompetitorPurpose; label: string }[] = [
  { value: 'rewrite',   label: 'Rewrite'   },
  { value: 'reference', label: 'Reference' },
  { value: 'trending',  label: 'Trending'  },
  { value: 'script',    label: 'Script'    },
]

type FetchState = 'idle' | 'loading' | 'ok' | 'error'

interface OEmbedResult {
  title: string
  author_name: string
  thumbnail_url: string
}

async function fetchOEmbed(url: string): Promise<OEmbedResult> {
  const endpoint = `https://www.youtube.com/oembed?url=${encodeURIComponent(url)}&format=json`
  const res = await fetch(endpoint)
  if (!res.ok) throw new Error('oEmbed failed')
  return res.json()
}

export default function AddCompetitorModal({ open, onClose, childProjectId, parentProjectId }: Props) {
  const { addCompetitor, competitors } = useAppStore()

  const [url, setUrl]           = useState('')
  const [purpose, setPurpose]   = useState<CompetitorPurpose>('rewrite')
  const [notes, setNotes]       = useState('')
  const [fetchState, setFetchState] = useState<FetchState>('idle')
  const [meta, setMeta]         = useState<OEmbedResult | null>(null)
  const [dupWarning, setDupWarning] = useState<string | null>(null)
  const [error, setError]       = useState<string | null>(null)

  // Auto-detect clipboard on open
  useEffect(() => {
    if (!open) return
    setUrl(''); setMeta(null); setDupWarning(null); setError(null); setFetchState('idle')
    navigator.clipboard.readText().then(text => {
      if (text && (text.includes('youtube.com') || text.includes('youtu.be'))) {
        setUrl(text.trim())
      }
    }).catch(() => {/* clipboard not granted */})
  }, [open])

  // Fetch oEmbed when URL changes (debounced)
  useEffect(() => {
    setMeta(null); setDupWarning(null); setError(null)
    const videoId = extractVideoId(url)
    if (!videoId) { setFetchState('idle'); return }

    // Check duplicate first
    const dup = competitors.find(c => c.videoId === videoId)
    if (dup) {
      setDupWarning(`This link already exists: "${dup.title}"`)
      setFetchState('idle')
      return
    }

    setFetchState('loading')
    const t = setTimeout(async () => {
      try {
        const result = await fetchOEmbed(url)
        setMeta(result)
        setFetchState('ok')
      } catch {
        setError('Could not load metadata. Check the URL and try again.')
        setFetchState('error')
      }
    }, 600)
    return () => clearTimeout(t)
  }, [url])

  const handleSubmit = () => {
    if (!meta || fetchState !== 'ok') return
    const videoId = extractVideoId(url)!
    const result = addCompetitor({
      videoId,
      url,
      title: meta.title,
      channel: meta.author_name,
      thumbnail: meta.thumbnail_url,
      purpose,
      notes,
      childProjectId,
      parentProjectId,
    })
    if (result.ok) {
      onClose()
    } else if (result.duplicate) {
      setDupWarning(`Matches existing item: "${result.duplicate.title}"`)
    }
  }

  return (
    <AnimatePresence>
      {open && (
        <>
          {/* Backdrop */}
          <motion.div
            initial={{ opacity: 0 }} animate={{ opacity: 1 }} exit={{ opacity: 0 }}
            className="fixed inset-0 bg-black/30 z-40"
            onClick={onClose}
          />

          {/* Modal */}
          <motion.div
            initial={{ opacity: 0, scale: 0.95, y: 8 }}
            animate={{ opacity: 1, scale: 1, y: 0 }}
            exit={{ opacity: 0, scale: 0.95, y: 8 }}
            transition={{ duration: 0.18, ease: 'easeOut' }}
            className="fixed inset-0 z-50 flex items-center justify-center pointer-events-none"
          >
            <div className="bg-white rounded-xl shadow-xl w-full max-w-md pointer-events-auto border border-surface-200">
              {/* Header */}
              <div className="flex items-center justify-between px-5 py-4 border-b border-surface-100">
                <h2 className="text-sm font-semibold text-surface-900">Add competitor link</h2>
                <button onClick={onClose} className="btn-icon text-surface-400 hover:text-surface-700 hover:bg-surface-100">
                  <X size={16} />
                </button>
              </div>

              {/* Body */}
              <div className="px-5 py-4 space-y-4">

                {/* URL input */}
                <div>
                  <label className="block text-xs font-medium text-surface-600 mb-1.5">YouTube URL</label>
                  <div className="relative">
                    <Link size={14} className="absolute left-3 top-1/2 -translate-y-1/2 text-surface-400" />
                    <input
                      value={url}
                      onChange={e => setUrl(e.target.value.trim())}
                      placeholder="https://youtube.com/watch?v=..."
                      className="w-full pl-8 pr-10 py-2.5 text-sm border border-surface-200 rounded-lg
                                 focus:outline-none focus:ring-2 focus:ring-primary-200 focus:border-primary-400"
                    />
                    <div className="absolute right-3 top-1/2 -translate-y-1/2">
                      {fetchState === 'loading' && <Loader2 size={14} className="animate-spin text-surface-400" />}
                      {fetchState === 'ok'      && <CheckCircle2 size={14} className="text-green-500" />}
                      {fetchState === 'error'   && <AlertTriangle size={14} className="text-red-400" />}
                    </div>
                  </div>

                  {/* Duplicate warning */}
                  {dupWarning && (
                    <p className="mt-1.5 text-xs text-amber-600 flex items-center gap-1">
                      <AlertTriangle size={12} /> {dupWarning}
                    </p>
                  )}
                  {error && (
                    <p className="mt-1.5 text-xs text-red-500">{error}</p>
                  )}
                </div>

                {/* Preview card */}
                <AnimatePresence>
                  {meta && (
                    <motion.div
                      initial={{ opacity: 0, height: 0 }}
                      animate={{ opacity: 1, height: 'auto' }}
                      exit={{ opacity: 0, height: 0 }}
                      className="overflow-hidden"
                    >
                      <div className="flex gap-3 p-3 bg-surface-50 rounded-lg border border-surface-200">
                        <img src={meta.thumbnail_url} alt="" className="w-20 h-14 object-cover rounded shrink-0" />
                        <div className="min-w-0">
                          <p className="text-xs font-semibold text-surface-800 line-clamp-2">{meta.title}</p>
                          <p className="text-xs text-surface-400 mt-1">{meta.author_name}</p>
                        </div>
                      </div>
                    </motion.div>
                  )}
                </AnimatePresence>

                {/* Purpose */}
                <div>
                  <label className="block text-xs font-medium text-surface-600 mb-1.5">Purpose</label>
                  <div className="flex gap-2 flex-wrap">
                    {PURPOSES.map(p => (
                      <button
                        key={p.value}
                        onClick={() => setPurpose(p.value)}
                        className={cn(
                          'px-3 py-1.5 rounded-full text-xs font-medium border transition-colors cursor-pointer',
                          purpose === p.value
                            ? 'bg-primary-500 text-white border-primary-500'
                            : 'bg-white text-surface-600 border-surface-200 hover:border-primary-300 hover:text-primary-600'
                        )}
                      >
                        {p.label}
                      </button>
                    ))}
                  </div>
                </div>

                {/* Notes */}
                <div>
                  <label className="block text-xs font-medium text-surface-600 mb-1.5">Notes (optional)</label>
                  <textarea
                    value={notes}
                    onChange={e => setNotes(e.target.value)}
                    placeholder="Add any notes about this video..."
                    rows={2}
                    className="w-full px-3 py-2 text-sm border border-surface-200 rounded-lg resize-none
                               focus:outline-none focus:ring-2 focus:ring-primary-200 focus:border-primary-400"
                  />
                </div>
              </div>

              {/* Footer */}
              <div className="flex justify-end gap-2 px-5 py-3 border-t border-surface-100">
                <button onClick={onClose} className="btn-secondary text-sm px-4 py-2">Cancel</button>
                <button
                  onClick={handleSubmit}
                  disabled={fetchState !== 'ok' || !!dupWarning}
                  className={cn(
                    'btn-primary text-sm px-4 py-2',
                    (fetchState !== 'ok' || !!dupWarning) && 'opacity-40 cursor-not-allowed'
                  )}
                >
                  Add competitor
                </button>
              </div>
            </div>
          </motion.div>
        </>
      )}
    </AnimatePresence>
  )
}
