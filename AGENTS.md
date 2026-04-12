# YouTube OS — AutoCapCut

Desktop automation tool for YouTube creators — automates CapCut, manages production workflow, SEO, rendering, upscaling, and AI generation.

---

## Project Context

### Platform
**macOS only** — uses PyAutoGUI, AtomAcos, PyObjC. Do not suggest Linux/Windows alternatives or cross-platform wrappers.

### Tech Stack
- **Frontend:** React + Tauri (Rust shell), port 1420 dev / embedded production
- **Backend:** FastAPI sidecar (Python), port 8765
- **Database:** SQLite via SQLModel
- **Other:** FFmpeg/ffprobe, Real-ESRGAN, WebRTC VAD, MiniMax AI, Rust SRT engine (optional)

### Architecture
```
Tauri (Rust shell)
    └── React UI (port 1420 dev / embedded production)
            ↕ HTTP
    FastAPI sidecar (Python, port 8765)
            ↕
    autocapcut/ package
        ├── api/        — FastAPI routes
        ├── services/   — business logic
        ├── automation/ — CapCut GUI control
        ├── database/   — SQLModel + SQLite
        └── render_v2/  — FFmpeg pipeline
```

### Key Files Map
| File/Folder | Purpose |
|---|---|
| `autocapcut/services/srt_generator.py` | Generate SRT from CapCut draft, align text |
| `autocapcut/services/sync_captions.py` | Sync image durations to captions |
| `autocapcut/services/silence_removal.py` | Cut silence via WebRTC VAD |
| `autocapcut/services/capcut_shortcuts.py` | Keyboard shortcuts for CapCut |
| `autocapcut/services/ffmpeg_render.py` | Batch render via FFmpeg |
| `autocapcut/services/upscale_engine.py` | Real-ESRGAN upscaling |
| `autocapcut/services/seo.py` / `raw_seo.py` | YouTube SEO metadata |
| `autocapcut/services/minimax_audio.py` | AI audio generation (MiniMax) |
| `autocapcut/services/roxy_upload.py` | Upload to Roxy platform |
| `autocapcut/automation/capcut_automation.py` | GUI automation controller |
| `autocapcut/database/models.py` | SQLModel schema definitions |
| `autocapcut/database/connection.py` | DB connection + migrations |
| `frontend/src/store/` | Zustand state stores |
| `rust/` | Optional Rust SRT matching engine |

---

## Dev Workflow

### Run Full App
```bash
cd "YouTube OS" && ./scripts/run_app.sh
```

### Run Backend Only
```bash
cd "YouTube OS" && ./scripts/run_api.sh --port 8765
```

### Run Frontend Dev (HMR)
```bash
cd "YouTube OS/frontend" && npm run dev:api
# Auto-kills old port 8765, starts Python API, then starts Vite
```

### Build for Production
```bash
# Build full Tauri desktop app
./scripts/build_app.sh

# Build Rust SRT engine (optional)
./scripts/build_rust_engine.sh
```

### Python Commands — ALWAYS use .venv
```bash
cd "YouTube OS"
source .venv/bin/activate
python -m pytest tests/

# Or directly:
.venv/bin/python -m pytest tests/
```

### First-time Setup
```bash
cd "YouTube OS"
python3.11 -m venv .venv
.venv/bin/pip install -r requirements.txt
cd frontend && npm install && cd ..
# Optional: build Rust SRT engine
./scripts/build_rust_engine.sh
```
**Prerequisites:** Python 3.11+, Node.js 20+, Rust toolchain, FFmpeg/ffprobe in PATH.

### Environment Variables
| Variable | Values | Default |
|---|---|---|
| `AUTOCAPCUT_SRT_ENGINE` | `auto\|rust\|python` | `auto` |
| `AUTOCAPCUT_SRT_ENGINE_BIN` | path to binary | auto-detect |

---

## Database

- **Engine:** SQLite
- **Location:** `~/.autocapcut/autocapcut.db`
- **Schema:** `autocapcut/database/models.py`
- **Connection:** `autocapcut/database/connection.py`
- **Migrations:** `_ensure_sqlite_columns()` in connection.py — manual ALTER TABLE approach

### Migration Rules
- Never modify `~/.autocapcut/autocapcut.db` directly
- Always test migrations on a copy first
- Enable `/careful` before any DB schema changes

---

## API Routes

Base URL: `http://127.0.0.1:8765/api/`

| Prefix | Router File | Purpose |
|---|---|---|
| `/projects` | `routes/projects.py` | Project management |
| `/parent-projects` | `routes/parent_projects.py` | Parent project CRUD |
| `/child-projects` | `routes/child_projects.py` | Child project CRUD |
| `/sync` | `routes/sync.py` | Caption sync |
| `/srt` | `routes/srt.py` | SRT generation |
| `/seo` | `routes/seo.py` | YouTube SEO metadata |
| `/animation` | `routes/animation.py` | Animation controls |
| `/competitors` | `routes/competitors.py` | Competitor tracking |
| `/audio` | `routes/audio.py` | AI audio generation |
| `/roxy` | `routes/roxy.py` | Roxy platform upload |
| `/ai-gen` | `routes/ai_gen.py` | AI content generation |
| `/livestream` | `routes/livestream.py` | Livestream management |
| `/fast-edit` | `routes/fast_edit.py` | Fast edit workflow |
| `/cut-automate` | `routes/cut_automate.py` | Cut automation |

---

## Frontend Pages & State

### Pages
| Page | Purpose |
|---|---|
| `Projects` | Manage Parent/Child projects |
| `ProductionPlanner` | Kanban/planning for video workflow |
| `RenderDashboard` | Track batch render jobs |
| `Analytics` | Statistics and reporting |
| `Competitors` | Competitor video tracking |

### State Management (Zustand)
- `store/app.store.ts` — app state, splitView, activePanelId
- `store/task.store.ts` — background task tracking
- `store/toast.store.ts` — toast notifications
- **Navigation:** No React Router — use Zustand state (`setMainView` / `setPanelMainView`)

---

## Hooks Configuration

Configured in `.Codex/settings.json`.

| Hook | Trigger | Purpose |
|---|---|---|
| `PreToolUse` | Before any tool runs | Block unsafe operations |
| `PostToolUse` | After Write/Edit tool | Auto lint Python/TypeScript |
| `SessionStart` | On session launch | Load `tasks/lessons.md` |
| `SessionStop` | On session end | Save session summary |
| `PreCommit` | Before git commit | Detect secrets, run tests |

---

## MCP Servers

Configured in `.mcp.json` at project root.

| MCP Server | Purpose | When to Use |
|---|---|---|
| `github` | PRs, issues, repos | Code review, issue tracking |
| `filesystem` | Scoped file access | Safe file operations |
| `playwright` | Browser automation | UI testing |

---

## Workflow Rules

### 1. Plan First
- Enter plan mode for ANY non-trivial task (3+ steps or architectural decisions)
- If something goes sideways → STOP, re-plan immediately, do not keep pushing
- Use plan mode for verification steps, not just building
- Write detailed specs upfront to reduce ambiguity

### 2. Subagent Strategy
- Use subagents liberally to keep main context window clean
- Offload research, exploration, and parallel analysis to subagents
- For complex problems, throw more compute at it via subagents
- One task per subagent for focused execution

### 3. Self-Improvement Loop
- After ANY correction from user → update `tasks/lessons.md` with the pattern
- Write rules for yourself that prevent the same mistake
- Ruthlessly iterate on these lessons until mistake rate drops
- Review `tasks/lessons.md` at the start of every session

### 4. Verification Before Done
- Never mark a task complete without proving it works
- Diff behavior between main and your changes when relevant
- Ask yourself: "Would a staff engineer approve this?"
- Run tests, check logs, demonstrate correctness

### 5. Demand Elegance
- For non-trivial changes: pause and ask "is there a more elegant way?"
- If a fix feels hacky: "Knowing everything I know now, implement the elegant solution"
- Skip this for simple, obvious fixes — don't over-engineer
- Challenge your own work before presenting it

### 6. Autonomous Bug Fixing
- When given a bug report: just fix it, don't ask for hand-holding
- Point at logs, errors, failing tests — then resolve them
- Zero context switching required from the user
- Go fix failing CI tests without being told how

---

## Task Management

1. **Plan First** — write plan to `tasks/todo.md` with checkable items
2. **Verify Plan** — check in before starting implementation
3. **Track Progress** — mark items complete as you go
4. **Explain Changes** — high-level summary at each step
5. **Document Results** — add review section to `tasks/todo.md`
6. **Capture Lessons** — update `tasks/lessons.md` after corrections

### tasks/ Folder Structure
```
tasks/
├── todo.md        — current task plan with checkboxes
├── lessons.md     — self-improvement rules accumulated over time
└── sessions/      — session summaries
```

---

## Git Workflow & Branch Strategy

- **Main branch:** `main` — always stable, never commit directly
- **Feature branches:** `feature/[description]`
- **Bug fix branches:** `fix/[description]`
- **Branch naming:** lowercase, hyphen-separated
- Always run tests before committing
- Never force push to `main`

### Review Checklist Before Commit
- [ ] Tests pass
- [ ] No secrets or API keys in code
- [ ] No `print()` in Python — use `logger`
- [ ] No `console.log()` left in production code
- [ ] Code follows project conventions
- [ ] No unintended files staged
- [ ] Docs updated if behaviour changed

---

## Testing Requirements

- **Unit tests:** `tests/`
- **Run all tests:** `.venv/bin/python -m pytest tests/`
- Write tests before marking any feature complete
- Never skip tests to speed up delivery

---

## Security & Compliance

- Never commit secrets, API keys, or credentials to git
- Use environment variables for all sensitive config
- Validate all external inputs at system boundaries
- No secrets in code or logs
- Credentials / API keys involved → STOP, do not handle autonomously, ask user first

---

## Skill Router — Select the Right Skill for Each Task

> When given a task → identify type → invoke the correct skill. Do NOT do it manually if a skill exists.

### UI / Interface Design

**Standard UI Pipeline — always follow this order:**
1. `/plan-design-review` — rate plan 0–10, fix until standard
2. `/ui-ux-pro-max` — build with 67 styles / 96 palettes / 99 UX guidelines
3. `/design-review` — visual QA after build, find AI slop, fix + screenshot

| Task | Skill Pipeline |
|---|---|
| Fix UI, change layout, adjust interface | `/plan-design-review` → `/ui-ux-pro-max` → `/design-review` |
| Design new screen, add new page | `/plan-design-review` → `/ui-ux-pro-max` → `/design-review` |
| UI looks bad, polish, visual QA | `/design-review` |
| UI ideas, how should this be designed | `/plan-design-review` |
| Create design system, unify colors/fonts | `/design-consultation` → `/ui-ux-pro-max` → `/design-review` |

**UI/UX Standards enforced by `/ui-ux-pro-max`:**
- 67 visual styles available
- 96 color palettes
- 99 UX best practice guidelines
- Spacing, typography, hierarchy, motion — all validated
- AI slop patterns detected and eliminated by `/design-review`

### Debug / Fix Bugs
| Task | Skill |
|---|---|
| Debug, error, not running, why does X... | `/investigate` |
| Fix bug, crash, exception | `/investigate` |

### New Features / Architecture
| Task | Skill |
|---|---|
| Add feature, build new functionality | `/plan-eng-review` |
| Rethink direction, find 10-star version | `/plan-ceo-review` |
| Refactor, clean up code | `/simplify` |
| Code review, review existing code | `/review` |

### Testing / QA
| Task | Skill |
|---|---|
| Test UI, QA app, find and fix bugs | `/qa` |
| Only list bugs, do not fix yet | `/qa-only` |
| Test on real browser | `/setup-browser-cookies` → `/browse` |

### Dangerous Operations
| Task | Skill |
|---|---|
| FFmpeg commands, delete files, DB ops | Enable `/careful` first |
| `rm`, `DROP TABLE`, force push | Enable `/careful` first |
| Maximum safety (careful + directory lock) | `/guard` |
| Debug FE only, don't touch BE | `/freeze` (lock outside `frontend/`) |
| Done debugging, re-open full project | `/unfreeze` |

### Operations / Shipping
| Task | Skill |
|---|---|
| Ship, deploy, create PR | `/ship` |
| Merge PR, wait for CI, verify production | `/land-and-deploy` |
| Update docs after shipping | `/document-release` |
| Weekly review, retrospective | `/retro` |
| Brainstorm, what to do next | `/office-hours` |

### Edge Cases — Complex Situations
| Situation | Approach |
|---|---|
| "improve this", "make it better" (vague) | `/qa-only` to assess first → then decide |
| "this doesn't work" (unclear if UI or logic) | `/qa-only` → UI bug → `/design-review`, logic bug → `/investigate` |
| Don't know what to do next | `/office-hours` before anything else |
| Request too large, scope unclear | `/plan-ceo-review` to define scope first |

### Multi-Domain (Both FE and BE)
| Situation | Pipeline |
|---|---|
| New feature with API + UI | `/plan-eng-review` → build BE → `/plan-design-review` → `/ui-ux-pro-max` → `/design-review` |
| Full app redesign | `/office-hours` → `/plan-ceo-review` → `/design-consultation` → UI pipeline |
| Fix bug + update UI | `/investigate` (root cause) → fix → `/design-review` (check visual impact) |
| High-risk change across many files | `/plan-eng-review` → `/freeze` specific scope → implement in phases |

### After Shipping
| Situation | Pipeline |
|---|---|
| Just shipped new feature | `/qa` (verify) → `/document-release` (update docs) |
| Bug appeared after shipping | `/investigate` (root cause) → fix → `/qa` (regression test) |
| Want to polish after shipping | `/design-review` → fix → `/document-release` |

---

## Context Management

| Context Usage | Action |
|---|---|
| 0–60% | Work normally |
| 60–70% | Monitor usage |
| 70–80% | Run `/compact` |
| 80%+ | `/clear` mandatory |

---

## Core Principles

- **Simplicity First** — make every change as simple as possible, impact minimal code
- **No Laziness** — find root causes, no temporary fixes, senior developer standards
- **Minimal Impact** — changes only touch what's necessary, avoid introducing bugs
- **No fixes without root cause** — Iron Law (Garry Tan)
- **Bad work is worse than no work** — if unsure, stop, clarify, then choose the right skill

---

## Escalation — When to Stop

Stop and report if:
- Tried **3 times** and failed → `STATUS: BLOCKED`
- Unsure about a security-related change → STOP, ask user
- Scope too large to verify → `STATUS: NEEDS_CONTEXT`
- Credentials / API keys involved → STOP immediately

```
STATUS: BLOCKED | NEEDS_CONTEXT
REASON: [1-2 sentence explanation]
ATTEMPTED: [what was tried]
RECOMMENDATION: [what the user should do next]
```

---

## Docs & Runbooks

- `docs/architecture.md` — full architecture diagram + data model
- `docs/runbooks/setup.md` — first-time setup step by step
- `docs/runbooks/capcut-automation.md` — Accessibility config + debug automation
- `docs/runbooks/rust-srt-engine.md` — build and use Rust SRT engine
- `docs/runbooks/ffmpeg-debug.md` — debug FFmpeg, render logs

## Prompt Templates (tools/prompts/)

Use these when adding new patterns to the project:
- `tools/prompts/add-api-route.md` — add new FastAPI route
- `tools/prompts/add-service.md` — add new Python service
- `tools/prompts/add-frontend-page.md` — add new React page

---

## Conventions

- **Python:** type hints required, loguru for logging — never use `print()`
- **FastAPI routes:** async handlers, `Depends(get_session)` for DB
- **React:** functional components, Radix UI primitives, Tailwind classes
- **API calls from frontend:** fetch directly to `http://127.0.0.1:8765/api/...`
- **Navigation:** no React Router — use Zustand state (`setMainView` / `setPanelMainView`)
- **File naming:** lowercase, hyphen-separated for frontend; snake_case for Python

## Do NOT

- Never `pip install` outside `.venv`
- Never modify `~/.autocapcut/autocapcut.db` directly
- Never suggest Windows/Linux paths — macOS only
- Never use `print()` in Python — use `logger.info/debug/warning/error`
- Never add external CSS frameworks outside Tailwind
- Never commit API keys or secrets
- Never force push to `main`
