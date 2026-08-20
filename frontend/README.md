# PulseAI Frontend

Dark, Kimi-inspired interface for the PulseAI news intelligence platform.
Browse events, search articles, chat with cited sources, and generate reports.

## Quick start

```bash
# Prerequisites: Node.js 18+, backend running on port 8090
npm install
npm run dev        # http://localhost:3000
```

## What it does

| Page | What you can do |
|---|---|
| **Login / Register** | Create account or sign in. Password toggle, form validation, auto-redirect if already authenticated. |
| **Search** | Find articles using semantic, keyword, or hybrid search. Results show scores, sources, and dates. |
| **Events** | Browse clustered news events. Confidence indicators, article counts, skeleton loading. |
| **Chat** | Ask questions, get answers with cited sources. SSE streaming shows tokens as they generate. Thinking stages visible during deep-path reasoning. |
| **Reports** | Generate executive intelligence reports on any topic. Choose timeframe, track status. |

## Tech stack

| Concern | Choice |
|---|---|
| Framework | Next.js 16 (App Router, Turbopack) |
| Language | TypeScript |
| Styling | Tailwind CSS v4 (`@theme inline`) |
| Data | TanStack Query v5 |
| Auth | JWT in `localStorage`, Bearer token on all requests |

## Design system

Dark palette with warm orange accent:

| Token | Hex | Role |
|---|---|---|
| `background` | `#0f1117` | Page background |
| `card` | `#181a20` | Card and panel surfaces |
| `primary` | `#ff6b35` | Buttons, active states, accent |
| `success` | `#22c55e` | Resolved, completed |
| `muted` | `#6b7280` | Secondary text |

**Fonts:** Space Grotesk (headings), Inter (body), JetBrains Mono (data).

## Architecture

```
src/
├── app/                    # Pages (login, register, search, events, chat, reports)
├── components/
│   ├── layout/Shell.tsx    # Sidebar navigation + mobile hamburger
│   ├── ui/Toast.tsx        # Global notifications
│   ├── AuthGuard.tsx       # Protected route wrapper
│   └── providers.tsx       # TanStack Query + Toast providers
└── lib/
    ├── api.ts              # API client (auth, search, events, chat, reports)
    └── utils.ts            # cn() class merger, logout helper
```

## Key features

- **Auth:** Login/register with validation. Token in localStorage. AuthGuard
  redirects unauthenticated users. Sign-out in sidebar.
- **Mobile:** Sidebar slides in with overlay on small screens. Hamburger menu.
- **Chat streaming:** `fetch` + `ReadableStream` consumes SSE via POST. Token-by-
  token display with blinking cursor. Thinking stages as animated dots.
- **Toasts:** `useToast()` hook. Auto-dismiss after 4 seconds.

## Environment

| Variable | Default | Description |
|---|---|---|
| `NEXT_PUBLIC_API_URL` | `http://127.0.0.1:8090` | Backend API URL |

## Lint and build

```bash
npm run lint       # ESLint
npm run build      # Production build
```

## React best practices

- `useCallback` on event handlers passed as props
- `React.memo` on `MessageBubble` (avoids re-rendering all messages per token)
- `useRef` for input in streaming callbacks (avoids stale closures)
- Hoisted `Intl.DateTimeFormat` (avoids per-render allocation)
- Ternary conditionals (`? :`) instead of `&&`
