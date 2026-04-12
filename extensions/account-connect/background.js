// Legacy no-op worker.
// The old session-sync flow used to open Studio comment tabs in the background.
// OAuth bridge mode must never auto-open YouTube Studio tabs anymore.

chrome.runtime.onInstalled.addListener(() => {
  chrome.alarms.clearAll().catch(() => {});
});

chrome.runtime.onStartup?.addListener(() => {
  chrome.alarms.clearAll().catch(() => {});
});

chrome.runtime.onMessage.addListener(() => false);
