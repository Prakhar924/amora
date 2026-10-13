# TODO

Remaining work, ordered by what a real launch actually depends on.

---

## Blocking a public launch

Deployment tooling exists for both a $0 free-tier stack
([DEPLOYMENT-FREE.md](DEPLOYMENT-FREE.md)) and a ~€4.50/mo VPS
([DEPLOYMENT.md](DEPLOYMENT.md)). What remains needs your accounts; nothing
has been provisioned or paid for.

- [ ] **Create the five free accounts** — Supabase, Upstash, Cloudflare R2,
      Resend, Render — and paste their credentials into Render. Checklist in
      DEPLOYMENT-FREE.md. *(Or provision a VPS for the paid path.)*
- [ ] **Decide the email trade-off.** Without a verified sending domain,
      Resend can only deliver to your own address. Either buy a cheap domain
      (~$1–3/yr) or set `REQUIRE_EMAIL_VERIFICATION=false` and accept
      unverified, unrecoverable accounts.
- [ ] **Keep the R2 bucket private.** A public bucket makes every user's
      photos permanently world-readable.
- [ ] **Set the `EXPO_PUBLIC_API_URL` repository variable** and re-run the
      Pages workflow, so the published frontend talks to your backend.
- [ ] **Promote your first admin account** after registering — production has
      no seeded users, and photos stay invisible until a moderator approves
      them.
- [ ] **Test a backup restore.** On the free tier Supabase provides no
      automatic backups at all, so until you run the documented `pg_dump` you
      have none. On the VPS the nightly dump sits on the same host as the
      database, which protects against a dropped table but not a dead server.
- [ ] **Watch the free-tier quotas** if you take that path: Upstash is 10k
      commands/day (the rate limiter fails open past that), Supabase pauses
      after 7 days idle, and Render's 750 instance-hours are consumed quickly
      by any keep-alive pinger.
- [ ] **Replace the placeholder legal documents.** `mobile/app/(auth)/terms.tsx`
      and `privacy.tsx` describe what the software genuinely does, but have not
      been reviewed by a lawyer. A dating product handling sensitive personal
      data needs real Terms, a real Privacy Policy, and a jurisdiction-specific
      data-processing basis.
- [x] **Real SMTP.** Wired for Resend/any SMTP provider in the production
      compose file; `EMAIL_CONSOLE_FALLBACK=false` is now *enforced* in
      production rather than merely recommended.
- [x] **Generate production secrets** — `deploy/generate-secrets.py` produces
      them and `deploy/check-config.py` validates the result before deploying.
- [x] **TLS everywhere.** Caddy terminates TLS with automatic Let's Encrypt
      certificates, which also gives `wss://` for chat. Plain-HTTP origins are
      rejected at boot.
- [x] **Database backups** — a nightly `pg_dump` container with retention.
      Still needs an off-site copy and a tested restore (listed above).
- [x] **Migrations run as a release step** in `deploy/deploy.sh`, before the
      API restarts, so a failed migration never leaves new code on an old schema.
- [x] **Security headers on the API** (CSP, HSTS, nosniff, DENY framing), set
      by both the app and Caddy.
- [ ] **Content Security Policy on the *frontend* build.** GitHub Pages cannot
      set headers, so this needs a `<meta>` CSP or a host that can. Tokens fall
      back to `localStorage` on web, so an XSS hole is a session-theft hole.

## Safety — the next things that actually matter

- [ ] **Automated photo pre-screening** before the human queue (nudity and
      violence classification). The schema is already shaped for it: set a
      photo's `moderation_status` ahead of review and write a verdict into
      `Report.auto_signal`.
- [ ] **Photo verification** ("take this pose") to curb catfishing. Note this is
      *verification that the photos match the account holder*, not identity
      verification — do not describe it as the latter.
- [ ] **Text classification on messages** for harassment and scam patterns,
      surfacing suspected threads to moderators rather than auto-banning.
- [ ] **Moderator audit log.** Every suspension and photo decision should record
      who did it and be reviewable. `moderated_by_id` and `reviewed_by_id`
      already capture the actor; there is no audit view yet.
- [ ] **Appeals flow.** A suspended user is currently told the reason but has no
      route to contest it.
- [ ] **Safety centre** in-app: dating safety guidance, an emergency-contact
      share, and a "share my date plans" feature.
- [ ] **Underage-report fast path.** An `underage` report should jump the queue
      rather than sit behind spam reports.

## Product

- [ ] **Push notifications** (Expo Push). Notifications persist and reach
      connected clients over the WebSocket, but a closed app currently learns
      nothing until it reopens.
- [ ] **Photo reordering UI.** The API supports it (`PUT /profiles/me/photos/order`);
      the app only supports long-press-to-delete.
- [ ] **Undo last swipe.** Users expect it, and a `pass` is currently final
      unless the other person likes them.
- [ ] **"Who liked you"** — the data exists; showing it is a product/monetisation
      decision.
- [ ] **Prompts in the Hinge style** (short answered questions) to give openers
      something better than "hey".
- [ ] **Richer messages**: images in chat, with the same moderation path as
      profile photos.
- [ ] **In-app notification centre.** `GET /notifications` is implemented and
      unused by any screen.
- [ ] **Account deactivation** as a reversible alternative to deletion. The
      `UserStatus.DEACTIVATED` state exists but nothing sets it.

## If you scale past one instance

The free deployment runs a single instance, and two things are only correct
because of that. Both are enforced by configuration today, so this is a
checklist for the day you add a second one.

- [ ] **Set `WS_FANOUT_ENABLED=true`** and move to a Redis that supports
      pub/sub over the TCP protocol. With it false, a chat message reaches
      only clients connected to the sender's own instance — a silent,
      half-working failure rather than an obvious one.
- [ ] **Re-check the database pool.** The app switches to `NullPool` behind a
      transaction pooler, which is right for one instance on a small plan but
      worth revisiting under load.

## Engineering

- [ ] **CI pipeline**: lint, typecheck, `pytest` against a Postgres service
      container, and an Expo bundle check on every push.
- [ ] **Frontend tests.** None yet. The highest-value targets are the API
      client's refresh-and-retry logic, the socket's reconnect/backoff, and the
      optimistic-send reconciliation in the chat screen.
- [ ] **Load-test discovery.** The bounding-box prefilter plus exact haversine
      avoids a PostGIS dependency, but has not been measured beyond ~50 rows.
      Move to PostGIS with a GiST index, or geohash buckets, when it matters.
- [ ] **Sweep unconfirmed photo uploads.** A reserved photo row whose upload
      never completed is never cleaned up. Needs an object-storage lifecycle
      rule plus a periodic job.
- [ ] **Purge expired sessions.** `sessions` grows without bound; revoked and
      expired rows should be deleted periodically.
- [ ] **Structured logging and error tracking** (Sentry or similar). Currently
      plain stdout logging.
- [x] **Trusted-proxy configuration** — uvicorn runs with `--proxy-headers`
      and Caddy sets `trusted_proxies`, so forwarded headers come only from
      the proxy.
- [ ] **Soft-delete sweep.** Unmatched matches and soft-deleted messages are
      retained indefinitely; they need a retention policy.
- [ ] **Metrics**: match rate, message-reply rate, report rate per 1k users —
      the numbers that tell you whether the product is working or being abused.

## Nice to have

- [ ] Internationalisation. All copy is currently hard-coded English.
- [ ] Accessibility audit. Roles and labels are set on interactive elements, but
      no screen-reader pass has been done.
- [ ] Profile completeness meter to nudge better profiles.
- [ ] Shared-interest highlighting on the discovery card.
- [ ] Haptics on native beyond the swipe and match events.
