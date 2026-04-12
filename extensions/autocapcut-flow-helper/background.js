const FLOW_HOME_URL = "https://labs.google/fx/tools/flow";
const CAPTURE_KEY = "latestFlowCaptureByTab";
const APP_TOKEN_ENDPOINT = "http://127.0.0.1:8765/api/ai-gen/flow-tokens";
const PROJECT_WAIT_TIMEOUT_MS = 15000;
const TOKEN_WAIT_TIMEOUT_MS = 9000;
const TOKEN_POLL_INTERVAL_MS = 400;

chrome.sidePanel
  .setPanelBehavior({ openPanelOnActionClick: true })
  .catch((error) => console.error("sidePanel.setPanelBehavior failed", error));

async function getLabsGoogleCookiesAsString() {
  try {
    const cookies = await chrome.cookies.getAll({ url: "https://labs.google/" });
    if (!cookies || cookies.length === 0) return "";
    return cookies.map(c => `${c.name}=${c.value}`).join("; ");
  } catch (_e) {
    return "";
  }
}

function normalizeCapture(payload = {}) {
  const projectId = typeof payload.projectId === "string" ? payload.projectId.trim() : "";
  const modelLabel = typeof payload.modelLabel === "string" ? payload.modelLabel.trim() : "";
  const imageModelName = typeof payload.imageModelName === "string" ? payload.imageModelName.trim() : "";
  const source = typeof payload.source === "string" ? payload.source.trim() : "unknown";
  const pageUrl = typeof payload.pageUrl === "string" ? payload.pageUrl.trim() : "";
  const bearerToken = typeof payload.bearerToken === "string" ? payload.bearerToken.trim() : "";
  const sessionCookie = typeof payload.sessionCookie === "string" ? payload.sessionCookie.trim() : "";
  return {
    projectId,
    modelLabel,
    imageModelName,
    source,
    pageUrl,
    bearerToken,
    sessionCookie,
    lastSeenAt: Date.now()
  };
}

async function getCaptureStore() {
  const stored = await chrome.storage.local.get(CAPTURE_KEY);
  return stored[CAPTURE_KEY] || {};
}

async function pushCaptureToApp(capture, autoStart = false) {
  if (!capture?.bearerToken) return;
  try {
    const sessionCookie = capture.sessionCookie || await getLabsGoogleCookiesAsString();
    await fetch(APP_TOKEN_ENDPOINT, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        bearerToken: capture.bearerToken || "",
        projectId: capture.projectId || "",
        sessionCookie: sessionCookie || "",
        autoStart: autoStart
      })
    });
  } catch (_e) {
    // App not running — silently ignore
  }
}

async function setCaptureForTab(tabId, capture) {
  if (!Number.isInteger(tabId) || tabId < 0) {
    return;
  }
  const store = await getCaptureStore();
  const existing = store[String(tabId)] || {};
  const merged = {
    ...existing,
    ...capture,
    bearerToken: capture.bearerToken || existing.bearerToken || "",
    sessionCookie: capture.sessionCookie || existing.sessionCookie || ""
  };
  store[String(tabId)] = merged;
  await chrome.storage.local.set({ [CAPTURE_KEY]: store });
}

async function getCaptureForTab(tabId) {
  const store = await getCaptureStore();
  return store[String(tabId)] || null;
}

async function waitForTokenCapture(tabId) {
  const startTime = Date.now();
  while (Date.now() - startTime < TOKEN_WAIT_TIMEOUT_MS) {
    const capture = await getCaptureForTab(tabId);
    if (capture?.bearerToken) {
      return capture;
    }
    await new Promise((resolve) => setTimeout(resolve, TOKEN_POLL_INTERVAL_MS));
  }
  return getCaptureForTab(tabId);
}

async function getActiveTab() {
  const tabs = await chrome.tabs.query({ active: true, currentWindow: true });
  return tabs[0] || null;
}

async function ensureFlowTab() {
  const activeTab = await getActiveTab();
  if (activeTab?.id && typeof activeTab.url === "string" && activeTab.url.includes("labs.google")) {
    return activeTab;
  }
  const tabs = await chrome.tabs.query({ url: "https://labs.google/*" });
  const existing = tabs.find((tab) => typeof tab.url === "string" && tab.url.includes("/fx/tools/flow"));
  if (existing?.id) {
    await chrome.tabs.update(existing.id, { active: true });
    if (typeof existing.windowId === "number") {
      await chrome.windows.update(existing.windowId, { focused: true });
    }
    return existing;
  }
  return chrome.tabs.create({ url: FLOW_HOME_URL, active: true });
}

async function waitForTabReady(tabId, timeoutMs = 12000) {
  const startTime = Date.now();
  while (Date.now() - startTime < timeoutMs) {
    const tab = await chrome.tabs.get(tabId);
    if (tab.status === "complete") {
      return tab;
    }
    await new Promise((resolve) => setTimeout(resolve, 250));
  }
  return chrome.tabs.get(tabId);
}

async function collectFromTab(tabId) {
  if (!Number.isInteger(tabId) || tabId < 0) {
    return null;
  }
  try {
    const response = await chrome.tabs.sendMessage(tabId, { type: "FLOW_HELPER_COLLECT_NOW" });
    if (response && response.capture) {
      const normalized = normalizeCapture(response.capture);
      if (normalized.projectId) {
        await setCaptureForTab(tabId, normalized);
      }
    }
  } catch (error) {
    return null;
  }
  return getCaptureForTab(tabId);
}

async function createOrDetectProject(tabId) {
  try {
    const response = await chrome.tabs.sendMessage(tabId, { type: "FLOW_HELPER_CREATE_PROJECT" });
    if (response?.capture?.projectId) {
      const normalized = normalizeCapture(response.capture);
      await setCaptureForTab(tabId, normalized);
      return { capture: normalized, status: "created" };
    }
    if (response?.status) {
      return { capture: await getCaptureForTab(tabId), status: response.status };
    }
  } catch (_error) {
    return { capture: await getCaptureForTab(tabId), status: "unavailable" };
  }

  const startTime = Date.now();
  while (Date.now() - startTime < PROJECT_WAIT_TIMEOUT_MS) {
    const capture = await collectFromTab(tabId);
    if (capture?.projectId) {
      return { capture, status: "detected" };
    }
    await new Promise((resolve) => setTimeout(resolve, 350));
  }
  return { capture: await getCaptureForTab(tabId), status: "timeout" };
}

async function runGeneration(tabId, payload) {
  try {
    const response = await chrome.tabs.sendMessage(tabId, {
      type: "FLOW_HELPER_RUN_GENERATION",
      payload
    });
    if (!response?.ok) {
      return {
        capture: await getCaptureForTab(tabId),
        status: response?.status || "failed",
        debug: response?.debug || []
      };
    }
    const capture = await waitForTokenCapture(tabId);
    return { capture, status: response.status || "generated", debug: response?.debug || [] };
  } catch (_error) {
    return { capture: await getCaptureForTab(tabId), status: "unavailable", debug: ["tabs_send_message_failed"] };
  }
}

chrome.runtime.onMessage.addListener((message, sender, sendResponse) => {
  if (message?.type === "FLOW_HELPER_CAPTURE") {
    const tabId = sender.tab?.id;
    if (!Number.isInteger(tabId)) {
      sendResponse({ ok: false });
      return false;
    }
    const capture = normalizeCapture(message.payload);
    setCaptureForTab(tabId, capture).then(() => sendResponse({ ok: true }));
    return true;
  }

  if (message?.type === "FLOW_HELPER_GET_ACTIVE_CAPTURE") {
    (async () => {
      const activeTab = await getActiveTab();
      if (!activeTab?.id) {
        sendResponse({ capture: null, tabId: null });
        return;
      }
      const capture = await getCaptureForTab(activeTab.id);
      sendResponse({ capture, tabId: activeTab.id, tabUrl: activeTab.url || "" });
    })();
    return true;
  }

  if (message?.type === "FLOW_HELPER_REFRESH_ACTIVE_CAPTURE") {
    (async () => {
      const activeTab = await getActiveTab();
      if (!activeTab?.id) {
        sendResponse({ capture: null, tabId: null });
        return;
      }
      const capture = await collectFromTab(activeTab.id);
      sendResponse({ capture, tabId: activeTab.id, tabUrl: activeTab.url || "" });
    })();
    return true;
  }

  if (message?.type === "FLOW_HELPER_OPEN_FLOW") {
    (async () => {
      const tab = await ensureFlowTab();
      sendResponse({ ok: true, tabId: tab?.id || null, tabUrl: tab?.url || FLOW_HOME_URL });
    })();
    return true;
  }

  if (message?.type === "FLOW_HELPER_CREATE_OR_DETECT_PROJECT") {
    (async () => {
      const tab = await ensureFlowTab();
      if (!tab?.id) {
        sendResponse({ capture: null, status: "unavailable", tabId: null });
        return;
      }
      await waitForTabReady(tab.id);
      const result = await createOrDetectProject(tab.id);
      const freshTab = await chrome.tabs.get(tab.id);
      sendResponse({
        capture: result.capture || null,
        status: result.status,
        debug: result.debug || [],
        tabId: tab.id,
        tabUrl: freshTab?.url || tab.url || ""
      });
    })();
    return true;
  }

  if (message?.type === "FLOW_HELPER_RUN_GENERATION") {
    (async () => {
      const tab = await ensureFlowTab();
      if (!tab?.id) {
        sendResponse({ capture: null, status: "unavailable", tabId: null });
        return;
      }
      await waitForTabReady(tab.id);
      const projectResult = await createOrDetectProject(tab.id);
      if (!projectResult.capture?.projectId) {
        const freshTab = await chrome.tabs.get(tab.id);
        sendResponse({
          capture: projectResult.capture || null,
          status: projectResult.status || "project_missing",
          tabId: tab.id,
          tabUrl: freshTab?.url || tab.url || ""
        });
        return;
      }
      const result = await runGeneration(tab.id, message.payload || {});
      // Push to app with autoStart:true — Get Token flow triggers auto-generation
      await pushCaptureToApp(result.capture, true);
      const freshTab = await chrome.tabs.get(tab.id);
      sendResponse({
        capture: result.capture || null,
        status: result.status,
        debug: result.debug || [],
        tabId: tab.id,
        tabUrl: freshTab?.url || tab.url || ""
      });
    })();
    return true;
  }

  if (message?.type === "FLOW_HELPER_REFRESH_TOKEN") {
    (async () => {
      const tab = await ensureFlowTab();
      if (!tab?.id) {
        sendResponse({ capture: null, status: "unavailable", tabId: null });
        return;
      }
      await waitForTabReady(tab.id);
      // Use existing project — do NOT create new one
      const existingCapture = await getCaptureForTab(tab.id);
      if (!existingCapture?.projectId) {
        sendResponse({ capture: existingCapture, status: "project_missing", tabId: tab.id });
        return;
      }
      const result = await runGeneration(tab.id, message.payload || {});
      // Push to app with autoStart:false — Refresh only updates token, no auto-generate
      await pushCaptureToApp(result.capture, false);
      const freshTab = await chrome.tabs.get(tab.id);
      sendResponse({
        capture: result.capture || null,
        status: result.status,
        debug: result.debug || [],
        tabId: tab.id,
        tabUrl: freshTab?.url || tab.url || ""
      });
    })();
    return true;
  }

  if (message?.type === "FLOW_HELPER_SESSION_DETECTED") {
    (async () => {
      const tabId = sender.tab?.id;
      const sessionCookie = await getLabsGoogleCookiesAsString();
      if (sessionCookie && Number.isInteger(tabId) && tabId >= 0) {
        const store = await getCaptureStore();
        const existing = store[String(tabId)] || {};
        if (existing.sessionCookie !== sessionCookie) {
          const updated = { ...existing, sessionCookie };
          store[String(tabId)] = updated;
          await chrome.storage.local.set({ [CAPTURE_KEY]: store });
          if (updated.bearerToken) {
            pushCaptureToApp(updated);
          }
        }
      }
      sendResponse({ ok: true });
    })();
    return true;
  }

  return false;
});

chrome.tabs.onRemoved.addListener(async (tabId) => {
  const store = await getCaptureStore();
  delete store[String(tabId)];
  await chrome.storage.local.set({ [CAPTURE_KEY]: store });
});
