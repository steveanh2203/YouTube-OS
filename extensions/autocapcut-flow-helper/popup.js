const projectIdEl = document.getElementById("project-id");
const sourceEl = document.getElementById("source");
const statusTextEl = document.getElementById("status-text");
const copyProjectInlineButton = document.getElementById("copy-project-inline");
const openFlowButton = document.getElementById("open-flow");
const createProjectButton = document.getElementById("create-project");
const refreshButton = document.getElementById("refresh");
const promptInput = document.getElementById("prompt-input");
const modelSelect = document.getElementById("model-select");
const logBox = document.getElementById("log-box");
const clearLogsButton = document.getElementById("clear-logs");
const bearerTokenEl = document.getElementById("bearer-token");
const copyBearerButton = document.getElementById("copy-bearer");
const sessionCookieEl = document.getElementById("session-cookie");
const copySessionCookieButton = document.getElementById("copy-session-cookie");
const DRAFT_KEY = "flowHelperDraft";
const DEFAULT_PROMPT = "a cat";
const LOGS_KEY = "flowHelperLogs";
const MAX_LOG_ENTRIES = 80;
const RUN_COOLDOWN_MS = 2000;
let currentCapture = null;
let logEntries = [];

function formatTime(value) {
  try {
    return new Date(value).toLocaleTimeString();
  } catch (_error) {
    return "--:--:--";
  }
}

function formatLogMeta(meta) {
  if (!meta) {
    return "";
  }
  try {
    return " " + JSON.stringify(meta);
  } catch (_error) {
    return "";
  }
}

async function persistLogs() {
  await chrome.storage.local.set({ [LOGS_KEY]: logEntries });
}

function renderLogs() {
  if (!logBox) {
    return;
  }
  if (!logEntries.length) {
    logBox.textContent = "No logs yet.";
    logBox.classList.add("empty");
    return;
  }
  logBox.classList.remove("empty");
  logBox.textContent = logEntries
    .map((entry) => "[" + formatTime(entry.ts) + "] " + entry.message + formatLogMeta(entry.meta))
    .join("\n");
  logBox.scrollTop = logBox.scrollHeight;
}

async function appendLog(message, meta) {
  logEntries.push({
    ts: Date.now(),
    message: String(message || "").trim(),
    meta: meta || null
  });
  if (logEntries.length > MAX_LOG_ENTRIES) {
    logEntries = logEntries.slice(-MAX_LOG_ENTRIES);
  }
  renderLogs();
  await persistLogs();
}

function setText(el, value, emptyText) {
  const text = (value || "").trim();
  el.textContent = text || emptyText;
  el.classList.toggle("empty", !text);
}

function renderCapture(capture, tabUrl, actionStatus = "") {
  currentCapture = capture || null;
  const hasProjectId = Boolean(capture?.projectId);
  const hasBearerToken = Boolean(capture?.bearerToken);
  setText(projectIdEl, capture?.projectId || "", "Open a Google Flow tab to detect a project ID.");
  setText(sourceEl, capture?.source || "", "Not detected");

  bearerTokenEl.textContent = capture?.bearerToken || "";
  bearerTokenEl.classList.toggle("empty", !hasBearerToken);
  if (!hasBearerToken) {
    bearerTokenEl.textContent = "Not captured yet — trigger a generate in Flow";
  }
  copyBearerButton.disabled = !hasBearerToken;

  const hasSessionCookie = Boolean(capture?.sessionCookie);
  if (sessionCookieEl) {
    sessionCookieEl.textContent = capture?.sessionCookie || "";
    sessionCookieEl.classList.toggle("empty", !hasSessionCookie);
    if (!hasSessionCookie) {
      sessionCookieEl.textContent = "Not captured yet — open a Flow tab";
    }
  }
  if (copySessionCookieButton) {
    copySessionCookieButton.disabled = !hasSessionCookie;
  }

  copyProjectInlineButton.disabled = !hasProjectId;

  if (actionStatus === "not_found") {
    statusTextEl.textContent = "Could not find the New Project button";
    return;
  }

  if (actionStatus === "timeout") {
    statusTextEl.textContent = "Clicked project creation, still waiting for project ID";
    return;
  }

  if (actionStatus === "prompt_not_found") {
    statusTextEl.textContent = "Could not find the prompt input on Flow";
    return;
  }

  if (actionStatus === "prompt_empty") {
    statusTextEl.textContent = "Enter a prompt before running generate";
    return;
  }

  if (actionStatus === "model_trigger_not_found" || actionStatus === "model_not_found") {
    statusTextEl.textContent = "Could not switch the selected Flow model";
    return;
  }

  if (actionStatus === "generate_not_found") {
    statusTextEl.textContent = "Could not find the Generate button on Flow";
    return;
  }

  if (actionStatus === "unavailable") {
    statusTextEl.textContent = "Could not talk to the Google Flow tab";
    return;
  }

  if (actionStatus === "project_missing") {
    statusTextEl.textContent = "Could not create or detect a Flow project";
    return;
  }

  if (hasProjectId) {
    if (actionStatus === "generated") {
      statusTextEl.textContent = "Generate action sent";
    } else {
      statusTextEl.textContent = actionStatus === "created" ? "Project created and detected" : "Project ID detected";
    }
    return;
  }

  if (typeof tabUrl === "string" && tabUrl.includes("labs.google")) {
    statusTextEl.textContent = "Waiting for Flow project activity";
    return;
  }

  statusTextEl.textContent = "Open Google Flow to begin";
}

async function refreshCapture(forceRefresh = false) {
  const type = forceRefresh ? "FLOW_HELPER_REFRESH_ACTIVE_CAPTURE" : "FLOW_HELPER_GET_ACTIVE_CAPTURE";
  const response = await chrome.runtime.sendMessage({ type });
  await appendLog(forceRefresh ? "Refresh capture requested" : "Loaded active capture", {
    status: response?.status || "",
    tabUrl: response?.tabUrl || "",
    projectId: response?.capture?.projectId || ""
  });
  renderCapture(response?.capture || null, response?.tabUrl || "", response?.status || "");
}

async function loadDraft() {
  const stored = await chrome.storage.local.get(DRAFT_KEY);
  const draft = stored[DRAFT_KEY] || {};
  const prompt = draft.prompt || DEFAULT_PROMPT;
  const modelLabel = draft.modelLabel || "Nano Banana Pro";
  promptInput.value = prompt;
  modelSelect.value = modelLabel;
  if (!draft.prompt || !draft.modelLabel) {
    await chrome.storage.local.set({
      [DRAFT_KEY]: {
        prompt,
        modelLabel
      }
    });
  }
}

async function loadLogs() {
  const stored = await chrome.storage.local.get(LOGS_KEY);
  logEntries = Array.isArray(stored[LOGS_KEY]) ? stored[LOGS_KEY] : [];
  renderLogs();
}

async function saveDraft() {
  await chrome.storage.local.set({
    [DRAFT_KEY]: {
      prompt: promptInput.value,
      modelLabel: modelSelect.value
    }
  });
}

openFlowButton.addEventListener("click", async () => {
  await appendLog("Open Google Flow clicked");
  await chrome.runtime.sendMessage({ type: "FLOW_HELPER_OPEN_FLOW" });
});

refreshButton.addEventListener("click", async () => {
  await saveDraft();
  refreshButton.disabled = true;
  createProjectButton.disabled = true;
  statusTextEl.textContent = "Refreshing token…";
  await appendLog("Refresh Token started");
  try {
    const response = await chrome.runtime.sendMessage({
      type: "FLOW_HELPER_REFRESH_TOKEN",
      payload: {
        prompt: promptInput.value,
        modelLabel: modelSelect.value
      }
    });
    await appendLog("Refresh Token finished", {
      status: response?.status || "",
      projectId: response?.capture?.projectId || ""
    });
    renderCapture(response?.capture || null, response?.tabUrl || "", response?.status || "");
    await new Promise((resolve) => setTimeout(resolve, RUN_COOLDOWN_MS));
  } finally {
    refreshButton.disabled = false;
    createProjectButton.disabled = false;
    await appendLog("Refresh cooldown ended");
  }
});

createProjectButton.addEventListener("click", async () => {
  await saveDraft();
  createProjectButton.disabled = true;
  refreshButton.disabled = true;
  statusTextEl.textContent = "Creating or detecting Flow project…";
  await appendLog("Get Token started");
  try {
    // Step 1: Create or detect project
    const createResponse = await chrome.runtime.sendMessage({ type: "FLOW_HELPER_CREATE_OR_DETECT_PROJECT" });
    await appendLog("Create / Detect finished", {
      status: createResponse?.status || "",
      projectId: createResponse?.capture?.projectId || "",
      tabUrl: createResponse?.tabUrl || ""
    });
    renderCapture(createResponse?.capture || null, createResponse?.tabUrl || "", createResponse?.status || "");

    // Step 2: Auto-run generate to capture tokens
    const projectId = createResponse?.capture?.projectId;
    if (!projectId) {
      await appendLog("No project ID — skipping auto-generate");
      return;
    }
    statusTextEl.textContent = "Auto-generating to capture tokens…";
    await appendLog("Auto Run Generate started", {
      promptLength: promptInput.value.trim().length,
      modelLabel: modelSelect.value
    });
    const genResponse = await chrome.runtime.sendMessage({
      type: "FLOW_HELPER_RUN_GENERATION",
      payload: {
        prompt: promptInput.value,
        modelLabel: modelSelect.value
      }
    });
    await appendLog("Auto Run Generate finished", {
      status: genResponse?.status || "",
      projectId: genResponse?.capture?.projectId || "",
      model: genResponse?.capture?.modelLabel || genResponse?.capture?.imageModelName || "",
      debug: genResponse?.debug || []
    });
    renderCapture(genResponse?.capture || null, genResponse?.tabUrl || "", genResponse?.status || "");
    await appendLog("Run cooldown started", { waitMs: RUN_COOLDOWN_MS });
    await new Promise((resolve) => setTimeout(resolve, RUN_COOLDOWN_MS));
    await appendLog("Run cooldown ended");
  } finally {
    createProjectButton.disabled = false;
    refreshButton.disabled = false;
  }
});

copyBearerButton.addEventListener("click", async () => {
  const token = currentCapture?.bearerToken || "";
  if (!token || copyBearerButton.disabled) {
    return;
  }
  await navigator.clipboard.writeText(token);
  await appendLog("Access Key copied");
  statusTextEl.textContent = "Access Key copied";
});

copyProjectInlineButton.addEventListener("click", async () => {
  const projectId = currentCapture?.projectId || "";
  if (!projectId || copyProjectInlineButton.disabled) {
    return;
  }
  await navigator.clipboard.writeText(projectId);
  await appendLog("Project ID copied", { projectId });
  statusTextEl.textContent = "Project ID copied";
});

if (copySessionCookieButton) {
  copySessionCookieButton.addEventListener("click", async () => {
    const cookie = currentCapture?.sessionCookie || "";
    if (!cookie || copySessionCookieButton.disabled) {
      return;
    }
    await navigator.clipboard.writeText(cookie);
    await appendLog("Session Cookie copied");
    statusTextEl.textContent = "Session Cookie copied";
  });
}

if (clearLogsButton) {
  clearLogsButton.addEventListener("click", async () => {
    logEntries = [];
    renderLogs();
    await persistLogs();
  });
}

window.addEventListener("error", (event) => {
  void appendLog("Panel runtime error", {
    message: event.message || "",
    source: event.filename || "",
    line: event.lineno || 0
  });
});

window.addEventListener("unhandledrejection", (event) => {
  const reason = event.reason && event.reason.message ? event.reason.message : String(event.reason || "");
  void appendLog("Unhandled promise rejection", { reason });
});

promptInput.addEventListener("input", () => {
  saveDraft();
});

modelSelect.addEventListener("change", () => {
  saveDraft();
});

Promise.all([loadLogs(), loadDraft()]).then(() => refreshCapture(false));
