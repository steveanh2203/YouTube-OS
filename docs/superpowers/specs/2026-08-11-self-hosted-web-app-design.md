# MasterOS Self-Hosted Web App Design

Date: 2026-08-11
Status: Approved for implementation planning

## Objective

Convert MasterOS from a Tauri desktop application into a single-user, self-hosted web application while preserving the useful production workflows backed by FastAPI, SQLite, and FFmpeg. CapCut automation is removed from the active product. Affiliate integration is outside this version.

The first supported host remains macOS. A user starts one local server, opens MasterOS in a browser, uploads or selects media from a managed workspace, runs processing jobs, and downloads the results.

## Product Scope

### Included

- React browser UI served by FastAPI in production.
- Existing parent/child projects and production-planning data.
- FFmpeg-based editing, rendering, audio, subtitle, visualizer, and upscale workflows that do not require CapCut.
- AI image/audio, Sora, Community, Reply Center, competitors, and other non-CapCut features.
- A browser-based Media Library for uploads, previews, selection, output browsing, and downloads.
- Background processing with progress, cancellation, completion, and failure reporting.
- One-command local startup and documented first-time setup.

### Excluded

- Tauri runtime and native Tauri dialogs.
- PyAutoGUI, AtomAcos, Quartz, and CapCut GUI control in the active application.
- CapCut draft discovery, draft mutation, and CapCut-only navigation.
- Multi-user accounts, roles, remote hosting hardening, billing, and affiliate integration.
- Arbitrary browser access to the host filesystem.

## Architecture

```text
Browser
  |-- React SPA
  |-- Media Library
  |-- Job status via WebSocket with polling fallback
  |
  +---- same-origin HTTP/WS ----> FastAPI
                                 |-- REST and WebSocket API
                                 |-- Static React build
                                 |-- Workspace service
                                 |-- Background job runner
                                 |-- Existing domain services
                                 |
                                 +--> SQLite
                                 +--> FFmpeg / ffprobe
                                 +--> Managed data workspace
```

Development uses Vite on port 1420 with a proxy to FastAPI on port 8765. Production builds the React SPA and serves it from FastAPI, so the user starts only the Python server and visits one URL.

## Runtime Configuration

- The frontend uses same-origin relative paths in production.
- Vite proxies `/api`, `/media`, and WebSocket traffic during development.
- A single frontend API module owns HTTP and WebSocket URL construction.
- The server binds to `127.0.0.1` by default because version one has no authentication.
- Application data keeps the existing `~/.autocapcut/` default to preserve current databases and can be overridden with `MASTEROS_DATA_DIR`.
- Upload limits and allowed media extensions are configurable with safe defaults.

## Managed Workspace

The browser cannot safely choose arbitrary host paths. MasterOS therefore owns a bounded workspace:

```text
~/.autocapcut/
  autocapcut.db
  workspace/
    uploads/
    projects/<project-id>/
    outputs/<job-id>/
    temp/
```

The workspace service provides:

- Chunked streaming uploads through FastAPI `UploadFile` without loading a whole video into memory.
- Folder upload support using browser relative paths when available.
- File listing, metadata, preview, download, rename, and recoverable deletion.
- Stable media IDs so frontend tools pass IDs instead of arbitrary absolute paths.
- Server-side resolution of media IDs to validated paths before invoking services.
- Cleanup of abandoned temporary files.

Every resolved path must remain under the configured workspace root. Absolute paths, `..` traversal, symlink escapes, and unsupported file types are rejected at the API boundary.

## Background Jobs

Long FFmpeg and AI operations run outside request handlers through an in-process single-user job runner.

- Job metadata is persisted in SQLite.
- One active heavy-media job is the safe default; lightweight jobs may run concurrently later.
- Each job records type, inputs, output media IDs, progress, timestamps, status, and sanitized error text.
- The runner retains the subprocess handle for cancellation.
- WebSocket events update the UI; polling remains available after a reconnect.
- On server restart, jobs left in `running` state become `failed` with an interrupted reason. They are never reported as still running.
- Service functions receive resolved `Path` objects and progress/cancellation callbacks, keeping HTTP concerns out of business logic.

## Frontend Changes

- Remove Tauri-only imports and runtime assumptions.
- Replace native open/save dialogs with Media Library selectors and download actions.
- Replace duplicated `http://127.0.0.1:8765` constants with the shared API client.
- Preserve Zustand panel navigation and split-view behavior.
- Remove CapCut-only menu entries and routes from the visible product.
- Keep long-running tool views mounted where their session state currently depends on keep-alive behavior.
- Add explicit empty, uploading, processing, disconnected, failed, and completed states.

## Backend Changes

- Serve the production SPA and provide an SPA fallback without shadowing `/api` or `/media` routes.
- Add workspace/media and unified job endpoints.
- Adapt retained FFmpeg services to accept workspace-resolved inputs and outputs.
- Stop registering CapCut-only routers in the active server.
- Move macOS automation dependencies out of the default web requirements.
- Tighten CORS: Vite development origin only in development; same-origin in production.
- Keep optional Chrome extensions separate. Their bridge URL becomes configurable, with localhost as the default.

## CapCut Removal Strategy

CapCut code is removed from the active dependency graph before physical deletion:

1. Classify routes and UI tools as web-compatible, adaptable, or CapCut-only.
2. Remove CapCut-only navigation and router registration.
3. Verify retained imports do not load automation or PyObjC packages.
4. Remove desktop-only modules and default requirements once no active references remain.
5. Rely on Git history instead of maintaining a legacy runtime path or dead fallbacks.

## Error Handling

- API errors use a consistent JSON envelope with a stable code, safe user message, and optional job ID.
- Raw command lines, stack traces, credentials, cookies, tokens, and host paths are not exposed to the browser.
- FFmpeg failures retain detailed server logs while returning a concise actionable message.
- Upload interruption leaves only a temporary partial file, which cleanup can remove.
- A lost WebSocket does not fail a job; the UI switches to polling and reconnects.
- Missing FFmpeg is detected at startup and shown as a readiness error before a job is accepted.

## Security Boundary

Version one is local single-user software, not an internet-facing service.

- Default host is loopback only.
- CORS wildcard is removed.
- All file operations are workspace-scoped.
- Filenames are normalized; stored paths are generated by the server.
- Upload type and size are validated.
- Secrets remain in environment or local config excluded from Git.
- Documentation warns that exposing the server beyond localhost requires authentication and transport security, which are outside this version.

## Migration Phases

1. **Web foundation:** shared API client, Vite proxy, FastAPI static serving, browser startup scripts, and health/readiness UI.
2. **Media workspace:** schema, media APIs, upload/library UI, preview, and download.
3. **Tool adaptation:** replace Tauri dialogs and arbitrary paths in retained tools, starting with one FFmpeg vertical slice.
4. **Job unification:** persistent background jobs, WebSocket updates, cancellation, and restart recovery.
5. **CapCut deactivation:** remove active UI/routes/dependencies and verify the web server starts without desktop automation packages.
6. **Full migration:** adapt remaining non-CapCut tools, extensions, settings, and documentation.
7. **Release verification:** production build, clean setup, end-to-end smoke tests, and public-repository secret scan.

The vertical slice in phase three is Audio Visualizer: upload an audio file and background image, render the existing FFmpeg-backed visualizer, observe progress, preview the MP4 result, and download it. It proves the architecture before migrating every tool.

## Testing Strategy

- Unit tests for path validation, media records, upload streaming, job transitions, cancellation, and restart recovery.
- Route tests for media, jobs, health/readiness, downloads, and SPA fallback.
- Existing service tests retained for non-CapCut functionality.
- Frontend lint and TypeScript build for all touched files.
- Browser tests for upload, media selection, job progress, errors, cancellation, preview, and download.
- A production smoke test starts FastAPI with the built SPA and completes the FFmpeg vertical slice.
- A dependency smoke test starts the web server without PyAutoGUI, AtomAcos, Quartz, or Tauri installed.

## Acceptance Criteria

- The app opens and navigates in a normal browser without Tauri.
- Production requires one running server process and no Vite dev server.
- No active frontend code imports Tauri APIs.
- No active server startup path imports CapCut/macOS automation dependencies.
- API and WebSocket connections work without hard-coded frontend localhost URLs.
- A user can upload audio and a background image, render Audio Visualizer, observe progress, cancel it, preview the MP4 output, and download the result.
- Existing project/planner data persists in SQLite.
- The server rejects paths outside the managed workspace.
- Automated tests and the production smoke test pass on a clean macOS setup.
