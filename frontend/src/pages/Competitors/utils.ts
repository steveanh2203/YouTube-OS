export function extractVideoId(url: string): string | null {
  try {
    const u = new URL(url)
    if (u.hostname.includes('youtu.be')) return u.pathname.slice(1)
    if (u.hostname.includes('youtube.com')) {
      if (u.pathname.includes('/shorts/')) return u.pathname.split('/shorts/')[1].split('/')[0]
      return u.searchParams.get('v')
    }
  } catch {
    return null
  }
  return null
}

export interface YoutubeOEmbedResult {
  title: string
  author_name: string
  author_url: string
  thumbnail_url: string
}

export async function fetchYoutubeOEmbed(url: string): Promise<YoutubeOEmbedResult | null> {
  const endpoint = `https://www.youtube.com/oembed?url=${encodeURIComponent(url)}&format=json`
  const res = await fetch(endpoint)
  if (!res.ok) return null
  return res.json()
}

export function getYoutubeThumbnailCandidates(videoId: string): string[] {
  return [
    `https://img.youtube.com/vi/${videoId}/maxresdefault.jpg`,
    `https://img.youtube.com/vi/${videoId}/hqdefault.jpg`,
    `https://img.youtube.com/vi/${videoId}/mqdefault.jpg`,
    `https://img.youtube.com/vi/${videoId}/default.jpg`,
  ]
}

function canUseThumbnail(url: string): Promise<boolean> {
  return new Promise((resolve) => {
    const img = new Image()
    img.onload = () => resolve(img.naturalWidth > 120 || url.includes('/default.jpg'))
    img.onerror = () => resolve(false)
    img.src = url
  })
}

export async function resolveYoutubeThumbnail(url: string): Promise<string> {
  const videoId = extractVideoId(url)
  if (!videoId) return ''

  for (const candidate of getYoutubeThumbnailCandidates(videoId)) {
    if (await canUseThumbnail(candidate)) return candidate
  }

  return ''
}

export async function resolveYoutubeMetadata(url: string): Promise<{
  title: string
  channelName: string
  thumbnailUrl: string
}> {
  const oembed = await fetchYoutubeOEmbed(url).catch(() => null)
  const fallbackThumbnail = await resolveYoutubeThumbnail(url)

  return {
    title: oembed?.title?.trim() ?? '',
    channelName: oembed?.author_name?.trim() ?? '',
    thumbnailUrl: oembed?.thumbnail_url?.trim() || fallbackThumbnail,
  }
}
