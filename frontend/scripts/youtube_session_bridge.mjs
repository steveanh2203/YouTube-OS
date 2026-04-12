import path from "node:path";
import { pathToFileURL } from "node:url";
import { Innertube, UniversalCache } from "youtubei.js";

function readStdin() {
  return new Promise((resolve, reject) => {
    let data = "";
    process.stdin.setEncoding("utf8");
    process.stdin.on("data", (chunk) => {
      data += chunk;
    });
    process.stdin.on("end", () => resolve(data));
    process.stdin.on("error", reject);
  });
}

function fail(message, detail = "") {
  process.stdout.write(JSON.stringify({
    ok: false,
    error: String(message || "Unknown error"),
    detail: String(detail || ""),
  }));
  process.exit(1);
}

function parseReplyCount(value) {
  const raw = String(value ?? "").trim();
  const digits = raw.replace(/[^\d]/g, "");
  return digits ? Number.parseInt(digits, 10) : 0;
}

function normalizeText(value) {
  return String(value ?? "").trim();
}

function describeError(error) {
  if (error instanceof Error && error.message) {
    return error.message.trim();
  }
  return String(error ?? "Unknown error").trim();
}

export function getVideoScanLimit(maxResults) {
  const base = Number(maxResults || 0);
  return Math.max(8, Math.min(base * 3, 30));
}

export function extractVideosFromRss(xml, limit = 10) {
  const source = String(xml || "");
  if (!source.trim()) return [];

  const entries = source.match(/<entry>[\s\S]*?<\/entry>/g) || [];
  const items = [];
  for (const entry of entries) {
    const videoId = entry.match(/<yt:videoId>([^<]+)<\/yt:videoId>/i)?.[1]?.trim() || "";
    const title = entry.match(/<title>([\s\S]*?)<\/title>/i)?.[1]?.trim() || "";
    if (!videoId) continue;
    items.push({ id: videoId, title });
    if (items.length >= limit) break;
  }
  return items;
}

export function isUcChannelId(value) {
  return /^UC[\w-]{6,}$/.test(normalizeText(value));
}

export function channelUrlFromTarget(value) {
  const target = normalizeText(value);
  if (!target) return "";
  if (target.startsWith("http://") || target.startsWith("https://")) return target;
  if (target.startsWith("@")) return `https://www.youtube.com/${target}`;
  return "";
}

async function createClient(session) {
  const sessionCookie = normalizeText(session?.session_cookie);
  if (!sessionCookie) {
    throw new Error("Session cookie is required.");
  }

  const accountIndex = Number.parseInt(normalizeText(session?.session_index) || "0", 10);
  return await Innertube.create({
    cookie: sessionCookie,
    account_index: Number.isFinite(accountIndex) ? accountIndex : 0,
    on_behalf_of_user: normalizeText(session?.delegated_session_id) || undefined,
    visitor_data: normalizeText(session?.visitor_data) || undefined,
    cache: new UniversalCache(false),
    retrieve_player: false,
  });
}

async function resolveChannelId(yt, channelTarget) {
  const target = normalizeText(channelTarget);
  if (!target) {
    throw new Error("Channel target is required.");
  }
  if (isUcChannelId(target)) {
    return target;
  }

  const url = channelUrlFromTarget(target);
  if (!url) {
    throw new Error(`Invalid channel target: ${target}`);
  }

  const endpoint = await yt.resolveURL(url);
  const browseId = normalizeText(endpoint?.payload?.browseId);
  if (!isUcChannelId(browseId)) {
    throw new Error(`Cannot resolve channel target: ${target}`);
  }
  return browseId;
}

async function collectFeedVideos(feed, bucket, seen, limit) {
  if (!feed) return;
  let currentFeed = feed;

  while (bucket.length < limit) {
    for (const video of currentFeed.videos || []) {
      const videoId = normalizeText(video?.id);
      if (!videoId || seen.has(videoId)) continue;
      seen.add(videoId);
      bucket.push({
        id: videoId,
        title: normalizeText(video?.title?.toString?.() || video?.title),
      });
      if (bucket.length >= limit) return;
    }

    if (!currentFeed.has_continuation) return;
    currentFeed = await currentFeed.getContinuation();
  }
}

async function getRecentVideosFromRss(channelId, limit) {
  const response = await fetch(`https://www.youtube.com/feeds/videos.xml?channel_id=${encodeURIComponent(channelId)}`);
  if (!response.ok) {
    throw new Error(`RSS fetch failed: HTTP ${response.status}`);
  }
  return extractVideosFromRss(await response.text(), limit);
}

async function getRecentVideos(yt, channelId, limit) {
  const resolvedChannelId = await resolveChannelId(yt, channelId);
  const channel = await yt.getChannel(resolvedChannelId);
  const items = [];
  const seen = new Set();

  if (channel.has_videos) {
    await collectFeedVideos(await channel.getVideos(), items, seen, limit);
  }

  if (items.length < limit && channel.has_shorts) {
    await collectFeedVideos(await channel.getShorts(), items, seen, limit);
  }

  if (items.length < limit && channel.has_live_streams) {
    await collectFeedVideos(await channel.getLiveStreams(), items, seen, limit);
  }

  if (items.length === 0 && isUcChannelId(resolvedChannelId)) {
    try {
      const rssItems = await getRecentVideosFromRss(resolvedChannelId, limit);
      for (const video of rssItems) {
        const videoId = normalizeText(video?.id);
        if (!videoId || seen.has(videoId)) continue;
        seen.add(videoId);
        items.push({
          id: videoId,
          title: normalizeText(video?.title),
        });
      }
    } catch (_error) {
      // Keep the channel-tab result if RSS is not available.
    }
  }

  return items.slice(0, limit);
}

async function loadVideoComments(yt, videoId) {
  try {
    const newest = await yt.getComments(videoId, "NEWEST_FIRST");
    if ((newest.contents || []).length > 0) {
      return newest;
    }
  } catch (_error) {
    // Fall through to TOP_COMMENTS below.
  }

  const top = await yt.getComments(videoId, "TOP_COMMENTS");
  if ((top.contents || []).length > 0) {
    return top;
  }

  try {
    return await top.applySort("NEWEST_FIRST");
  } catch (_error) {
    return top;
  }
}

function mapComment(video, thread) {
  const comment = thread?.comment;
  if (!comment?.comment_id || !comment?.content) return null;

  const replyCount = parseReplyCount(comment.reply_count);
  const isFromOwner = Boolean(comment.author_is_channel_owner);

  return {
    thread_id: normalizeText(thread.comment?.comment_id || thread.comment_id || comment.comment_id),
    comment_id: normalizeText(comment.comment_id),
    video_id: normalizeText(video?.id) || null,
    video_title: normalizeText(video?.title) || null,
    author_display_name: normalizeText(comment.author?.name) || "Unknown viewer",
    author_channel_id: normalizeText(comment.author?.id) || null,
    text: normalizeText(comment.content?.toString?.() || comment.content),
    published_at: normalizeText(comment.published_time) || null,
    updated_at: null,
    reply_count: replyCount,
    is_from_channel_owner: isFromOwner,
    can_auto_reply: Boolean(comment.comment_id && !isFromOwner && replyCount === 0),
  };
}

async function listInbox(input) {
  const yt = await createClient(input.session);
  const channelId = normalizeText(input.channel_id);
  const maxResults = Math.max(1, Math.min(Number(input.max_results || 20), 100));
  const videos = await getRecentVideos(yt, channelId, getVideoScanLimit(maxResults));
  const comments = [];
  const seenComments = new Set();
  const failures = [];

  if (videos.length === 0) {
    throw new Error("Khong tim thay video public nao cua kenh de quet comment. Video test co the dang unlisted, private, hoac chua len feed cong khai.");
  }

  for (const video of videos) {
    let page;
    try {
      page = await loadVideoComments(yt, video.id);
    } catch (error) {
      failures.push({
        video_id: normalizeText(video?.id),
        title: normalizeText(video?.title),
        message: describeError(error),
      });
      continue;
    }

    for (const thread of page.contents || []) {
      const item = mapComment(video, thread);
      if (!item || seenComments.has(item.comment_id)) continue;
      seenComments.add(item.comment_id);
      comments.push(item);
      if (comments.length >= maxResults) {
        return { comments, next_page_token: null };
      }
    }
  }

  if (comments.length === 0 && failures.length === videos.length) {
    const firstFailure = failures[0];
    throw new Error(
      `Khong doc duoc comment tu ${failures.length} video gan nhat. Loi dau tien: ${firstFailure?.message || "Unknown error"}`,
    );
  }

  return { comments, next_page_token: null };
}

async function replyToComment(input) {
  const yt = await createClient(input.session);
  const videoId = normalizeText(input.video_id);
  const commentId = normalizeText(input.comment_id);
  const replyText = normalizeText(input.reply_text);

  if (!videoId) {
    throw new Error("Video ID is required for session reply mode.");
  }
  if (!commentId) {
    throw new Error("Comment ID is required for session reply mode.");
  }
  if (!replyText) {
    throw new Error("Reply text is required.");
  }

  const comments = await yt.getComments(videoId, "NEWEST_FIRST", commentId);
  const thread = (comments.contents || []).find((item) => item?.comment?.comment_id === commentId);
  if (!thread?.comment) {
    throw new Error("Cannot find the target comment in current session.");
  }

  const response = await thread.comment.reply(replyText);
  return {
    reply_id: normalizeText(
      response?.data?.commentReplyDialogEndpoint?.dialog?.replyButton?.serviceEndpoint?.createCommentReplyEndpoint?.commentReplyId,
    ) || commentId,
  };
}

async function main() {
  try {
    const raw = await readStdin();
    const input = JSON.parse(raw || "{}");
    const action = normalizeText(input.action);

    if (action === "list_inbox") {
      process.stdout.write(JSON.stringify({
        ok: true,
        ...(await listInbox(input)),
      }));
      return;
    }

    if (action === "reply_comment") {
      process.stdout.write(JSON.stringify({
        ok: true,
        ...(await replyToComment(input)),
      }));
      return;
    }

    fail("Unsupported action.");
  } catch (error) {
    fail(error instanceof Error ? error.message : "Unknown helper error.");
  }
}

const isDirectRun = Boolean(process.argv[1]) && import.meta.url === pathToFileURL(path.resolve(process.argv[1])).href;

if (isDirectRun) {
  await main();
}
