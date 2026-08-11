import { apiFetch } from './api.ts'

export type AudioProvider = 'elevenlabs' | 'minimax'
export type HealthStatus = 'good' | 'degraded' | 'down'

export interface VoiceOption {
  voice_id: string
  name: string
  provider: AudioProvider
  description?: string | null
  category?: string | null
  preview_url?: string | null
  voice_type?: string | null
  source?: 'default' | 'shared' | 'my-voice' | 'custom'
  tags?: string[]
  labels?: {
    accent?: string
    gender?: string
    age?: string
    descriptive?: string
    language?: string
    locale?: string
  }
}

export interface ModelOption {
  model_id: string
  name: string
  description?: string
  provider: AudioProvider
}

export interface ELVoiceSettings {
  stability: number
  similarity_boost: number
  style: number
  use_speaker_boost: boolean
  language_code?: string
  loudness_normalization?: boolean
  apply_text_normalization?: boolean
}

export interface MiniMaxVoiceSettings {
  speed: number
  vol: number
  pitch: number
  emotion: string
}

export interface DialogueItem {
  text: string
  voice_id: string
}

interface MiniMaxVoiceResponse {
  voice_id: string
  voice_name: string
  voice_type: string
}

interface AudioSaveResponse {
  ok: boolean
  path?: string | null
  message: string
}

const ELEVENLABS_MODELS: ModelOption[] = [
  {
    model_id: 'eleven_multilingual_v2',
    name: 'Eleven Multilingual v2',
    description: 'Stable multilingual speech',
    provider: 'elevenlabs',
  },
]

const MINIMAX_MODELS: ModelOption[] = [
  { model_id: 'speech-02-hd', name: 'Speech 2.8 HD Preview', description: 'Highest quality', provider: 'minimax' },
  { model_id: 'speech-02-turbo', name: 'Speech 2.8 Turbo', description: 'Fast generation', provider: 'minimax' },
  { model_id: 'speech-01-hd', name: 'Speech 2.5 HD Preview', description: 'HD quality', provider: 'minimax' },
  { model_id: 'speech-01-turbo', name: 'Speech 2.5 Turbo', description: 'Fast generation', provider: 'minimax' },
]

async function responseError(response: Response): Promise<Error> {
  const body = await response.json().catch(() => null) as { detail?: string; message?: string } | null
  return new Error(body?.detail || body?.message || `Audio request failed (${response.status})`)
}

async function fetchJson<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await apiFetch(path, init)
  if (!response.ok) throw await responseError(response)
  return response.json() as Promise<T>
}

function mapMiniMaxVoice(voice: MiniMaxVoiceResponse): VoiceOption {
  const personal = voice.voice_type === 'voice_cloning' || voice.voice_type === 'voice_generation'
  return {
    voice_id: voice.voice_id,
    name: voice.voice_name,
    provider: 'minimax',
    voice_type: voice.voice_type,
    source: personal ? 'my-voice' : 'default',
  }
}

export async function listVoices(provider: AudioProvider): Promise<VoiceOption[]> {
  if (provider === 'minimax') {
    const voices = await fetchJson<MiniMaxVoiceResponse[]>('/api/audio/minimax/voices')
    return voices.map(mapMiniMaxVoice)
  }

  return fetchJson<VoiceOption[]>('/api/audio/elevenlabs/voices')
}

export async function listSharedVoices(): Promise<VoiceOption[]> {
  return fetchJson<VoiceOption[]>('/api/audio/elevenlabs/shared-voices')
}

export async function listModels(provider: AudioProvider): Promise<ModelOption[]> {
  return provider === 'minimax' ? MINIMAX_MODELS : ELEVENLABS_MODELS
}

export async function generateTtsAudio(
  provider: AudioProvider,
  text: string,
  voiceId: string,
  modelId: string,
  settings: ELVoiceSettings | MiniMaxVoiceSettings,
): Promise<string> {
  const path = provider === 'minimax'
    ? '/api/audio/minimax/tts'
    : '/api/audio/elevenlabs/tts'
  const body = provider === 'minimax'
    ? {
        text,
        voice_id: voiceId,
        model: modelId,
        speed: (settings as MiniMaxVoiceSettings).speed,
        volume: (settings as MiniMaxVoiceSettings).vol,
        pitch: (settings as MiniMaxVoiceSettings).pitch,
        emotion: (settings as MiniMaxVoiceSettings).emotion,
      }
    : {
        text,
        voice_id: voiceId,
        model_id: modelId,
        voice_settings: settings,
      }

  const response = await apiFetch(path, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  })
  if (!response.ok) throw await responseError(response)
  return URL.createObjectURL(await response.blob())
}

export async function createDialogueAudio(inputs: DialogueItem[]): Promise<Blob> {
  const response = await apiFetch('/api/audio/elevenlabs/dialogue', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ inputs }),
  })
  if (!response.ok) throw await responseError(response)
  return response.blob()
}

async function providerHealth(path: string): Promise<HealthStatus> {
  try {
    const response = await apiFetch(path)
    if (!response.ok) return 'down'
    const body = await response.json().catch(() => null) as { configured?: boolean } | null
    return body?.configured === false ? 'degraded' : 'good'
  } catch {
    return 'down'
  }
}

export async function getHealth(): Promise<{ elevenlabs: HealthStatus; minimax: HealthStatus }> {
  const [elevenlabs, minimax] = await Promise.all([
    providerHealth('/api/audio/elevenlabs/health'),
    providerHealth('/api/audio/minimax/health'),
  ])
  return { elevenlabs, minimax }
}

function blobToBase64(blob: Blob): Promise<string> {
  return new Promise((resolve, reject) => {
    const reader = new FileReader()
    reader.onload = () => resolve(String(reader.result).split(',', 2)[1] ?? '')
    reader.onerror = () => reject(reader.error ?? new Error('Cannot read audio file'))
    reader.readAsDataURL(blob)
  })
}

export async function saveAudioZipFile(
  savePath: string,
  files: Array<{ filename: string; blob: Blob }>,
): Promise<AudioSaveResponse> {
  const payload = {
    save_path: savePath,
    files: await Promise.all(files.map(async (file) => ({
      filename: file.filename,
      content_base64: await blobToBase64(file.blob),
    }))),
  }
  return fetchJson<AudioSaveResponse>('/api/audio/save-zip', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  })
}
