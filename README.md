# AI Calendar — Scheduling Chatbot

Conversational assistant that reads **Google Calendar** free/busy data, suggests slots over the **next 2–3 days**, and completes booking via **`events.insert`** (or hands off to **Google Appointment Scheduling** when `GOOGLE_APPOINTMENT_SCHEDULE_URL` is set). Natural language uses **[Pollinations](http://gen.pollinations.ai/docs)** (OpenAI-compatible `chat/completions`: JSON intent extraction + reply text).

The “Google Calendar scheduling interface” in this project is the **Calendar API** (not browser automation of calendar.google.com).

---

## Assignment brief → this repo

| Requirement | How it is met | Where to look |
|-------------|----------------|----------------|
| Conversational UI, preferences, slots, confirm | Next.js chat + slot picker; greets user; collects title/guest/timezone; lists slots; in-chat confirm | `frontend/src/components/ChatInterface.tsx`, `SlotSelector.tsx`, `useChat.ts` |
| NLP / intent | Pollinations OpenAI-compatible API: structured intent + assistant reply | `backend/app/services/ai_service.py`, `integrations/pollinations_client.py` |
| Google Calendar read + availability | Free/busy + busy blocks; configurable window (`AVAILABILITY_WINDOW_DAYS`, default 3) | `backend/app/integrations/google_calendar.py`, `services/scheduling_service.py` |
| Appointment creation | `events.insert` via Calendar API; optional booking-page URL | `scheduling_service.py`, `routes.py` (`/api/schedule/confirm`, `/api/chat`) |
| Connect any test calendar | OAuth to your Google account; `GOOGLE_CALENDAR_ID` (`primary` or specific ID) | `backend/app/api/auth_routes.py`, `.env.example` |
| Error handling | Typed domain errors, HTTP mapping, user-facing banners in chat | `backend/app/core/exceptions.py`, `routes.py`, `frontend` error strings |
| Logging | Colored structured logs, configurable `LOG_LEVEL` | `backend/app/logger.py`, service modules |
| Unit tests | Backend pytest suites + frontend Jest | `backend/tests/`, `frontend/tests/` |
| Security / privacy | Server-only refresh token; HttpOnly sessions; env validation | `env_validation.py`, `google_credentials.py`, section below |

**Out of scope for this submission (bonus / future):** multiple calendar accounts in one UI, Outlook provider, multi-agent NLP orchestration.

### Evaluation criteria (how reviewers can verify)

- **Code quality:** Typed TypeScript and Python; clear split between API routes, services, and integrations; Zustand + React Query on the client.
- **Robustness:** Environment validated at API startup; domain errors mapped to HTTP responses; scheduling chat survives **browser refresh** in the same tab via `sessionStorage` + persisted `conversation_id` (server thread stays aligned).
- **NLP:** Pollinations (OpenAI-compatible) for JSON intent extraction and natural replies; model selectable via `POLLINATIONS_MODEL`.
- **Documentation:** Root README (setup, architecture, security, limitations, demo script); `frontend/README.md` points here.
- **Errors / edge cases:** Calendar access, no slots, conflicts, invalid slot paths covered in backend with user-visible chat context where applicable.
- **Security / privacy:** Google refresh token on server only (gitignored); HttpOnly app sessions when enabled; `SECRET_KEY` strength enforced in production; browser cache limitations documented above.

---

## Architecture

```text
Next.js (localhost:3000)          FastAPI (localhost:8000)
        │                                    │
        ├─ GET  /api/auth/me                 │
        ├─ GET  /api/auth/google/start       │  OAuth (PKCE + server-side verifier backup)
        ├─ GET  /api/auth/google/calendar/start│  Calendar scopes; admin allow-list
        ├─ POST /api/auth/google/calendar/disconnect│  Remove server refresh token (admin)
        ├─ POST /api/chat                      │  Pollinations LLM + SchedulingService → slots
        ├─ POST /api/schedule/confirm          │  create_event (Calendar API)
        └─ GET  /api/calendar/context          │
                     │                           ▼
                     └────────────────── Google Calendar API, Pollinations (`gen.pollinations.ai`)
```

- **Credentials:** Long-lived Google **refresh token** on the API host only (`backend/.google_refresh_token` by default, gitignored). The browser never stores it.
- **Sessions:** In-memory chat `user_sessions` and scheduling `conversation_states` on the API (demo scope; lost on API restart). The browser keeps the **same** `conversation_id` and transcript in **`sessionStorage`** for the current tab so F5 does not orphan the server thread (see `frontend/src/store/schedulingStore.ts`, `utils/chatSessionStorage.ts`).

---

## Prerequisites

| Requirement | Notes |
|-------------|--------|
| Node.js | 20+ (LTS recommended) |
| Python | 3.11+ |
| Google Cloud | OAuth **Web** client; **Google Calendar API** enabled |
| Pollinations | Server API key (`sk_` recommended); set `POLLINATIONS_MODEL` (see `GET https://gen.pollinations.ai/v1/models`). [Docs](http://gen.pollinations.ai/docs), [keys](https://enter.pollinations.ai/) |

---

## Google Cloud & environment

1. Create an OAuth **Web application** client; set **Authorized redirect URI** exactly to  
   `http://localhost:8000/api/auth/google/callback` (or your deployed API URL + `/api/auth/google/callback`).
2. Copy **Client ID** and **Client secret** into `backend/.env` (see `backend/.env.example`).
3. Set **`GOOGLE_REDIRECT_URI`** to that **same** callback URL (no trailing spaces).
4. Set **`FRONTEND_PUBLIC_URL`** to the app origin (e.g. `http://localhost:3000`). After OAuth, the API redirects the browser here with `?signed_in=1`, `?calendar_connected=1`, or `?calendar_error=...`.
5. **`GOOGLE_CALENDAR_ID`:** `primary` or a specific calendar ID.
6. **`GOOGLE_CALENDAR_ADMIN_EMAILS`:** Comma-separated list of Google **account emails** allowed to run **Connect calendar** and to **persist** the server refresh token. The signed-in user’s email (from Google sign-in) must match one entry (case-insensitive). If empty, server calendar linking from the UI is disabled.
7. **`SECRET_KEY`:** Random string used for OAuth `state` JWTs and optional app session cookies.

**OAuth note:** PKCE is stored in an HttpOnly cookie **and** duplicated in a short-lived **server-side map** keyed by `state`, so token exchange still works when the browser drops the cookie on cross-origin `localhost:3000` → `localhost:8000` flows.

### Troubleshooting: `token_exchange_failed` (often Workspace / admin only)

The code path does **not** treat admin emails differently before Google returns tokens. If **one** Google account fails and **another** works, the usual causes are on **Google Cloud** or **Workspace policy**, not this app’s allow-list (`GOOGLE_CALENDAR_ADMIN_EMAILS` is only checked **after** a successful token exchange).

| Cause | What to do |
|--------|------------|
| **OAuth consent in “Testing”** | Only **Test users** (up to ~100) may use the app. Add the **exact** failing user email under **Google Cloud Console → APIs & Services → OAuth consent screen → Audience / Test users**. Personal Gmail may already work; a Workspace admin account often will not until it is added. |
| **Workspace blocks third-party clients** | As Workspace admin: **Security → Access and data control → API controls** (or “Google Workspace updates” for OAuth). Allow the OAuth client or trusted internal apps as required by your org. |
| **Wrong redirect URI** | `GOOGLE_REDIRECT_URI` must match the authorized redirect URI **character-for-character** (scheme, host, port, path). Trailing slashes or `http` vs `https` mismatches cause `invalid_grant`. |
| **Rare grant quirks** | This app no longer sends `include_granted_scopes` on the authorize URL to avoid incremental-consent edge cases. |

Check the **API server logs** after a failure: you should see a line like `Google OAuth token endpoint error` with Google’s `error` and `error_description` (e.g. `invalid_grant`).

---

## Setup (Windows)

Use a new terminal for the API and another for the frontend. If the repo path contains spaces, always `cd` with quotes, e.g. `cd "C:\Users\you\OneDrive\Desktop\AI Calendar\backend"`.

### Backend — PowerShell

```powershell
cd "path\to\AI Calendar\backend"
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
Copy-Item .env.example .env
notepad .env
python -m uvicorn main:app --reload --host 0.0.0.0 --port 8000
```

If activation fails with “running scripts is disabled”:

```powershell
Set-ExecutionPolicy -ExecutionPolicy RemoteSigned -Scope CurrentUser
```

Then run `.\.venv\Scripts\Activate.ps1` again. Leave the API running. To exit the venv: `deactivate`.

### Backend — Command Prompt (CMD)

```bat
cd /d "path\to\AI Calendar\backend"
python -m venv .venv
.venv\Scripts\activate.bat
pip install -r requirements.txt
copy /Y .env.example .env
notepad .env
python -m uvicorn main:app --reload --host 127.0.0.1 --port 8000
```

### Frontend — PowerShell

```powershell
cd "path\to\AI Calendar\frontend"
npm install
Copy-Item .env.local.example .env.local
notepad .env.local
npm run dev
```

Set **`NEXT_PUBLIC_API_URL`** in `.env.local` to the API origin (e.g. `http://localhost:8000`).

### Frontend — CMD

```bat
cd /d "path\to\AI Calendar\frontend"
npm install
copy /Y .env.local.example .env.local
notepad .env.local
npm run dev
```

Open **http://localhost:3000**. Sign in with **Continue with Google**. If your email is in `GOOGLE_CALENDAR_ADMIN_EMAILS`, use **Connect** in the **Admin calendar** panel (compact button with icon) once; the API writes the refresh token file and uses it for chat slots and optional API booking. **Disconnect** in that same panel removes the server token (others will see “Calendar not connected” until an admin reconnects).

### Optional: app password login

If **`APP_LOGIN_USERNAME`** and **`APP_LOGIN_PASSWORD`** are set, the API enforces session cookies for protected routes. The current UI entry point is still **Google sign-in** for the demo; password login is available via **`POST /api/auth/login`** for tooling or a custom UI.

---

## Run & test

1. Start the API (backend venv active, Uvicorn on port 8000).
2. Start the frontend (`npm run dev`, port 3000).
3. Ensure `NEXT_PUBLIC_API_URL` matches the API.

**Backend**

```powershell
cd backend
.\.venv\Scripts\Activate.ps1
pytest tests/ -q
```

**Frontend**

```powershell
cd frontend
npm test -- --watchAll=false
npm run lint
npm run build
```

`npm run build` catches type and production bundle issues before you record a demo or open a PR.

---

## Example usage (demo script)

1. Configure `backend/.env` and `frontend/.env.local` as in [Google Cloud & environment](#google-cloud--environment).
2. Start API and app; open `http://localhost:3000`.
3. **Continue with Google** — use an account listed in `GOOGLE_CALENDAR_ADMIN_EMAILS` if you need **Connect calendar** for server-side free/busy.
4. In chat, ask for a meeting (e.g. “tomorrow afternoon, 30 minutes”). Ask to **show available times** if the assistant requests it.
5. Pick a slot from the in-chat list when shown; confirm in natural language when prompted.
6. Open **View event in Google Calendar** when the assistant returns a link, or use your configured appointment scheduling URL if set.

---

## API (quick reference)

| Method | Path | Purpose |
|--------|------|---------|
| GET | `/api/health` | Liveness |
| GET | `/api/auth/me` | `auth_configured`, `authenticated`, `username`, `can_link_server_calendar` |
| GET | `/api/auth/google/start` | Start Google **sign-in** (returns `authorization_url`, `state`) |
| GET | `/api/auth/google/calendar/start` | Start **calendar** OAuth (requires session + admin email) |
| POST | `/api/auth/google/calendar/disconnect` | Remove server `.google_refresh_token` file (session + admin email only) |
| GET | `/api/auth/google/callback` | Google redirect target (do not open manually) |
| POST | `/api/auth/login` / `/api/auth/logout` | Optional password session |
| GET | `/api/calendar/context` | Resolved calendar + `booking_page_url` if configured |
| POST | `/api/chat` | Body: `conversation_id`, `message`, `user_timezone`; optional `selected_slot_start`, `meeting_title`, `participant_email`, `meeting_description` when merging UI fields into scheduling |
| POST | `/api/schedule/confirm` | Body: `conversation_id`, `selected_slot`, `meeting_title`, optional `participant_email` (omit for self-booking — server uses connected Google account), optional `meeting_description` |

Manual API testing may send `Authorization: Bearer <google_access_token>` and `google_refresh_token` in JSON where supported.

---

## Security & privacy (demo posture)

- Google **refresh token** only on the server; file is gitignored (`backend/.gitignore`).
- App session (when enabled) uses an **HTTP-only** cookie; use a strong **`SECRET_KEY`** and HTTPS in production. `validate_settings` rejects weak secrets when `ENVIRONMENT=production`.
- Calendar scopes: read + events (see `GoogleCalendarService` in code).
- **Browser:** Scheduling fields and chat messages for the active conversation are cached in **`sessionStorage`** (same tab, cleared when the tab closes). Do not treat this as confidential-device storage; use a private browser profile on shared machines.

---

## Known limitations

| Area | Notes |
|------|--------|
| State | In-memory API sessions only (no SQL database); server restart drops `user_sessions` / scheduling state. The client restores the same `conversation_id` and transcript from `sessionStorage` after refresh **only** while the tab stays open. |
| Calendar | One `GOOGLE_CALENDAR_ID` per deployment; not multi-tenant. |
| Booking | Optional `GOOGLE_APPOINTMENT_SCHEDULE_URL` opens Google’s scheduling page from the UI; chat/dialog can still create events via the Calendar API and return an `event_link` (see `POST /api/chat` response). |
| NLP | Single intent JSON pass + one reply model call; not a multi-agent planner. |
| CORS | `main.py` allows `localhost:3000` / `3001`; add your deployed frontend origin before hosting publicly. |

---

## Submission (assignment-style)

| Deliverable | Notes |
|-------------|--------|
| Source | `frontend/` and `backend/` at repo root |
| README | Prerequisites, setup, run, test, architecture, usage, limitations (this file) |
| Video | Record end-to-end (sign-in → chat → slot → confirm / handoff); host per course instructions |
| Deadline | **May 13, 2026** end of day (per brief) |

**Pre-submit checklist**

- [ ] `pytest tests/ -q` (backend) passes.
- [ ] `npm test -- --watchAll=false` and `npm run build` (frontend) pass.
- [ ] No secrets in git: `.env`, `.env.local`, `.google_refresh_token` absent from commits.
- [ ] Google Cloud **Authorized redirect URI** matches `GOOGLE_REDIRECT_URI` exactly.
- [ ] Repository pushed to GitHub (or submission platform); link README at repo root.
- [ ] Demo video uploaded and linked in your submission cover note if required.
