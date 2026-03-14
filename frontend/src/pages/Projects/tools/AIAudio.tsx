import { useState } from 'react'
import { useAppStore } from '@/store/app.store'
import { Mic, Save, Music, FileAudio } from 'lucide-react'

export default function AIAudio() {
  const { childProjects, selectedChildId } = useAppStore()
  const child = childProjects.find(c => c.id === selectedChildId)

  const [notes, setNotes] = useState('')
  const [cues, setCues] = useState('')
  const [saved, setSaved] = useState(false)

  const handleSave = () => { setSaved(true); setTimeout(() => setSaved(false), 2000) }

  if (!child) return (
    <div className="flex items-center justify-center h-full text-surface-400 text-sm">
      Select a child project first.
    </div>
  )

  return (
    <div className="flex flex-col h-full">
      <div className="flex items-center justify-between px-6 py-4 border-b border-surface-200 bg-white">
        <div className="flex items-center gap-3">
          <div className="w-8 h-8 rounded-lg bg-blue-50 flex items-center justify-center">
            <Mic size={16} className="text-blue-500" />
          </div>
          <div>
            <h1 className="page-title">AI Audio</h1>
            <p className="page-sub mt-0.5">{child.title}</p>
          </div>
        </div>
        <button className="btn-primary" onClick={handleSave}>
          <Save size={14} />
          {saved ? 'Saved!' : 'Save'}
        </button>
      </div>

      <div className="flex-1 overflow-y-auto p-6">
        <div className="max-w-2xl space-y-5">
          <div className="card p-5">
            <div className="flex items-center gap-2 mb-3">
              <Music size={14} className="text-blue-500" />
              <h2 className="text-sm font-semibold text-surface-800">Audio Notes</h2>
            </div>
            <label className="label">Production notes for audio generation</label>
            <textarea className="textarea" rows={5} value={notes} onChange={e => setNotes(e.target.value)}
              placeholder="e.g. Upbeat background music, fade in at 0:05, lower volume at voiceover sections..." />
          </div>

          <div className="card p-5">
            <div className="flex items-center gap-2 mb-3">
              <FileAudio size={14} className="text-blue-500" />
              <h2 className="text-sm font-semibold text-surface-800">Generation Cues</h2>
            </div>
            <label className="label">Timestamp cues (one per line)</label>
            <textarea className="textarea" rows={5} value={cues} onChange={e => setCues(e.target.value)}
              placeholder="0:00 - Intro jingle&#10;0:15 - Background ambient&#10;2:30 - Outro music" />
            <p className="text-xs text-surface-400 mt-1.5">
              {cues.split('\n').filter(Boolean).length} cue{cues.split('\n').filter(Boolean).length !== 1 ? 's' : ''} defined
            </p>
          </div>
        </div>
      </div>
    </div>
  )
}
