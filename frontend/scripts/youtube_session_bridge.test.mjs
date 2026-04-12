import test from "node:test";
import assert from "node:assert/strict";

import { channelUrlFromTarget, extractVideosFromRss, getVideoScanLimit, isUcChannelId } from "./youtube_session_bridge.mjs";

test("isUcChannelId accepts real channel ids", () => {
  assert.equal(isUcChannelId("UC8BWyj0cU8Jj_IzGsCJvUqA"), true);
  assert.equal(isUcChannelId("@JamesAnh-h9h"), false);
});

test("channelUrlFromTarget normalizes handles to YouTube urls", () => {
  assert.equal(channelUrlFromTarget("@JamesAnh-h9h"), "https://www.youtube.com/@JamesAnh-h9h");
  assert.equal(channelUrlFromTarget("https://www.youtube.com/@JamesAnh-h9h"), "https://www.youtube.com/@JamesAnh-h9h");
  assert.equal(channelUrlFromTarget("UC8BWyj0cU8Jj_IzGsCJvUqA"), "");
});

test("getVideoScanLimit scans enough videos without going wild", () => {
  assert.equal(getVideoScanLimit(1), 8);
  assert.equal(getVideoScanLimit(5), 15);
  assert.equal(getVideoScanLimit(20), 30);
});

test("extractVideosFromRss parses upload entries", () => {
  const items = extractVideosFromRss(`
    <feed>
      <entry><yt:videoId>abc123</yt:videoId><title>Video A</title></entry>
      <entry><yt:videoId>def456</yt:videoId><title>Video B</title></entry>
    </feed>
  `, 10);

  assert.deepEqual(items, [
    { id: "abc123", title: "Video A" },
    { id: "def456", title: "Video B" },
  ]);
});
