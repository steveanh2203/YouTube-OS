import type { ApiYouTubeAISettings, ApiYouTubeConfig } from './api.ts'

export const DEFAULT_MODEL = 'openrouter/free'
export const DEFAULT_SYSTEM_PROMPT = [
  'You reply to YouTube comments for the channel owner.',
  'Be warm, polite, natural, and concise.',
  'Thank the viewer when it fits naturally.',
  'When relevant, invite them to subscribe for more videos, but never sound pushy.',
  'Adapt to the comment. Answer questions clearly, acknowledge praise, and handle criticism calmly.',
  'Keep replies short and clean. No fluff, no hashtags, no emojis, no fake hype.',
  'If the comment is spam, abusive, unrelated, or should not be answered, respond with exactly SKIP.',
].join('\n')

export type AutoReplyMode = 'manual' | 'safe-only'

export interface YouTubeReplyConfig {
  accessToken: string
  channelId: string
  channelName: string
  openRouterKey: string
  model: string
  systemPrompt: string
  channelDna: string
  audienceProfile: string
  targetMarket: string
  autoReplyMode: AutoReplyMode
  providerMode: 'api'
  connected: boolean
  verifiedAt: string
}

export interface YouTubeAIReplySettings {
  openRouterKey: string
  primaryModel: string
  fallbackModels: string[]
  verifiedAt: string
}

export const OPENROUTER_FREE_MODEL_GROUPS = [
  {
    label: 'OpenRouter',
    options: [
      { value: DEFAULT_MODEL, label: 'OpenRouter Free Router' },
      { value: 'google/gemma-3-27b-it:free', label: 'Gemma 3 27B (Free)' },
      { value: 'meta-llama/llama-3.3-70b-instruct:free', label: 'Llama 3.3 70B (Free)' },
    ],
  },
] as const

export function sanitizeYouTubeReplyConfig(value: Partial<YouTubeReplyConfig> = {}): YouTubeReplyConfig {
  return {
    accessToken: String(value.accessToken ?? ''),
    channelId: String(value.channelId ?? '').trim(),
    channelName: String(value.channelName ?? '').trim(),
    openRouterKey: String(value.openRouterKey ?? '').trim(),
    model: String(value.model ?? '').trim() || DEFAULT_MODEL,
    systemPrompt: String(value.systemPrompt ?? '').trim() || DEFAULT_SYSTEM_PROMPT,
    channelDna: String(value.channelDna ?? '').trim(),
    audienceProfile: String(value.audienceProfile ?? '').trim(),
    targetMarket: String(value.targetMarket ?? '').trim(),
    autoReplyMode: value.autoReplyMode === 'safe-only' ? 'safe-only' : 'manual',
    providerMode: 'api',
    connected: Boolean(value.connected),
    verifiedAt: String(value.verifiedAt ?? '').trim(),
  }
}

export function fromApiYouTubeConfig(value: ApiYouTubeConfig): YouTubeReplyConfig {
  return sanitizeYouTubeReplyConfig({
    accessToken: value.access_token,
    channelId: value.channel_id,
    channelName: value.channel_name,
    openRouterKey: value.openrouter_key,
    model: value.model,
    systemPrompt: value.system_prompt,
    channelDna: value.channel_dna,
    audienceProfile: value.audience_profile,
    targetMarket: value.target_market,
    autoReplyMode: value.auto_reply_mode === 'safe-only' ? 'safe-only' : 'manual',
    providerMode: 'api',
    connected: value.connected,
    verifiedAt: value.verified_at,
  })
}

export function toApiYouTubeConfig(value: YouTubeReplyConfig): ApiYouTubeConfig {
  const clean = sanitizeYouTubeReplyConfig(value)
  return {
    access_token: clean.accessToken,
    channel_id: clean.channelId,
    channel_name: clean.channelName,
    openrouter_key: clean.openRouterKey,
    model: clean.model,
    system_prompt: clean.systemPrompt,
    channel_dna: clean.channelDna,
    audience_profile: clean.audienceProfile,
    target_market: clean.targetMarket,
    auto_reply_mode: clean.autoReplyMode,
    provider_mode: 'api',
    connected: clean.connected,
    verified_at: clean.verifiedAt,
  }
}

export function sanitizeYouTubeAIReplySettings(
  value: Partial<YouTubeAIReplySettings> = {},
): YouTubeAIReplySettings {
  const primaryModel = String(value.primaryModel ?? '').trim() || DEFAULT_MODEL
  const fallbackModels = [...new Set((value.fallbackModels ?? []).map((item) => item.trim()).filter(Boolean))]
    .filter((item) => item !== primaryModel)
    .slice(0, 4)
  return {
    openRouterKey: String(value.openRouterKey ?? '').trim(),
    primaryModel,
    fallbackModels,
    verifiedAt: String(value.verifiedAt ?? '').trim(),
  }
}

export function fromApiYouTubeAIReplySettings(value: ApiYouTubeAISettings): YouTubeAIReplySettings {
  return sanitizeYouTubeAIReplySettings({
    openRouterKey: value.openrouter_key,
    primaryModel: value.primary_model,
    fallbackModels: value.fallback_models,
    verifiedAt: value.verified_at,
  })
}

export function toApiYouTubeAIReplySettings(value: YouTubeAIReplySettings): ApiYouTubeAISettings {
  const clean = sanitizeYouTubeAIReplySettings(value)
  return {
    openrouter_key: clean.openRouterKey,
    primary_model: clean.primaryModel,
    fallback_models: clean.fallbackModels,
    verified_at: clean.verifiedAt,
  }
}

export function buildEffectiveSystemPrompt(value: YouTubeReplyConfig): string {
  const clean = sanitizeYouTubeReplyConfig(value)
  const context = [
    clean.channelDna && `Channel DNA:\n${clean.channelDna}`,
    clean.audienceProfile && `Audience profile:\n${clean.audienceProfile}`,
    clean.targetMarket && `Target market:\n${clean.targetMarket}`,
  ].filter(Boolean)
  return [clean.systemPrompt, ...context].join('\n\n')
}
