import { useState, useEffect, useRef, useCallback } from 'react'
import { open as tauriOpen } from '@tauri-apps/plugin-dialog'
import { convertFileSrc } from '@tauri-apps/api/core'
import { cn } from '@/lib/utils'
import { toast } from '@/store/toast.store'
import {
  Music, Play, Square, Save, Trash2, ChevronDown, ChevronUp,
  Loader, FolderOpen, AudioWaveform, CheckCircle, XCircle,
} from 'lucide-react'

const API = 'http://127.0.0.1:8765/api/audio-visualizer'
const buildPreviewUrl = (path: string) => `${API}/preview-source?audio_path=${encodeURIComponent(path)}&t=${Date.now()}`
const normalizeAudioPath = (raw: string) => {
  const value = raw.trim()
  if (value.startsWith('file://')) {
    try {
      const u = new URL(value)
      return decodeURIComponent(u.pathname)
    } catch {
      return value.replace(/^file:\/\//, '')
    }
  }
  return value
}

// ─── Types ────────────────────────────────────────────────────────────────────

type VisStyle = 'wave' | 'mirror_wave' | 'spectrum_bars' | 'point_wave' | 'cqt' | 'vectorscope' | 'histogram' | 'cqt_color'
type BgMode = 'solid' | 'gradient' | 'image'
type CoverPos = 'center' | 'bottom_third' | 'top_right'
type OutputFmt = 'mp4' | 'mov' | 'webm'
type Resolution = '1920x1080' | '1080x1920' | '1080x1080' | 'custom'
type JobStatus = 'idle' | 'queued' | 'running' | 'done' | 'failed' | 'cancelled' | 'lost'

interface VisualizerConfig {
  style: VisStyle
  visualizer_color: string
  background_mode: BgMode
  background_solid_color: string
  background_gradient_color1: string
  background_gradient_color2: string
  background_gradient_angle: number
  background_image_path: string | null
  cover_art_path: string | null
  cover_art_position: CoverPos
  text_title: string
  text_subtitle: string
  text_color: string
  text_title_size: number
  text_subtitle_size: number
  resolution: Resolution
  resolution_custom_w: number | null
  resolution_custom_h: number | null
  output_format: OutputFmt
}

interface Preset { id: number; name: string; config: VisualizerConfig; created_at: string; updated_at: string }

// ─── Defaults ─────────────────────────────────────────────────────────────────

const DEFAULT_CONFIG: VisualizerConfig = {
  style: 'wave',
  visualizer_color: '#00BFFF',
  background_mode: 'solid',
  background_solid_color: '#0a0a0a',
  background_gradient_color1: '#0a0a0a',
  background_gradient_color2: '#1a1a2e',
  background_gradient_angle: 135,
  background_image_path: null,
  cover_art_path: null,
  cover_art_position: 'center',
  text_title: '',
  text_subtitle: '',
  text_color: '#ffffff',
  text_title_size: 48,
  text_subtitle_size: 28,
  resolution: '1920x1080',
  resolution_custom_w: null,
  resolution_custom_h: null,
  output_format: 'mp4',
}

const STYLES: { id: VisStyle; label: string; desc: string; approx?: boolean }[] = [
  { id: 'spectrum_bars', label: 'Dual Mirrored Bars', desc: 'CAVA-inspired text-centered mirrored spectrum' },
]

const RESOLUTIONS: { value: Resolution; label: string }[] = [
  { value: '1920x1080', label: 'YouTube 1080p (1920×1080)' },
  { value: '1080x1920', label: 'YouTube Shorts (1080×1920)' },
  { value: '1080x1080', label: 'Square (1080×1080)' },
  { value: 'custom',    label: 'Custom...' },
]

const hexToRgb = (hex: string) => {
  const r = parseInt(hex.slice(1, 3), 16)
  const g = parseInt(hex.slice(3, 5), 16)
  const b = parseInt(hex.slice(5, 7), 16)
  return `${r},${g},${b}`
}

const brightenHex = (hex: string, amount = 0.35) => {
  const r = parseInt(hex.slice(1, 3), 16)
  const g = parseInt(hex.slice(3, 5), 16)
  const b = parseInt(hex.slice(5, 7), 16)
  const to = (v: number) => Math.min(255, Math.round(v + (255 - v) * amount))
  const rr = to(r).toString(16).padStart(2, '0')
  const gg = to(g).toString(16).padStart(2, '0')
  const bb = to(b).toString(16).padStart(2, '0')
  return `#${rr}${gg}${bb}`
}

const clamp01 = (value: number) => Math.max(0, Math.min(1, value))

const SPECTRUM_ANALYSER_FFT_SIZE = 2048
const SPECTRUM_ANALYSER_SMOOTHING = 0.08
const SPECTRUM_ANALYSER_MIN_DB = -92
const SPECTRUM_ANALYSER_MAX_DB = -18
const SPECTRUM_FREQ_ATTACK = 0.64
const SPECTRUM_FREQ_RELEASE = 0.34
const SPECTRUM_MONSTERCAT_AMOUNT = 2.55
const SPECTRUM_BAR_ATTACK = 0.84
const SPECTRUM_BAR_RELEASE = 0.22
const SPECTRUM_BAR_SHAPE_BLEND = 0.14
const SPECTRUM_PEAK_RISE = 0.02
const SPECTRUM_PEAK_DECAY = 0.03

// Adapted from CAVA's "monstercat" smoothing idea.
const applyMonstercat = (bars: Float32Array, amount = 1.8) => {
  const out = new Float32Array(bars)
  for (let z = 0; z < out.length; z++) {
    for (let y = z - 1; y >= 0; y--) {
      const de = z - y
      out[y] = Math.max(out[y], out[z] / Math.pow(amount * 1.5, de))
    }
    for (let y = z + 1; y < out.length; y++) {
      const de = y - z
      out[y] = Math.max(out[y], out[z] / Math.pow(amount * 1.5, de))
    }
  }
  return out
}

const sampleSpectrumBins = (source: Float32Array, bins: number) => {
  const out = new Float32Array(bins)
  const maxIndex = source.length - 1
  let framePeak = 0
  for (let i = 0; i < bins; i++) {
    const startT = i / Math.max(1, bins - 1)
    const endT = (i + 1) / bins
    const mapStart = startT * 0.58 + Math.pow(startT, 1.12) * 0.42
    const mapEnd = endT * 0.58 + Math.pow(endT, 1.12) * 0.42
    const startIdx = Math.floor(mapStart * maxIndex)
    const endIdx = Math.max(startIdx + 1, Math.floor(mapEnd * maxIndex))
    let weightedSum = 0
    let totalWeight = 0
    let slicePeak = 0
    for (let idx = startIdx; idx <= endIdx && idx <= maxIndex; idx++) {
      const position = maxIndex <= 0 ? 0 : idx / maxIndex
      const psychoWeight = 0.9 + position * 1.45
      const weighted = source[idx] * psychoWeight
      weightedSum += weighted
      totalWeight += psychoWeight
      slicePeak = Math.max(slicePeak, weighted)
    }
    const avg = totalWeight > 0 ? weightedSum / totalWeight : 0
    const blended = avg * 0.45 + slicePeak * 0.55
    const edgeCompensation = 0.96 + Math.pow(i / Math.max(1, bins - 1), 1.18) * 1.15
    const compensated = blended * edgeCompensation
    out[i] = compensated
    framePeak = Math.max(framePeak, compensated)
  }

  if (framePeak <= 0) {
    return out
  }

  const envelope = blurBins(out)
  const floor = framePeak * 0.05
  const normalized = new Float32Array(bins)
  let avgLifted = 0
  for (let i = 0; i < bins; i++) {
    const lifted = clamp01((out[i] - floor) / Math.max(1e-5, framePeak - floor))
    normalized[i] = lifted
    avgLifted += lifted
  }
  avgLifted /= Math.max(1, bins)

  for (let i = 0; i < bins; i++) {
    const lifted = normalized[i]
    const localEnvelope = Math.max(envelope[i], framePeak * 0.14)
    const localContrast = clamp01(out[i] / localEnvelope)
    const position = i / Math.max(1, bins - 1)
    const distanceFromCenter = Math.abs(position - 0.5) / 0.5
    const centerFocus = 1 - Math.pow(clamp01(distanceFromCenter), 1.25)
    const bellyEnvelope = 0.18 + centerFocus * 0.92
    const sharedMotion = avgLifted * (0.2 + centerFocus * 0.55)
    const edgeDrop = 0.02 + centerFocus * 0.06
    const centerLift = centerFocus * (0.12 + avgLifted * 0.28)
    const localDetail = localContrast * (0.05 + centerFocus * 0.16)
    const spread = lifted * (0.26 + centerFocus * 0.34) + sharedMotion * 0.28 + localDetail
    const rhythmicFloor = sharedMotion * (0.54 + centerFocus * 0.26) + centerLift
    out[i] = Math.pow(clamp01((Math.max(spread, rhythmicFloor) + edgeDrop) * bellyEnvelope), 0.98)
  }
  return out
}

const blurBins = (bars: Float32Array) => {
  const out = new Float32Array(bars.length)
  for (let i = 0; i < bars.length; i++) {
    const left2 = bars[Math.max(0, i - 2)] ?? bars[i]
    const left1 = bars[Math.max(0, i - 1)] ?? bars[i]
    const center = bars[i]
    const right1 = bars[Math.min(bars.length - 1, i + 1)] ?? bars[i]
    const right2 = bars[Math.min(bars.length - 1, i + 2)] ?? bars[i]
    out[i] = left2 * 0.02 + left1 * 0.12 + center * 0.72 + right1 * 0.12 + right2 * 0.02
  }
  return out
}

// ─── Canvas preview renderer ──────────────────────────────────────────────────

function useAudioPreview(config: VisualizerConfig, canvasRef: React.RefObject<HTMLCanvasElement | null>) {
  const rafRef = useRef<number>(0)
  const analyserRef = useRef<AnalyserNode | null>(null)
  const sourceRef = useRef<MediaElementAudioSourceNode | null>(null)
  const audioCtxRef = useRef<AudioContext | null>(null)
  const boundAudioElRef = useRef<HTMLAudioElement | null>(null)
  const gainRef = useRef<GainNode | null>(null)
  const prevStyleRef = useRef<VisStyle>(config.style)
  const styleChangedAtRef = useRef<number>(0)
  const smoothTimeRef = useRef<Float32Array | null>(null)
  const smoothFreqRef = useRef<Float32Array | null>(null)
  const spectrumBarsRef = useRef<Float32Array | null>(null)
  const spectrumPeaksRef = useRef<Float32Array | null>(null)
  const spectrumScratchRef = useRef<Float32Array | null>(null)
  const spectrumGlowRef = useRef(0)

  const drawFrame = useCallback(function renderFrame() {
    // Always reschedule — loop must never die
    rafRef.current = requestAnimationFrame(renderFrame)

    const canvas = canvasRef.current
    if (!canvas) return

    const ctx = canvas.getContext('2d')
    if (!ctx) return

    const { width: W, height: H } = canvas
    const style = config.style
    const analyser = analyserRef.current
    const now = performance.now()
    const styleBlendRaw = styleChangedAtRef.current === 0
      ? 1
      : Math.min(1, (now - styleChangedAtRef.current) / 280)
    const styleBlend = 1 - Math.pow(1 - styleBlendRaw, 3)

    // Background
    if (config.background_mode === 'solid') {
      ctx.fillStyle = config.background_solid_color
      ctx.fillRect(0, 0, W, H)
    } else if (config.background_mode === 'gradient') {
      const angle = config.background_gradient_angle * Math.PI / 180
      const x1 = W / 2 - Math.cos(angle) * W / 2
      const y1 = H / 2 - Math.sin(angle) * H / 2
      const x2 = W / 2 + Math.cos(angle) * W / 2
      const y2 = H / 2 + Math.sin(angle) * H / 2
      const grad = ctx.createLinearGradient(x1, y1, x2, y2)
      grad.addColorStop(0, config.background_gradient_color1)
      grad.addColorStop(1, config.background_gradient_color2)
      ctx.fillStyle = grad
      ctx.fillRect(0, 0, W, H)
    } else {
      ctx.fillStyle = '#0a0a0a'
      ctx.fillRect(0, 0, W, H)
    }

    const visColor = config.visualizer_color
    const visH = H / 3
    // Do not render fake/demo waveforms before real preview playback
    if (!analyser) {
      return
    }

    const bufLen = analyser.frequencyBinCount

    if (style === 'wave' || style === 'mirror_wave' || style === 'point_wave') {
      const data = new Float32Array(bufLen)
      analyser.getFloatTimeDomainData(data)
      if (!smoothTimeRef.current || smoothTimeRef.current.length !== bufLen) {
        smoothTimeRef.current = new Float32Array(data)
      } else {
        for (let i = 0; i < bufLen; i++) {
          smoothTimeRef.current[i] = smoothTimeRef.current[i] * 0.82 + data[i] * 0.18
        }
      }
      const smoothed = smoothTimeRef.current
      let maxAbs = 0
      for (let i = 0; i < bufLen; i++) maxAbs = Math.max(maxAbs, Math.abs(smoothed[i]))
      if (maxAbs < 0.002) {
        return
      }
      ctx.strokeStyle = visColor
      ctx.lineWidth = 2.4
      ctx.lineJoin = 'round'
      ctx.lineCap = 'round'
      ctx.beginPath()
      const sliceW = W / bufLen
      let x = 0
      const midY = H / 2
      const amp = (visH / 2) * styleBlend

      if (style === 'point_wave') {
        ctx.fillStyle = visColor
        for (let i = 0; i < bufLen; i++) {
          const y = midY + smoothed[i] * amp
          ctx.beginPath()
          ctx.arc(x, y, 1.5, 0, Math.PI * 2)
          ctx.fill()
          x += sliceW
        }
      } else {
        for (let i = 0; i < bufLen; i++) {
          const y = midY + smoothed[i] * amp
          if (i === 0) ctx.moveTo(x, y); else ctx.lineTo(x, y)
          x += sliceW
        }
        ctx.stroke()

        if (style === 'mirror_wave') {
          ctx.beginPath()
          x = 0
          for (let i = 0; i < bufLen; i++) {
            const y = midY - smoothed[i] * amp
            if (i === 0) ctx.moveTo(x, y); else ctx.lineTo(x, y)
            x += sliceW
          }
          ctx.stroke()
        }
      }
    } else if (style === 'spectrum_bars' || style === 'histogram') {
      const data = new Uint8Array(bufLen)
      analyser.getByteFrequencyData(data)
      if (!smoothFreqRef.current || smoothFreqRef.current.length !== bufLen) {
        smoothFreqRef.current = Float32Array.from(data)
      } else {
        for (let i = 0; i < bufLen; i++) {
          const prev = smoothFreqRef.current[i]
          const current = data[i]
          const factor = current > prev ? SPECTRUM_FREQ_ATTACK : SPECTRUM_FREQ_RELEASE
          smoothFreqRef.current[i] = prev + (current - prev) * factor
        }
      }
      const smoothed = smoothFreqRef.current
      let maxVal = 0
      for (let i = 0; i < bufLen; i++) maxVal = Math.max(maxVal, smoothed[i])
      if (style === 'spectrum_bars') {
        const bins = Math.max(72, Math.min(118, Math.floor(W / 14)))
        const barW = Math.max(5, Math.floor(W / bins) - 3)
        const gap = 2
        const pitch = barW + gap
        const totalW = bins * pitch - gap
        const startX = Math.max(0, (W - totalW) / 2)
        const centerY = H * 0.5
        const centerGap = H * 0.18
        const topBaseY = centerY - centerGap / 2
        const bottomBaseY = centerY + centerGap / 2
        const maxH = H * 0.2
        const minH = 6
        const tipColor = brightenHex(visColor, 0.32)
        const glowColor = brightenHex(visColor, 0.18)
        const baseColor = `${visColor}50`
        const barColor = `${visColor}D8`

        const sampled = sampleSpectrumBins(smoothed, bins)
        const energized = applyMonstercat(sampled, SPECTRUM_MONSTERCAT_AMOUNT)
        const blurred = blurBins(energized)

        if (!spectrumBarsRef.current || spectrumBarsRef.current.length !== bins) {
          spectrumBarsRef.current = new Float32Array(blurred)
        }
        if (!spectrumPeaksRef.current || spectrumPeaksRef.current.length !== bins) {
          spectrumPeaksRef.current = new Float32Array(blurred)
        }
        if (!spectrumScratchRef.current || spectrumScratchRef.current.length !== bins) {
          spectrumScratchRef.current = new Float32Array(blurred)
        }

        const bars = spectrumBarsRef.current
        const peaks = spectrumPeaksRef.current
        const scratch = spectrumScratchRef.current
        for (let i = 0; i < bins; i++) {
          const prev = bars[i]
          const prevSample = sampled[Math.max(0, i - 1)] ?? sampled[i]
          const nextSample = sampled[Math.min(bins - 1, i + 1)] ?? sampled[i]
          const localDelta = sampled[i] - ((prevSample + nextSample) * 0.5)
          const microDetail = clamp01(sampled[i] + localDelta * 0.55)
          const nearAvg = (
            (blurred[Math.max(0, i - 1)] ?? blurred[i]) +
            blurred[i] +
            (blurred[Math.min(bins - 1, i + 1)] ?? blurred[i])
          ) / 3
          const transientBoost = Math.max(0, sampled[i] - prev) * 0.12
          const target = clamp01(nearAvg * 0.74 + microDetail * 0.26 + transientBoost)
          const attack = SPECTRUM_BAR_ATTACK
          const release = SPECTRUM_BAR_RELEASE
          const next = target > prev
            ? prev + (target - prev) * attack
            : prev + (target - prev) * release
          scratch[i] = next * 0.88 + target * 0.12
        }

        let energy = 0
        for (let i = 0; i < bins; i++) {
          const left = scratch[Math.max(0, i - 1)] ?? scratch[i]
          const center = scratch[i]
          const right = scratch[Math.min(bins - 1, i + 1)] ?? scratch[i]
          const shaped = center * (1 - SPECTRUM_BAR_SHAPE_BLEND) + ((left + center + right) / 3) * SPECTRUM_BAR_SHAPE_BLEND
          bars[i] = shaped

          const peakTarget = shaped + SPECTRUM_PEAK_RISE
          peaks[i] = peakTarget > peaks[i]
            ? peakTarget
            : Math.max(shaped, peaks[i] - SPECTRUM_PEAK_DECAY)
          energy += shaped
        }
        const avgEnergy = bins > 0 ? energy / bins : 0
        spectrumGlowRef.current = spectrumGlowRef.current * 0.68 + avgEnergy * 0.32

        ctx.save()
        ctx.shadowColor = glowColor
        ctx.shadowBlur = 8 + spectrumGlowRef.current * 14

        for (let i = 0; i < bins; i++) {
          const eased = Math.pow(clamp01(bars[i]), 0.92)
          const peak = Math.pow(clamp01(peaks[i]), 0.9)
          const h = minH + eased * maxH * styleBlend
          const peakH = minH + peak * maxH * styleBlend
          const x = startX + i * pitch
          const bodyGradTop = ctx.createLinearGradient(0, topBaseY - h, 0, topBaseY)
          bodyGradTop.addColorStop(0, tipColor)
          bodyGradTop.addColorStop(0.35, barColor)
          bodyGradTop.addColorStop(1, baseColor)
          const bodyGradBottom = ctx.createLinearGradient(0, bottomBaseY, 0, bottomBaseY + h)
          bodyGradBottom.addColorStop(0, baseColor)
          bodyGradBottom.addColorStop(0.65, barColor)
          bodyGradBottom.addColorStop(1, tipColor)

          ctx.fillStyle = baseColor
          ctx.fillRect(x, topBaseY - minH, barW, minH)
          ctx.fillRect(x, bottomBaseY, barW, minH)

          if (h > minH + 2) {
            ctx.fillStyle = bodyGradTop
            ctx.fillRect(x, topBaseY - h, barW, h - minH + 1)
            ctx.fillStyle = bodyGradBottom
            ctx.fillRect(x, bottomBaseY + minH - 1, barW, h - minH + 1)
            ctx.fillStyle = tipColor
            ctx.fillRect(x, topBaseY - h, barW, 2)
            ctx.fillRect(x, bottomBaseY + h - 2, barW, 2)
          }

          if (peakH > h + 4) {
            ctx.fillStyle = tipColor
            ctx.fillRect(x, topBaseY - peakH, barW, 2)
            ctx.fillRect(x, bottomBaseY + peakH - 2, barW, 2)
          }
        }

        ctx.restore()

        ctx.strokeStyle = `${glowColor}66`
        ctx.lineWidth = 1
        ctx.beginPath()
        ctx.moveTo(startX, topBaseY)
        ctx.lineTo(startX + totalW, topBaseY)
        ctx.moveTo(startX, bottomBaseY)
        ctx.lineTo(startX + totalW, bottomBaseY)
        ctx.stroke()
      } else {
        const bins = 180
        const group = Math.max(1, Math.floor(bufLen / bins))
        const barW = W / bins
        let bx = 0
        const hiColor = brightenHex(visColor, 0.52)
        const grad = ctx.createLinearGradient(0, H / 2 + visH / 2, 0, H / 2 - visH / 2)
        grad.addColorStop(0, visColor)
        grad.addColorStop(0.5, visColor)
        grad.addColorStop(1, hiColor)
        ctx.fillStyle = grad
        ctx.shadowColor = visColor
        ctx.shadowBlur = 8
        for (let i = 0; i < bins; i++) {
          let sum = 0
          let count = 0
          for (let j = 0; j < group; j++) {
            const idx = i * group + j
            if (idx >= bufLen) break
            sum += smoothed[idx]
            count += 1
          }
          const raw = count > 0 ? sum / count : 0
          const boosted = Math.min(255, raw * 2.8)
          const normalized = Math.pow(boosted / 255, 0.58)
          const bh = Math.max(5, normalized * visH * styleBlend)
          ctx.fillRect(bx, H / 2 + visH / 2 - bh, Math.max(2, barW - 1), bh)
          ctx.fillRect(bx, H / 2 - visH / 2, Math.max(2, barW - 1), bh)
          bx += barW
        }
        ctx.shadowBlur = 0
      }
    } else if (style === 'cqt' || style === 'cqt_color' || style === 'vectorscope') {
      // Approximated — show freq bars with a note
      const data = new Uint8Array(bufLen)
      analyser.getByteFrequencyData(data)
      if (!smoothFreqRef.current || smoothFreqRef.current.length !== bufLen) {
        smoothFreqRef.current = Float32Array.from(data)
      } else {
        for (let i = 0; i < bufLen; i++) {
          smoothFreqRef.current[i] = smoothFreqRef.current[i] * 0.88 + data[i] * 0.12
        }
      }
      const smoothed = smoothFreqRef.current
      let maxVal = 0
      for (let i = 0; i < bufLen; i++) maxVal = Math.max(maxVal, smoothed[i])
      if (maxVal < 2) {
        return
      }
      const barW = W / bufLen * 2
      let bx = 0
      const rgb = hexToRgb(visColor)
      for (let i = 0; i < bufLen; i++) {
        const t = i / bufLen
        const bh = (smoothed[i] / 255) * visH * styleBlend
        if (style === 'cqt_color') {
          const hue = Math.round(t * 280)
          ctx.fillStyle = `hsl(${hue},90%,60%)`
        } else {
          ctx.fillStyle = `rgba(${rgb},${0.4 + t * 0.6})`
        }
        ctx.fillRect(bx, H / 2 + visH / 2 - bh, barW - 1, bh)
        bx += barW + 1
      }
      // Approximate badge
      ctx.fillStyle = 'rgba(255,200,0,0.85)'
      ctx.font = 'bold 11px monospace'
      ctx.fillText('preview is approximate', 8, H - 8)
    }

    // Text overlays (preview only — basic)
    if (config.text_title.trim()) {
      ctx.fillStyle = config.text_color
      ctx.font = `bold ${Math.round(config.text_title_size * 0.5)}px sans-serif`
      ctx.textAlign = 'center'
      ctx.fillText(config.text_title.slice(0, 60), W / 2, H * 0.82)
    }
    if (config.text_subtitle.trim()) {
      ctx.fillStyle = config.text_color
      ctx.font = `${Math.round(config.text_subtitle_size * 0.5)}px sans-serif`
      ctx.textAlign = 'center'
      ctx.fillText(config.text_subtitle.slice(0, 80), W / 2, H * 0.90)
    }
  }, [config, canvasRef])

  const setupAnalyser = useCallback((audioEl: HTMLAudioElement) => {
    if (!audioCtxRef.current) {
      audioCtxRef.current = new AudioContext({ latencyHint: 'interactive' })
    }
    if (sourceRef.current && boundAudioElRef.current === audioEl && analyserRef.current) return

    if (sourceRef.current) {
      try { sourceRef.current.disconnect() } catch { /* ignore */ }
    }
    if (analyserRef.current) {
      try { analyserRef.current.disconnect() } catch { /* ignore */ }
    }
    if (gainRef.current) {
      try { gainRef.current.disconnect() } catch { /* ignore */ }
    }

    sourceRef.current = audioCtxRef.current.createMediaElementSource(audioEl)
    analyserRef.current = audioCtxRef.current.createAnalyser()
    gainRef.current = audioCtxRef.current.createGain()
    analyserRef.current.fftSize = SPECTRUM_ANALYSER_FFT_SIZE
    analyserRef.current.smoothingTimeConstant = SPECTRUM_ANALYSER_SMOOTHING
    analyserRef.current.minDecibels = SPECTRUM_ANALYSER_MIN_DB
    analyserRef.current.maxDecibels = SPECTRUM_ANALYSER_MAX_DB
    sourceRef.current.connect(analyserRef.current)
    analyserRef.current.connect(gainRef.current)
    gainRef.current.gain.value = 1
    gainRef.current.connect(audioCtxRef.current.destination)
    boundAudioElRef.current = audioEl
  }, [])

  useEffect(() => {
    if (prevStyleRef.current !== config.style) {
      prevStyleRef.current = config.style
      styleChangedAtRef.current = performance.now()
    }
  }, [config.style])

  useEffect(() => {
    cancelAnimationFrame(rafRef.current)
    rafRef.current = requestAnimationFrame(drawFrame)
    return () => cancelAnimationFrame(rafRef.current)
  }, [drawFrame])

  return { setupAnalyser, audioCtxRef }
}

// ─── Main component ───────────────────────────────────────────────────────────

export default function AudioVisualizer() {
  const [audioPath, setAudioPath] = useState<string | null>(null)
  const [audioUrl, setAudioUrl] = useState<string | null>(null)
  const [config, setConfig] = useState<VisualizerConfig>(DEFAULT_CONFIG)
  const [jobId, setJobId] = useState<string | null>(null)
  const [jobStatus, setJobStatus] = useState<JobStatus>('idle')
  const [progress, setProgress] = useState(0)
  const [outputPath, setOutputPath] = useState<string | null>(null)
  const [presets, setPresets] = useState<Preset[]>([])
  const [presetName, setPresetName] = useState('')
  const [showPresets, setShowPresets] = useState(false)
  const [showAdvanced, setShowAdvanced] = useState(false)
  const [isPlaying, setIsPlaying] = useState(false)
  const [isDraggingAudio, setIsDraggingAudio] = useState(false)

  const canvasRef = useRef<HTMLCanvasElement>(null)
  const audioElRef = useRef<HTMLAudioElement | null>(null)
  const previewFallbackRef = useRef(false)
  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null)
  const pollStartRef = useRef<number>(0)
  const pollLastProgressRef = useRef<number>(0)
  const progressRef = useRef<number>(0)

  const { setupAnalyser, audioCtxRef } = useAudioPreview(config, canvasRef)

  useEffect(() => {
    if (config.style !== 'spectrum_bars') {
      setConfig(c => ({ ...c, style: 'spectrum_bars' }))
    }
  }, [config.style])

  // Load presets on mount
  useEffect(() => { loadPresets() }, [])

  // Poll render status
  useEffect(() => {
    progressRef.current = progress
  }, [progress])

  useEffect(() => {
    if (!jobId || jobStatus === 'idle') return
    if (['done', 'failed', 'cancelled', 'lost'].includes(jobStatus)) {
      if (pollRef.current) clearInterval(pollRef.current)
      return
    }
    pollRef.current = setInterval(async () => {
      const now = Date.now()
      const totalElapsed = now - pollStartRef.current
      const stalledElapsed = now - pollLastProgressRef.current
      if (totalElapsed > 60 * 60 * 1000) {
        clearInterval(pollRef.current!)
        setJobStatus('failed')
        toast.error('Render timed out after 60 minutes')
        return
      }
      if (stalledElapsed > 10 * 60 * 1000) {
        clearInterval(pollRef.current!)
        setJobStatus('failed')
        toast.error('Render stalled for over 10 minutes')
        return
      }
      try {
        const res = await fetch(`${API}/render/${jobId}`)
        const data = await res.json()
        setJobStatus(data.status as JobStatus)
        const nextProgress = data.progress_pct ?? 0
        if (nextProgress > progressRef.current) {
          pollLastProgressRef.current = Date.now()
        }
        setProgress(nextProgress)
        if (data.output_path) setOutputPath(data.output_path)
        if (data.status === 'done') {
          clearInterval(pollRef.current!)
          toast.success('Render complete!')
        } else if (data.status === 'failed') {
          clearInterval(pollRef.current!)
          toast.error(`Render failed: ${data.error || 'Unknown error'}`)
        } else if (data.status === 'lost') {
          clearInterval(pollRef.current!)
          toast.error('Render session was lost — please re-render')
        }
      } catch {
        // Silent retry — 5 consecutive errors handled by timeout above
      }
    }, 1500)
    return () => { if (pollRef.current) clearInterval(pollRef.current) }
  }, [jobId, jobStatus])

  const loadPresets = async () => {
    try {
      const res = await fetch(`${API}/presets`)
      if (res.ok) setPresets(await res.json())
    } catch { /* silent */ }
  }

  const pickAudio = async () => {
    const path = await tauriOpen({
      multiple: false,
      filters: [{ name: 'Audio', extensions: ['mp3', 'wav', 'm4a', 'flac', 'ogg'] }],
    })
    if (!path || typeof path !== 'string') return
    const normalized = normalizeAudioPath(path)
    setAudioPath(normalized)
    setAudioUrl(buildPreviewUrl(normalized))
    previewFallbackRef.current = false
    setJobStatus('idle')
    setOutputPath(null)
    setProgress(0)
  }

  const AUDIO_EXTS = ['mp3', 'wav', 'm4a', 'flac', 'ogg', 'aac']

  const dropAudio = (e: React.DragEvent<HTMLDivElement>) => {
    e.preventDefault()
    setIsDraggingAudio(false)
    const file = e.dataTransfer.files[0]
    if (!file) return
    const ext = file.name.split('.').pop()?.toLowerCase() ?? ''
    if (!AUDIO_EXTS.includes(ext)) { toast.error('Unsupported format. Use mp3, wav, m4a, flac, ogg.'); return }
    // Tauri exposes the real filesystem path on the File object
    const rawPath = (file as File & { path?: string }).path
    if (!rawPath) {
      toast.error('Không đọc được đường dẫn thật của file audio')
      return
    }
    const filePath = normalizeAudioPath(rawPath)
    setAudioPath(filePath)
    setAudioUrl(buildPreviewUrl(filePath))
    previewFallbackRef.current = false
    setJobStatus('idle')
    setOutputPath(null)
    setProgress(0)
  }

  const pickImage = async (field: 'background_image_path' | 'cover_art_path') => {
    const path = await tauriOpen({
      multiple: false,
      filters: [{ name: 'Image', extensions: ['png', 'jpg', 'jpeg', 'webp'] }],
    })
    if (!path || typeof path !== 'string') return
    setConfig(c => ({ ...c, [field]: path }))
  }

  const startRender = async () => {
    if (!audioPath) { toast.error('Please select an audio file first'); return }
    if (['queued', 'running'].includes(jobStatus)) return

    setJobStatus('queued')
    setProgress(0)
    setOutputPath(null)

    try {
      const res = await fetch(`${API}/render`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ audio_path: audioPath, config: { ...config, style: 'spectrum_bars' } }),
      })
      if (!res.ok) {
        const err = await res.json().catch(() => ({}))
        throw new Error(err.detail?.message || err.detail || 'Render request failed')
      }
      const data = await res.json()
      setJobId(data.job_id)
      pollStartRef.current = Date.now()
      pollLastProgressRef.current = Date.now()
    } catch (e: unknown) {
      setJobStatus('failed')
      toast.error(e instanceof Error ? e.message : 'Render failed')
    }
  }

  const stopRender = async () => {
    if (!jobId) return
    await fetch(`${API}/render/${jobId}/cancel`, { method: 'POST' })
    setJobStatus('cancelled')
    toast.info('Render cancelled')
  }

  const savePreset = async () => {
    if (!presetName.trim()) { toast.error('Enter a preset name'); return }
    try {
      const res = await fetch(`${API}/presets`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ name: presetName.trim(), config }),
      })
      if (!res.ok) {
        const err = await res.json().catch(() => ({}))
        throw new Error(err.detail?.message || 'Save failed')
      }
      toast.success(`Preset "${presetName}" saved`)
      setPresetName('')
      loadPresets()
    } catch (e: unknown) {
      toast.error(e instanceof Error ? e.message : 'Save failed')
    }
  }

  const deletePreset = async (id: number, name: string) => {
    await fetch(`${API}/presets/${id}`, { method: 'DELETE' })
    toast.success(`Preset "${name}" deleted`)
    loadPresets()
  }

  const loadPreset = (preset: Preset) => {
    setConfig({ ...preset.config, style: 'spectrum_bars' })
    setShowPresets(false)
  }

  const set = <K extends keyof VisualizerConfig>(key: K, value: VisualizerConfig[K]) =>
    setConfig(c => ({ ...c, [key]: value }))

  const isRendering = ['queued', 'running'].includes(jobStatus)

  const togglePlay = async () => {
    if (!audioElRef.current) return
    if (isPlaying) {
      audioElRef.current.pause()
      setIsPlaying(false)
    } else {
      audioElRef.current.muted = false
      audioElRef.current.volume = 1
      try {
        setupAnalyser(audioElRef.current)
      } catch {
        toast.error('Không kết nối được audio analyzer, chọn lại file audio giúp mình')
        return
      }
      // Resume AudioContext — must be called inside a user gesture handler
      if (audioCtxRef.current && audioCtxRef.current.state === 'suspended') {
        await audioCtxRef.current.resume()
      }
      try {
        await audioElRef.current.play()
        setIsPlaying(true)
      } catch (error) {
        toast.error(error instanceof Error ? error.message : 'Không preview được audio')
      }
    }
  }

  return (
    <div className="flex flex-col h-full overflow-hidden bg-surface-50">

      {/* Hidden audio element for preview */}
      {audioUrl && (
        <audio
          ref={el => { audioElRef.current = el }}
          onPlay={() => setIsPlaying(true)}
          onPause={() => setIsPlaying(false)}
          onCanPlay={e => {
            try { setupAnalyser(e.currentTarget) } catch { /* handled on Play click */ }
          }}
          onError={() => {
            if (audioPath && !previewFallbackRef.current) {
              previewFallbackRef.current = true
              setAudioUrl(convertFileSrc(audioPath))
              return
            }
            toast.error('Không load được file audio preview')
          }}
          crossOrigin="anonymous"
          src={audioUrl}
          preload="auto"
          className="hidden"
        />
      )}

      {/* Header */}
      <div className="flex items-center justify-between px-6 py-4 border-b border-surface-200 bg-white shrink-0">
        <div>
          <h1 className="page-title flex items-center gap-2">
            <AudioWaveform size={18} className="text-primary-500" />
            Audio Visualizer
          </h1>
          <p className="page-sub mt-0.5">Generate podcast video with FFmpeg</p>
        </div>
        <div className="flex items-center gap-2">
          {/* Preset panel toggle */}
          <button className="btn-secondary" onClick={() => setShowPresets(p => !p)}>
            <Save size={14} />
            Presets {presets.length > 0 && <span className="ml-1 text-xs text-surface-400">({presets.length})</span>}
          </button>
        </div>
      </div>

      <div className="flex flex-1 min-h-0 overflow-hidden">

        {/* ── Left: canvas preview ────────────────────────────────────────────── */}
        <div className="flex flex-col flex-1 min-w-0 p-4 gap-3 overflow-y-auto">

          {/* Canvas */}
          <div className="relative rounded-xl overflow-hidden bg-black aspect-video w-full max-h-[340px] shrink-0 shadow-md">
            <canvas
              ref={canvasRef}
              width={640}
              height={360}
              className="w-full h-full"
            />
            {!audioPath && (
              <div className="absolute inset-0 flex flex-col items-center justify-center text-white/40 gap-2">
                <Music size={32} />
                <p className="text-sm">No audio loaded</p>
              </div>
            )}
            {/* Play button overlay */}
            {audioPath && (
              <button
                className="absolute bottom-3 left-3 flex items-center gap-1.5 px-3 py-2 rounded-full bg-black/60 text-white hover:bg-black/85 transition text-xs font-medium"
                onClick={togglePlay}
                title={isPlaying ? 'Stop Preview' : 'Preview'}
              >
                {isPlaying ? <Square size={14} /> : <Play size={14} />}
                <span>{isPlaying ? 'Stop' : 'Preview'}</span>
              </button>
            )}
          </div>

          {/* Audio file — click or drag-and-drop */}
          <div
            className={cn(
              'card p-4 transition border-2',
              isDraggingAudio ? 'border-primary-400 bg-primary-50' : 'border-transparent',
            )}
            onDragOver={e => { e.preventDefault(); setIsDraggingAudio(true) }}
            onDragEnter={e => { e.preventDefault(); setIsDraggingAudio(true) }}
            onDragLeave={() => setIsDraggingAudio(false)}
            onDrop={dropAudio}
          >
            <div className="flex items-center gap-3">
              <button className="btn-primary flex items-center gap-1.5" onClick={pickAudio}>
                <FolderOpen size={14} />
                {audioPath ? 'Change Audio' : 'Pick Audio File'}
              </button>
              {audioPath ? (
                <p className="text-xs text-surface-500 truncate flex-1" title={audioPath}>
                  {audioPath.split('/').pop()}
                </p>
              ) : (
                <p className="text-xs text-surface-400">or drag & drop an audio file here</p>
              )}
            </div>
          </div>

          {/* Style picker */}
          <div className="card p-4">
            <p className="text-xs font-semibold text-surface-600 mb-3">Visualizer Style</p>
            <div className="grid grid-cols-2 gap-2">
              {STYLES.map(s => (
                <button
                  key={s.id}
                  onClick={() => set('style', s.id)}
                  className={cn(
                    'text-left px-3 py-2 rounded-lg border transition text-xs',
                    config.style === s.id
                      ? 'border-primary-500 bg-primary-50 text-primary-700'
                      : 'border-surface-200 bg-white text-surface-600 hover:border-surface-300',
                  )}
                >
                  <div className="font-semibold">{s.label}</div>
                  <div className="text-[10px] text-surface-400 mt-0.5">{s.desc}</div>
                  {s.approx && (
                    <div className="text-[10px] text-amber-500 mt-0.5">~ approx preview</div>
                  )}
                </button>
              ))}
            </div>
          </div>

          {/* Colors */}
          <div className="card p-4">
            <p className="text-xs font-semibold text-surface-600 mb-3">Colors</p>
            <div className="grid grid-cols-2 gap-3">
              <ColorField label="Visualizer" value={config.visualizer_color} onChange={v => set('visualizer_color', v)} />

              {/* Background mode */}
              <div className="col-span-2">
                <p className="text-[11px] text-surface-500 mb-1">Background</p>
                <div className="flex gap-2 mb-2">
                  {(['solid', 'gradient', 'image'] as BgMode[]).map(m => (
                    <button key={m} onClick={() => set('background_mode', m)}
                      className={cn('text-xs px-2.5 py-1 rounded-md border capitalize transition',
                        config.background_mode === m
                          ? 'border-primary-500 bg-primary-50 text-primary-700'
                          : 'border-surface-200 text-surface-500 hover:border-surface-300'
                      )}>
                      {m}
                    </button>
                  ))}
                </div>
                {config.background_mode === 'solid' && (
                  <ColorField label="Color" value={config.background_solid_color} onChange={v => set('background_solid_color', v)} />
                )}
                {config.background_mode === 'gradient' && (
                  <div className="flex flex-col gap-2">
                    <div className="flex gap-2">
                      <ColorField label="Color 1" value={config.background_gradient_color1} onChange={v => set('background_gradient_color1', v)} />
                      <ColorField label="Color 2" value={config.background_gradient_color2} onChange={v => set('background_gradient_color2', v)} />
                    </div>
                    <div>
                      <p className="text-[11px] text-surface-500 mb-1">Angle: {config.background_gradient_angle}°</p>
                      <input type="range" min={0} max={360} value={config.background_gradient_angle}
                        onChange={e => set('background_gradient_angle', Number(e.target.value))}
                        className="w-full accent-primary-500" />
                    </div>
                  </div>
                )}
                {config.background_mode === 'image' && (
                  <div className="flex items-center gap-2">
                    <button className="btn-secondary text-xs" onClick={() => pickImage('background_image_path')}>
                      <FolderOpen size={12} /> Pick Image
                    </button>
                    {config.background_image_path && (
                      <span className="text-xs text-surface-500 truncate">{config.background_image_path.split('/').pop()}</span>
                    )}
                  </div>
                )}
              </div>
            </div>
          </div>

          {/* Advanced: cover art + text + resolution */}
          <div className="card p-4">
            <button
              className="flex items-center gap-2 text-xs font-semibold text-surface-600 w-full"
              onClick={() => setShowAdvanced(p => !p)}
            >
              {showAdvanced ? <ChevronUp size={14} /> : <ChevronDown size={14} />}
              Cover Art, Text & Output Settings
            </button>

            {showAdvanced && (
              <div className="mt-4 flex flex-col gap-4">

                {/* Cover art */}
                <div>
                  <p className="text-xs font-semibold text-surface-600 mb-2">Cover Art (optional)</p>
                  <div className="flex items-center gap-2 mb-2">
                    <button className="btn-secondary text-xs" onClick={() => pickImage('cover_art_path')}>
                      <FolderOpen size={12} /> Pick Cover Art
                    </button>
                    {config.cover_art_path && (
                      <>
                        <span className="text-xs text-surface-500 truncate flex-1">{config.cover_art_path.split('/').pop()}</span>
                        <button className="btn-icon text-surface-400" onClick={() => set('cover_art_path', null)}><XCircle size={13} /></button>
                      </>
                    )}
                  </div>
                  {config.cover_art_path && (
                    <div className="flex gap-2">
                      {(['center', 'bottom_third', 'top_right'] as CoverPos[]).map(p => (
                        <button key={p} onClick={() => set('cover_art_position', p)}
                          className={cn('text-[11px] px-2 py-1 rounded border capitalize transition',
                            config.cover_art_position === p
                              ? 'border-primary-500 bg-primary-50 text-primary-700'
                              : 'border-surface-200 text-surface-500'
                          )}>
                          {p.replace('_', ' ')}
                        </button>
                      ))}
                    </div>
                  )}
                </div>

                {/* Text overlays */}
                <div>
                  <p className="text-xs font-semibold text-surface-600 mb-2">Text Overlays (optional)</p>
                  <div className="flex flex-col gap-2">
                    <div>
                      <p className="text-[11px] text-surface-500 mb-1">Title (max 80 chars)</p>
                      <input className="input text-xs" maxLength={80} placeholder="Episode title..."
                        value={config.text_title} onChange={e => set('text_title', e.target.value)} />
                    </div>
                    <div>
                      <p className="text-[11px] text-surface-500 mb-1">Subtitle (max 100 chars)</p>
                      <input className="input text-xs" maxLength={100} placeholder="Channel name or description..."
                        value={config.text_subtitle} onChange={e => set('text_subtitle', e.target.value)} />
                    </div>
                    <ColorField label="Text color" value={config.text_color} onChange={v => set('text_color', v)} />
                  </div>
                </div>

                {/* Resolution */}
                <div>
                  <p className="text-xs font-semibold text-surface-600 mb-2">Resolution</p>
                  <select className="input text-xs" value={config.resolution}
                    onChange={e => set('resolution', e.target.value as Resolution)}>
                    {RESOLUTIONS.map(r => <option key={r.value} value={r.value}>{r.label}</option>)}
                  </select>
                  {config.resolution === 'custom' && (
                    <div className="flex gap-2 mt-2">
                      <input className="input text-xs" type="number" placeholder="Width" min={480} max={3840} step={2}
                        value={config.resolution_custom_w ?? ''} onChange={e => set('resolution_custom_w', Number(e.target.value) || null)} />
                      <input className="input text-xs" type="number" placeholder="Height" min={480} max={3840} step={2}
                        value={config.resolution_custom_h ?? ''} onChange={e => set('resolution_custom_h', Number(e.target.value) || null)} />
                    </div>
                  )}
                </div>

                {/* Output format */}
                <div>
                  <p className="text-xs font-semibold text-surface-600 mb-2">Output Format</p>
                  <div className="flex gap-2">
                    {(['mp4', 'mov', 'webm'] as OutputFmt[]).map(f => (
                      <button key={f} onClick={() => set('output_format', f)}
                        className={cn('text-xs px-3 py-1.5 rounded-md border uppercase font-mono transition',
                          config.output_format === f
                            ? 'border-primary-500 bg-primary-50 text-primary-700'
                            : 'border-surface-200 text-surface-500 hover:border-surface-300'
                        )}>
                        {f}
                        {f === 'webm' && <span className="ml-1 text-[10px] text-surface-400">slower</span>}
                      </button>
                    ))}
                  </div>
                </div>
              </div>
            )}
          </div>
        </div>

        {/* ── Right: render panel + presets ──────────────────────────────────── */}
        <div className="w-64 shrink-0 border-l border-surface-200 bg-white flex flex-col overflow-y-auto">

          {/* Render button */}
          <div className="p-4 border-b border-surface-200">
            {!isRendering ? (
              <button
                className={cn('btn-primary w-full', !audioPath && 'opacity-40 cursor-not-allowed')}
                onClick={startRender}
                disabled={!audioPath}
              >
                <Play size={14} />
                Render Video
              </button>
            ) : (
              <button className="btn-danger w-full" onClick={stopRender}>
                <Square size={14} />
                Cancel
              </button>
            )}

            {/* Progress bar */}
            {isRendering && (
              <div className="mt-3">
                <div className="flex items-center justify-between text-xs text-surface-500 mb-1">
                  <span className="flex items-center gap-1.5"><Loader size={11} className="animate-spin" /> Rendering…</span>
                  <span>{progress}%</span>
                </div>
                <div className="w-full bg-surface-100 rounded-full h-1.5">
                  <div className="bg-primary-500 h-1.5 rounded-full transition-all" style={{ width: `${progress}%` }} />
                </div>
              </div>
            )}

            {/* Result */}
            {jobStatus === 'done' && outputPath && (
              <div className="mt-3 p-2.5 rounded-lg bg-green-50 border border-green-200">
                <div className="flex items-center gap-1.5 text-green-700 text-xs font-semibold mb-1">
                  <CheckCircle size={13} /> Done
                </div>
                <p className="text-[11px] text-green-600 break-all">{outputPath.split('/').pop()}</p>
                <p className="text-[10px] text-green-500 mt-0.5 break-all">{outputPath}</p>
              </div>
            )}
            {jobStatus === 'failed' && (
              <div className="mt-3 p-2.5 rounded-lg bg-red-50 border border-red-200 text-xs text-red-600 flex items-center gap-1.5">
                <XCircle size={13} /> Render failed
              </div>
            )}
          </div>

          {/* Presets panel */}
          {showPresets && (
            <div className="flex flex-col flex-1 overflow-hidden">
              <div className="p-3 border-b border-surface-100">
                <p className="text-xs font-semibold text-surface-700 mb-2">Save current as preset</p>
                <div className="flex gap-1.5">
                  <input
                    className="input text-xs flex-1"
                    placeholder="Preset name..."
                    value={presetName}
                    onChange={e => setPresetName(e.target.value)}
                    onKeyDown={e => e.key === 'Enter' && savePreset()}
                  />
                  <button className="btn-primary px-2" onClick={savePreset}><Save size={13} /></button>
                </div>
              </div>
              <div className="flex-1 overflow-y-auto">
                {presets.length === 0 && (
                  <p className="text-xs text-surface-400 p-3">No presets saved yet.</p>
                )}
                {presets.map(p => (
                  <div key={p.id} className="flex items-center gap-1 px-3 py-2 border-b border-surface-100 hover:bg-surface-50 group">
                    <button className="flex-1 text-left text-xs text-surface-700 truncate" onClick={() => loadPreset(p)}>
                      {p.name}
                    </button>
                    <button className="btn-icon opacity-0 group-hover:opacity-100 text-red-400"
                      onClick={() => deletePreset(p.id, p.name)}>
                      <Trash2 size={12} />
                    </button>
                  </div>
                ))}
              </div>
            </div>
          )}
        </div>
      </div>
    </div>
  )
}

// ─── ColorField helper ────────────────────────────────────────────────────────

function ColorField({ label, value, onChange }: { label: string; value: string; onChange: (v: string) => void }) {
  return (
    <div className="flex items-center gap-2">
      <input
        type="color"
        value={value}
        onChange={e => onChange(e.target.value)}
        className="w-8 h-8 rounded-md border border-surface-200 cursor-pointer p-0.5 bg-white"
        title={label}
      />
      <div>
        <p className="text-[11px] text-surface-500">{label}</p>
        <p className="text-[10px] font-mono text-surface-400">{value}</p>
      </div>
    </div>
  )
}
