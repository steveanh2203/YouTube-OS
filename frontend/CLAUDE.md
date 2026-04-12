# Frontend — React + Tauri

Tauri 2 desktop app với React 19, Vite, Tailwind CSS, Radix UI.

## Stack
- React 19 + TypeScript
- Vite 8 (dev server port 1420) — confirmed: `"vite": "^8.0.0"` trong package.json
- Tailwind CSS 3
- Radix UI (primitives: Dialog, DropdownMenu, Tabs, Select, Switch, Tooltip, etc.)
- Framer Motion (animations)
- Zustand (state management)
- Lucide React (icons)

## Structure
```
frontend/
├── src/
│   ├── App.tsx            — Root component, split-panel layout + ErrorBoundary
│   ├── components/
│   │   ├── layout/        — TopBar, WorkspacePanel, SplitDivider
│   │   └── ui/            — reusable UI components
│   ├── pages/             — full-page views (Projects, ProductionPlanner, RenderDashboard, Analytics, Competitors)
│   ├── store/             — Zustand stores
│   ├── hooks/             — custom React hooks
│   ├── contexts/          — React contexts
│   └── lib/               — utilities
├── src-tauri/             — Rust/Tauri config
│   └── tauri.conf.json    — app config, window settings
└── public/                — static assets
```

## Dev Commands
```bash
npm run dev:api      # RECOMMENDED: tự start Python API (port 8765) + Vite (port 1420)
npm run dev          # Vite only — cần Python API chạy riêng (xem AutoCapCut/CLAUDE.md)
npm run tauri:dev    # Full Tauri dev (Rust + React hot reload)
npm run build        # Production build
```

## API Calls
Backend tại `http://127.0.0.1:8765`. Gọi trực tiếp bằng fetch:
```typescript
const res = await fetch('http://127.0.0.1:8765/api/projects')
const data = await res.json()
```

## State Pattern (Zustand)
```typescript
import { useAppStore } from '@/store/app.store'
const { splitView, setActivePanelId, panels } = useAppStore()
```

## Navigation
Không dùng React Router. Điều hướng qua Zustand store:
```typescript
import { useAppStore } from '@/store/app.store'
const { setMainView, setPanelMainView } = useAppStore()

// Single panel (legacy):
setMainView('projects')   // 'projects' | 'planner' | 'render' | 'analytics' | 'competitors'

// Dual panel:
setPanelMainView('left', 'render')
setPanelMainView('right', 'projects')
```

## Thêm page mới
1. Tạo `src/pages/MyPage/index.tsx`
2. Thêm view name vào `MainView` type trong `app.store.ts`
3. Import và render trong `WorkspacePanel`

## Component Conventions
- Functional components với TypeScript types
- Radix UI cho interactive primitives (không tự build dropdown/dialog/tooltip)
- Tailwind classes trực tiếp, không CSS modules
- Framer Motion cho animations

## Error Handling / Logging
- Dùng `console.error(err)` cho unexpected errors
- ErrorBoundary trong `App.tsx` bắt toàn bộ render errors và hiển thị inline
- Không có centralized frontend logger — giữ `console.warn/error` nhất quán

## UI Pipeline — Garry Tan / gstack (luôn theo thứ tự này)

Mọi thay đổi UI đều đi qua 3 bước:

```
Bước 1: /plan-design-review
  → Đánh giá ý tưởng/plan trước khi code
  → Rate từng dimension 0-10, fix đến khi đạt chuẩn
  → Chạy ở plan mode, không touch code

Bước 2: /ui-ux-pro-max
  → Build thực sự với database design chuẩn:
     67 styles | 96 palettes | 57 font pairings
     99 UX guidelines | accessibility rules
  → Stack: Tailwind + Radix UI + Framer Motion

Bước 3: /design-review
  → QA mắt designer sau khi build xong
  → Tìm spacing issues, hierarchy problems, AI slop
  → Screenshot before/after, fix từng issue, commit từng cái
```

**Shortcut nhanh:**
- Chỉ cần QA visual đã có → chỉ chạy `/design-review`
- Chỉ cần ý tưởng/plan → chỉ chạy `/plan-design-review`
- Build mới từ đầu → chạy cả 3 bước theo thứ tự
