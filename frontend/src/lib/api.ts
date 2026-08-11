function normalizePath(path: string): string {
  return path.startsWith('/') ? path : `/${path}`
}

export function apiUrl(path: string): string {
  return normalizePath(path)
}

export function wsUrl(path: string): string {
  const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:'
  return `${protocol}//${window.location.host}${normalizePath(path)}`
}

export function apiFetch(path: string, init?: RequestInit): Promise<Response> {
  return fetch(apiUrl(path), init)
}

async function requestJson<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await apiFetch(path, init)
  const contentType = response.headers.get('content-type') ?? ''
  const payload: unknown = contentType.includes('application/json')
    ? await response.json()
    : await response.text()

  if (!response.ok) {
    const detail = typeof payload === 'object' && payload !== null && 'detail' in payload
      ? (payload as { detail: unknown }).detail
      : payload
    const message = typeof detail === 'string' ? detail : `Request failed (${response.status})`
    throw new Error(message)
  }
  return payload as T
}

function postJson<TResponse, TBody extends object>(path: string, body: TBody): Promise<TResponse> {
  return requestJson<TResponse>(path, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  })
}

function putJson<TResponse, TBody extends object>(path: string, body: TBody): Promise<TResponse> {
  return requestJson<TResponse>(path, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  })
}

function patchJson<TResponse, TBody extends object>(path: string, body: TBody): Promise<TResponse> {
  return requestJson<TResponse>(path, {
    method: 'PATCH',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  })
}

function postEmpty<TResponse>(path: string): Promise<TResponse> {
  return requestJson<TResponse>(path, { method: 'POST' })
}

export interface RoxyWorkspaceInfo {
  workspace_id: number
  workspace_name: string
}

export interface RoxyProfileInfo {
  dir_id: string
  display_name: string
}

export interface RoxyConnectRequest {
  api_host: string
  api_token: string
}

export interface RoxyWorkspacesResponse {
  ok: boolean
  workspaces: RoxyWorkspaceInfo[]
  message: string
}

export const roxyApi = {
  workspaces: (
    hostOrBody: string | RoxyConnectRequest,
    apiToken?: string,
  ): Promise<RoxyWorkspacesResponse> => postJson(
    '/api/roxy/workspaces',
    typeof hostOrBody === 'string'
      ? { api_host: hostOrBody, api_token: apiToken ?? '' }
      : hostOrBody,
  ),
  profiles: (apiHost: string, apiToken: string, workspaceId: number): Promise<RoxyProfilesResponse> => postJson(
    '/api/roxy/profiles',
    { api_host: apiHost, api_token: apiToken, workspace_id: workspaceId },
  ),
  upload: (body: RoxyUploadRequest): Promise<RoxyUploadResponse> => postJson('/api/roxy/upload', body),
  folderVideos: (folderPath: string): Promise<RoxyFolderVideosResponse> => postJson(
    '/api/roxy/folder-videos',
    { folder_path: folderPath },
  ),
}

export interface RoxyProfilesResponse {
  ok: boolean
  profiles: RoxyProfileInfo[]
  message: string
}

export interface RoxyUploadRequest {
  api_host: string
  api_token: string
  workspace_id: number
  profile_id: string
  video_path: string
  schedule_at?: string | null
  close_after?: boolean
  close_tab_after?: boolean
  check_wait_seconds?: number | null
}

export interface RoxyUploadResponse {
  ok: boolean
  message: string
  debugger_address: string
  schedule_applied: boolean
  scheduled_at: string | null
}

export interface RoxyFolderVideosResponse {
  ok: boolean
  folder_path: string
  videos: string[]
  message: string
}

export interface SEOFileResult {
  file: string
  ok: boolean
  message: string
  renamed_to?: string | null
}

export interface SEOResponse {
  ok: boolean
  message: string
  results: SEOFileResult[]
}

export const seoApi = {
  apply: (body: {
    folder_path?: string
    video_path?: string
    title: string
    description: string
    keywords_raw: string
    rename_to_title: boolean
  }): Promise<SEOResponse> => postJson('/api/seo/apply', body),
}

export interface FastEditSystemStatus {
  ffmpeg_available: boolean
  ffprobe_available: boolean
  ready_to_render: boolean
  whisper_available: boolean
}

export interface FastEditRenderScene {
  index: number
  audio_path: string
  image_path: string
  subtitle_path: string | null
  output_path: string
  duration: number
  success: boolean
  error: string | null
}

export interface FastEditRenderRequest {
  audio_directory: string
  image_directory: string
  output_directory: string
  subtitle_directory?: string | null
  create_individual?: boolean
  create_combined?: boolean
  frame_rate?: number
  resolution_width?: number
  resolution_height?: number
  video_codec?: string
  video_bitrate?: string
  audio_bitrate?: string
  burn_subtitles?: boolean
  subtitle_style?: object | null
  animation?: object | null
  transition?: object | null
  use_hardware_acceleration?: boolean
  keep_intermediate?: boolean
  combined_filename?: string
  video_filters?: string[]
  audio_filters?: string[]
  sync_mode?: string
  background_music_directory?: string | null
  logo_file?: string | null
  logo_enabled?: boolean
  logo_size?: number
  logo_opacity?: number
  logo_x?: number
  logo_y?: number
}

export interface FastEditRenderResponse {
  ok: boolean
  message: string
  scenes: FastEditRenderScene[]
  combined: FastEditRenderScene | null
  total_duration: number
}

export interface FastEditRenameResult {
  original_path: string
  new_path: string
  success: boolean
  error: string | null
}

export interface FastEditBatchRenameResponse {
  ok: boolean
  message: string
  results: FastEditRenameResult[]
  total: number
  succeeded: number
  failed: number
}

export const fastEditApi = {
  status: (): Promise<FastEditSystemStatus> => requestJson('/api/fast-edit/status'),
  fileCount: (directory: string, assetType: string): Promise<{ total: number; error: string | null }> => (
    postJson('/api/fast-edit/file-count', { directory, asset_type: assetType })
  ),
  render: (body: FastEditRenderRequest): Promise<FastEditRenderResponse> => postJson('/api/fast-edit/render', body),
  rename: (body: {
    directory: string
    asset_type: string
    prefix?: string
    start_index?: number
    pad_width?: number
    separator?: string
    lowercase_extension?: boolean
  }): Promise<FastEditBatchRenameResponse> => postJson('/api/fast-edit/rename', body),
}

export interface CutAutonateScanResult {
  project_dir: string
  required: string[]
  present: string[]
  missing: string[]
  is_complete: boolean
}

export interface CutAutomateInitResult {
  project_dir: string
  created: string[]
}

export interface CutAutomateJobData {
  job_id: string
  step: string
  status: 'running' | 'done' | 'error' | 'cancelled'
  message: string
  result: { ok: boolean; message: string; data: unknown } | null
  stop_requested: boolean
  started_at: number
  finished_at: number | null
}

export interface CutAutomateTaskResponse<T = unknown> {
  ok: boolean
  message: string
  data: T | null
}

export type CutAutomateJobResponse = CutAutomateTaskResponse<CutAutomateJobData>

export const cutAutomateApi = {
  status: (): Promise<{ ffmpeg_available: boolean }> => requestJson('/api/cut-automate/status'),
  startJob: (step: string, payload: Record<string, unknown>): Promise<CutAutomateJobResponse> => (
    postJson('/api/cut-automate/jobs/start', { step, payload })
  ),
  getJob: (jobId: string): Promise<CutAutomateJobResponse> => requestJson(`/api/cut-automate/jobs/${jobId}`),
  stopJob: (jobId: string): Promise<CutAutomateJobResponse> => postEmpty(`/api/cut-automate/jobs/${jobId}/stop`),
  scanProject: (projectDir: string): Promise<CutAutomateTaskResponse<CutAutonateScanResult>> => (
    postJson('/api/cut-automate/scan-project', { project_dir: projectDir })
  ),
  initProject: (projectDir: string): Promise<CutAutomateTaskResponse<CutAutomateInitResult>> => (
    postJson('/api/cut-automate/init-project', { project_dir: projectDir, create_if_missing: true })
  ),
}

export const srtApi = {
  generate: (srtPath: string, contentText: string): Promise<{ ok: boolean; srt_output: string; message: string }> => (
    postJson('/api/srt/generate', { srt_path: srtPath, content_text: contentText })
  ),
}

export interface CommunityPostItem {
  id: number
  parent_project_id: number
  child_project_id: number | null
  body: string
  image_path: string | null
  status: string
  post_mode: string
  schedule_at: string | null
  queued_at: string | null
  posting_started_at: string | null
  published_at: string | null
  youtube_post_url: string | null
  youtube_post_id: string | null
  channel_url: string | null
  last_attempt_at: string | null
  attempt_count: number
  updated_at: string
  created_at: string
  last_error?: string | null
}

export interface CommunityLogEntry {
  ts: string
  level: string
  message: string
  meta: Record<string, unknown> | null
}

export interface CommunityMutationResponse {
  ok: boolean
  post: CommunityPostItem | null
  message: string
}

export const communityApi = {
  list: (params: { parent_project_id: number; status?: string; q?: string }): Promise<{ ok: boolean; posts: CommunityPostItem[]; message: string }> => {
    const query = new URLSearchParams({ parent_project_id: String(params.parent_project_id) })
    if (params.status) query.set('status', params.status)
    if (params.q) query.set('q', params.q)
    return requestJson(`/api/community/posts?${query}`)
  },
  due: (): Promise<{ ok: boolean; posts: CommunityPostItem[]; message: string }> => requestJson('/api/community/due'),
  create: (body: Record<string, unknown>): Promise<CommunityMutationResponse> => postJson('/api/community/posts', body),
  update: (id: number, body: Record<string, unknown>): Promise<CommunityMutationResponse> => (
    patchJson(`/api/community/posts/${id}`, body)
  ),
  queue: (id: number): Promise<CommunityMutationResponse> => postEmpty(`/api/community/posts/${id}/queue`),
  retry: (id: number): Promise<CommunityMutationResponse> => postEmpty(`/api/community/posts/${id}/retry`),
  publishNow: (id: number, body: Record<string, unknown>): Promise<CommunityMutationResponse> => (
    postJson(`/api/community/posts/${id}/publish-now`, body)
  ),
  logs: (id: number): Promise<{ ok: boolean; logs: CommunityLogEntry[]; message: string }> => requestJson(`/api/community/posts/${id}/logs`),
}

export interface ApiYouTubeConfig {
  access_token: string
  channel_id: string
  channel_name: string
  openrouter_key: string
  model: string
  system_prompt: string
  channel_dna: string
  audience_profile: string
  target_market: string
  auto_reply_mode: string
  provider_mode: string
  connected: boolean
  verified_at: string
}

export interface YouTubeConfigMapResponse {
  ok: boolean
  items: Record<string, ApiYouTubeConfig>
}

export interface YouTubeCommentReplyItem {
  reply_id: string
  author_display_name: string
  author_channel_id: string | null
  text: string
  published_at: string | null
  updated_at: string | null
  is_from_channel_owner: boolean
}

export interface YouTubeCommentItem {
  thread_id: string
  comment_id: string
  video_id: string | null
  video_title: string | null
  author_display_name: string
  author_channel_id: string | null
  text: string
  published_at: string | null
  updated_at: string | null
  reply_count: number
  is_from_channel_owner: boolean
  can_auto_reply: boolean
  replies: YouTubeCommentReplyItem[]
}

export interface YouTubeQuotaSnapshot {
  date_pt: string
  estimated_units_used: number
  daily_limit: number
  remaining_units: number
  usage_ratio: number
  updated_at: string
  breakdown: Record<string, number>
}

export const youtubeReplyApi = {
  configs: (): Promise<YouTubeConfigMapResponse> => requestJson('/api/youtube-reply/configs'),
  saveConfig: (parentId: string | number, config: ApiYouTubeConfig): Promise<{ ok: boolean; config: ApiYouTubeConfig; message: string }> => (
    putJson(`/api/youtube-reply/config/${parentId}`, config)
  ),
  aiSettings: (): Promise<{ ok: boolean; settings: ApiYouTubeAISettings; message: string }> => (
    requestJson('/api/youtube-reply/ai-settings')
  ),
  saveAiSettings: (settings: ApiYouTubeAISettings): Promise<{ ok: boolean; settings: ApiYouTubeAISettings; message: string }> => (
    putJson('/api/youtube-reply/ai-settings', settings)
  ),
  quota: (): Promise<{ ok: boolean; quota: YouTubeQuotaSnapshot; message: string }> => requestJson('/api/youtube-reply/quota'),
  inbox: (body: Record<string, unknown>): Promise<{ ok: boolean; comments: YouTubeCommentItem[]; next_page_token: string | null; message: string }> => (
    postJson('/api/youtube-reply/inbox', body)
  ),
  draft: (body: Record<string, unknown>): Promise<{ ok: boolean; reply_text: string; should_skip: boolean; message: string }> => (
    postJson('/api/youtube-reply/draft', body)
  ),
  verifyOpenRouter: (body: { api_key: string }): Promise<{ ok: boolean; message: string }> => (
    postJson('/api/youtube-reply/verify-openrouter', body)
  ),
  reply: (body: Record<string, unknown>): Promise<{ ok: boolean; reply_id: string; message: string }> => (
    postJson('/api/youtube-reply/reply', body)
  ),
}

export interface ApiYouTubeAISettings {
  openrouter_key: string
  primary_model: string
  fallback_models: string[]
  verified_at: string
}

export interface AccountConnectSettingsResponse {
  ok: boolean
  bridge_url: string
  bridge_key: string
  extension_name: string
  extension_connected: boolean
  extension_client_count: number
  oauth_client_id: string
  oauth_ready: boolean
  message: string
}

export interface OAuthSettingsResponse {
  ok: boolean
  ready: boolean
  settings: { client_id: string; client_secret: string; verified_at: string }
  message: string
}

export const accountConnectApi = {
  settings: (): Promise<AccountConnectSettingsResponse> => requestJson('/api/account-connect/settings'),
  regenerate: (): Promise<AccountConnectSettingsResponse> => postEmpty('/api/account-connect/settings/regenerate'),
  oauthSettings: (): Promise<OAuthSettingsResponse> => requestJson('/api/account-connect/oauth-settings'),
  verifyOauth: (body: { client_id: string; client_secret: string }): Promise<OAuthSettingsResponse> => (
    postJson('/api/account-connect/oauth-settings/verify', body)
  ),
}
