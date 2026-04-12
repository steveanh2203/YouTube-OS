const SETTINGS_KEY = "ytb_connect_settings";
const RUNTIME_KEY = "ytb_connect_runtime";
const DEFAULT_BRIDGE_URL = "http://127.0.0.1:8765";
const YOUTUBE_SCOPE = "https://www.googleapis.com/auth/youtube.force-ssl";

const bridgeUrlEl = document.getElementById("bridge-url");
const bridgeKeyEl = document.getElementById("bridge-key");
const parentProjectEl = document.getElementById("parent-project");
const statusBoxEl = document.getElementById("status-box");
const bridgeSummaryEl = document.getElementById("bridge-summary");
const channelSummaryEl = document.getElementById("channel-summary");
const verifyBridgeButton = document.getElementById("verify-bridge");
const connectYoutubeButton = document.getElementById("connect-youtube");
const disconnectYoutubeButton = document.getElementById("disconnect-youtube");

const state = {
  parentProjects: [],
  bridgeConnected: false,
  oauthReady: false,
  oauthClientId: "",
  pairingInFlight: false,
  disconnectInFlight: false,
  selectedParentProjectId: "",
  lastStatusMessage: "",
  lastStatusTone: "warning",
};

function normalizeBridgeUrl(value) {
  return String(value || "").trim().replace(/\/+$/, "") || DEFAULT_BRIDGE_URL;
}

function setStatus(message, tone = "neutral") {
  const normalizedMessage = String(message || "").trim() || "Chưa có trạng thái.";
  statusBoxEl.textContent = normalizedMessage;
  statusBoxEl.dataset.tone = tone;
  state.lastStatusMessage = normalizedMessage;
  state.lastStatusTone = tone;
  void saveRuntimeState();
}

function authHeaders(settings) {
  return {
    "Content-Type": "application/json",
    "X-Account-Connect-Key": settings.bridgeKey,
  };
}

async function fetchJson(url, init) {
  const response = await fetch(url, init);
  const data = await response.json().catch(() => ({}));
  if (!response.ok) {
    throw new Error(data.detail || data.message || `HTTP ${response.status}`);
  }
  return data;
}

function setButtonVariant(button, variant) {
  if (!button) return;
  button.classList.remove("btn-primary", "btn-secondary", "btn-success");
  button.classList.add(variant);
}

function currentProject() {
  return state.parentProjects.find((item) => String(item.id) === String(parentProjectEl.value || state.selectedParentProjectId || ""));
}

function updateUiState() {
  const selectedProject = currentProject();
  const hasProject = Boolean(parentProjectEl.value);
  const projectConnected = Boolean(selectedProject?.connected && selectedProject?.channel_id);

  verifyBridgeButton.textContent = state.bridgeConnected ? "Đã nối app" : "Xác thực app";
  setButtonVariant(verifyBridgeButton, state.bridgeConnected ? "btn-success" : "btn-secondary");

  connectYoutubeButton.disabled = !state.bridgeConnected || !state.oauthReady || !hasProject || state.pairingInFlight;
  connectYoutubeButton.textContent = state.pairingInFlight
    ? "Đang login..."
    : !state.bridgeConnected
      ? "Xác thực app trước"
      : !state.oauthReady
        ? "Verify OAuth trước"
        : !hasProject
          ? "Chọn project"
          : "Login Google";
  setButtonVariant(connectYoutubeButton, connectYoutubeButton.disabled ? "btn-primary" : "btn-success");

  disconnectYoutubeButton.disabled = !state.bridgeConnected || !projectConnected || state.disconnectInFlight;
  disconnectYoutubeButton.textContent = state.disconnectInFlight ? "Đang ngắt..." : "Huỷ kết nối";
  setButtonVariant(disconnectYoutubeButton, projectConnected ? "btn-secondary" : "btn-primary");

  bridgeSummaryEl.textContent = state.bridgeConnected ? "Đã xác thực" : "Chưa xác thực";
  if (!selectedProject) {
    channelSummaryEl.textContent = "Chưa chọn project";
  } else if (projectConnected) {
    channelSummaryEl.textContent = selectedProject.channel_name || selectedProject.channel_id || "Đã pair";
  } else {
    channelSummaryEl.textContent = "Chưa pair";
  }
}

async function loadSettings() {
  const stored = await chrome.storage.local.get(SETTINGS_KEY);
  const settings = stored[SETTINGS_KEY] || {};
  bridgeUrlEl.value = normalizeBridgeUrl(settings.bridgeUrl || DEFAULT_BRIDGE_URL);
  bridgeKeyEl.value = settings.bridgeKey || "";
}

async function saveSettings() {
  const settings = {
    bridgeUrl: normalizeBridgeUrl(bridgeUrlEl.value),
    bridgeKey: bridgeKeyEl.value.trim(),
  };
  await chrome.storage.local.set({ [SETTINGS_KEY]: settings });
  return settings;
}

async function loadRuntimeState() {
  const stored = await chrome.storage.local.get(RUNTIME_KEY);
  const runtime = stored[RUNTIME_KEY] || {};
  state.selectedParentProjectId = String(runtime.selectedParentProjectId || "").trim();
  state.lastStatusMessage = String(runtime.lastStatusMessage || "").trim();
  state.lastStatusTone = String(runtime.lastStatusTone || "warning").trim() || "warning";
}

async function saveRuntimeState() {
  await chrome.storage.local.set({
    [RUNTIME_KEY]: {
      selectedParentProjectId: parentProjectEl.value || state.selectedParentProjectId || "",
      lastStatusMessage: state.lastStatusMessage || "",
      lastStatusTone: state.lastStatusTone || "warning",
    },
  });
}

async function hydrateBridgeSettings(bridgeUrl) {
  try {
    const data = await fetchJson(`${bridgeUrl}/api/account-connect/settings`);
    if (data.bridge_key && !bridgeKeyEl.value.trim()) {
      bridgeKeyEl.value = String(data.bridge_key).trim();
    }
    state.oauthReady = Boolean(data.oauth_ready);
    state.oauthClientId = String(data.oauth_client_id || "").trim();
    await saveSettings();
    return data;
  } catch {
    state.oauthReady = false;
    state.oauthClientId = "";
    return null;
  }
}

function renderParentProjects(items) {
  parentProjectEl.innerHTML = "";
  if (!items.length) {
    parentProjectEl.innerHTML = '<option value="">Chưa có project</option>';
    updateUiState();
    return;
  }

  const placeholder = document.createElement("option");
  placeholder.value = "";
  placeholder.textContent = "Chọn project";
  parentProjectEl.appendChild(placeholder);

  items.forEach((item) => {
    const option = document.createElement("option");
    option.value = String(item.id);
    option.textContent = item.connected && item.channel_name
      ? `${item.name} · ${item.channel_name}`
      : item.name;
    parentProjectEl.appendChild(option);
  });

  if (state.selectedParentProjectId) {
    const hasSavedOption = [...parentProjectEl.options].some((option) => option.value === state.selectedParentProjectId);
    if (hasSavedOption) {
      parentProjectEl.value = state.selectedParentProjectId;
    }
  }

  updateUiState();
}

async function loadParentProjects(settings) {
  const data = await fetchJson(`${settings.bridgeUrl}/api/account-connect/parent-projects`, {
    headers: authHeaders(settings),
  });
  state.parentProjects = Array.isArray(data.items) ? data.items : [];
  renderParentProjects(state.parentProjects);
}

async function verifyBridge(settings) {
  if (!settings.bridgeUrl) {
    state.bridgeConnected = false;
    setStatus("Thiếu Bridge URL.", "error");
    updateUiState();
    return false;
  }

  const bridgeMeta = await hydrateBridgeSettings(settings.bridgeUrl);
  const hydratedSettings = await saveSettings();
  if (!hydratedSettings.bridgeKey) {
    state.bridgeConnected = false;
    setStatus("Thiếu Bridge Key. Mở Settings trong app để lấy lại bridge.", "error");
    updateUiState();
    return false;
  }

  try {
    await fetchJson(`${hydratedSettings.bridgeUrl}/api/account-connect/health`, {
      headers: authHeaders(hydratedSettings),
    });
    state.bridgeConnected = true;
    state.oauthReady = Boolean(bridgeMeta?.oauth_ready);
    state.oauthClientId = String(bridgeMeta?.oauth_client_id || "").trim();
    await loadParentProjects(hydratedSettings);
    if (!state.oauthReady) {
      setStatus("Desktop app đã nối, nhưng OAuth chưa ready. Vào Settings trong app rồi bấm Verify.", "warning");
    } else {
      setStatus("Desktop app đã sẵn sàng. Chọn project rồi bấm Login Google để pair kênh.", "success");
    }
    updateUiState();
    return true;
  } catch (error) {
    state.bridgeConnected = false;
    state.parentProjects = [];
    renderParentProjects([]);
    setStatus(`Không nối được desktop app: ${error instanceof Error ? error.message : "Unknown error"}`, "error");
    updateUiState();
    return false;
  }
}

async function launchOAuthFlow(clientId) {
  const redirectUri = chrome.identity.getRedirectURL("oauth2");
  const params = new URLSearchParams({
    client_id: clientId,
    redirect_uri: redirectUri,
    response_type: "code",
    scope: YOUTUBE_SCOPE,
    access_type: "offline",
    prompt: "consent",
    include_granted_scopes: "true",
  });
  const authUrl = `https://accounts.google.com/o/oauth2/v2/auth?${params.toString()}`;

  const callbackUrl = await new Promise((resolve, reject) => {
    chrome.identity.launchWebAuthFlow({ url: authUrl, interactive: true }, (responseUrl) => {
      const runtimeError = chrome.runtime.lastError;
      if (runtimeError) {
        reject(new Error(runtimeError.message || "OAuth flow failed."));
        return;
      }
      if (!responseUrl) {
        reject(new Error("Không lấy được callback URL từ Google OAuth."));
        return;
      }
      resolve(responseUrl);
    });
  });

  const parsedUrl = new URL(String(callbackUrl));
  const code = parsedUrl.searchParams.get("code");
  if (!code) {
    throw new Error("Google OAuth không trả về authorization code.");
  }

  return { code, redirectUri };
}

async function connectYoutube() {
  const settings = await saveSettings();
  const projectId = Number(parentProjectEl.value || 0);
  if (!projectId) {
    setStatus("Chọn project trước khi login Google.", "warning");
    return;
  }

  const verified = state.bridgeConnected ? true : await verifyBridge(settings);
  if (!verified) return;
  if (!state.oauthReady || !state.oauthClientId) {
    setStatus("OAuth chưa sẵn sàng. Vào Settings trong app rồi bấm Verify trước.", "warning");
    return;
  }

  state.pairingInFlight = true;
  updateUiState();
  setStatus("Đang mở Google OAuth trong profile hiện tại...", "warning");

  try {
    const { code, redirectUri } = await launchOAuthFlow(state.oauthClientId);
    const response = await fetchJson(`${settings.bridgeUrl}/api/account-connect/oauth/exchange`, {
      method: "POST",
      headers: authHeaders(settings),
      body: JSON.stringify({
        parent_project_id: projectId,
        code,
        redirect_uri: redirectUri,
      }),
    });
    state.selectedParentProjectId = String(projectId);
    await saveRuntimeState();
    await loadParentProjects(settings);
    setStatus(
      response.channel_name
        ? `Đã pair ${response.channel_name} với project này.`
        : "Đã pair kênh YouTube với project.",
      "success",
    );
  } catch (error) {
    setStatus(`Login Google thất bại: ${error instanceof Error ? error.message : "Unknown error"}`, "error");
  } finally {
    state.pairingInFlight = false;
    updateUiState();
  }
}

async function disconnectYoutube() {
  const settings = await saveSettings();
  const projectId = Number(parentProjectEl.value || 0);
  if (!projectId) {
    setStatus("Chọn project trước khi huỷ kết nối.", "warning");
    return;
  }

  state.disconnectInFlight = true;
  updateUiState();
  try {
    await fetchJson(`${settings.bridgeUrl}/api/account-connect/disconnect`, {
      method: "POST",
      headers: authHeaders(settings),
      body: JSON.stringify({ parent_project_id: projectId }),
    });
    await loadParentProjects(settings);
    setStatus("Đã huỷ kết nối kênh khỏi project. Có thể login lại để map project khác.", "success");
  } catch (error) {
    setStatus(`Không huỷ được kết nối: ${error instanceof Error ? error.message : "Unknown error"}`, "error");
  } finally {
    state.disconnectInFlight = false;
    updateUiState();
  }
}

async function bootstrap() {
  await loadSettings();
  await loadRuntimeState();
  if (state.lastStatusMessage) {
    setStatus(state.lastStatusMessage, state.lastStatusTone);
  } else {
    setStatus("Nối desktop app trước, rồi login Google để map kênh vào project.", "warning");
  }
  const settings = await saveSettings();
  if (settings.bridgeUrl) {
    await verifyBridge(settings);
  } else {
    updateUiState();
  }
}

verifyBridgeButton?.addEventListener("click", async () => {
  const settings = await saveSettings();
  await verifyBridge(settings);
});

connectYoutubeButton?.addEventListener("click", () => {
  void connectYoutube();
});

disconnectYoutubeButton?.addEventListener("click", () => {
  void disconnectYoutube();
});

parentProjectEl?.addEventListener("change", async () => {
  state.selectedParentProjectId = parentProjectEl.value || "";
  await saveRuntimeState();
  updateUiState();
});

void bootstrap();
