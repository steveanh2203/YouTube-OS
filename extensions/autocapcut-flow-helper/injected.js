(function installFlowHelperHooks() {
  if (window.__flowHelperInjected) {
    return;
  }
  window.__flowHelperInjected = true;

  const PROJECT_ID_REGEX = /\/projects\/([0-9a-f-]{36})(?:\/|$)/i;
  const PROJECT_PAGE_REGEX = /\/project\/([0-9a-f-]{36})(?:\/|$)/i;

  function emitCapture(payload) {
    window.postMessage(
      {
        source: "FLOW_HELPER_PAGE",
        type: "FLOW_HELPER_CAPTURE",
        payload
      },
      "*"
    );
  }

  function emitCommandResult(requestId, payload) {
    window.postMessage(
      {
        source: "FLOW_HELPER_PAGE",
        type: "FLOW_HELPER_COMMAND_RESULT",
        requestId,
        payload
      },
      "*"
    );
  }

  function extractProjectId(value) {
    if (typeof value !== "string") {
      return "";
    }
    const pageMatch = value.match(PROJECT_PAGE_REGEX);
    if (pageMatch) {
      return pageMatch[1];
    }
    const apiMatch = value.match(PROJECT_ID_REGEX);
    return apiMatch ? apiMatch[1] : "";
  }

  function safeJsonParse(bodyText) {
    if (!bodyText || typeof bodyText !== "string") {
      return null;
    }
    try {
      return JSON.parse(bodyText);
    } catch (_error) {
      return null;
    }
  }

  function inferModelLabel(imageModelName) {
    switch (imageModelName) {
      case "GEM_PIX_2":
        return "Nano Banana Pro";
      case "NARWHAL":
        return "Nano Banana 2";
      case "IMAGEN_3_5":
        return "Imagen 4";
      default:
        return "";
    }
  }

  function extractBearerToken(headers) {
    if (!headers || typeof headers !== "object") {
      return "";
    }
    const auth =
      headers["authorization"] ||
      headers["Authorization"] ||
      "";
    if (typeof auth === "string" && auth.toLowerCase().startsWith("bearer ")) {
      return auth.slice(7).trim();
    }
    return "";
  }

  function captureFromPayload(url, payload, source, headers) {
    if (!payload || typeof payload !== "object") {
      return;
    }
    const projectId =
      extractProjectId(url) ||
      payload?.clientContext?.projectId ||
      payload?.requests?.[0]?.clientContext?.projectId ||
      "";
    if (!projectId) {
      return;
    }
    const imageModelName =
      payload?.requests?.[0]?.imageModelName ||
      payload?.imageModelName ||
      "";
    const bearerToken = extractBearerToken(headers);
    emitCapture({
      projectId,
      imageModelName,
      modelLabel: inferModelLabel(imageModelName),
      bearerToken,
      source,
      pageUrl: window.location.href
    });
  }

  function collectFromLocation(source) {
    const projectId = extractProjectId(window.location.href);
    if (!projectId) {
      return;
    }
    emitCapture({
      projectId,
      source,
      pageUrl: window.location.href
    });
  }

  function findSlateEditor() {
    return document.querySelector(
      "[data-slate-editor='true'][data-slate-node='value'][role='textbox'][contenteditable='true']"
    );
  }

  function normalizeEditorText(value) {
    return String(value || "")
      .replace(/\s+/g, " ")
      .trim()
      .toLowerCase();
  }

  function selectAllContents(element) {
    const selection = window.getSelection();
    if (!selection) {
      return;
    }
    const range = document.createRange();
    range.selectNodeContents(element);
    selection.removeAllRanges();
    selection.addRange(range);
  }

  function wait(ms) {
    return new Promise((resolve) => window.setTimeout(resolve, ms));
  }

  function buildKeyEventInit(key) {
    const normalizedKey = key === " " ? " " : key;
    const codeMap = {
      " ": "Space",
      Backspace: "Backspace",
      Enter: "Enter"
    };
    const code = codeMap[normalizedKey] || ("Key" + String(normalizedKey).toUpperCase());
    const keyCodeMap = {
      " ": 32,
      Backspace: 8,
      Enter: 13
    };
    const keyCode = keyCodeMap[normalizedKey] || String(normalizedKey).toUpperCase().charCodeAt(0);
    return {
      bubbles: true,
      cancelable: true,
      key: normalizedKey,
      code,
      keyCode,
      which: keyCode
    };
  }

  function dispatchKeyboardSequence(editor, key) {
    const eventInit = buildKeyEventInit(key);
    editor.dispatchEvent(new KeyboardEvent("keydown", eventInit));
    editor.dispatchEvent(new KeyboardEvent("keypress", eventInit));
    editor.dispatchEvent(new KeyboardEvent("keyup", eventInit));
  }

  function insertCharacter(editor, char) {
    editor.focus();
    let inserted = false;
    try {
      inserted = document.execCommand("insertText", false, char);
    } catch (_error) {
      inserted = false;
    }
    if (!inserted) {
      return false;
    }
    const eventInit = {
      bubbles: true,
      cancelable: true,
      inputType: "insertText",
      data: char
    };
    editor.dispatchEvent(new InputEvent("beforeinput", eventInit));
    editor.dispatchEvent(new InputEvent("input", eventInit));
    return true;
  }

  function deleteBackward(editor) {
    editor.focus();
    let deleted = false;
    try {
      deleted = document.execCommand("delete", false);
    } catch (_error) {
      deleted = false;
    }
    if (!deleted) {
      return false;
    }
    const eventInit = {
      bubbles: true,
      cancelable: true,
      inputType: "deleteContentBackward",
      data: null
    };
    editor.dispatchEvent(new InputEvent("beforeinput", eventInit));
    editor.dispatchEvent(new InputEvent("input", eventInit));
    return true;
  }

  async function typePromptInEditor(prompt, _speedMs) {
    const editor = findSlateEditor();
    if (!editor) {
      return { ok: false, status: "prompt_not_found", debug: ["editor_not_found"] };
    }

    const debug = ["editor_found"];
    editor.focus();
    selectAllContents(editor);
    debug.push("editor_focused");

    try {
      document.execCommand("delete", false);
    } catch (_error) {
      debug.push("initial_delete_failed");
    }

    const promptText = String(prompt || "");
    if (!promptText) {
      return { ok: false, status: "prompt_empty", debug: debug.concat(["prompt_empty"]) };
    }

    // Primary: ClipboardEvent paste — Slate.js handles paste natively and updates its internal model.
    // execCommand("insertText") is deprecated and React/Slate may not pick up the DOM change.
    let pasteOk = false;
    try {
      const dt = new DataTransfer();
      dt.setData("text/plain", promptText);
      editor.dispatchEvent(new ClipboardEvent("paste", {
        bubbles: true,
        cancelable: true,
        clipboardData: dt
      }));
      debug.push("paste_dispatched");
      await wait(120);
      pasteOk = normalizeEditorText(editor.textContent).includes(normalizeEditorText(promptText));
      debug.push(pasteOk ? "paste_ok" : "paste_content_mismatch");
    } catch (_error) {
      debug.push("paste_error");
    }

    // Fallback: execCommand insertText
    if (!pasteOk) {
      debug.push("fallback_exec_command");
      const insertedPrompt = insertCharacter(editor, promptText);
      if (!insertedPrompt) {
        return {
          ok: false,
          status: "prompt_type_failed",
          debug: debug.concat(["insert_prompt_failed"])
        };
      }
      debug.push("prompt_typed_exec");
      dispatchKeyboardSequence(editor, " ");
      if (!insertCharacter(editor, " ")) {
        return {
          ok: false,
          status: "prompt_type_failed",
          debug: debug.concat(["space_insert_failed"])
        };
      }
      debug.push("space_commit");
      editor.dispatchEvent(new Event("change", { bubbles: true }));
    }

    const ok = normalizeEditorText(editor.textContent).includes(normalizeEditorText(promptText));
    return {
      ok,
      status: ok ? "prompt_typed" : "prompt_type_failed",
      text: editor.textContent || "",
      debug
    };
  }

  function setPromptInEditor(prompt) {
    const editor = findSlateEditor();
    if (!editor) {
      return { ok: false, status: "prompt_not_found" };
    }

    editor.focus();
    selectAllContents(editor);

    try {
      document.execCommand("delete", false);
    } catch (_error) {}

    let inserted = false;
    try {
      inserted = document.execCommand("insertText", false, prompt);
    } catch (_error) {
      inserted = false;
    }

    if (!inserted) {
      return { ok: false, status: "prompt_set_failed", debug: ["insert_text_failed"] };
    }

    const eventInit = {
      bubbles: true,
      cancelable: true,
      inputType: "insertText",
      data: prompt
    };
    editor.dispatchEvent(new InputEvent("beforeinput", eventInit));
    editor.dispatchEvent(new InputEvent("input", eventInit));
    editor.dispatchEvent(new Event("change", { bubbles: true }));

    return {
      ok: normalizeEditorText(editor.textContent).includes(normalizeEditorText(prompt)),
      status: "prompt_set",
      text: editor.textContent || "",
      debug: ["prompt_set"]
    };
  }

  function submitEditorEnter() {
    const editor = findSlateEditor();
    if (!editor) {
      return { ok: false, status: "prompt_not_found" };
    }
    editor.focus();
    const eventInit = {
      bubbles: true,
      cancelable: true,
      key: "Enter",
      code: "Enter",
      keyCode: 13,
      which: 13
    };
    editor.dispatchEvent(new KeyboardEvent("keydown", eventInit));
    editor.dispatchEvent(new KeyboardEvent("keypress", eventInit));
    editor.dispatchEvent(new KeyboardEvent("keyup", eventInit));
    return { ok: true, status: "enter_sent", debug: ["enter_sent"] };
  }

  async function waitAndSubmitEnter(delayMs) {
    const editor = findSlateEditor();
    if (!editor) {
      return { ok: false, status: "prompt_not_found", debug: ["editor_not_found_for_enter"] };
    }
    const waitTime = Math.max(2000, Math.min(3000, Number(delayMs) || 2500));
    await wait(waitTime);
    const result = submitEditorEnter();
    return {
      ok: Boolean(result?.ok),
      status: result?.status || "enter_sent",
      debug: ["enter_wait_ms:" + String(waitTime)].concat(result?.debug || [])
    };
  }

  const originalFetch = window.fetch;
  window.fetch = async function flowHelperFetch(input, init) {
    const url = typeof input === "string" ? input : input?.url || "";
    const bodyText = typeof init?.body === "string" ? init.body : "";
    if (url.includes("/projects/") || url.includes("batchGenerateImages") || url.includes("createOrUpdateWorkflow")) {
      let headers = {};
      if (init?.headers) {
        if (init.headers instanceof Headers) {
          init.headers.forEach((value, key) => { headers[key] = value; });
        } else {
          headers = Object.assign({}, init.headers);
        }
      }
      captureFromPayload(url, safeJsonParse(bodyText), "fetch", headers);
    }
    // Detect session endpoint — triggers cookie capture in background
    if (url.includes("/fx/api/auth/session")) {
      const resp = originalFetch.apply(this, arguments);
      resp.then(() => {
        window.postMessage(
          { source: "FLOW_HELPER_PAGE", type: "FLOW_HELPER_SESSION_DETECTED" },
          "*"
        );
      }).catch(() => {});
      return resp;
    }
    return originalFetch.apply(this, arguments);
  };

  const originalOpen = XMLHttpRequest.prototype.open;
  const originalSend = XMLHttpRequest.prototype.send;
  const originalSetRequestHeader = XMLHttpRequest.prototype.setRequestHeader;

  XMLHttpRequest.prototype.open = function flowHelperOpen(method, url) {
    this.__flowHelperUrl = typeof url === "string" ? url : "";
    this.__flowHelperHeaders = {};
    return originalOpen.apply(this, arguments);
  };

  XMLHttpRequest.prototype.setRequestHeader = function flowHelperSetHeader(name, value) {
    if (!this.__flowHelperHeaders) {
      this.__flowHelperHeaders = {};
    }
    this.__flowHelperHeaders[String(name).toLowerCase()] = value;
    return originalSetRequestHeader.apply(this, arguments);
  };

  XMLHttpRequest.prototype.send = function flowHelperSend(body) {
    if (typeof this.__flowHelperUrl === "string" && this.__flowHelperUrl) {
      const bodyText = typeof body === "string" ? body : "";
      if (
        this.__flowHelperUrl.includes("/projects/") ||
        this.__flowHelperUrl.includes("batchGenerateImages") ||
        this.__flowHelperUrl.includes("createOrUpdateWorkflow")
      ) {
        captureFromPayload(this.__flowHelperUrl, safeJsonParse(bodyText), "xhr", this.__flowHelperHeaders || {});
      }
    }
    return originalSend.apply(this, arguments);
  };

  const originalPushState = history.pushState;
  const originalReplaceState = history.replaceState;

  history.pushState = function flowHelperPushState() {
    const result = originalPushState.apply(this, arguments);
    collectFromLocation("history");
    return result;
  };

  history.replaceState = function flowHelperReplaceState() {
    const result = originalReplaceState.apply(this, arguments);
    collectFromLocation("history");
    return result;
  };

  window.addEventListener("popstate", () => collectFromLocation("history"));
  window.addEventListener("message", (event) => {
    const message = event.data;
    if (event.source !== window || !message || message.source !== "FLOW_HELPER_CONTENT") {
      return;
    }
    if (message.type === "FLOW_HELPER_FORCE_COLLECT") {
      collectFromLocation("manual");
      return;
    }
    if (message.type === "FLOW_HELPER_PAGE_SET_PROMPT") {
      emitCommandResult(message.requestId, setPromptInEditor(String(message.payload?.prompt || "")));
      return;
    }
    if (message.type === "FLOW_HELPER_PAGE_TYPE_PROMPT") {
      void (async () => {
        const speedMs = Math.max(8, Math.min(80, Number(message.payload?.speedMs) || 22));
        const result = await typePromptInEditor(String(message.payload?.prompt || ""), speedMs);
        emitCommandResult(message.requestId, result);
      })();
      return;
    }
    if (message.type === "FLOW_HELPER_PAGE_SUBMIT_ENTER") {
      emitCommandResult(message.requestId, submitEditorEnter());
      return;
    }
    if (message.type === "FLOW_HELPER_PAGE_WAIT_AND_SUBMIT_ENTER") {
      void (async () => {
        const result = await waitAndSubmitEnter(message.payload?.delayMs);
        emitCommandResult(message.requestId, result);
      })();
    }
  });

  collectFromLocation("page");
})();
