import { lazy, Suspense, useState } from 'react'
import { AudioWaveform, Film, Scissors } from 'lucide-react'
import { cn } from '@/lib/utils'

const FastEdit = lazy(() => import('@/pages/Projects/tools/FastEdit'))
const CutAutomate = lazy(() => import('@/pages/Projects/tools/CutAutomate'))
const AudioVisualizer = lazy(() => import('@/pages/Projects/tools/AudioVisualizer'))

type StudioTool = 'compose' | 'cut' | 'visualizer'

const tools: Array<{ id: StudioTool; label: string; description: string; icon: React.ElementType }> = [
  { id: 'compose', label: 'Compose', description: 'Render audio, images and subtitles with FFmpeg', icon: Film },
  { id: 'cut', label: 'Cut & Batch', description: 'Run the multi-step FFmpeg batch workflow', icon: Scissors },
  { id: 'visualizer', label: 'Visualizer', description: 'Turn audio into a downloadable video', icon: AudioWaveform },
]

export default function RenderDashboard() {
  const [activeTool, setActiveTool] = useState<StudioTool>('compose')

  return (
    <div className="flex h-full min-h-0 flex-col bg-surface-50">
      <header className="border-b border-surface-200 bg-white px-6 py-4">
        <h1 className="page-title">FFmpeg Studio</h1>
        <p className="page-sub mt-0.5">Browser-controlled rendering inside your self-hosted workspace.</p>
        <div className="mt-4 grid max-w-4xl grid-cols-3 gap-2" role="tablist" aria-label="FFmpeg tools">
          {tools.map(({ id, label, description, icon: Icon }) => (
            <button
              key={id}
              type="button"
              role="tab"
              aria-selected={activeTool === id}
              className={cn(
                'flex items-start gap-3 rounded-lg border px-3 py-2.5 text-left transition-colors',
                activeTool === id
                  ? 'border-primary-300 bg-primary-50 text-primary-800'
                  : 'border-surface-200 bg-white text-surface-600 hover:border-surface-300 hover:bg-surface-50',
              )}
              onClick={() => setActiveTool(id)}
            >
              <Icon size={16} className="mt-0.5 shrink-0" />
              <span className="min-w-0">
                <span className="block text-sm font-semibold">{label}</span>
                <span className="mt-0.5 block text-[11px] leading-4 text-surface-500">{description}</span>
              </span>
            </button>
          ))}
        </div>
      </header>

      <div className="min-h-0 flex-1 overflow-hidden" role="tabpanel">
        <Suspense fallback={<div className="flex h-full items-center justify-center text-sm text-surface-400">Loading FFmpeg tool…</div>}>
          {activeTool === 'compose' && <FastEdit />}
          {activeTool === 'cut' && <CutAutomate />}
          {activeTool === 'visualizer' && <AudioVisualizer />}
        </Suspense>
      </div>
    </div>
  )
}
