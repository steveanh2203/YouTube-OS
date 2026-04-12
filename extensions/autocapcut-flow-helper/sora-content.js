(() => {
  if (window.__masterosSoraWorkerLoaded__) {
    return;
  }

  window.__masterosSoraWorkerLoaded__ = true;

  const API_BASE = "http://127.0.0.1:8765/api/sora";
  const STORAGE = {
    key: "masterosSoraBridgeKey",
    verified: "masterosSoraBridgeVerified",
    status: "masterosSoraWorkerStatus",
    detail: "masterosSoraWorkerDetail",
    lastSeenAt: "masterosSoraLastSeenAt",
    lastError: "masterosSoraLastError",
    currentJob: "masterosSoraCurrentJob",
    logs: "masterosSoraLogs",
    workerId: "masterosSoraWorkerId"
  };
  const MAX_LOGS = 60;
  const HEARTBEAT_MS = 8000;
  const JOB_WAIT_MS = 10 * 60 * 1000;
  const CREATE_TIMEOUT_MS = 30 * 1000;
  const POST_TIMEOUT_MS = 30 * 1000;
  const NO_WATERMARK_WAIT_MS = 60 * 1000;
  const NO_WATERMARK_POLL_MS = 2500;
  const AUTH_HEADER_NAMES = [
    "authorization",
    "oai-device-id",
    "oai-language",
    "openai-sentinel-token"
  ];
  const RATIO_LABEL_MAP = {
    "16:9": ["16:9", "landscape", "widescreen", "horizontal"],
    "9:16": ["9:16", "portrait", "vertical"],
    "1:1": ["1:1", "square"]
  };
  const DURATION_LABEL_MAP = {
    5: ["5s", "5 s", "5sec", "5 sec", "5"],
    10: ["10s", "10 s", "10sec", "10 sec", "10"],
    20: ["20s", "20 s", "20sec", "20 sec", "20"]
  };

  let runningJobs = 0;
  let submitLock = false;
  const MAX_CONCURRENT_JOBS = 3;
  let tickTimer = null;
  let bridgeReadyPromise = null;
  const bridgeSubscribers = new Set();
  const capturedAuthHeaders = new Map();

  function sleep(ms) {
    return new Promise((resolve) => setTimeout(resolve, ms));
  }

  function textOf(el) {
    return (el?.textContent || el?.innerText || "").replace(/\s+/g, " ").trim();
  }

  function normalizeWhitespace(value) {
    return String(value || "").replace(/\s+/g, " ").trim();
  }

  function isVisible(el) {
    if (!(el instanceof HTMLElement)) return false;
    const style = window.getComputedStyle(el);
    if (style.display === "none" || style.visibility === "hidden" || style.opacity === "0") {
      return false;
    }
    const rect = el.getBoundingClientRect();
    return rect.width > 0 && rect.height > 0;
  }

  function isDisabled(el) {
    return el?.matches?.(":disabled, [aria-disabled='true'], [data-disabled='true']") || false;
  }

  async function storageGet(keys) {
    return chrome.storage.local.get(keys);
  }

  async function storageSet(values) {
    return chrome.storage.local.set(values);
  }

  async function appendLog(message, meta = null) {
    const stored = await storageGet([STORAGE.logs]);
    const next = Array.isArray(stored[STORAGE.logs]) ? stored[STORAGE.logs] : [];
    next.push({
      ts: Date.now(),
      message: String(message || "").trim(),
      meta
    });
    await storageSet({
      [STORAGE.logs]: next.slice(-MAX_LOGS)
    });
  }

  async function ensureWorkerId() {
    const stored = await storageGet([STORAGE.workerId]);
    const existing = String(stored[STORAGE.workerId] || "").trim();
    if (existing) return existing;
    const created = `sora-${Math.random().toString(36).slice(2, 10)}`;
    await storageSet({ [STORAGE.workerId]: created });
    return created;
  }

  async function setWorkerState(status, detail = "", extra = {}) {
    await storageSet({
      [STORAGE.status]: status,
      [STORAGE.detail]: detail,
      [STORAGE.lastSeenAt]: Date.now(),
      ...extra
    });
  }

  async function getBridgeConfig() {
    const stored = await storageGet([
      STORAGE.key,
      STORAGE.verified,
      STORAGE.workerId
    ]);
    const apiKey = String(stored[STORAGE.key] || "").trim();
    const verified = stored[STORAGE.verified] === true;
    const workerId = String(stored[STORAGE.workerId] || "").trim() || await ensureWorkerId();
    return { apiKey, verified, workerId };
  }

  async function apiFetch(path, options = {}) {
    const config = await getBridgeConfig();
    const headers = new Headers(options.headers || {});
    headers.set("X-API-Key", config.apiKey);
    headers.set("X-Worker-Id", config.workerId);
    return fetch(`${API_BASE}${path}`, {
      ...options,
      headers
    });
  }

  async function sendHeartbeat() {
    const res = await apiFetch("/heartbeat", { method: "POST" });
    if (!res.ok) {
      throw new Error(`Heartbeat failed (${res.status})`);
    }
    const data = await res.json();
    await setWorkerState("connected", `${data.worker_count || 1} worker connected`);
  }

  async function fetchNextJob() {
    const res = await apiFetch("/next-job");
    if (!res.ok) {
      throw new Error(`Job poll failed (${res.status})`);
    }
    const data = await res.json();
    return data.job || null;
  }

  async function reportProgress(jobId, progress) {
    const normalized = Math.max(0, Math.min(100, Number(progress) || 0));
    await apiFetch("/progress", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ job_id: jobId, progress: normalized })
    });
  }

  async function reportFailure(jobId, error) {
    await apiFetch("/job-done", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        job_id: jobId,
        error: String(error || "Unknown Sora extension failure.")
      })
    });
  }

  async function reportDone(jobId, payload) {
    const res = await apiFetch("/job-done", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        job_id: jobId,
        no_watermark_url: payload.noWatermarkUrl || null,
        downloadable_url: payload.downloadableUrl || null,
        generation_id: payload.generationId || null,
        public_permalink: payload.publicPermalink || null
      })
    });
    if (!res.ok) {
      const data = await res.json().catch(() => ({}));
      throw new Error(data.detail || `Job completion failed (${res.status})`);
    }
    return res.json();
  }

  async function uploadVideo(jobId, blob, generationId, permalink) {
    const form = new FormData();
    form.append("job_id", String(jobId));
    if (generationId) form.append("generation_id", generationId);
    form.append("public_permalink", permalink || location.href);
    form.append("file", new File([blob], `sora-job-${jobId}.mp4`, { type: blob.type || "video/mp4" }));
    const res = await apiFetch("/job-upload", {
      method: "POST",
      body: form
    });
    if (!res.ok) {
      const data = await res.json().catch(() => ({}));
      throw new Error(data.detail || `Upload failed (${res.status})`);
    }
    return res.json();
  }

  function findPromptField() {
    const candidates = [
      ...document.querySelectorAll("textarea"),
      ...document.querySelectorAll("[contenteditable='true']"),
      ...document.querySelectorAll("div[role='textbox']")
    ];

    const scored = candidates
      .filter(isVisible)
      .filter((node) => !isDisabled(node))
      .map((element) => ({ element, score: scorePromptField(element) }))
      .filter((item) => item.score > 0)
      .sort((left, right) => right.score - left.score);

    return scored[0]?.element || null;
  }

  function scorePromptField(element) {
    let score = 1;
    const descriptor = [
      element.getAttribute?.("placeholder"),
      element.getAttribute?.("aria-label"),
      element.getAttribute?.("data-placeholder"),
      textOf(element.closest?.("label") || null),
      textOf(element)
    ].join(" ").toLowerCase();

    if (element.matches?.("textarea")) score += 12;
    if (element.matches?.("[contenteditable='true'], [role='textbox']")) score += 8;
    if (/prompt|describe|video|story|scene|create/i.test(descriptor)) score += 20;
    if (element.closest?.("form")) score += 4;
    if (element.clientHeight >= 80) score += 4;
    return score;
  }

  function findButtons() {
    return [...document.querySelectorAll("button,[role='button'],[role='radio'],[role='option']")]
      .filter(isVisible)
      .filter((button) => !isDisabled(button));
  }

  function findGenerateButton() {
    return findButtons()
      .map((element) => ({ element, score: scoreGenerateButton(element) }))
      .filter((item) => item.score > 0)
      .sort((left, right) => right.score - left.score)[0]?.element || null;
  }

  function scoreGenerateButton(element) {
    const text = normalizeWhitespace([
      element.getAttribute?.("aria-label"),
      element.getAttribute?.("title"),
      element.getAttribute?.("name"),
      element.getAttribute?.("data-testid"),
      textOf(element.querySelector?.(".sr-only") || null),
      textOf(element)
    ].filter(Boolean).join(" ")).toLowerCase();

    if (!text) return 0;

    let score = 0;
    if (/create video/.test(text)) score += 30;
    if (/generate|create|render|make/.test(text)) score += 16;
    if (/video|sora/.test(text)) score += 8;
    if (element.tagName === "BUTTON") score += 3;
    return score;
  }

  function setPromptValue(node, value) {
    if (!node) return false;
    node.scrollIntoView({ block: "center", inline: "center" });
    node.focus();

    if (node instanceof HTMLTextAreaElement || node instanceof HTMLInputElement) {
      const prototype = node.tagName === "TEXTAREA"
        ? window.HTMLTextAreaElement.prototype
        : window.HTMLInputElement.prototype;
      const descriptor = Object.getOwnPropertyDescriptor(prototype, "value");
      descriptor?.set?.call(node, value);
      node.dispatchEvent(new Event("input", { bubbles: true }));
      node.dispatchEvent(new Event("change", { bubbles: true }));
      return true;
    }

    if (node instanceof HTMLElement && (node.isContentEditable || node.getAttribute("contenteditable") === "true")) {
      node.textContent = value;
      placeCaretAtEnd(node);
      node.dispatchEvent(new InputEvent("input", { bubbles: true, data: value, inputType: "insertText" }));
      node.dispatchEvent(new Event("change", { bubbles: true }));
      return true;
    }

    return false;
  }

  function clickElement(el) {
    if (!(el instanceof HTMLElement)) return false;
    el.scrollIntoView({ block: "center", inline: "center" });
    el.focus();
    for (const type of ["pointerdown", "mousedown", "pointerup", "mouseup", "click"]) {
      el.dispatchEvent(new MouseEvent(type, { bubbles: true, cancelable: true, view: window }));
    }
    return true;
  }

  function placeCaretAtEnd(element) {
    const selection = window.getSelection();
    if (!selection) return;
    const range = document.createRange();
    range.selectNodeContents(element);
    range.collapse(false);
    selection.removeAllRanges();
    selection.addRange(range);
  }

  async function applyAspectRatio(ratio) {
    const targets = RATIO_LABEL_MAP[ratio] || [ratio];
    for (const label of targets) {
      const direct = document.querySelector(
        `button[aria-label*="${label}" i], button[data-ratio="${label}"], [role="radio"][aria-label*="${label}" i], [data-value="${label}"]`
      );
      if (direct instanceof HTMLElement && !isDisabled(direct) && isVisible(direct)) {
        clickElement(direct);
        return true;
      }
    }

    const matched = findButtons().find((button) => {
      const text = textOf(button).toLowerCase();
      return targets.some((target) => text.includes(String(target).toLowerCase()));
    });
    if (matched) {
      clickElement(matched);
      return true;
    }
    return false;
  }

  async function applyDuration(duration) {
    const targets = DURATION_LABEL_MAP[duration] || [`${duration}s`];
    for (const label of targets) {
      const direct = document.querySelector(
        `button[aria-label*="${label}" i], button[data-duration="${duration}"], [role="radio"][aria-label*="${label}" i], [data-value="${duration}"]`
      );
      if (direct instanceof HTMLElement && !isDisabled(direct) && isVisible(direct)) {
        clickElement(direct);
        return true;
      }
    }

    const matched = findButtons().find((button) => {
      const text = textOf(button).toLowerCase();
      return targets.some((target) => text === String(target).toLowerCase() || text.startsWith(String(target).toLowerCase()));
    });
    if (matched) {
      clickElement(matched);
      return true;
    }
    return false;
  }

  function handleBridgeMessage(event) {
    if (event.source !== window || event.origin !== window.location.origin) {
      return;
    }
    if (event.data?.source !== "SORA_AUTOMATION_BRIDGE") {
      return;
    }

    rememberAuthHeaders(event.data.payload);
    for (const subscriber of bridgeSubscribers) {
      subscriber(event.data.payload);
    }
  }

  function subscribeToBridge(callback) {
    bridgeSubscribers.add(callback);
    return () => bridgeSubscribers.delete(callback);
  }

  async function ensurePageBridgeInjected() {
    if (bridgeReadyPromise) {
      return bridgeReadyPromise;
    }

    bridgeReadyPromise = new Promise((resolve, reject) => {
      if (document.getElementById("masteros-sora-page-bridge")) {
        resolve();
        return;
      }

      const script = document.createElement("script");
      script.id = "masteros-sora-page-bridge";
      script.src = chrome.runtime.getURL("pageBridge.js");
      script.async = false;
      script.onload = () => {
        script.remove();
        resolve();
      };
      script.onerror = () => reject(new Error("Could not inject Sora page bridge."));
      (document.head || document.documentElement).appendChild(script);
    }).catch((error) => {
      bridgeReadyPromise = null;
      throw error;
    });

    return bridgeReadyPromise;
  }

  function waitForBridgePayload(predicate, timeoutMs, timeoutMessage) {
    return new Promise((resolve, reject) => {
      const unsubscribe = subscribeToBridge((payload) => {
        const result = predicate(payload);
        if (!result) return;
        unsubscribe();
        clearTimeout(timeoutId);
        resolve(result);
      });

      const timeoutId = window.setTimeout(() => {
        unsubscribe();
        reject(new Error(timeoutMessage));
      }, timeoutMs);
    });
  }

  function waitForCreateTask() {
    return waitForBridgePayload((payload) => {
      if (!isCreatePayload(payload)) return null;
      const taskId = payload.responseBody?.id;
      if (!taskId) return null;
      return {
        taskId,
        taskType: payload.responseBody?.task_type || null
      };
    }, CREATE_TIMEOUT_MS, "Create request was not observed.");
  }

  function waitForTaskCompletion(taskId, jobId) {
    return new Promise((resolve, reject) => {
      let lastProgress = null;
      let lastStatus = null;

      const unsubscribe = subscribeToBridge((payload) => {
        const draft = extractDraftFromPayload(payload, taskId);
        if (draft) {
          unsubscribe();
          clearTimeout(timeoutId);
          resolve(draft);
          return;
        }

        const pendingTask = extractPendingTaskFromPayload(payload, taskId);
        if (!pendingTask) {
          return;
        }

        const status = pendingTask.status || "unknown";
        if (status !== lastStatus) {
          lastStatus = status;
          void appendLog(`Task ${taskId} is ${status}`, { taskId, status });
        }

        if (typeof pendingTask.progress_pct === "number") {
          const normalized = Math.max(0, Math.min(100, Math.round(pendingTask.progress_pct * 100)));
          if (normalized !== lastProgress) {
            lastProgress = normalized;
            void reportProgress(jobId, 45 + Math.round(normalized * 0.35));
          }
        }

        if (status === "failed" || status === "cancelled") {
          const failureReason = String(pendingTask.failure_reason || `Task ${taskId} failed.`);
          if (/unable[_\s-]*to[_\s-]*generate/i.test(failureReason)) {
            void appendLog("Unable to generate detected", {
              taskId,
              status,
              failureReason
            });
          }
          unsubscribe();
          clearTimeout(timeoutId);
          reject(new Error(
            /unable[_\s-]*to[_\s-]*generate/i.test(failureReason)
              ? "Unable to generate. Sora could not create this video for the current prompt."
              : failureReason
          ));
        }
      });

      const timeoutId = window.setTimeout(() => {
        unsubscribe();
        reject(new Error(`Task ${taskId} did not resolve before timeout.`));
      }, JOB_WAIT_MS);
    });
  }

  async function publishGeneration(generationId, prompt) {
    const postPromise = waitForPublicPost(generationId);
    const publishHeaders = buildPublicPostHeaders();
    let response = await requestBridgeFetch("/backend/project_y/post", {
      method: "POST",
      credentials: "same-origin",
      headers: publishHeaders,
      body: JSON.stringify({
        attachments_to_create: [
          {
            generation_id: generationId,
            kind: "sora"
          }
        ],
        post_text: prompt
      })
    }, POST_TIMEOUT_MS);

    if (!response.ok) {
      await appendLog("Publish request returned non-OK, retrying cookie-only", {
        generationId,
        status: response.status || null
      });
      response = await requestBridgeFetch("/backend/project_y/post", {
        method: "POST",
        credentials: "same-origin",
        headers: {
          accept: "*/*",
          "content-type": "application/json"
        },
        body: JSON.stringify({
          attachments_to_create: [
            {
              generation_id: generationId,
              kind: "sora"
            }
          ],
          post_text: prompt
        })
      }, POST_TIMEOUT_MS);
    }

    if (!response.ok) {
      throw new Error(`Public post request failed with status ${response.status || "unknown"}.`);
    }

    return postPromise;
  }

  function waitForPublicPost(generationId) {
    return waitForBridgePayload((payload) => {
      if (
        payload?.type !== "fetch" ||
        payload.method !== "POST" ||
        payload.status !== 200 ||
        !payload.url?.includes("/backend/project_y/post")
      ) {
        return null;
      }

      const attachment = payload.requestBody?.attachments_to_create?.[0];
      const responseGenerationId = payload.responseBody?.post?.attachments?.[0]?.generation_id;
      if (attachment?.generation_id !== generationId && responseGenerationId !== generationId) {
        return null;
      }

      const publicPermalink = payload.responseBody?.post?.permalink || null;
      const publicPostId = payload.responseBody?.post?.id || null;
      if (!publicPermalink) {
        return null;
      }

      return { publicPermalink, publicPostId };
    }, POST_TIMEOUT_MS, `Public post creation for ${generationId} was not observed.`);
  }

  function requestBridgeFetch(url, init, timeoutMs) {
    return new Promise((resolve, reject) => {
      const requestId = globalThis.crypto?.randomUUID?.() || `bridge-${Date.now()}`;
      const unsubscribe = subscribeToBridge((payload) => {
        if (payload?.requestId !== requestId || payload?.command !== "run-fetch") {
          return;
        }

        unsubscribe();
        clearTimeout(timeoutId);

        if (payload.type === "command-error") {
          reject(new Error(payload.error || "Bridge fetch failed."));
          return;
        }

        resolve({
          ok: payload.ok,
          status: payload.status,
          url: payload.url,
          responseBody: payload.responseBody || null
        });
      });

      const timeoutId = window.setTimeout(() => {
        unsubscribe();
        reject(new Error("Bridge fetch timed out."));
      }, timeoutMs);

      window.postMessage({
        source: "SORA_AUTOMATION_COMMAND",
        payload: {
          type: "run-fetch",
          requestId,
          url,
          init
        }
      }, window.location.origin);
    });
  }

  function rememberAuthHeaders(payload) {
    if (!isRelevantAuthPayload(payload)) {
      return;
    }

    const headers = payload.requestHeaders;
    if (!headers || typeof headers !== "object") {
      return;
    }

    for (const headerName of AUTH_HEADER_NAMES) {
      const value = headers[headerName];
      if (typeof value === "string" && value.trim()) {
        capturedAuthHeaders.set(headerName, value);
      }
    }
  }

  function isRelevantAuthPayload(payload) {
    if (payload?.type !== "fetch") {
      return false;
    }

    const url = payload.url || "";
    if (!url.startsWith(window.location.origin)) {
      return false;
    }

    return (
      url.includes("/backend/nf/create") ||
      url.includes("/backend/nf/pending/v2") ||
      url.includes("/backend/nf/check") ||
      url.includes("/backend/project_y/profile/drafts/v2") ||
      url.includes("/backend/project_y/post")
    );
  }

  function buildSoraAuthHeaders(extraHeaders = {}, options = {}) {
    const headers = {
      accept: "*/*",
      ...extraHeaders
    };
    for (const headerName of AUTH_HEADER_NAMES) {
      const value = capturedAuthHeaders.get(headerName);
      if (typeof value === "string" && value.trim()) {
        headers[headerName] = value;
      }
    }

    const requireCaptured = options.requireCaptured === true;

    if (requireCaptured && !headers.authorization) {
      throw new Error("Could not capture Sora authorization headers for publish.");
    }
    if (requireCaptured && !headers["oai-device-id"]) {
      throw new Error("Could not capture Sora device headers for publish.");
    }

    return headers;
  }

  function buildPublicPostHeaders() {
    return buildSoraAuthHeaders({
      "content-type": "application/json"
    });
  }

  async function fetchLatestDraft(taskId) {
    let response = await requestBridgeFetch("/backend/project_y/profile/drafts/v2", {
      method: "GET",
      credentials: "same-origin",
      headers: buildSoraAuthHeaders()
    }, POST_TIMEOUT_MS);

    if (!response.ok) {
      response = await requestBridgeFetch("/backend/project_y/profile/drafts/v2", {
        method: "GET",
        credentials: "same-origin",
        headers: {
          accept: "*/*"
        }
      }, POST_TIMEOUT_MS);
    }

    return extractDraftFromPayload({
      type: "fetch",
      status: response.status,
      url: response.url,
      responseBody: response.responseBody
    }, taskId);
  }

  async function waitForNoWatermarkDraft(taskId, jobId) {
    let latestDraft = null;
    const deadline = Date.now() + NO_WATERMARK_WAIT_MS;

    while (Date.now() < deadline) {
      try {
        const draft = await fetchLatestDraft(taskId);
        if (draft) {
          latestDraft = draft;
          if (draft.noWatermarkUrl) {
            await appendLog("No-watermark draft resolved", {
              id: jobId,
              taskId,
              generationId: draft.generationId
            });
            await reportProgress(jobId, 70);
            return draft;
          }
        }
      } catch (error) {
        await appendLog("No-watermark draft refresh failed", {
          id: jobId,
          taskId,
          reason: error instanceof Error ? error.message : String(error || "draft_refresh_failed")
        });
      }

      await sleep(NO_WATERMARK_POLL_MS);
    }

    return latestDraft;
  }

  function isCreatePayload(payload) {
    return (
      payload?.type === "fetch" &&
      payload.method === "POST" &&
      payload.status === 200 &&
      payload.url?.includes("/backend/nf/create")
    );
  }

  function extractPendingTaskFromPayload(payload, taskId) {
    if (
      payload?.type !== "fetch" ||
      payload.status !== 200 ||
      !payload.url?.includes("/backend/nf/pending/v2") ||
      !Array.isArray(payload.responseBody)
    ) {
      return null;
    }

    return payload.responseBody.find((task) => task?.id === taskId) || null;
  }

  function extractDraftFromPayload(payload, taskId) {
    if (
      payload?.type !== "fetch" ||
      payload.status !== 200 ||
      !payload.url?.includes("/backend/project_y/profile/drafts/v2") ||
      !payload.responseBody?.items
    ) {
      return null;
    }

    const drafts = flattenDraftItems(payload.responseBody.items);
    const matchingDraft = drafts.find((draft) => draft?.task_id === taskId);
    if (!matchingDraft) {
      return null;
    }

    return {
      generationId: matchingDraft.generation_id || matchingDraft.id,
      href: matchingDraft.generation_id ? `/d/${matchingDraft.generation_id}` : null,
      previewUrl: matchingDraft.url || matchingDraft.encodings?.md?.path || null,
      downloadableUrl:
        matchingDraft.download_urls?.no_watermark ||
        matchingDraft.downloadable_url ||
        matchingDraft.download_urls?.watermark ||
        null,
      noWatermarkUrl: matchingDraft.download_urls?.no_watermark || null,
      prompt: matchingDraft.prompt || matchingDraft.creation_config?.prompt || null
    };
  }

  function flattenDraftItems(items) {
    const flattened = [];
    for (const item of items) {
      if (!item || typeof item !== "object") {
        continue;
      }
      if (item.task_id) {
        flattened.push(item);
      }
      if (Array.isArray(item.drafts)) {
        for (const nestedDraft of item.drafts) {
          if (nestedDraft?.task_id) {
            flattened.push(nestedDraft);
          }
        }
      }
    }
    return flattened;
  }

  async function fetchNoWatermarkDirect(postId, permalink) {
    // Derive ID from postId, or extract from permalink /p/{id} as fallback
    const id = postId || permalink?.match(/\/p\/([a-zA-Z0-9_-]+)/)?.[1];
    if (!id) return null;
    try {
      const response = await requestBridgeFetch(
        `/backend/project_y/post/${id}`,
        { method: "GET", credentials: "same-origin", headers: buildSoraAuthHeaders() },
        POST_TIMEOUT_MS
      );
      if (!response.ok) {
        await appendLog("Direct no-watermark fetch failed", { status: response.status, postId: id });
        return null;
      }
      return response.responseBody?.post?.attachments?.[0]?.encodings?.source?.path || null;
    } catch (error) {
      await appendLog("Direct no-watermark fetch error", {
        reason: error instanceof Error ? error.message : String(error || "unknown")
      });
      return null;
    }
  }

  async function fetchVideoBlob(src) {
    const response = await fetch(src);
    if (!response.ok) {
      throw new Error(`Could not read generated video (${response.status}).`);
    }
    return response.blob();
  }

  // Phase 1: inject prompt + click Generate + wait for taskId (serialized)
  async function submitPhase(job) {
    const prompt = String(job.prompt || "").trim();
    if (!prompt) throw new Error("Prompt is empty.");

    await ensurePageBridgeInjected();
    await appendLog("Running Sora job", { id: job.id });
    await setWorkerState("running", `Running job #${job.index}`, {
      [STORAGE.currentJob]: `#${job.index}`
    });

    const input = findPromptField();
    if (!input) throw new Error("Could not find the Sora prompt input.");

    await reportProgress(job.id, 10);
    if (!setPromptValue(input, prompt)) throw new Error("Could not populate the prompt field.");

    if (job.ratio) { await applyAspectRatio(job.ratio); await sleep(250); }
    if (job.duration) { await applyDuration(job.duration); await sleep(250); }

    const generateButton = findGenerateButton();
    if (!generateButton) throw new Error("Could not find the Generate button on the Sora page.");

    const createTaskPromise = waitForCreateTask();
    clickElement(generateButton);
    await reportProgress(job.id, 25);

    const createTask = await createTaskPromise;
    await appendLog("Sora task created", { id: job.id, taskId: createTask.taskId });
    await reportProgress(job.id, 40);
    return createTask;
  }

  // Phase 2: wait for completion + publish + download (runs in parallel across jobs)
  async function completionPhase(job, createTask) {
    const prompt = String(job.prompt || "").trim();

    const generatedEntry = await waitForTaskCompletion(createTask.taskId, job.id);
    await appendLog("Sora task finished", {
      id: job.id,
      taskId: createTask.taskId,
      generationId: generatedEntry.generationId
    });
    await reportProgress(job.id, 48);

    let publicPermalink = null;
    let publicPostId = null;
    const capturedHeaderCount = capturedAuthHeaders.size;
    if (capturedHeaderCount === 0) {
      await appendLog("Warning: no auth headers captured before publish — may fail", { id: job.id });
    }
    const MAX_PUBLISH_ATTEMPTS = 3;
    for (let attempt = 1; attempt <= MAX_PUBLISH_ATTEMPTS; attempt++) {
      try {
        const publicPost = await publishGeneration(generatedEntry.generationId, prompt);
        publicPermalink = publicPost.publicPermalink;
        publicPostId = publicPost.publicPostId;
        await appendLog("Public post created", {
          id: job.id,
          generationId: generatedEntry.generationId,
          publicPermalink,
          attempt
        });
        await reportProgress(job.id, 58);
        break;
      } catch (error) {
        const reason = error instanceof Error ? error.message : String(error || "publish_failed");
        if (attempt < MAX_PUBLISH_ATTEMPTS) {
          await appendLog("Publish attempt failed, retrying", { id: job.id, attempt, reason });
          await new Promise((resolve) => setTimeout(resolve, 4000));
        } else {
          await appendLog("Public post skipped after retries", { id: job.id, attempts: MAX_PUBLISH_ATTEMPTS, reason });
        }
      }
    }

    let resolvedEntry = generatedEntry;
    if (!resolvedEntry.noWatermarkUrl) {
      const refreshedEntry = await waitForNoWatermarkDraft(createTask.taskId, job.id);
      if (refreshedEntry) {
        resolvedEntry = {
          ...resolvedEntry,
          ...refreshedEntry,
          downloadableUrl: refreshedEntry.downloadableUrl || resolvedEntry.downloadableUrl,
          noWatermarkUrl: refreshedEntry.noWatermarkUrl || resolvedEntry.noWatermarkUrl,
          previewUrl: refreshedEntry.previewUrl || resolvedEntry.previewUrl,
          href: refreshedEntry.href || resolvedEntry.href
        };
      }
    }

    const noWatermarkUrl = resolvedEntry.noWatermarkUrl || await fetchNoWatermarkDirect(publicPostId, publicPermalink);
    const downloadableUrl = noWatermarkUrl || resolvedEntry.downloadableUrl || null;
    if (noWatermarkUrl) {
      await appendLog("Remove watermark complete", { id: job.id, generationId: resolvedEntry.generationId });
      await reportProgress(job.id, 70);
    }

    if (noWatermarkUrl || downloadableUrl) {
      try {
        await reportDone(job.id, {
          noWatermarkUrl,
          downloadableUrl,
          generationId: resolvedEntry.generationId,
          publicPermalink
        });
      } catch (error) {
        await appendLog("Backend URL handoff failed, falling back to upload", {
          id: job.id,
          reason: error instanceof Error ? error.message : String(error || "job_done_failed")
        });
        const fallbackSrc = resolvedEntry.previewUrl || downloadableUrl || null;
        if (!fallbackSrc) throw error;
        const blob = await fetchVideoBlob(fallbackSrc);
        await uploadVideo(job.id, blob, resolvedEntry.generationId, publicPermalink || location.href);
      }
    } else if (resolvedEntry.previewUrl) {
      const blob = await fetchVideoBlob(resolvedEntry.previewUrl);
      await uploadVideo(job.id, blob, resolvedEntry.generationId, publicPermalink || location.href);
    } else {
      throw new Error("Sora finished but no video URL was found.");
    }

    await reportProgress(job.id, 100);
    await appendLog("Sora job completed", { id: job.id, publicPermalink, noWatermarkUrl });
    if (runningJobs <= 1) {
      await setWorkerState("connected", "Connected to MasterOS", { [STORAGE.currentJob]: "" });
    }
  }

  async function tick() {
    const { apiKey, verified } = await getBridgeConfig();
    if (!verified || !apiKey) {
      await setWorkerState("idle", "Paste bridge key from MasterOS");
      return;
    }
    // submitLock: only 1 job in submit phase at a time (prevents waitForCreateTask cross-match)
    if (submitLock || runningJobs >= MAX_CONCURRENT_JOBS) return;

    try {
      await sendHeartbeat();
      const job = await fetchNextJob();
      if (!job) return;

      submitLock = true;
      runningJobs++;

      let createTask;
      try {
        createTask = await submitPhase(job);
      } catch (error) {
        submitLock = false;
        runningJobs--;
        const message = error instanceof Error ? error.message : String(error || "Submit failed.");
        await appendLog("Sora job submit failed", { id: job.id, error: message });
        await reportFailure(job.id, message);
        await storageSet({ [STORAGE.lastError]: message });
        await setWorkerState("error", message, { [STORAGE.currentJob]: "" });
        return;
      }

      // Release submit lock — immediately chain next job without waiting 8s
      submitLock = false;
      void tick();

      // Fire and forget — completion runs in parallel with other jobs
      completionPhase(job, createTask)
        .catch(async (error) => {
          const message = error instanceof Error ? error.message : String(error || "Unknown Sora worker error.");
          await appendLog("Sora job failed", { id: job.id, error: message });
          await reportFailure(job.id, message);
          await storageSet({ [STORAGE.lastError]: message });
          await setWorkerState("error", message, { [STORAGE.currentJob]: "" });
        })
        .finally(() => { runningJobs--; });

    } catch (error) {
      const message = error instanceof Error ? error.message : String(error || "Bridge check failed.");
      await storageSet({ [STORAGE.lastError]: message });
      await setWorkerState("error", message);
    }
  }

  async function startWorker() {
    if (tickTimer) return;
    window.addEventListener("message", handleBridgeMessage);
    await appendLog("Sora worker initialized", { href: location.href });
    await tick();
    tickTimer = window.setInterval(() => {
      void tick();
    }, HEARTBEAT_MS);
  }

  if (
    location.hostname === "sora.com" ||
    location.hostname === "sora.chatgpt.com" ||
    (location.hostname === "chatgpt.com" && /sora/i.test(location.pathname))
  ) {
    void startWorker();
  }
})();
