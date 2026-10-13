# Amora

A dating platform for adults — swipe-based discovery, deterministic mutual
matching, real-time chat, and the trust-and-safety machinery a dating product
needs. Built as an original implementation on an open-source stack: FastAPI and
PostgreSQL on the server, React Native (Expo) for iOS, Android and the web.

> **18+ only.** Age eligibility is enforced from a submitted date of birth at
> registration and again in the discovery query. There is no identity or age
> *verification* — see [Honest limitations](#honest-limitations).

---

## Contents

- [Quick start](#quick-start)
- [What works today](#what-works-today)
- [Architecture](#architecture)
- [Repository layout](#repository-layout)
- [Running the backend](#running-the-backend)
- [Running the app](#running-the-app)
- [Demo accounts](#demo-accounts)
- [Testing](#testing)
- [API reference](#api-reference)
- [Security and privacy design](#security-and-privacy-design)
- [Honest limitations](#honest-limitations)
- [Production deployment](#production-deployment) · full guide in [DEPLOYMENT.md](DEPLOYMENT.md)

---

## Quick start

Requires **Docker** and **Node 20+**.

```bash
git clone <your-remote> amora && cd amora
docker compose up --build
```

That brings up PostgreSQL, Redis, S3-compatible object storage, an SMTP catcher
and the API. Migrations run automatically on boot, and the database is seeded
with synthetic profiles so discovery and chat are usable immediately.

| Service | URL | Notes |
| --- | --- | --- |
| API docs (OpenAPI) | <http://localhost:8000/docs> | Interactive |
| API health | <http://localhost:8000/health> | Liveness; `/health/ready` checks DB, Redis and storage |
| Mailpit inbox | <http://localhost:8025> | Every verification/reset email lands here |
| Object storage | <http://localhost:4566> | LocalStack S3 |
| PostgreSQL | `localhost:5434` | `amora` / `amora` |
| Redis | `localhost:6380` | |

Then start the app:

```bash
cd mobile && npm install && npm run web
```

Open <http://localhost:8081> and sign in with `mira@amora.dev` /
`DemoPass123!`.

> Host ports 5434 and 6380 are used instead of the defaults, because 5432/5433
> and 6379 are commonly already taken. Override with `POSTGRES_PORT_HOST` and
> `REDIS_PORT_HOST`.

---

## What works today

Everything in this list has been exercised end to end against a running stack,
not just written.

**Accounts**
- Registration with an 18+ date-of-birth gate and explicit terms/privacy consent
- Email verification (links delivered to Mailpit locally)
- Login, logout, logout-everywhere, password change, forgot/reset password
- Short-lived JWT access tokens with rotating, hashed-at-rest refresh tokens
- Account deletion that genuinely scrubs personal data

**Profiles**
- Multi-photo upload straight to object storage via presigned URLs
- Bio, work, education, height, pronouns, curated interests
- Location capture, used only to compute distance
- Visibility controls: hide from discovery, or show only to matches

**Discovery and matching**
- Swipe deck with gesture-driven cards (drag, fling, tap to page photos)
- Distance, age and gender filters, all applied in SQL
- Never re-shows a profile you have already judged
- Mutual likes create exactly one match, safely under concurrency
- Match celebration, match list, unmatch

**Chat**
- One-to-one messaging over WebSockets, with a REST fallback
- Persistent history with cursor pagination
- Typing indicators, read receipts, unread counters
- Reconnection with exponential backoff; idempotent sends survive a reconnect
- Only mutually matched, unblocked users can exchange messages — enforced
  server-side on both transports

**Trust and safety**
- Block (mutual, dissolves the match, prevents re-matching)
- Report, with context, validated so you cannot report a stranger's messages
- Photo moderation queue — photos are invisible until approved
- Report queue with repeat-offender counts, suspension and reinstatement
- Rate limits on login, registration, swiping, messaging and reporting

---

## Architecture

```
┌──────────────────────────────────────────────────────────┐
│  Expo app  (iOS · Android · Web)                         │
│  expo-router · NativeWind · Reanimated gestures          │
└───────────────┬──────────────────────┬───────────────────┘
                │ REST (JWT)           │ WebSocket (JWT)
                ▼                      ▼
┌──────────────────────────────────────────────────────────┐
│  FastAPI                                                 │
│  routers → services → SQLAlchemy 2.0 (async)             │
│                                                          │
│  auth · profiles · discovery · matching · chat · safety  │
│  · admin                                                 │
└───┬───────────────┬──────────────┬───────────────────────┘
    │               │              │
    ▼               ▼              ▼
┌─────────┐   ┌──────────┐   ┌──────────────┐
│ Postgres│   │  Redis   │   │  S3 storage  │
│ 15 tbls │   │ limits + │   │   photos     │
│         │   │ WS fanout│   │  (private)   │
└─────────┘   └──────────┘   └──────────────┘
```

### Why this shape

**A modular monolith, not microservices.** Matching, chat and moderation all
read the same handful of tables and need transactional consistency between
them. Splitting them would buy deployment independence at the cost of
distributed transactions, which is the wrong trade at this scale.

**Async all the way down.** Chat is the defining feature, so the stack is
`asyncio` end to end (FastAPI, SQLAlchemy async, asyncpg, redis.asyncio)
rather than mixing sync and async execution models.

**Redis is not optional-but-unused.** It does two real jobs: rate-limit
counters, and pub/sub fan-out so WebSocket delivery works across multiple API
workers. Worker A can deliver a message to a recipient connected to worker B.

**Photos never pass through the API.** Uploads use presigned `PUT`s and reads
use short-lived presigned `GET`s. The API process never streams image bytes, and
the bucket stays private.

### Two decisions worth reading the code for

**Mutual matching is race-safe by construction** (`app/services/matching.py`).
Two users can like each other in the same millisecond, in separate
transactions. Two distinct failures have to be prevented:

- *Duplicate* matches — prevented structurally. The pair is stored in canonical
  order (`user_a_id < user_b_id`) under a `UNIQUE` constraint, so both racing
  transactions attempt a byte-identical row and `ON CONFLICT DO NOTHING` makes
  the loser a no-op.
- *Lost* matches — the subtler one. Under `READ COMMITTED`, neither transaction
  can see the other's uncommitted like, so both would find no reciprocity and
  **no match would be created at all**. A transaction-scoped advisory lock keyed
  on the canonical pair serializes just that pair, so the second transaction
  sees the first's committed like.

  This was caught by `test_simultaneous_mutual_likes_create_exactly_one_match`,
  which failed with "got 0" before the lock was added.

**Location is private by construction** (`app/services/serializers.py`).
Precise coordinates are stored, but there is exactly one function that builds
another user's profile, and it has no access path to them. Distance is rounded
before it reaches a response. Tests assert that the strings `latitude`,
`longitude` and the actual coordinate values never appear in a feed payload.

---

## Repository layout

```
amora/
├── docker-compose.yml          Full local stack
├── backend/
│   ├── Dockerfile              Multi-stage, non-root runtime
│   ├── entrypoint.sh           Waits for Postgres → migrates → serves
│   ├── alembic/                Migrations
│   ├── app/
│   │   ├── main.py             App factory, CORS, lifespan, health
│   │   ├── core/               Settings, security, rate limiting
│   │   ├── db/                 Engine, session, declarative base
│   │   ├── models/             15 SQLAlchemy models
│   │   ├── schemas/            Pydantic request/response contracts
│   │   ├── api/
│   │   │   ├── deps.py         The authorization ladder
│   │   │   └── routers/        auth, profiles, discovery, matches,
│   │   │                       messages, safety, admin, ws
│   │   ├── services/           matching, discovery, chat, storage,
│   │   │                       email, account, serializers, age
│   │   ├── ws/manager.py       WebSocket hub + Redis fan-out
│   │   └── seed.py             Synthetic development data
│   └── tests/                  125 tests
└── mobile/
    ├── app/                    expo-router file-based routes
    │   ├── (auth)/             welcome, register, login, reset, legal
    │   ├── (tabs)/             discover, matches, chats, me
    │   ├── onboarding/         profile setup
    │   ├── chat/[id].tsx       Real-time conversation
    │   ├── match/[id].tsx      Match celebration
    │   ├── profile/[id].tsx    Public profile + report/block
    │   ├── settings/           preferences, privacy, blocked, password
    │   └── admin/              dashboard, photo queue, report queue
    └── src/
        ├── lib/                api client, auth context, socket, types
        └── components/         ui kit, profile card, shell, legal
```

---

## Running the backend

### With Docker (recommended)

```bash
docker compose up --build          # everything
docker compose logs -f api         # follow API logs
docker compose down -v             # stop and wipe data
```

### Without Docker

You still need Postgres, Redis and S3-compatible storage; the easiest path is to
run just those in Docker and the API on the host:

```bash
docker compose up -d postgres redis s3 mailpit

cd backend
python -m venv .venv
.venv/Scripts/activate            # Windows;  source .venv/bin/activate on Unix
pip install -r requirements-dev.txt
cp .env.example .env

alembic upgrade head               # apply migrations
python -m app.seed                 # load demo data
uvicorn app.main:app --reload      # http://localhost:8000/docs
```

### Common tasks

```bash
alembic upgrade head                        # migrate
alembic revision --autogenerate -m "..."    # new migration from model changes
alembic downgrade -1                        # roll back one
python -m app.seed                          # seed (idempotent)
python -m app.seed --reset                  # wipe seeded users, then seed
ruff check . && ruff check --fix .          # lint
pytest                                      # tests
```

---

## Running the app

```bash
cd mobile
npm install
npm run web        # browser
npm run android    # Android emulator or device
npm run ios        # iOS simulator (macOS only)
npm run typecheck  # tsc --noEmit
```

The API base URL is resolved automatically: `localhost` on web and the iOS
simulator, `10.0.2.2` on the Android emulator, and the LAN address Expo is
already serving from when running on a physical device. Override it with
`EXPO_PUBLIC_API_URL`:

```bash
EXPO_PUBLIC_API_URL=http://192.168.1.20:8000/api/v1 npm run web
```

On a browser window wider than 700px the app renders as a centred,
phone-width column — the layouts are designed phone-first, and stretching a
swipe deck across a desktop monitor looks broken.

---

## Demo accounts

Created by the seed script. **All synthetic** — invented names, generated
gradient photos, and coordinates jittered around city centres. No real
person's data is used anywhere.

| Account | Password | Purpose |
| --- | --- | --- |
| `mira@amora.dev` | `DemoPass123!` | Ordinary user, has a match and a conversation |
| `arlo@amora.dev` | `DemoPass123!` | Ordinary user |
| `admin@amora.dev` | `DemoPass123!` | Moderator — unlocks the admin dashboard |
| `unverified@amora.dev` | `DemoPass123!` | Cannot sign in; exercises the verification gate |
| `nophotos@amora.dev` | `DemoPass123!` | Incomplete profile; excluded from discovery |
| `hidden@amora.dev` | `DemoPass123!` | Discovery switched off |
| `suspended@amora.dev` | `DemoPass123!` | Suspended; exercises enforcement |

Roughly 40 further profiles exist across six cities. Any of them signs in with
`DemoPass123!`.

---

## Testing

```bash
cd backend
pytest                      # everything
pytest -m slow              # just the concurrency tests
pytest tests/test_e2e_happy_path.py -v
```

Tests run against a real PostgreSQL database (`amora_test`, created
automatically), not SQLite — the application depends on native enums, `ARRAY`,
`JSONB`, `ON CONFLICT` and advisory locks, so another engine would not be
testing the real thing. Object storage, email and WebSocket delivery are
stubbed; rate limiting is disabled by default and re-enabled per test.

| Suite | Tests | Covers |
| --- | ---: | --- |
| `test_auth.py` | 30 | Registration, the 18+ boundary, consent, password rules, login, refresh rotation and theft detection, recovery |
| `test_discovery.py` | 23 | Every exclusion, age/gender/distance filters, pagination, feed privacy |
| `test_matching.py` | 16 | Mutual matching, duplicate prevention, **concurrent likes**, unmatch |
| `test_chat.py` | 17 | Chat authorization, validation, idempotency, unread counts, pagination |
| `test_safety.py` | 38 | Blocking, reporting, photo moderation, suspension, deletion, profile privacy |
| `test_e2e_happy_path.py` | 1 | The entire product in one journey |
| `test_production_config.py` | 30 | Production guard: secrets, TLS origins, host allow-list, email delivery, seed refusal |
| **Total** | **155** | |

---

## API reference

Interactive docs at `/docs`; the OpenAPI schema is at `/openapi.json`. All 44
endpoints are listed there. Everything except `/auth/*` and `/health` requires
`Authorization: Bearer <access_token>`.

Endpoints are gated by an explicit authorization ladder (`app/api/deps.py`):
*authenticated* → *email verified* → *active (not suspended)* → *profile
complete* → *admin*. Swiping, matching and chat all require a complete profile,
so an empty profile cannot browse while showing a blank card in return.

The WebSocket lives at `ws://localhost:8000/ws/chat?token=<access_token>`.
Frames are JSON discriminated by `type`; the protocol is documented at the top
of `app/api/routers/ws.py`. Every inbound frame is re-authorized — an open
socket is never treated as standing permission to post into a thread.

---

## Security and privacy design

| Concern | How it is handled |
| --- | --- |
| Password storage | bcrypt, SHA-256 pre-hashed so long passphrases are not silently truncated at 72 bytes |
| Access tokens | Short-lived JWTs carrying a `token_version`; bumping it retires every outstanding token with no blocklist |
| Refresh tokens | Long random strings, only a SHA-256 hash stored. Rotated on every use; replaying a consumed token revokes the whole session family |
| Session invalidation | Password change, logout-everywhere and suspension all take effect immediately |
| Account enumeration | Registration, login and forgot-password return identical responses whether or not the address exists |
| Photo access | Private bucket, short-lived presigned URLs, mandatory moderation before a photo is public |
| Location | Coordinates stored but never serialized; only a rounded distance and a city name are exposed |
| Chat authorization | One shared guard used by both REST and WebSocket, so the rule cannot hold on one path and not the other |
| Report integrity | A referenced message must belong to a conversation the reporter is in |
| Moderation trail | Reports filed *against* a user survive that user deleting their account |
| Rate limiting | Redis fixed-window on login, registration, swipes, messages, reports; fails open on a Redis outage rather than locking everyone out |
| Secrets | Entirely environment-driven. The app refuses to boot with `APP_ENV=production` while defaults are in place |
| Container | Multi-stage build, non-root user, healthcheck |

---

## Honest limitations

Stated plainly, because a dating product that overstates its safety is
dangerous:

- **No identity or age verification.** Age eligibility is enforced from a
  self-reported date of birth. Nothing stops a determined user from lying. Real
  verification needs a document or selfie-matching vendor.
- **Photo moderation is human-only.** The schema is ready for an automated
  classifier (`Report.auto_signal`, and a photo's status can be pre-set), but
  none is wired up. Every photo waits for a moderator.
- **No push notifications.** Notifications are persisted and delivered over the
  WebSocket to connected clients; a closed app receives nothing until it
  reopens. Wiring Expo Push is the natural next step.
- **Email delivery is local-only.** Mailpit catches everything in development.
  Production needs real SMTP credentials.
- **No message encryption at rest** beyond what the database provides, and no
  end-to-end encryption. Moderators can read a reported message by design.
- **Discovery ranking is distance-then-recency.** There is no engagement-based
  ranking or recommendation model. That is deliberate: the brief called for
  deterministic rule-based matching first.
- **Web token storage.** On native, tokens live in the device keychain via
  `expo-secure-store`. On the web there is no such API, so they fall back to
  `localStorage`, which any script on the origin can read. Serve the web build
  with a strict CSP.
- **The Terms and Privacy Policy are placeholder product copy.** They describe
  what the software actually does, but they have not been reviewed by a lawyer.

---

## Production deployment

**Not yet deployed.** Everything needed is in the repository; what remains
needs your accounts and about **€4.50/month**.

See **[DEPLOYMENT.md](DEPLOYMENT.md)** for the full walkthrough. In short:

```bash
# 1. Prepare a VPS (installs Docker, firewall, swap, auto-updates)
curl -fsSL https://raw.githubusercontent.com/Prakhar924/amora/main/deploy/provision.sh | bash

# 2. Generate secrets, fill in your values, validate
python deploy/generate-secrets.py -o .env.production
python deploy/check-config.py .env.production

# 3. Deploy
scp .env.production root@YOUR_SERVER:/opt/amora/
./deploy/deploy.sh root@YOUR_SERVER
```

Then set the repository variable `EXPO_PUBLIC_API_URL` to
`https://your-api-domain/api/v1` and re-run the Pages workflow; the frontend
picks up the backend automatically.

### The production stack

| Component | Service | Cost |
| --- | --- | --- |
| API, PostgreSQL, Redis | One small VPS behind Caddy (automatic HTTPS/WSS) | ~€4.50/mo |
| Photo storage | Cloudflare R2 — 10GB, **zero egress** | free |
| Email | Resend — 3,000/mo | free |
| Frontend | GitHub Pages | free |

Self-hosting Postgres and Redis is the deliberate trade: managed equivalents
cost ~$17/month on their own, roughly 4x the entire server. Storage and email
go the other way — their free tiers are genuinely free and self-hosting either
would be worse.

`docker-compose.prod.yml` differs from the development stack in ways that
matter: no Mailpit or LocalStack, no source bind-mount or `--reload`, Postgres
and Redis publish **no host ports**, Redis requires a password, OpenAPI docs
are off, nightly database backups run, and every container has memory limits
and log rotation.

### Enforced, not advisory

The API refuses to boot in production with a default signing key, a
plain-HTTP or localhost CORS origin, a wildcard host allow-list, a console
email fallback, or weak password hashing — among other checks. `deploy/check-config.py`
runs the same guard against your env file *before* you deploy, and
`tests/test_production_config.py` (30 tests) keeps it honest.

**Scaling notes**

The discovery query uses a bounding-box prefilter on an index over
`(latitude, longitude)` plus an exact haversine, which avoids a PostGIS
dependency. At large scale, move to PostGIS with a GiST index, or precompute
geohash buckets. The matching advisory lock is per-pair, so it does not
serialize unrelated swipes.
