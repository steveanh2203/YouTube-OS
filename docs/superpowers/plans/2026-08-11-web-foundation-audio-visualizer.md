# Web Foundation and Audio Visualizer Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Produce the first working self-hosted web milestone: FastAPI serves the React SPA, browser uploads are stored in a safe managed workspace, and Audio Visualizer renders an uploaded audio/image pair with FFmpeg and returns a downloadable MP4.

**Architecture:** Keep the existing React, FastAPI, SQLModel, and Audio Visualizer renderer. Add a bounded workspace and media API, use same-origin frontend URLs, and adapt Audio Visualizer from host paths/Tauri dialogs to media IDs/browser uploads. This milestone deliberately proves one end-to-end vertical slice before migrating every remaining tool.

**Tech Stack:** Python 3.11+, FastAPI, SQLModel/SQLite, FFmpeg/ffprobe, React 19, TypeScript 5.9, Vite 8, Zustand, Tailwind CSS 3.

## Global Constraints

- The first supported self-host host is macOS.
- Run every Python command through `.venv/bin/python`; never install outside `.venv`.
- Keep the existing database at `~/.autocapcut/autocapcut.db`; never edit it directly.
- Bind production to `127.0.0.1` by default because this milestone has no authentication.
- Browser requests may reference media IDs only; never accept arbitrary absolute host paths for the new web flow.
- Resolve and validate every file under `~/.autocapcut/workspace/` or `MASTEROS_DATA_DIR/workspace/`.
- Use type hints and Loguru in Python; do not add `print()`.
- Use functional React components, Zustand navigation, Radix/Tailwind conventions, and no React Router.
- Do not expose command lines, stack traces, tokens, cookies, API keys, or absolute host paths in frontend errors.
- Do not add affiliate integration in this milestone.

## File Map

New runtime units:

- `autocapcut/api/spa.py` — mounts the production React build and SPA fallback.
- `autocapcut/services/workspace.py` — resolves safe workspace paths and streams uploads.
- `autocapcut/api/routes/media.py` — media list/upload/content/download/delete API.
- `frontend/src/lib/api.ts` — same-origin HTTP and WebSocket URL construction.
- `frontend/src/lib/media.ts` — typed client for `/api/media`.
- `frontend/src/components/media/MediaPicker.tsx` — reusable browser upload/library selector.
- `scripts/run_web.sh` — one-command production build and local server startup.

New tests:

- `tests/test_spa.py`
- `tests/test_workspace.py`
- `tests/test_media_routes.py`
- `tests/test_audio_visualizer_web.py`
- `frontend/scripts/web-source-contracts.test.mjs`

Existing files changed:

- `autocapcut/api/server.py`
- `autocapcut/api/routes/__init__.py`
- `autocapcut/api/routes/audio_visualizer.py`
- `autocapcut/services/audio_visualizer.py`
- `autocapcut/database/models.py`
- `frontend/vite.config.ts`
- `frontend/package.json`
- `frontend/src/pages/Projects/tools/AudioVisualizer.tsx`
- `README.md`
- `docs/runbooks/setup.md`
- `tasks/todo.md`

---

### Task 1: Serve the React SPA from FastAPI

**Files:**
- Create: `autocapcut/api/spa.py`
- Create: `tests/test_spa.py`
- Modify: `autocapcut/api/server.py`

**Interfaces:**
- Consumes: `FastAPI`, a resolved frontend distribution `Path`.
- Produces: `install_spa(app: FastAPI, static_dir: Path | None = None) -> bool`.

- [ ] **Step 1: Write failing SPA tests**

```python
class SpaTests(unittest.TestCase):
    def test_install_spa_serves_index_and_assets(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            dist = Path(raw)
            (dist / "assets").mkdir()
            (dist / "index.html").write_text("<main>MasterOS</main>", encoding="utf-8")
            (dist / "assets" / "app.js").write_text("export {}", encoding="utf-8")
            app = FastAPI()
            self.assertTrue(install_spa(app, dist))
            client = TestClient(app)
            self.assertIn("MasterOS", client.get("/planner").text)
            self.assertEqual(client.get("/assets/app.js").status_code, 200)

    def test_install_spa_is_noop_without_build(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            self.assertFalse(install_spa(FastAPI(), Path(raw)))
```

- [ ] **Step 2: Run the tests and verify the missing module failure**

Run: `.venv/bin/python -m unittest tests.test_spa -v`

Expected: FAIL because `autocapcut.api.spa` does not exist.

- [ ] **Step 3: Implement the SPA installer**

```python
FRONTEND_DIST = Path(__file__).resolve().parents[2] / "frontend" / "dist"

def install_spa(app: FastAPI, static_dir: Path | None = None) -> bool:
    root = (static_dir or FRONTEND_DIST).resolve()
    index = root / "index.html"
    if not index.is_file():
        return False
    assets = root / "assets"
    if assets.is_dir():
        app.mount("/assets", StaticFiles(directory=assets), name="assets")

    @app.get("/{requested_path:path}", include_in_schema=False)
    async def spa_fallback(requested_path: str) -> FileResponse:
        candidate = (root / requested_path).resolve()
        if root in candidate.parents and candidate.is_file():
            return FileResponse(candidate)
        return FileResponse(index)

    return True
```

Call `install_spa(app)` only after all API routers and `/api/health` are registered in `server.py`.

- [ ] **Step 4: Run focused and server import tests**

Run: `.venv/bin/python -m unittest tests.test_spa -v`

Run: `.venv/bin/python -c "from autocapcut.api.server import app; print(app.title)"`

Expected: tests PASS and the import prints `MasterOS API`.

- [ ] **Step 5: Commit the runtime slice**

```bash
git add autocapcut/api/spa.py autocapcut/api/server.py tests/test_spa.py
git commit -m "feat(web): serve React SPA from FastAPI"
```

---

### Task 2: Centralize browser API URLs and development proxying

**Files:**
- Create: `frontend/src/lib/api.ts`
- Create: `frontend/scripts/web-source-contracts.test.mjs`
- Modify: `frontend/vite.config.ts`
- Modify: `frontend/package.json`

**Interfaces:**
- Produces: `apiUrl(path: string) -> string`, `wsUrl(path: string) -> string`, and `apiFetch(path: string, init?: RequestInit) -> Promise<Response>`.
- Development contracts: Vite proxies `/api` and `/media` to `http://127.0.0.1:8765`, including WebSocket support on `/api`.

- [ ] **Step 1: Add the failing source-contract test**

```javascript
import test from 'node:test'
import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'

test('web API client and Vite proxy are configured', async () => {
  const api = await readFile(new URL('../src/lib/api.ts', import.meta.url), 'utf8')
  const vite = await readFile(new URL('../vite.config.ts', import.meta.url), 'utf8')
  assert.match(api, /export function apiUrl/)
  assert.match(api, /export function wsUrl/)
  assert.doesNotMatch(api, /127\.0\.0\.1:8765/)
  assert.match(vite, /proxy/)
  assert.match(vite, /'\/api'/)
})
```

Add `"test:web-contracts": "node --test scripts/web-source-contracts.test.mjs"` to `frontend/package.json`.

- [ ] **Step 2: Run the contract and verify failure**

Run: `cd frontend && npm run test:web-contracts`

Expected: FAIL because `src/lib/api.ts` is missing and Vite has no proxy.

- [ ] **Step 3: Implement the shared API client**

```typescript
function normalizePath(path: string): string {
  return path.startsWith('/') ? path : `/${path}`
}

export function apiUrl(path: string): string {
  return normalizePath(path)
}

export function wsUrl(path: string): string {
  const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:'
  return `${protocol}//${window.location.host}${normalizePath(path)}`
}

export function apiFetch(path: string, init?: RequestInit): Promise<Response> {
  return fetch(apiUrl(path), init)
}
```

Add Vite proxy entries for `/api` and `/media`, both targeting `http://127.0.0.1:8765`; set `ws: true` for `/api`.

- [ ] **Step 4: Verify contracts and TypeScript**

Run: `cd frontend && npm run test:web-contracts`

Run: `cd frontend && ./node_modules/.bin/tsc -b --pretty false`

Expected: contract PASS. Record any pre-existing TypeScript errors separately; no new error may originate from `api.ts` or `vite.config.ts`.

- [ ] **Step 5: Commit API configuration**

```bash
git add frontend/src/lib/api.ts frontend/scripts/web-source-contracts.test.mjs frontend/vite.config.ts frontend/package.json
git commit -m "feat(web): add same-origin API client"
```

---

### Task 3: Add safe workspace storage and media records

**Files:**
- Create: `autocapcut/services/workspace.py`
- Create: `tests/test_workspace.py`
- Modify: `autocapcut/database/models.py`

**Interfaces:**
- Produces: `Workspace(root: Path)`, `Workspace.from_environment()`, `get_workspace() -> Workspace`, `Workspace.resolve(relative_path: str) -> Path`, `Workspace.allocate_upload(original_name: str) -> tuple[str, Path]`, and `Workspace.allocate_output(job_id: str, suffix: str) -> tuple[str, Path]`.
- Produces SQLModel `MediaAsset` fields: `id`, `kind`, `original_name`, `relative_path`, `mime_type`, `size_bytes`, `created_at`.

- [ ] **Step 1: Write failing workspace tests**

```python
class WorkspaceTests(unittest.TestCase):
    def test_resolve_rejects_escape(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            workspace = Workspace(Path(raw))
            with self.assertRaises(WorkspacePathError):
                workspace.resolve("../secret.txt")

    def test_allocate_upload_normalizes_filename(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            workspace = Workspace(Path(raw))
            relative, target = workspace.allocate_upload("../../My Voice.mp3")
            self.assertTrue(relative.startswith("uploads/"))
            self.assertEqual(target.parent, Path(raw) / "uploads")
            self.assertEqual(target.suffix, ".mp3")

    def test_allocate_output_is_job_scoped(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            workspace = Workspace(Path(raw))
            relative, target = workspace.allocate_output("abc123", ".mp4")
            self.assertEqual(relative, "outputs/abc123/result.mp4")
            self.assertEqual(target, Path(raw) / relative)
```

- [ ] **Step 2: Run tests and verify failure**

Run: `.venv/bin/python -m unittest tests.test_workspace -v`

Expected: FAIL because `Workspace` does not exist.

- [ ] **Step 3: Implement workspace boundaries and `MediaAsset`**

```python
class WorkspacePathError(ValueError):
    pass

@dataclass(frozen=True)
class Workspace:
    root: Path

    @classmethod
    def from_environment(cls) -> "Workspace":
        data_dir = Path(os.getenv("MASTEROS_DATA_DIR", Path.home() / ".autocapcut"))
        return cls((data_dir / "workspace").resolve())

    def resolve(self, relative_path: str) -> Path:
        if Path(relative_path).is_absolute():
            raise WorkspacePathError("Absolute paths are not allowed")
        candidate = (self.root / relative_path).resolve()
        if candidate != self.root and self.root not in candidate.parents:
            raise WorkspacePathError("Path escapes workspace")
        return candidate

def get_workspace() -> Workspace:
    return Workspace.from_environment()
```

Generate server-owned UUID filenames while retaining the sanitized original name only as metadata. Create upload/output directories on demand. Define `MediaAsset.id` as a UUID hex string primary key and `relative_path` as unique/indexed.

- [ ] **Step 4: Run workspace and database creation tests**

Run: `.venv/bin/python -m unittest tests.test_workspace -v`

Run: `.venv/bin/python -c "from autocapcut.database.models import MediaAsset; print(MediaAsset.__tablename__)"`

Expected: PASS and `media_assets`.

- [ ] **Step 5: Commit workspace primitives**

```bash
git add autocapcut/services/workspace.py autocapcut/database/models.py tests/test_workspace.py
git commit -m "feat(web): add managed media workspace"
```

---

### Task 4: Add media upload, listing, preview, download, and deletion API

**Files:**
- Create: `autocapcut/api/routes/media.py`
- Create: `tests/test_media_routes.py`
- Modify: `autocapcut/api/routes/__init__.py`
- Modify: `autocapcut/api/server.py`

**Interfaces:**
- Produces `POST /api/media/upload`, `GET /api/media/`, `GET /api/media/{id}/content`, `GET /api/media/{id}/download`, and `DELETE /api/media/{id}`.
- Produces response shape `{id, kind, name, mime_type, size_bytes, content_url, download_url, created_at}`.
- Consumes `Workspace`, `MediaAsset`, and `Depends(get_session)`.

- [ ] **Step 1: Write failing route tests with isolated files and mocked session**

```python
class MediaRouteTests(unittest.TestCase):
    def test_upload_streams_to_workspace_and_adds_asset(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            session = AsyncMock()
            upload = UploadFile(filename="voice.mp3", file=io.BytesIO(b"audio"))
            with patch.object(media, "get_workspace", return_value=Workspace(Path(raw))):
                result = asyncio.run(media.upload_media(upload, session))
            self.assertEqual(result.name, "voice.mp3")
            self.assertEqual(result.size_bytes, 5)
            session.add.assert_called_once()
            session.commit.assert_awaited_once()

    def test_content_rejects_missing_file(self) -> None:
        session = AsyncMock()
        session.get.return_value = None
        with self.assertRaises(HTTPException) as ctx:
            asyncio.run(media.get_media_content("missing", session))
        self.assertEqual(ctx.exception.status_code, 404)
```

- [ ] **Step 2: Run tests and verify failure**

Run: `.venv/bin/python -m unittest tests.test_media_routes -v`

Expected: FAIL because the media router is missing.

- [ ] **Step 3: Implement bounded streaming media routes**

Use a fixed 1 MiB read loop:

```python
size = 0
with target.open("wb") as output:
    while chunk := await file.read(1024 * 1024):
        size += len(chunk)
        if size > settings.max_upload_bytes:
            raise HTTPException(status_code=413, detail="Upload exceeds configured limit")
        output.write(chunk)
```

On validation or write failure, delete the partial target. Resolve every response file through `Workspace.resolve(asset.relative_path)`. Delete database metadata only after the file deletion succeeds. Return preview responses with inline content disposition and download responses with attachment disposition.

- [ ] **Step 4: Register the router and run route tests**

Register with `prefix="/api/media"` before installing the SPA fallback.

Run: `.venv/bin/python -m unittest tests.test_media_routes -v`

Run: `.venv/bin/python -c "from autocapcut.api.server import app; print(any(r.path == '/api/media/' for r in app.routes))"`

Expected: tests PASS and route check prints `True`.

- [ ] **Step 5: Commit media API**

```bash
git add autocapcut/api/routes/media.py autocapcut/api/routes/__init__.py autocapcut/api/server.py tests/test_media_routes.py
git commit -m "feat(web): add media library API"
```

---

### Task 5: Build the browser Media Picker

**Files:**
- Create: `frontend/src/lib/media.ts`
- Create: `frontend/src/components/media/MediaPicker.tsx`
- Modify: `frontend/scripts/web-source-contracts.test.mjs`

**Interfaces:**
- Produces `MediaAssetDto` and `mediaApi.list/upload/remove/contentUrl/downloadUrl`.
- Produces `<MediaPicker accept="audio" | "image" value={MediaAssetDto | null} onChange={(asset) => void} />`.
- Consumes `apiFetch` and `apiUrl` from `frontend/src/lib/api.ts`.

- [ ] **Step 1: Extend the failing source-contract test**

```javascript
test('MediaPicker uses browser input and shared media client', async () => {
  const picker = await readFile(new URL('../src/components/media/MediaPicker.tsx', import.meta.url), 'utf8')
  assert.match(picker, /type="file"/)
  assert.match(picker, /mediaApi\.upload/)
  assert.doesNotMatch(picker, /@tauri-apps/)
})
```

- [ ] **Step 2: Run the contract and verify missing component failure**

Run: `cd frontend && npm run test:web-contracts`

Expected: FAIL because `MediaPicker.tsx` is missing.

- [ ] **Step 3: Implement typed media client and picker**

```typescript
export interface MediaAssetDto {
  id: string
  kind: 'audio' | 'image' | 'video' | 'other'
  name: string
  mime_type: string
  size_bytes: number
  content_url: string
  download_url: string
  created_at: string
}

async function upload(file: File): Promise<MediaAssetDto> {
  const body = new FormData()
  body.append('file', file)
  const response = await apiFetch('/api/media/upload', { method: 'POST', body })
  if (!response.ok) throw new Error(await readApiError(response))
  return response.json()
}
```

The picker lists compatible existing assets, uploads through a hidden native browser input, shows upload progress state, and calls `onChange` only after a successful API response. It displays server-sanitized errors through the existing toast store.

- [ ] **Step 4: Verify source contracts, lint, and types**

Run: `cd frontend && npm run test:web-contracts`

Run: `cd frontend && ./node_modules/.bin/eslint src/lib/api.ts src/lib/media.ts src/components/media/MediaPicker.tsx`

Run: `cd frontend && ./node_modules/.bin/tsc -b --pretty false`

Expected: contracts and targeted lint PASS. No new TypeScript errors may originate in the new files.

- [ ] **Step 5: Commit Media Picker**

```bash
git add frontend/src/lib/media.ts frontend/src/components/media/MediaPicker.tsx frontend/scripts/web-source-contracts.test.mjs
git commit -m "feat(web): add browser media picker"
```

---

### Task 6: Adapt Audio Visualizer backend to workspace media IDs

**Files:**
- Create: `tests/test_audio_visualizer_web.py`
- Modify: `autocapcut/api/routes/audio_visualizer.py`
- Modify: `autocapcut/services/audio_visualizer.py`

**Interfaces:**
- `RenderRequest` consumes `audio_media_id: str`, optional `background_media_id: str`, optional `cover_media_id: str`, and `config: dict`.
- `render_visualizer(job_id, audio_path, config, *, output_path: str | None = None) -> None` writes to the supplied workspace output path.
- `JobStatus` and `JobStatusResponse` produce `output_media_id: str | None`; `JobStatusResponse` never exposes `output_path` for the web request path.

- [ ] **Step 1: Write failing web render tests**

```python
class AudioVisualizerWebTests(unittest.TestCase):
    def test_render_request_rejects_unknown_audio_asset(self) -> None:
        session = AsyncMock()
        session.get.return_value = None
        request = audio_visualizer.RenderRequest(audio_media_id="missing", config={})
        with self.assertRaises(HTTPException) as ctx:
            asyncio.run(audio_visualizer.start_render(request, BackgroundTasks(), session))
        self.assertEqual(ctx.exception.status_code, 404)

    def test_renderer_honors_explicit_output_path(self) -> None:
        signature = inspect.signature(render_visualizer)
        self.assertIn("output_path", signature.parameters)
        self.assertEqual(signature.parameters["output_path"].kind, inspect.Parameter.KEYWORD_ONLY)
```

- [ ] **Step 2: Run tests and verify schema/signature failure**

Run: `.venv/bin/python -m unittest tests.test_audio_visualizer_web -v`

Expected: FAIL because the route accepts `audio_path` and the renderer has no `output_path` parameter.

- [ ] **Step 3: Resolve media IDs and allocate workspace output**

In the route, load each `MediaAsset`, verify its kind, resolve it through `Workspace`, create the existing renderer job ID, and allocate `outputs/<job-id>/result.<format>`. Pass only resolved server paths into `VisualizerConfig` and `render_visualizer`.

```python
class RenderRequest(BaseModel):
    audio_media_id: str = Field(min_length=1)
    background_media_id: str | None = None
    cover_media_id: str | None = None
    config: dict = Field(default_factory=dict)
```

Change the renderer output selection to:

```python
resolved_output = Path(output_path) if output_path else (
    audio_p.parent / f"{audio_p.stem}_visualizer.{config.output_format}"
)
resolved_output.parent.mkdir(parents=True, exist_ok=True)
```

Add `output_media_id: str | None = None` to `JobStatus` and its `to_dict()` result. The background wrapper waits for rendering, then opens its own `AsyncSession(async_engine, expire_on_commit=False)` because the request session has already closed. On success it creates `MediaAsset(kind="video", relative_path=output_relative_path)`, commits it, and assigns the new asset ID to `get_job(job_id).output_media_id`. Persist only a sanitized one-sentence error on failure.

- [ ] **Step 4: Run web and existing visualizer regression tests**

Run: `.venv/bin/python -m unittest tests.test_audio_visualizer_web tests.test_audio_visualizer -v`

Expected: all tests PASS.

- [ ] **Step 5: Commit backend adaptation**

```bash
git add autocapcut/api/routes/audio_visualizer.py autocapcut/services/audio_visualizer.py tests/test_audio_visualizer_web.py
git commit -m "feat(web): render visualizer from media assets"
```

---

### Task 7: Convert Audio Visualizer UI from Tauri paths to media assets

**Files:**
- Modify: `frontend/src/pages/Projects/tools/AudioVisualizer.tsx`
- Modify: `frontend/scripts/web-source-contracts.test.mjs`

**Interfaces:**
- Consumes `MediaPicker`, `MediaAssetDto`, `mediaApi`, and `apiFetch`.
- Sends `{audio_media_id, background_media_id, cover_media_id, config}`.
- Uses `/api/media/{id}/content` for preview and `/api/media/{id}/download` for output download.

- [ ] **Step 1: Add failing Audio Visualizer web contracts**

```javascript
test('Audio Visualizer has no Tauri or local-path API contract', async () => {
  const source = await readFile(new URL('../src/pages/Projects/tools/AudioVisualizer.tsx', import.meta.url), 'utf8')
  assert.doesNotMatch(source, /@tauri-apps/)
  assert.doesNotMatch(source, /audio_path:/)
  assert.doesNotMatch(source, /http:\/\/127\.0\.0\.1:8765/)
  assert.match(source, /audio_media_id:/)
  assert.match(source, /<MediaPicker/)
})
```

- [ ] **Step 2: Run contract and verify current failures**

Run: `cd frontend && npm run test:web-contracts`

Expected: FAIL on Tauri imports, hard-coded API, and `audio_path`.

- [ ] **Step 3: Replace path state and dialogs**

Replace `audioPath`/`outputPath` with `audioAsset: MediaAssetDto | null` and `outputAsset: MediaAssetDto | null`. Remove `tauriOpen`, `convertFileSrc`, file-URL normalization, local drag paths, and fallback conversion. Use `mediaApi.contentUrl(audioAsset.id)` for the hidden audio preview.

Render requests must be:

```typescript
body: JSON.stringify({
  audio_media_id: audioAsset.id,
  background_media_id: backgroundAsset?.id ?? null,
  cover_media_id: coverAsset?.id ?? null,
  config: {
    ...config,
    style: 'spectrum_bars',
    background_image_path: null,
    cover_art_path: null,
  },
})
```

When status becomes `done`, read `output_media_id`, fetch that asset, show an HTML `<video controls>` preview, and render an `<a download>` using `mediaApi.downloadUrl(id)`.

- [ ] **Step 4: Verify frontend contracts and targeted lint**

Run: `cd frontend && npm run test:web-contracts`

Run: `cd frontend && ./node_modules/.bin/eslint src/pages/Projects/tools/AudioVisualizer.tsx src/components/media/MediaPicker.tsx src/lib/api.ts src/lib/media.ts`

Run: `cd frontend && npm run build`

Expected: contracts and targeted lint PASS; production build PASS after fixing any touched-file type errors.

- [ ] **Step 5: Commit frontend vertical slice**

```bash
git add frontend/src/pages/Projects/tools/AudioVisualizer.tsx frontend/scripts/web-source-contracts.test.mjs
git commit -m "feat(web): run Audio Visualizer in browser"
```

---

### Task 8: Add one-command web startup and milestone documentation

**Files:**
- Create: `scripts/run_web.sh`
- Modify: `frontend/package.json`
- Modify: `README.md`
- Modify: `docs/runbooks/setup.md`
- Modify: `tasks/todo.md`

**Interfaces:**
- Produces `./scripts/run_web.sh` that installs existing dependencies when absent, builds React, verifies FFmpeg/ffprobe, starts FastAPI on `127.0.0.1:8765`, and preserves all arguments passed to the API entrypoint.

- [ ] **Step 1: Add a failing script contract test**

Extend `frontend/scripts/web-source-contracts.test.mjs` to read `new URL('../../scripts/run_web.sh', import.meta.url)` and assert it contains `npm run build`, `ffmpeg`, `ffprobe`, and `start_api.py`.

- [ ] **Step 2: Run contract and verify missing script failure**

Run: `cd frontend && npm run test:web-contracts`

Expected: FAIL because `scripts/run_web.sh` does not exist.

- [ ] **Step 3: Implement the startup script**

```bash
#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
command -v ffmpeg >/dev/null
command -v ffprobe >/dev/null

if [ ! -d "$ROOT_DIR/.venv" ]; then
  python3.11 -m venv "$ROOT_DIR/.venv"
fi
"$ROOT_DIR/.venv/bin/pip" install -r "$ROOT_DIR/requirements.txt"

cd "$ROOT_DIR/frontend"
if [ ! -d node_modules ]; then npm install; fi
npm run build

cd "$ROOT_DIR"
exec "$ROOT_DIR/.venv/bin/python" start_api.py --host 127.0.0.1 --port 8765 "$@"
```

Document development (`npm run dev:api`), production (`./scripts/run_web.sh`), workspace location, localhost-only security boundary, and the Audio Visualizer smoke path.

- [ ] **Step 4: Run full milestone verification**

Run: `.venv/bin/python -m unittest tests.test_spa tests.test_workspace tests.test_media_routes tests.test_audio_visualizer_web tests.test_audio_visualizer -v`

Run: `cd frontend && npm run test:web-contracts && npm run lint && npm run build`

Run: `./scripts/run_web.sh`

Verify with HTTP: `curl -f http://127.0.0.1:8765/api/health` and `curl -f http://127.0.0.1:8765/`.

Browser smoke sequence:

1. Open `http://127.0.0.1:8765`.
2. Open a child project and Audio Visualizer.
3. Upload one supported audio file and one supported image.
4. Preview the audio and render `spectrum_bars`.
5. Confirm progress advances, cancel works on a second run, MP4 preview loads, and download succeeds.
6. Attempt a media request with `../` and confirm HTTP 400/404 without revealing an absolute path.

Expected: all focused tests PASS, frontend build PASS, health and SPA return HTTP 200, and browser smoke completes.

- [ ] **Step 5: Review diff and commit the milestone**

Run: `git diff --check`

Run: `git status --short`

Confirm no secret, local database, uploaded media, rendered MP4, `node_modules`, `.venv`, or frontend build output is staged.

```bash
git add scripts/run_web.sh frontend/package.json README.md docs/runbooks/setup.md tasks/todo.md
git commit -m "docs(web): document self-hosted milestone"
```

## Milestone Completion Gate

The milestone is complete only when the production FastAPI process serves the SPA and the Audio Visualizer upload-to-download smoke test passes without Tauri. This gate validates the architecture but does not complete the user's request. Execution must continue through persistent unified jobs, migration of every remaining native-dialog tool, configurable extension bridges, and full CapCut dependency removal before the overall web-conversion task can be marked complete.
