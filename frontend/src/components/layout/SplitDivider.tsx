import { useRef, useCallback } from 'react'
import { getMinPanelWidth } from '@/components/layout/splitLayout'

interface SplitDividerProps {
  /** callback receives the new left-panel width in pixels */
  onResize: (leftWidth: number) => void
  containerRef: React.RefObject<HTMLDivElement | null>
}

export default function SplitDivider({ onResize, containerRef }: SplitDividerProps) {
  const dragging = useRef(false)

  const onMouseDown = useCallback((e: React.MouseEvent) => {
    e.preventDefault()
    dragging.current = true
    document.body.style.cursor = 'col-resize'
    document.body.style.userSelect = 'none'

    const onMouseMove = (ev: MouseEvent) => {
      if (!dragging.current || !containerRef.current) return
      const rect = containerRef.current.getBoundingClientRect()
      const newLeft = ev.clientX - rect.left
      const total = rect.width
      const minPanelWidth = getMinPanelWidth(total)
      const clamped = Math.max(minPanelWidth, Math.min(newLeft, total - minPanelWidth))
      onResize(clamped)
    }

    const onMouseUp = () => {
      dragging.current = false
      document.body.style.cursor = ''
      document.body.style.userSelect = ''
      window.removeEventListener('mousemove', onMouseMove)
      window.removeEventListener('mouseup', onMouseUp)
    }

    window.addEventListener('mousemove', onMouseMove)
    window.addEventListener('mouseup', onMouseUp)
  }, [onResize, containerRef])

  return (
    <div
      className="relative w-1 shrink-0 bg-surface-200 hover:bg-primary-400 active:bg-primary-500 cursor-col-resize transition-colors duration-150 group"
      onMouseDown={onMouseDown}
    >
      {/* Visual handle pip */}
      <div className="absolute top-1/2 left-1/2 -translate-x-1/2 -translate-y-1/2 w-1 h-8 rounded-full bg-surface-300 group-hover:bg-primary-400 transition-colors duration-150" />
    </div>
  )
}
