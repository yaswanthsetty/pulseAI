# PulseAI Frontend

Next.js 16 application with a Kimi-inspired dark UI for the PulseAI intelligence platform.

## What you can do

| Page | What it does |
|---|---|
| `/dashboard` | Live pipeline stats, 14-day ingestion chart, trending events by momentum |
| `/login` | Sign in with email/password, password visibility toggle, form validation |
| `/register` | Create account, auto-login after registration |
| `/search` | Semantic, keyword, or hybrid search with mode and intent selectors; save searches |
| `/events` | Browse clustered news events, click for timeline with day-grouped articles |
| `/article/[id]` | Full article view with bookmark toggle, source credibility, event link |
| `/library` | Saved searches, bookmarks, alert rules, and the notification inbox |
| `/chat` | Ask questions about your news corpus, get streaming cited answers |
| `/reports` | Generate executive intelligence reports, export as CSV |
| `/admin` | Manage users and their roles (admin only) |
| `/settings` | Create and revoke API keys |

## Tech stack

- **Next.js 16** (App Router, Turbopack)
- **React 19** with TypeScript
- **Tailwind CSS v4** (CSS-first configuration)
- **TanStack Query** for data fetching and caching
- **cmdk** for command palette (Cmd+K)

## Setup

```bash
npm install
cp .env.local.example .env.local   # set NEXT_PUBLIC_API_URL
npm run dev
```

Open [http://localhost:3000](http://localhost:3000).

## Environment variables

| Variable | Default | Description |
|---|---|---|
| `NEXT_PUBLIC_API_URL` | `http://127.0.0.1:8090` | Backend API URL |

## Architecture

```
src/
├── app/                    # Pages (Next.js App Router)
│   ├── login/page.tsx      # Authentication
│   ├── register/page.tsx   # Account creation
│   ├── search/page.tsx     # Article search
│   ├── events/page.tsx     # Event browser + detail
│   ├── chat/page.tsx       # LLM chat with streaming
│   ├── reports/page.tsx    # Report generation
│   ├── admin/page.tsx      # User management
│   └── settings/page.tsx   # API key management
├── components/
│   ├── layout/
│   │   ├── Shell.tsx           # Sidebar + mobile nav
│   │   └── CommandPalette.tsx  # Cmd+K navigation
│   ├── AuthGuard.tsx       # Protected route wrapper
│   ├── ErrorBoundary.tsx   # Error catching
│   ├── providers.tsx       # Query client + toast provider
│   └── ui/Toast.tsx        # Toast notifications
└── lib/
    ├── api.ts              # Backend API client
    └── utils.ts            # cn() helper, logout
```

## Key features

- **Auth**: JWT-based login, protected routes via AuthGuard, 401 auto-redirect
- **Themes**: Dark UI by default with a light-mode toggle (persisted in localStorage)
- **Mobile**: Hamburger menu sidebar on small screens
- **Chat**: Server-Sent Events streaming, thinking indicators, evidence citations
- **Events**: Split-panel layout — event list with search, detail with timeline
- **Library**: Bookmark articles, save searches, create alert rules, read notifications
- **Dashboard**: CSS-only volume chart, momentum-ranked trending events
- **Command palette**: Press Cmd+K (or Ctrl+K) to navigate anywhere
- **Toast notifications**: Success/error feedback on actions
- **Error boundary**: Catches rendering errors, shows reload option

## Build

```bash
npm run build    # Production build (13 routes)
npm run lint     # ESLint
```

## Docker

`Dockerfile` (at the frontend root) builds a standalone Next.js server. The
production compose file builds it with `NEXT_PUBLIC_API_URL` as a build arg —
client-side code bakes the API origin at build time, so set it before building.
