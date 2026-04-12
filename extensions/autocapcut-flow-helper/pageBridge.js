(() => {
  if (window.__soraAutomationPageBridgeLoaded__) {
    return;
  }

  window.__soraAutomationPageBridgeLoaded__ = true;

  const originalFetch = window.fetch;

  window.addEventListener("message", handleCommandMessage);

  window.fetch = async function patchedFetch(input, init) {
    const url = normalizeUrl(typeof input === "string" ? input : input?.url);
    const method = normalizeMethod(init?.method || input?.method);
    const requestBody = extractRequestBody(init?.body);
    const requestHeaders = extractRequestHeaders(input, init);
    const startedAt = Date.now();

    try {
      const response = await originalFetch.apply(this, arguments);
      const responseBody = await extractResponseBody(response);

      emitBridgeEvent({
        type: "fetch",
        url,
        method,
        status: response.status,
        ok: response.ok,
        requestBody,
        requestHeaders,
        responseBody,
        startedAt,
        endedAt: Date.now(),
      });

      return response;
    } catch (error) {
      emitBridgeEvent({
        type: "fetch-error",
        url,
        method,
        requestBody,
        requestHeaders,
        error: error?.message || String(error),
        startedAt,
        endedAt: Date.now(),
      });

      throw error;
    }
  };

  function emitBridgeEvent(payload) {
    window.postMessage(
      {
        source: "SORA_AUTOMATION_BRIDGE",
        payload,
      },
      window.location.origin,
    );
  }

  async function handleCommandMessage(event) {
    if (event.source !== window) {
      return;
    }

    if (event.origin !== window.location.origin) {
      return;
    }

    if (event.data?.source !== "SORA_AUTOMATION_COMMAND") {
      return;
    }

    if (event.data?.payload?.type !== "run-fetch") {
      return;
    }

    const { requestId, url, init } = event.data.payload;

    try {
      const response = await window.fetch(url, init);
      const responseBody = await extractResponseBody(response);
      emitBridgeEvent({
        type: "command-result",
        command: "run-fetch",
        requestId,
        ok: response.ok,
        status: response.status,
        url: normalizeUrl(url),
        responseBody,
      });
    } catch (error) {
      emitBridgeEvent({
        type: "command-error",
        command: "run-fetch",
        requestId,
        url: normalizeUrl(url),
        error: error?.message || String(error),
      });
    }
  }

  function normalizeMethod(method) {
    return String(method || "GET").toUpperCase();
  }

  function normalizeUrl(url) {
    try {
      return new URL(url, window.location.origin).toString();
    } catch {
      return String(url || "");
    }
  }

  function extractRequestBody(body) {
    if (!body) {
      return null;
    }

    if (typeof body === "string") {
      try {
        return JSON.parse(body);
      } catch {
        return body;
      }
    }

    return null;
  }

  function extractRequestHeaders(input, init) {
    const headers = new Headers();

    copyHeaders(headers, input?.headers);
    copyHeaders(headers, init?.headers);

    const normalized = {};
    for (const [key, value] of headers.entries()) {
      normalized[String(key).toLowerCase()] = String(value);
    }

    return Object.keys(normalized).length ? normalized : null;
  }

  function copyHeaders(target, source) {
    if (!source) {
      return;
    }

    try {
      for (const [key, value] of new Headers(source).entries()) {
        target.set(key, value);
      }
    } catch {
      // Ignore malformed header objects.
    }
  }

  async function extractResponseBody(response) {
    try {
      const contentType = response.headers.get("content-type") || "";
      if (contentType.includes("application/json")) {
        return await response.clone().json();
      }

      if (contentType.startsWith("text/")) {
        return await response.clone().text();
      }
    } catch {
      return null;
    }

    return null;
  }
})();
