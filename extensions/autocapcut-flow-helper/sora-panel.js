const SORA_API_BASE = "http://127.0.0.1:8765/api/sora";
const SORA_STORAGE = {
  key: "masterosSoraBridgeKey",
  verified: "masterosSoraBridgeVerified",
  status: "masterosSoraWorkerStatus",
  detail: "masterosSoraWorkerDetail",
  lastSeenAt: "masterosSoraLastSeenAt",
  lastError: "masterosSoraLastError",
  currentJob: "masterosSoraCurrentJob",
  logs: "masterosSoraLogs"
};
const VERIFY_DEBOUNCE_MS = 700;

const soraApiKeyInput = document.getElementById("sora-api-key");
const soraStatusPill = document.getElementById("sora-status-pill");
const soraStatusText = document.getElementById("sora-status-text");
const soraStatusSub = document.getElementById("sora-status-sub");
const soraOpenButton = document.getElementById("sora-open-tab");
const soraCopyButton = document.getElementById("sora-copy-key");
const soraLogBox = document.getElementById("sora-log-box");

let verifyTimer = null;

function formatSoraTime(value) {
  if (!value) return "Never";
  try {
    return new Date(value).toLocaleTimeString();
  } catch (_error) {
    return "Never";
  }
}

async function soraStorageGet(keys) {
  return chrome.storage.local.get(keys);
}

async function soraStorageSet(values) {
  return chrome.storage.local.set(values);
}

function setSoraPillTone(tone) {
  if (!soraStatusPill) return;
  soraStatusPill.classList.remove("is-success", "is-error", "is-idle");
  soraStatusPill.classList.add(tone);
}

async function renderSoraPanel() {
  if (!soraApiKeyInput || !soraStatusText || !soraStatusSub) return;
  const stored = await soraStorageGet(Object.values(SORA_STORAGE));
  const key = String(stored[SORA_STORAGE.key] || "").trim();
  const verified = stored[SORA_STORAGE.verified] === true;
  const status = String(stored[SORA_STORAGE.status] || "").trim() || "idle";
  const detail = String(stored[SORA_STORAGE.detail] || "").trim();
  const currentJob = String(stored[SORA_STORAGE.currentJob] || "").trim();
  const lastError = String(stored[SORA_STORAGE.lastError] || "").trim();
  const logs = Array.isArray(stored[SORA_STORAGE.logs]) ? stored[SORA_STORAGE.logs] : [];

  if (document.activeElement !== soraApiKeyInput) {
    soraApiKeyInput.value = key;
  }

  if (!key) {
    setSoraPillTone("is-idle");
    soraStatusText.textContent = "Paste bridge key";
    soraStatusSub.textContent = "Copy the key from MasterOS > Sora Gen.";
  } else if (status === "error") {
    setSoraPillTone("is-error");
    soraStatusText.textContent = "Retry needed";
    soraStatusSub.textContent = lastError || detail || "Sora worker hit an error.";
  } else if (!verified) {
    setSoraPillTone("is-idle");
    soraStatusText.textContent = "Verifying";
    soraStatusSub.textContent = "Checking the bridge key against the desktop app...";
  } else if (status === "connected" || status === "running") {
    setSoraPillTone("is-success");
    soraStatusText.textContent = status === "running" ? "Connected • Running" : "Connected";
    soraStatusSub.textContent = currentJob
      ? `Desktop app linked. Processing ${currentJob}.`
      : detail || "Desktop app linked and worker is alive.";
  } else {
    setSoraPillTone("is-idle");
    soraStatusText.textContent = "Ready";
    soraStatusSub.textContent = detail || `Last seen ${formatSoraTime(stored[SORA_STORAGE.lastSeenAt])}`;
  }

  if (soraCopyButton) {
    soraCopyButton.disabled = !key;
  }

  if (soraLogBox) {
    if (!logs.length) {
      soraLogBox.textContent = "No Sora logs yet.";
      soraLogBox.classList.add("empty");
    } else {
      soraLogBox.classList.remove("empty");
      soraLogBox.textContent = logs
        .slice(-12)
        .map((entry) => `[${formatSoraTime(entry.ts)}] ${entry.message}`)
        .join("\n");
      soraLogBox.scrollTop = soraLogBox.scrollHeight;
    }
  }
}

async function verifySoraKey(key) {
  const trimmed = String(key || "").trim();
  await soraStorageSet({
    [SORA_STORAGE.key]: trimmed,
    [SORA_STORAGE.verified]: false,
    [SORA_STORAGE.status]: "idle",
    [SORA_STORAGE.detail]: trimmed ? "Checking bridge key..." : "Paste bridge key from MasterOS",
    [SORA_STORAGE.lastError]: ""
  });
  await renderSoraPanel();

  if (!trimmed) return;

  try {
    const res = await fetch(`${SORA_API_BASE}/verify-key`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ api_key: trimmed })
    });
    if (!res.ok) {
      const data = await res.json().catch(() => ({}));
      throw new Error(data.detail || "Bridge key is invalid.");
    }

    await soraStorageSet({
      [SORA_STORAGE.verified]: true,
      [SORA_STORAGE.status]: "connected",
      [SORA_STORAGE.detail]: "Desktop app connected. Open a Sora tab to start polling jobs.",
      [SORA_STORAGE.lastSeenAt]: Date.now(),
      [SORA_STORAGE.lastError]: ""
    });
  } catch (error) {
    const message = error instanceof Error ? error.message : "Bridge key verification failed.";
    await soraStorageSet({
      [SORA_STORAGE.verified]: false,
      [SORA_STORAGE.status]: "error",
      [SORA_STORAGE.detail]: message,
      [SORA_STORAGE.lastError]: message
    });
  }
  await renderSoraPanel();
}

function scheduleSoraVerify() {
  if (!soraApiKeyInput) return;
  window.clearTimeout(verifyTimer);
  verifyTimer = window.setTimeout(() => {
    void verifySoraKey(soraApiKeyInput.value);
  }, VERIFY_DEBOUNCE_MS);
}

if (soraApiKeyInput) {
  soraApiKeyInput.addEventListener("input", scheduleSoraVerify);
  soraApiKeyInput.addEventListener("blur", () => {
    void verifySoraKey(soraApiKeyInput.value);
  });
}

if (soraOpenButton) {
  soraOpenButton.addEventListener("click", async () => {
    await chrome.tabs.create({ url: "https://sora.com/" });
  });
}

if (soraCopyButton) {
  soraCopyButton.addEventListener("click", async () => {
    if (!soraApiKeyInput?.value.trim()) return;
    await navigator.clipboard.writeText(soraApiKeyInput.value.trim());
  });
}

chrome.storage.onChanged.addListener((changes, areaName) => {
  if (areaName !== "local") return;
  if (Object.keys(changes).some((key) => Object.values(SORA_STORAGE).includes(key))) {
    void renderSoraPanel();
  }
});

void renderSoraPanel();
