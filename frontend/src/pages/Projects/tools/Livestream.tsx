import { useState } from 'react'
import { useAppStore } from '@/store/app.store'
import { Radio, Save, Clapperboard, ListOrdered, Play } from 'lucide-react'

export default function Livestream() {
  const { childProjects, selectedChildId } = useAppStore()
  const child = childProjects.find(c => c.id === selectedChildId)

  const [script, setScript] = useState('')
  const [sceneNotes, setSceneNotes] = useState('')
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
          <div className="w-8 h-8 rounded-lg bg-rose-50 flex items-center justify-center">
            <Radio size={16} className="text-rose-500" />
          </div>
          <div>
            <h1 className="page-title">Livestream</h1>
            <p className="page-sub mt-0.5">{child.title}</p>
          </div>
        </div>
        <div className="flex items-center gap-2">
          <button className="btn-success">
            <Play size={13} />
            Go Live
          </button>
          <button className="btn-primary" onClick={handleSave}>
            <Save size={14} />
            {saved ? 'Saved!' : 'Save'}
          </button>
        </div>
      </div>

      <div className="flex-1 overflow-y-auto p-6">
        <div className="max-w-2xl space-y-5">
          <div className="card p-5">
            <div className="flex items-center gap-2 mb-3">
              <Clapperboard size={14} className="text-rose-500" />
              <h2 className="text-sm font-semibold text-surface-800">Stream Script</h2>
            </div>
            <label className="label">Full script / talking points</label>
            <textarea className="textarea" rows={7} value={script} onChange={e => setScript(e.target.value)}
              placeholder="Write your full stream script or bullet points here..." />
          </div>

          <div className="card p-5">
            <div className="flex items-center gap-2 mb-3">
              <ListOrdered size={14} className="text-rose-500" />
              <h2 className="text-sm font-semibold text-surface-800">Scene Notes</h2>
            </div>
            <label className="label">Scene-by-scene notes (one per line)</label>
            <textarea className="textarea" rows={5} value={sceneNotes} onChange={e => setSceneNotes(e.target.value)}
              placeholder="Scene 1: Intro - show product&#10;Scene 2: Demo - explain features&#10;Scene 3: Q&A" />
            <p className="text-xs text-surface-400 mt-1.5">
              {sceneNotes.split('\n').filter(Boolean).length} scene{sceneNotes.split('\n').filter(Boolean).length !== 1 ? 's' : ''} planned
            </p>
          </div>

          {/* Stream Operations */}
          <div className="card p-5">
            <h2 className="text-sm font-semibold text-surface-800 mb-3">Stream Operations</h2>
            <div className="grid grid-cols-3 gap-2">
              {['Start Stream', 'Pause Stream', 'End Stream', 'Switch Scene', 'Mute Audio', 'Show Camera'].map(op => (
                <button key={op} className="btn-secondary text-xs py-2 justify-center">{op}</button>
              ))}
            </div>
          </div>
        </div>
      </div>
    </div>
  )
}
