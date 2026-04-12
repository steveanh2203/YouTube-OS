(function bootstrapFlowHelper() {
  if (window.__flowHelperContentBootstrapped) {
    return;
  }
  window.__flowHelperContentBootstrapped = true;

  const script = document.createElement("script");
  script.src = chrome.runtime.getURL("injected.js");
  script.async = false;
  (document.documentElement || document.head || document.body).appendChild(script);
  script.remove();

  const pendingPageCommands = new Map();
  let pageCommandCounter = 0;

  window.addEventListener("message", (event) => {
    if (event.source !== window) {
      return;
    }
    const message = event.data;
    if (!message || message.source !== "FLOW_HELPER_PAGE") {
      return;
    }
    if (message.type === "FLOW_HELPER_CAPTURE") {
      chrome.runtime.sendMessage({
        type: "FLOW_HELPER_CAPTURE",
        payload: message.payload || {}
      });
      return;
    }
    if (message.type === "FLOW_HELPER_SESSION_DETECTED") {
      chrome.runtime.sendMessage({ type: "FLOW_HELPER_SESSION_DETECTED" });
      return;
    }
    if (message.type === "FLOW_HELPER_COMMAND_RESULT" && message.requestId) {
      const resolver = pendingPageCommands.get(message.requestId);
      if (resolver) {
        pendingPageCommands.delete(message.requestId);
        resolver(message.payload || {});
      }
    }
  });

  chrome.runtime.onMessage.addListener((message, _sender, sendResponse) => {
    if (message?.type === "FLOW_HELPER_COLLECT_NOW") {
      window.postMessage(
        {
          source: "FLOW_HELPER_CONTENT",
          type: "FLOW_HELPER_FORCE_COLLECT"
        },
        "*"
      );
      const projectId = extractProjectId(window.location.href);
      sendResponse({
        capture: {
          projectId,
          source: projectId ? "url" : "page",
          pageUrl: window.location.href
        }
      });
      return false;
    }

    if (message?.type === "FLOW_HELPER_CREATE_PROJECT") {
      createOrDetectProject().then((result) => sendResponse(result));
      return true;
    }

    if (message?.type === "FLOW_HELPER_RUN_GENERATION") {
      runGenerationFlow(message.payload || {}).then((result) => sendResponse(result));
      return true;
    }

    return false;
  });

  function extractProjectId(url) {
    if (typeof url !== "string") {
      return "";
    }
    const matches = url.match(/\/project\/([0-9a-f-]{36})/i);
    return matches ? matches[1] : "";
  }

  function sendPageCommand(type, payload, timeoutMs = 3000) {
    return new Promise((resolve) => {
      const requestId = "cmd_" + String(++pageCommandCounter) + "_" + String(Date.now());
      pendingPageCommands.set(requestId, resolve);
      window.postMessage(
        {
          source: "FLOW_HELPER_CONTENT",
          type,
          requestId,
          payload: payload || {}
        },
        "*"
      );
      setTimeout(() => {
        if (pendingPageCommands.has(requestId)) {
          pendingPageCommands.delete(requestId);
          resolve({ ok: false, status: "command_timeout" });
        }
      }, timeoutMs);
    });
  }

  function normalizeText(value) {
    return String(value || "")
      .normalize("NFD")
      .replace(/[\u0300-\u036f]/g, "")
      .replace(/\s+/g, " ")
      .trim()
      .toLowerCase();
  }

  function isVisible(element) {
    if (!(element instanceof Element)) {
      return false;
    }
    const style = window.getComputedStyle(element);
    if (style.display === "none" || style.visibility === "hidden" || style.opacity === "0") {
      return false;
    }
    const rect = element.getBoundingClientRect();
    return rect.width > 0 && rect.height > 0;
  }

  function collectCandidateText(element) {
    return normalizeText(
      [
        element.textContent || "",
        element.getAttribute("aria-label") || "",
        element.getAttribute("title") || "",
        element.getAttribute("placeholder") || "",
        element.getAttribute("aria-placeholder") || "",
        element.getAttribute("data-placeholder") || "",
        element.getAttribute("name") || ""
      ].join(" ")
    );
  }

  function elementHasText(element, phrases) {
    const text = collectCandidateText(element);
    return phrases.some((phrase) => text.includes(phrase));
  }

  function findCreateProjectElement() {
    const phrases = ["new project", "du an moi", "tao du an", "create project"];
    const selectors = [
      "button",
      "[role='button']",
      "a",
      "[tabindex='0']"
    ];
    const seen = new Set();
    for (const selector of selectors) {
      const nodes = document.querySelectorAll(selector);
      for (const node of nodes) {
        if (seen.has(node) || !isVisible(node)) {
          continue;
        }
        seen.add(node);
        const text = collectCandidateText(node);
        if (phrases.some((phrase) => text.includes(phrase))) {
          return node;
        }
      }
    }
    return null;
  }

  function clickElement(element) {
    if (!(element instanceof Element)) {
      return false;
    }
    element.scrollIntoView({ block: "center", inline: "center", behavior: "instant" });
    const rect = element.getBoundingClientRect();
    const clientX = rect.left + rect.width / 2;
    const clientY = rect.top + rect.height / 2;
    const pointedElement = document.elementFromPoint(clientX, clientY);
    const target = element.contains(pointedElement) ? pointedElement : element;
    const eventInit = {
      bubbles: true,
      cancelable: true,
      view: window,
      clientX,
      clientY
    };
    target.dispatchEvent(new PointerEvent("pointerover", eventInit));
    target.dispatchEvent(new MouseEvent("mouseover", eventInit));
    target.dispatchEvent(new PointerEvent("pointerdown", eventInit));
    target.dispatchEvent(new MouseEvent("mousedown", eventInit));
    target.dispatchEvent(new PointerEvent("pointerup", eventInit));
    target.dispatchEvent(new MouseEvent("mouseup", eventInit));
    target.dispatchEvent(new MouseEvent("click", eventInit));
    if (typeof element.click === "function") {
      element.click();
    }
    return true;
  }

  function dispatchTextEvents(element) {
    element.dispatchEvent(new Event("input", { bubbles: true }));
    element.dispatchEvent(new Event("change", { bubbles: true }));
  }

  function dispatchEnterEvents(element) {
    if (!(element instanceof Element)) {
      return;
    }
    element.focus();
    const eventInit = {
      bubbles: true,
      cancelable: true,
      key: "Enter",
      code: "Enter",
      keyCode: 13,
      which: 13
    };
    element.dispatchEvent(new KeyboardEvent("keydown", eventInit));
    element.dispatchEvent(new KeyboardEvent("keypress", eventInit));
    element.dispatchEvent(new KeyboardEvent("keyup", eventInit));
  }

  function isPromptLike(element) {
    const text = collectCandidateText(element);
    if (text.includes("new project") || text.includes("du an moi")) {
      return false;
    }
    return [
      "prompt",
      "describe",
      "describe your",
      "nhap prompt",
      "mo ta",
      "nhap mo ta"
    ].some((phrase) => text.includes(phrase));
  }

  function findPromptInput() {
    const slateEditor = document.querySelector(
      "[data-slate-editor='true'][data-slate-node='value'][role='textbox'][contenteditable='true']"
    );
    if (slateEditor && isVisible(slateEditor)) {
      return slateEditor;
    }

    const candidates = [
      ...document.querySelectorAll("textarea"),
      ...document.querySelectorAll("input[type='text']"),
      ...document.querySelectorAll("[contenteditable='true']"),
      ...document.querySelectorAll("[role='textbox']")
    ];
    let fallback = null;
    for (const element of candidates) {
      if (!isVisible(element)) {
        continue;
      }
      const text = collectCandidateText(element);
      if (isPromptLike(element)) {
        return element;
      }
      if (
        !fallback &&
        (
          element.tagName === "TEXTAREA" ||
          text.includes("placeholder") ||
          text.includes("prompt") ||
          text.includes("describe") ||
          text.includes("mo ta")
        )
      ) {
        fallback = element;
      }
    }
    return fallback;
  }

  function findPromptCluster(promptInput) {
    if (!(promptInput instanceof Element)) {
      return null;
    }
    let current = promptInput.parentElement;
    while (current && current !== document.body) {
      const hasTextbox = current.querySelector(
        "[data-slate-editor='true'][data-slate-node='value'][role='textbox'][contenteditable='true']"
      );
      const hasModelButton = current.querySelector("button[aria-haspopup='menu']");
      const hasActionButton = Array.from(current.querySelectorAll("button")).some((button) => {
        const text = collectCandidateText(button);
        const htmlText = normalizeText(button.innerHTML || "");
        return (
          isVisible(button) &&
          !button.disabled &&
          button.getAttribute("aria-haspopup") !== "menu" &&
          (htmlText.includes("arrow_forward") || text.includes("tao") || text.includes("generate") || text.includes("create"))
        );
      });
      if (hasTextbox && hasModelButton && hasActionButton) {
        return current;
      }
      current = current.parentElement;
    }
    return null;
  }

  function escapeHtml(value) {
    return String(value || "")
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;");
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

  function setSlateEditorValue(element, value) {
    clickElement(element);
    element.focus();
    selectAllContents(element);

    let inserted = false;
    try {
      inserted = document.execCommand("insertText", false, value);
    } catch (_error) {
      inserted = false;
    }

    if (!inserted) {
      element.innerHTML =
        '<p data-slate-node="element"><span data-slate-node="text"><span data-slate-leaf="true"><span data-slate-string="true">' +
        escapeHtml(value) +
        "</span></span></span></p>";
    }

    element.dispatchEvent(
      new InputEvent("beforeinput", {
        bubbles: true,
        cancelable: true,
        inputType: "insertText",
        data: value
      })
    );
    element.dispatchEvent(
      new InputEvent("input", {
        bubbles: true,
        inputType: "insertText",
        data: value
      })
    );
    element.dispatchEvent(new Event("change", { bubbles: true }));

    const lastTextNode = element.querySelector("[data-slate-string='true']");
    if (lastTextNode) {
      selectAllContents(lastTextNode);
    }
    return normalizeText(element.textContent || "").includes(normalizeText(value));
  }

  function setInputValue(element, value) {
    if (!(element instanceof Element)) {
      return false;
    }
    element.focus();
    if (element instanceof HTMLTextAreaElement || element instanceof HTMLInputElement) {
      const descriptor = Object.getOwnPropertyDescriptor(element.constructor.prototype, "value");
      if (descriptor?.set) {
        descriptor.set.call(element, value);
      } else {
        element.value = value;
      }
      dispatchTextEvents(element);
      return true;
    }
    if (element.getAttribute("contenteditable") === "true" || element.getAttribute("role") === "textbox") {
      element.innerHTML = "";
      element.textContent = value;
      element.dispatchEvent(new InputEvent("beforeinput", { bubbles: true, cancelable: true, inputType: "insertText", data: value }));
      dispatchTextEvents(element);
      return true;
    }
    return false;
  }

  function findGenerateElement(promptInput = null) {
    const phrases = ["generate", "create", "tao", "tạo", "run", "tao anh", "tao hinh", "make"];
    const ignorePhrases = ["new project", "du an moi", "create project", "tao du an", "view guide"];
    const promptCluster = findPromptCluster(promptInput);
    const buttonScope = promptCluster || document;

    const primaryButtons = buttonScope.querySelectorAll("button");

    for (const button of primaryButtons) {
      if (!isVisible(button) || button.disabled) {
        continue;
      }
      const htmlText = normalizeText(button.innerHTML || "");
      if (htmlText.includes("arrow_forward")) {
        return button;
      }
    }

    for (const button of primaryButtons) {
      if (!isVisible(button) || button.disabled) {
        continue;
      }
      const popupType = button.getAttribute("aria-haspopup");
      if (popupType === "menu" || popupType === "dialog") {
        continue;
      }
      const text = collectCandidateText(button);
      const htmlText = normalizeText(button.innerHTML || "");
      const hasArrowIcon = htmlText.includes("arrow_forward");
      if (ignorePhrases.some((phrase) => text.includes(phrase))) {
        continue;
      }
      if (phrases.some((phrase) => text.includes(phrase)) || hasArrowIcon) {
        return button;
      }
    }

    const selectors = ["button", "[role='button']", "a", "[tabindex='0']"];
    const seen = new Set();
    for (const selector of selectors) {
      const nodes = buttonScope.querySelectorAll(selector);
      for (const node of nodes) {
        if (seen.has(node) || !isVisible(node)) {
          continue;
        }
        seen.add(node);
        const text = collectCandidateText(node);
        if (ignorePhrases.some((phrase) => text.includes(phrase))) {
          continue;
        }
        if (node instanceof HTMLButtonElement && node.disabled) {
          continue;
        }
        if (phrases.some((phrase) => text.includes(phrase))) {
          return node;
        }
      }
    }
    return null;
  }

  async function chooseModel(label, promptInput = null) {
    if (!label) {
      return { ok: true, status: "model_skipped" };
    }
    const normalizedLabel = normalizeText(label);
    const promptCluster = findPromptCluster(promptInput);
    const modelScope = promptCluster || document;
    const nativeSelects = document.querySelectorAll("select");
    for (const select of nativeSelects) {
      if (!isVisible(select)) {
        continue;
      }
      const options = Array.from(select.options);
      const option = options.find((item) => normalizeText(item.textContent || item.label || "").includes(normalizedLabel));
      if (option) {
        select.value = option.value;
        dispatchTextEvents(select);
        return { ok: true, status: "model_selected" };
      }
    }

    const menuButtons = modelScope.querySelectorAll("button[aria-haspopup='menu']");
    for (const button of menuButtons) {
      if (!isVisible(button)) {
        continue;
      }
      const text = collectCandidateText(button);
      if (text.includes("nano banana") || text.includes("imagen")) {
        clickElement(button);
        await new Promise((resolve) => setTimeout(resolve, 350));
        const result = await selectModelOption(normalizedLabel);
        if (result.ok) {
          return result;
        }
      }
    }

    const controls = modelScope.querySelectorAll("button, [role='button'], [role='combobox'], div");
    let trigger = null;
    for (const control of controls) {
      if (!isVisible(control)) {
        continue;
      }
      const text = collectCandidateText(control);
      if (text.includes("model") || text.includes("nano banana") || text.includes("imagen")) {
        trigger = control;
        break;
      }
    }
    if (!trigger) {
      return { ok: false, status: "model_trigger_not_found" };
    }

    clickElement(trigger);
    await new Promise((resolve) => setTimeout(resolve, 350));
    return selectModelOption(normalizedLabel, promptCluster);
  }

  async function selectModelOption(normalizedLabel, promptCluster = null) {
    const scope = promptCluster || document;
    const options = document.querySelectorAll(
      "[role='option'], [role='menuitem'], [role='menuitemradio'], [role='radio'], li, button, div"
    );
    for (const option of options) {
      if (!isVisible(option)) {
        continue;
      }
      if (scope !== document && scope.contains(option)) {
        continue;
      }
      const text = collectCandidateText(option);
      if (text.includes(normalizedLabel)) {
        clickElement(option);
        await new Promise((resolve) => setTimeout(resolve, 250));
        return { ok: true, status: "model_selected" };
      }
    }
    return { ok: false, status: "model_not_found" };
  }

  async function runGenerationFlow(payload) {
    const debug = [];
    const prompt = String(payload.prompt || "").trim();
    const modelLabel = String(payload.modelLabel || "").trim();
    if (!prompt) {
      return { ok: false, status: "prompt_empty", debug: ["prompt_empty"] };
    }
    debug.push("prompt_received");

    let promptInput = findPromptInput();
    if (!promptInput) {
      // After generation, Flow may render results and temporarily hide the editor.
      // Wait briefly and retry before giving up.
      await new Promise((resolve) => setTimeout(resolve, 900));
      promptInput = findPromptInput();
    }
    if (!promptInput) {
      return { ok: false, status: "prompt_not_found", debug: debug.concat(["prompt_input_not_found"]) };
    }
    // Ensure the editor is in viewport before interacting with it.
    promptInput.scrollIntoView({ behavior: "instant", block: "center" });
    await new Promise((resolve) => setTimeout(resolve, 200));
    debug.push("prompt_input_found");
    let promptSet = false;
    if (promptInput.getAttribute("data-slate-editor") === "true") {
      const result = await sendPageCommand(
        "FLOW_HELPER_PAGE_TYPE_PROMPT",
        { prompt, speedMs: 20 },
        15000
      );
      if (Array.isArray(result?.debug)) {
        debug.push(...result.debug);
      }
      promptSet = Boolean(result?.ok);
    } else {
      promptSet = setInputValue(promptInput, prompt);
      debug.push(promptSet ? "prompt_input_set" : "prompt_input_set_failed");
    }
    if (!promptSet) {
      return { ok: false, status: "prompt_not_found", debug };
    }
    await new Promise((resolve) => setTimeout(resolve, 250));
    debug.push("prompt_ready");

    const modelResult = await chooseModel(modelLabel, promptInput);
    if (!modelResult.ok && modelResult.status !== "model_skipped") {
      debug.push("model_select_skipped:" + String(modelResult.status || "unknown"));
    } else {
      debug.push(modelResult.status || "model_selected");
    }

    if (promptInput.getAttribute("data-slate-editor") === "true") {
      const submitResult = await sendPageCommand(
        "FLOW_HELPER_PAGE_WAIT_AND_SUBMIT_ENTER",
        { delayMs: 2500 },
        7000
      );
      if (Array.isArray(submitResult?.debug)) {
        debug.push(...submitResult.debug);
      }
      if (!submitResult?.ok) {
        return { ok: false, status: submitResult?.status || "submit_failed", debug };
      }
    } else {
      dispatchEnterEvents(promptInput);
      debug.push("enter_sent");
    }
    await new Promise((resolve) => setTimeout(resolve, 350));
    debug.push("submit_wait_done");
    window.postMessage(
      {
        source: "FLOW_HELPER_CONTENT",
        type: "FLOW_HELPER_FORCE_COLLECT"
      },
      "*"
    );
    return { ok: true, status: "generated", debug: debug.concat(["enter_only_submit"]) };
  }

  async function waitForProjectId(timeoutMs) {
    const startTime = Date.now();
    while (Date.now() - startTime < timeoutMs) {
      const projectId = extractProjectId(window.location.href);
      if (projectId) {
        return {
          capture: {
            projectId,
            source: "url",
            pageUrl: window.location.href
          },
          status: "created"
        };
      }
      await new Promise((resolve) => setTimeout(resolve, 250));
    }
    return {
      capture: {
        projectId: "",
        source: "page",
        pageUrl: window.location.href
      },
      status: "timeout"
    };
  }

  async function createOrDetectProject() {
    const existingProjectId = extractProjectId(window.location.href);
    if (existingProjectId) {
      return {
        capture: {
          projectId: existingProjectId,
          source: "url",
          pageUrl: window.location.href
        },
        status: "detected"
      };
    }

    let trigger = findCreateProjectElement();
    if (!trigger) {
      await new Promise((resolve) => setTimeout(resolve, 800));
      trigger = findCreateProjectElement();
    }
    if (!trigger) {
      return {
        capture: {
          projectId: "",
          source: "page",
          pageUrl: window.location.href
        },
        status: "not_found"
      };
    }

    clickElement(trigger);
    window.postMessage(
      {
        source: "FLOW_HELPER_CONTENT",
        type: "FLOW_HELPER_FORCE_COLLECT"
      },
      "*"
    );
    return waitForProjectId(12000);
  }
})();
