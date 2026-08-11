import assert from 'node:assert/strict'
import test from 'node:test'
import { fileURLToPath } from 'node:url'
import { loadConfigFromFile } from 'vite'

async function loadOptionalModule(relativeUrl) {
  try {
    return await import(new URL(relativeUrl, import.meta.url))
  } catch {
    return null
  }
}

test('apiUrl keeps browser requests on the current origin', async () => {
  const api = await import(new URL('../src/lib/api.ts', import.meta.url))

  assert.equal(typeof api?.apiUrl, 'function')
  assert.equal(api.apiUrl('api/health'), '/api/health')
  assert.equal(api.apiUrl('/api/media/asset-1/content'), '/api/media/asset-1/content')
})

test('wsUrl derives a secure websocket URL from the browser location', async () => {
  const previousWindow = globalThis.window
  globalThis.window = { location: { protocol: 'https:', host: 'masteros.local' } }

  try {
    const api = await import(new URL('../src/lib/api.ts', import.meta.url))
    assert.equal(typeof api?.wsUrl, 'function')
    assert.equal(api.wsUrl('/api/jobs/ws'), 'wss://masteros.local/api/jobs/ws')
  } finally {
    globalThis.window = previousWindow
  }
})

test('cn resolves conditional classes and Tailwind conflicts', async () => {
  const utils = await import(new URL('../src/lib/utils.ts', import.meta.url))

  assert.equal(typeof utils?.cn, 'function')
  assert.equal(utils.cn('px-2', false && 'hidden', 'px-4'), 'px-4')
})

test('Vite proxies browser API and media requests to FastAPI', async () => {
  const loaded = await loadConfigFromFile(
    { command: 'serve', mode: 'development' },
    fileURLToPath(new URL('../vite.config.ts', import.meta.url)),
  )
  const config = loaded?.config

  assert.equal(config?.server?.proxy?.['/api']?.target, 'http://127.0.0.1:8765')
  assert.equal(config.server.proxy['/api'].ws, true)
  assert.equal(config.server.proxy['/media'].target, 'http://127.0.0.1:8765')
})

test('projectsApi discovers projects through the same-origin API', async () => {
  const api = await import(new URL('../src/lib/api.ts', import.meta.url))
  const previousFetch = globalThis.fetch
  let requestUrl = ''
  let requestInit
  globalThis.fetch = async (url, init) => {
    requestUrl = String(url)
    requestInit = init
    return new Response(JSON.stringify([{ id: 'draft-1', name: 'Draft 1' }]), {
      status: 200,
      headers: { 'content-type': 'application/json' },
    })
  }

  try {
    assert.equal(typeof api.projectsApi?.discover, 'function')
    const projects = await api.projectsApi.discover()
    assert.deepEqual(projects, [{ id: 'draft-1', name: 'Draft 1' }])
    assert.equal(requestUrl, '/api/projects/discover')
    assert.equal(requestInit, undefined)
  } finally {
    globalThis.fetch = previousFetch
  }
})

test('roxyApi sends workspace credentials as JSON', async () => {
  const api = await import(new URL('../src/lib/api.ts', import.meta.url))
  const previousFetch = globalThis.fetch
  let requestUrl = ''
  let requestInit
  globalThis.fetch = async (url, init) => {
    requestUrl = String(url)
    requestInit = init
    return new Response(JSON.stringify({ ok: true, workspaces: [], message: 'Found 0' }), {
      status: 200,
      headers: { 'content-type': 'application/json' },
    })
  }

  try {
    assert.equal(typeof api.roxyApi?.workspaces, 'function')
    assert.deepEqual(await api.roxyApi.workspaces({ api_host: 'http://roxy.test', api_token: 'secret' }), {
      ok: true,
      workspaces: [],
      message: 'Found 0',
    })
    assert.equal(requestUrl, '/api/roxy/workspaces')
    assert.equal(requestInit.method, 'POST')
    assert.equal(requestInit.headers['Content-Type'], 'application/json')
    assert.equal(requestInit.body, JSON.stringify({ api_host: 'http://roxy.test', api_token: 'secret' }))
  } finally {
    globalThis.fetch = previousFetch
  }
})

test('Roxy config normalizes hosts and round-trips browser storage', async () => {
  const previousStorage = globalThis.localStorage
  const values = new Map()
  globalThis.localStorage = {
    getItem: (key) => values.get(key) ?? null,
    setItem: (key, value) => values.set(key, String(value)),
    removeItem: (key) => values.delete(key),
  }

  try {
    const roxy = await loadOptionalModule('../src/lib/roxy.ts')
    assert.equal(typeof roxy?.normalizeRoxyHost, 'function')
    assert.equal(roxy.normalizeRoxyHost(' http://127.0.0.1:5005/// '), 'http://127.0.0.1:5005')
    roxy.saveRoxyConfig({ apiHost: 'http://roxy.test/', apiToken: 'token', workspaceId: 7, profileId: 'p1' })
    assert.deepEqual(roxy.loadRoxyConfig(), {
      apiHost: 'http://roxy.test',
      apiToken: 'token',
      workspaceId: 7,
      profileId: 'p1',
    })
  } finally {
    globalThis.localStorage = previousStorage
  }
})

test('YouTube reply config maps API snake_case to UI camelCase with safe defaults', async () => {
  const reply = await loadOptionalModule('../src/lib/youtubeReply.ts')
  assert.equal(typeof reply?.fromApiYouTubeConfig, 'function')
  const config = reply.fromApiYouTubeConfig({
    access_token: '',
    channel_id: 'UC123',
    channel_name: 'MasterOS',
    openrouter_key: '',
    model: '',
    system_prompt: '',
    channel_dna: 'Direct',
    audience_profile: 'Creators',
    target_market: 'US',
    auto_reply_mode: 'safe-only',
    provider_mode: 'api',
    connected: true,
    verified_at: '2026-08-11T00:00:00Z',
  })

  assert.equal(config.channelId, 'UC123')
  assert.equal(config.autoReplyMode, 'safe-only')
  assert.equal(config.model, 'openrouter/free')
  assert.match(reply.buildEffectiveSystemPrompt(config), /Channel DNA:\nDirect/)
})

test('audio service maps MiniMax voices from the same-origin FastAPI route', async () => {
  const audio = await loadOptionalModule('../src/lib/audioService.ts')
  const previousFetch = globalThis.fetch
  let requestUrl = ''
  globalThis.fetch = async (url) => {
    requestUrl = String(url)
    return new Response(JSON.stringify([
      { voice_id: 'voice-1', voice_name: 'Narrator', voice_type: 'voice_cloning', created_time: null },
    ]), { status: 200, headers: { 'content-type': 'application/json' } })
  }

  try {
    assert.equal(typeof audio?.listVoices, 'function')
    assert.deepEqual(await audio.listVoices('minimax'), [{
      voice_id: 'voice-1',
      name: 'Narrator',
      provider: 'minimax',
      voice_type: 'voice_cloning',
      source: 'my-voice',
    }])
    assert.equal(requestUrl, '/api/audio/minimax/voices')
  } finally {
    globalThis.fetch = previousFetch
  }
})

test('audio service sends MiniMax TTS settings to FastAPI and returns a playable URL', async () => {
  const audio = await loadOptionalModule('../src/lib/audioService.ts')
  const previousFetch = globalThis.fetch
  const previousCreateObjectURL = URL.createObjectURL
  let requestUrl = ''
  let requestInit
  globalThis.fetch = async (url, init) => {
    requestUrl = String(url)
    requestInit = init
    return new Response(new Uint8Array([1, 2, 3]), { status: 200, headers: { 'content-type': 'audio/mpeg' } })
  }
  URL.createObjectURL = () => 'blob:generated-audio'

  try {
    assert.equal(typeof audio?.generateTtsAudio, 'function')
    const result = await audio.generateTtsAudio('minimax', 'Hello', 'voice-1', 'speech-02-hd', {
      speed: 1.1,
      vol: 0.8,
      pitch: 2,
      emotion: 'happy',
    })
    assert.equal(result, 'blob:generated-audio')
    assert.equal(requestUrl, '/api/audio/minimax/tts')
    assert.equal(requestInit.method, 'POST')
    assert.deepEqual(JSON.parse(requestInit.body), {
      text: 'Hello',
      voice_id: 'voice-1',
      model: 'speech-02-hd',
      speed: 1.1,
      volume: 0.8,
      pitch: 2,
      emotion: 'happy',
    })
  } finally {
    globalThis.fetch = previousFetch
    URL.createObjectURL = previousCreateObjectURL
  }
})

test('mediaApi uploads browser files and keeps paths opaque', async () => {
  const media = await loadOptionalModule('../src/lib/media.ts')
  const previousFetch = globalThis.fetch
  let requestUrl = ''
  let requestInit
  globalThis.fetch = async (url, init) => {
    requestUrl = String(url)
    requestInit = init
    return new Response(JSON.stringify({
      id: 'asset-1',
      reference: 'media:asset-1',
      original_name: 'voice.mp3',
      kind: 'audio',
      content_type: 'audio/mpeg',
      size_bytes: 5,
      sha256: 'hash',
      created_at: '2026-08-11T00:00:00',
      content_url: '/api/media/assets/asset-1/content',
      download_url: '/api/media/assets/asset-1/download',
    }), { status: 201, headers: { 'content-type': 'application/json' } })
  }

  try {
    assert.equal(typeof media?.mediaApi?.upload, 'function')
    const file = new File(['audio'], 'voice.mp3', { type: 'audio/mpeg' })
    const asset = await media.mediaApi.upload(file)
    assert.equal(asset.reference, 'media:asset-1')
    assert.equal(requestUrl, '/api/media/assets')
    assert.equal(requestInit.method, 'POST')
    assert.equal(requestInit.body instanceof FormData, true)
    assert.equal(requestInit.body.get('file'), file)
  } finally {
    globalThis.fetch = previousFetch
  }
})
