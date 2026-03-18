import { useState, useEffect } from 'react'
import { useAppStore } from '@/store/app.store'
import { usePanelContext } from '@/contexts/PanelContext'
import { Sparkles, Play, Loader, Trash2 } from 'lucide-react'
import { animationApi, type AnimationPresetInfo } from '@/lib/api'
import { cn } from '@/lib/utils'
import { taskStore } from '@/store/task.store'

const CATEGORY_LABEL: Record<string, string> = {
  transition: 'Transition',
  in: 'In',
  out: 'Out',
  combo: 'Combo',
  effect: 'Effect',
}

export default function Animation() {
  const { childProjects, capcutProjects } = useAppStore()
  const { selectedChildId } = usePanelContext()
  const child = childProjects.find(c => c.id === selectedChildId)

  const [presets, setPresets]         = useState<AnimationPresetInfo[]>([])
  const [loadingPresets, setLoadingPresets] = useState(true)
  const [selectedPreset, setSelectedPreset] = useState<AnimationPresetInfo | null>(null)
  const [projectPath, setProjectPath] = useState('')
  const [applying, setApplying]       = useState(false)
  const [toast, setToast]             = useState<{ ok: boolean; msg: string } | null>(null)

  useEffect(() => {
    animationApi.presets()
      .then(setPresets)
      .catch(() => setPresets([]))
      .finally(() => setLoadingPresets(false))
  }, [])

  const showToast = (ok: boolean, msg: string) => {
    setToast({ ok, msg })
    setTimeout(() => setToast(null), 4000)
  }

  const handleApply = async () => {
    if (!projectPath.trim() || !selectedPreset) return
    setApplying(true)
    const taskId = taskStore.add({ toolId: 'animation', toolLabel: 'Animation', label: `Apply "${selectedPreset.name}"` })
    try {
      const action =
        selectedPreset.category === 'transition' ? 'transition' :
        selectedPreset.category === 'in'         ? 'animation_in' :
        selectedPreset.category === 'out'        ? 'animation_out' :
        selectedPreset.category === 'combo'      ? 'animation_combo' : 'effect'
      const res = await animationApi.apply({ project_path: projectPath, action, preset_key: selectedPreset.key })
      showToast(res.ok, res.message)
      taskStore.complete(taskId, res.ok ? 'done' : 'error', res.message)
    } catch (err: any) {
      const msg = err?.message ?? 'API error'
      showToast(false, msg)
      taskStore.complete(taskId, 'error', msg)
    } finally {
      setApplying(false)
    }
  }

  const handleClear = async (action: 'clear_transitions' | 'clear_animations' | 'clear_effects') => {
    if (!projectPath.trim()) return
    setApplying(true)
    const label = action === 'clear_transitions' ? 'Clear transitions' : action === 'clear_animations' ? 'Clear animations' : 'Clear effects'
    const taskId = taskStore.add({ toolId: 'animation', toolLabel: 'Animation', label })
    try {
      const res = await animationApi.apply({ project_path: projectPath, action })
      showToast(res.ok, res.message)
      taskStore.complete(taskId, res.ok ? 'done' : 'error', res.message)
    } catch (err: any) {
      const msg = err?.message ?? 'API error'
      showToast(false, msg)
      taskStore.complete(taskId, 'error', msg)
    } finally {
      setApplying(false)
    }
  }

  const categories = [...new Set(presets.map(p => p.category))]

  return (
    <div className="flex flex-col h-full">
      <div className="flex items-center gap-3 px-6 py-4 border-b border-surface-200 bg-white">
        <div className="w-8 h-8 rounded-lg bg-pink-100 flex items-center justify-center">
          <Sparkles size={16} className="text-pink-600" />
        </div>
        <div>
          <h1 className="page-title">Animation & Transitions</h1>
          <p className="page-sub">{child?.name ?? 'No child selected'} · Apply effects to a CapCut project</p>
        </div>
      </div>

      <div className="flex-1 overflow-y-auto p-6 space-y-5">
        {/* Toast */}
        {toast && (
          <div className={cn('rounded-lg px-4 py-2.5 text-sm font-medium',
            toast.ok ? 'bg-green-50 border border-green-200 text-green-700' : 'bg-red-50 border border-red-200 text-red-700')}>
            {toast.msg}
          </div>
        )}

        {/* Project path */}
        <div>
          <label className="label">CapCut project path</label>
          <input className="input font-mono text-xs" placeholder="/path/to/capcut/project"
            value={projectPath} onChange={e => setProjectPath(e.target.value)} />
          {capcutProjects.length > 0 && (
            <div className="mt-2">
              <label className="label">Or choose from the list</label>
              <select className="input text-xs" value={projectPath}
                onChange={e => setProjectPath(e.target.value)}>
                <option value="">-- select a project --</option>
                {capcutProjects.map(p => (
                  <option key={p.id} value={p.path}>{p.name}</option>
                ))}
              </select>
            </div>
          )}
        </div>

        {/* Preset picker */}
        <div>
          <label className="label">Choose a preset</label>
          {loadingPresets ? (
            <div className="flex items-center gap-2 text-sm text-surface-400 py-4">
              <Loader size={14} className="animate-spin" /> Loading presets... (API must be running)
            </div>
          ) : presets.length === 0 ? (
            <p className="text-sm text-surface-400 py-4">Could not load presets. Check whether the API is running.</p>
          ) : (
            <div className="space-y-4">
              {categories.map(cat => (
                <div key={cat}>
                  <p className="text-xs font-semibold text-surface-500 uppercase tracking-wider mb-2">
                    {CATEGORY_LABEL[cat] ?? cat}
                  </p>
                  <div className="flex flex-wrap gap-2">
                    {presets.filter(p => p.category === cat).map(p => (
                      <button
                        key={p.key}
                        onClick={() => setSelectedPreset(p)}
                        className={cn(
                          'px-3 py-1.5 rounded-lg text-xs font-medium border transition-all',
                          selectedPreset?.key === p.key
                            ? 'bg-primary-600 text-white border-primary-600 shadow-sm'
                            : 'bg-white text-surface-600 border-surface-200 hover:border-primary-300',
                        )}
                      >
                        {p.name}
                      </button>
                    ))}
                  </div>
                </div>
              ))}
            </div>
          )}
        </div>

        {/* Actions */}
        <div className="flex items-center gap-2 pt-2 flex-wrap">
          <button className="btn-primary" onClick={handleApply}
            disabled={applying || !projectPath.trim() || !selectedPreset}>
            {applying ? <Loader size={14} className="animate-spin" /> : <Play size={14} />}
            Apply {selectedPreset ? `"${selectedPreset.name}"` : 'Preset'}
          </button>

          <div className="flex-1" />

          <button className="btn-secondary text-xs" onClick={() => handleClear('clear_transitions')}
            disabled={applying || !projectPath.trim()}>
            <Trash2 size={12} /> Clear transitions
          </button>
          <button className="btn-secondary text-xs" onClick={() => handleClear('clear_animations')}
            disabled={applying || !projectPath.trim()}>
            <Trash2 size={12} /> Clear animations
          </button>
          <button className="btn-secondary text-xs" onClick={() => handleClear('clear_effects')}
            disabled={applying || !projectPath.trim()}>
            <Trash2 size={12} /> Clear effects
          </button>
        </div>
      </div>
    </div>
  )
}
