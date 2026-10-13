# Progress

Status of the Amora build, by phase. Everything marked **Done** has been run
and verified against a live stack — not merely written.

Last updated: 13 October 2026

---

## Summary

| Phase | Status |
| --- | --- |
| 1 — Foundation | ✅ Done |
| 2 — Profiles | ✅ Done |
| 3 — Dating features | ✅ Done |
| 4 — Communication | ✅ Done |
| 5 — Safety and quality | ✅ Done |
| 6 — Finalisation | ◐ Mostly done — production deploy not performed |
| 7 — Production readiness | ◐ Code complete — awaiting your accounts and billing |

**155 backend tests pass. Frontend typechecks and builds. The full stack runs
locally and the complete user journey has been exercised in a browser against
the real API. Production tooling is written and validated locally; nothing has
been deployed and no paid resources exist.**

---

## Phase 0 — Research and strategy ✅

- Inspected the working directory: a drive root holding unrelated projects, so
  this is a greenfield build rather than an extension.
- Evaluated the two suggested open-source projects:

  | Project | License | Stack | Verdict |
  | --- | --- | --- | --- |
  | [duolicious](https://github.com/duolicious/duolicious) | **AGPL-3.0** | Python + Postgres, Expo | Rejected |
  | [alovoa](https://github.com/Alovoa/alovoa) | **AGPL-3.0** | Java/Spring + MariaDB | Rejected |

- **Decision: build new.** Both are AGPL-3.0 — strong copyleft with a network
  clause, which would oblige source disclosure to every user of a hosted
  service. That is incompatible with the stated goal of a commercial product.
  Beyond licensing, duolicious is deliberately *not* swipe-based (the opposite
  of the requested interaction), and alovoa's Java/MariaDB stack conflicts with
  the required FastAPI/PostgreSQL one.
- No code or branding from either project was copied. All dependencies are
  permissively licensed (MIT / BSD / Apache-2.0).

---

## Phase 1 — Foundation ✅

- Monorepo: `backend/` (FastAPI) and `mobile/` (Expo), plus `docker-compose.yml`.
- `docker compose up --build` brings up Postgres, Redis, S3-compatible storage,
  an SMTP catcher and the API; migrations run from the container entrypoint.
- Settings are entirely environment-driven, and `assert_production_ready()`
  refuses to boot with `APP_ENV=production` while dev secrets are in place.
- 15-table normalised schema with foreign keys, unique constraints, check
  constraints and indexes; one Alembic migration, applied and verified in the
  database.
- Authentication: registration with an 18+ gate and explicit consent, email
  verification, login, refresh-token rotation with theft detection, logout,
  logout-everywhere, password change, forgot/reset password, account deletion.

**Verified:** migration applied to a live database; all 17 constraints
confirmed present via `pg_constraint`; 30 auth tests pass.

---

## Phase 2 — Profiles ✅

- Profile fields: display name, bio, gender, pronouns, work, education, height,
  interests (curated vocabulary of 46 tags).
- Photos: up to 6, uploaded straight to object storage via presigned `PUT`,
  confirmed by a `HEAD` so a client cannot register an upload it never made.
- Preferences: interested-in, age range, maximum distance, notification opt-ins.
- Location capture with coordinates stored privately.
- Visibility: hide from discovery, or show only to existing matches.

**Verified:** presigned upload round-trip exercised against LocalStack S3;
a presigned `GET` served a real 30KB PNG with `content-type: image/png`.

---

## Phase 3 — Dating features ✅

- Discovery feed with every exclusion applied in SQL: self, inactive,
  suspended, hidden, incomplete, under-18, already swiped, swiped-on-you-with-a-pass,
  blocked in either direction, plus the viewer's age/gender/distance filters.
- Cursor pagination that never repeats a profile across pages.
- Swipe deck with gesture-driven cards: drag, fling, rotation, LIKE/NOPE
  stamps, tap-to-page photos, optimistic swipes that restore the card on failure.
- Deterministic mutual matching; no AI, as specified.
- Unmatch, which also retracts the like so the pair cannot silently re-match.

**Verified:** 23 discovery tests and 16 matching tests pass, including three
concurrency tests.

### Bugs found and fixed in this phase

1. **Discovery returned zero profiles.** The 18+ hard floor unpacked the wrong
   end of the date-of-birth range, requiring everyone to be over 121. Caught by
   a manual smoke test against the live API.
2. **Simultaneous mutual likes produced no match at all.** Under `READ
   COMMITTED` neither transaction could see the other's uncommitted like, so
   both concluded "not reciprocated". The `ON CONFLICT` guard prevented
   *duplicate* matches but not *lost* ones. Fixed with a transaction-scoped
   advisory lock keyed on the canonical pair. Caught by
   `test_simultaneous_mutual_likes_create_exactly_one_match`.

---

## Phase 4 — Communication ✅

- One-to-one chat over WebSockets with a REST fallback for a broken socket.
- Message persistence, cursor-paginated history, typing indicators, read
  receipts, unread counters.
- Reconnection with exponential backoff and jitter; heartbeats every 25s;
  re-authentication on a policy-violation close rather than retrying a dead
  token.
- Idempotent sends: `(conversation_id, client_nonce)` is unique, so a retry
  after a reconnect cannot duplicate a message.
- Redis pub/sub fan-out so delivery works across multiple API workers.
- Match notifications written in the same transaction as the match.

**Verified:** sent a message from the browser over the WebSocket, confirmed the
bubble rendered and the row persisted in PostgreSQL; 17 chat tests pass.

---

## Phase 5 — Safety and quality ✅

- Block: mutual, dissolves the match, records a mutual pass so neither
  reappears, and hides both profiles from each other.
- Report: with optional conversation/message/photo context, validated so a user
  cannot report a message from a conversation they are not in.
- Photo moderation queue — photos are invisible and the profile is
  undiscoverable until a moderator approves.
- Report queue with repeat-offender counts; resolve, suspend, reinstate.
- Suspension takes effect immediately (sessions revoked, token version bumped).
- Account deletion that scrubs personal data while preserving reports filed
  against the user.
- Rate limits on login, registration, swipes, messages and reports.
- 125 automated tests; `ruff` passes clean.

**Verified:** all 38 safety tests pass, plus the end-to-end journey.

### Bugs found and fixed in this phase

3. **Approving a photo never completed the profile.** The session uses
   `autoflush=False`, so the `COUNT` of approved photos ran before the pending
   status change was flushed and always saw the old state. The same flaw
   affected photo confirmation. Fixed with explicit flushes.
4. **Updating interests returned an empty list.** A second read in the same
   request returned the identity-mapped profile with its already-loaded
   collections. Fixed with `populate_existing`.

---

## Phase 6 — Finalisation ◐

Done:

- Full UI across 20 screens, with real navigation and backend integration —
  no mock data anywhere.
- Original visual identity: warm rose/plum palette, Fraunces display + Inter
  body, soft gradients, fluid card animations. No copied branding.
- Light and dark mode throughout.
- Loading, empty and error states as first-class shared components.
- Responsive web: a centred phone-width column above 700px.
- README, PROGRESS.md, TODO.md.
- Local startup verified from a clean `docker compose up --build`.
- Production deployment *guidance* written (README → Production deployment).

Not done:

- **No production deployment has been performed.** The guidance is written but
  untested against a real host, TLS terminator or managed database.
- No CI pipeline.
- No frontend unit tests (the frontend is verified by typecheck, a successful
  web bundle, and manual browser exercise of the full journey).

### Bugs found and fixed in this phase

5. **NativeWind crashed on web** with "Cannot manually set color scheme".
   Tailwind's default `media` dark mode conflicts with NativeWind's own DOM
   observer. Fixed with `darkMode: 'class'`.
6. **Swipe cards rendered as a diagonal sliver.** Two causes:
   `Dimensions.get('window')` returns 0 at module scope during web's first
   render, collapsing the rotation interpolation range so the card sat at its
   maximum tilt; and NativeWind `className` layout is dropped on an
   `Animated.View` that also receives a `style` prop, collapsing the height.
   Fixed with `useWindowDimensions()` and explicit layout in `style`.
7. **The matches grid stretched across the whole desktop viewport.** Fixed with
   the responsive shell and a percentage tile width.

---

## Phase 7 — Production readiness ◐

Everything needed to host the backend publicly now exists in the repository.
Nothing has been provisioned, deployed, or paid for.

Done:

- **Production stack** (`docker-compose.prod.yml`): Caddy terminating TLS with
  automatic Let's Encrypt certificates — which is what makes `wss://` work for
  chat — plus Postgres and Redis with no published host ports, a Redis
  password, memory limits, log rotation and nightly `pg_dump` backups. No
  Mailpit, no LocalStack, no bind-mount, no `--reload`, no seeding.
- **Boot-time guard**: the API now refuses to start in production with a
  default signing key, default database or storage credentials, debug mode, a
  plain-HTTP or localhost CORS origin, a wildcard host allow-list, plain-HTTP
  storage/frontend URLs, a console email fallback, a development SMTP host, or
  weak bcrypt rounds. Ten distinct checks, each covered by a test.
- **Runtime hardening**: `TrustedHostMiddleware`, security headers (CSP, HSTS,
  `nosniff`, `DENY` framing) set by both the app and Caddy, and OpenAPI docs
  disabled by default in production.
- **`/health/ready`**: verifies database, Redis *and* object storage, returning
  503 when degraded so a deploy can gate on the status code. `/health` stays
  cheap for container liveness.
- **Deployment tooling**: `provision.sh` (Docker, firewall, swap, auto-updates),
  `generate-secrets.py`, `check-config.py` (runs the app's own guard against an
  env file before deploying), and `deploy.sh` (rsync, build, migrate as a
  separate release step, verify readiness; secrets are never transmitted).
- **Frontend connectivity**: `EXPO_PUBLIC_API_URL` wired through the Pages
  workflow as a repository variable, with the build failing if it is not
  `https://`.
- **[DEPLOYMENT.md](DEPLOYMENT.md)**: costed walkthrough, ~€4.50/month.

Not done — needs your accounts and billing approval:

- No server, domain, object storage bucket or email account exists.
- The nightly backup lives on the same host as the database, so it is not yet
  a real backup. An off-site copy and a tested restore are listed in TODO.md.

### Bugs found and fixed in this phase

8. **WebSocket URL derivation was wrong for some configurations.** The regex
   stripped a literal `/api/v1` suffix; given an API URL without one it
   produced `wss://host` with no path, and chat would never connect. Rewritten
   to strip any `/api/vN` and verified across five URL shapes.
9. **Shell scripts were stored with CRLF line endings.** Git on Windows would
   have shipped them to a Linux server where they fail with
   `bash: : command not found` — breaking the very first deploy. Fixed with
   `.gitattributes` and the executable bit set in the index.
10. **`/health/ready` crashed on first use** (missing imports after an earlier
    lint autofix removed them as unused). Caught by calling it against the
    running stack rather than assuming it worked.

---

## Verification log

| What | How | Result |
| --- | --- | --- |
| Schema | `alembic upgrade head` against live Postgres | 15 tables + `alembic_version` |
| Constraints | `pg_constraint` query | 17 unique/check constraints present |
| Seed | `python -m app.seed` | 44 profiles, 2 matches, 4 messages, photos in S3 |
| API surface | `app.openapi()` | 44 endpoints registered |
| Smoke test | Live API, 10 scenarios | All passed, incl. privacy and authorization |
| Backend tests | `pytest` | **155 passed** in 68s |
| Lint | `ruff check .` | All checks passed |
| Frontend types | `tsc --noEmit` | Clean |
| Frontend build | `expo export --platform web` | 2.4MB JS + 20KB CSS |
| Browser journey | Sign in → discover → chat → send → matches | Working, message persisted |
| Production compose | `docker compose -f docker-compose.prod.yml config` | Valid; only Caddy publishes ports |
| Config generator | `generate-secrets.py` → `check-config.py` | Unedited template fails with 8 problems; completed config passes |
| Readiness probe | `GET /health/ready` against live stack | All four dependencies `ok` |
| Security headers | `curl -D -` | CSP, nosniff, DENY, Referrer-Policy present |
| WSS derivation | 5 URL shapes | `https` → `wss`, prefix stripped correctly |
| Line endings | `git show :file` | LF for every script executed on Linux |
| Mobile viewport | 375×812 emulation | Renders correctly |

---

## Known issues

None blocking. Current limitations are documented in the README under
[Honest limitations](README.md#honest-limitations); the remaining work is
tracked in [TODO.md](TODO.md).
