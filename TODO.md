# TODO

Remaining work, ordered by what a real launch actually depends on.

---

## Blocking a public launch

- [ ] **Replace the placeholder legal documents.** `mobile/app/(auth)/terms.tsx`
      and `privacy.tsx` describe what the software genuinely does, but have not
      been reviewed by a lawyer. A dating product handling sensitive personal
      data needs real Terms, a real Privacy Policy, and a jurisdiction-specific
      data-processing basis.
- [ ] **Real SMTP.** Set credentials and `EMAIL_CONSOLE_FALLBACK=false`, so a
      mail outage surfaces loudly instead of silently logging verification links
      where nobody sees them.
- [ ] **Generate production secrets** and set `APP_ENV=production`. The app
      refuses to boot otherwise, which is the intended behaviour.
- [ ] **TLS everywhere.** Terminate at a proxy; restrict `CORS_ORIGINS` to the
      real origins. Tokens must never travel over plain HTTP.
- [ ] **Managed Postgres and Redis** with backups and point-in-time recovery.
      A dating app losing its matches table is unrecoverable, reputationally.
- [ ] **Run migrations as a release step** rather than from the app container,
      before scaling past one replica.
- [ ] **Content Security Policy on the web build.** Tokens fall back to
      `localStorage` on web, so an XSS hole is a session-theft hole.

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
- [ ] **Trusted-proxy configuration** so `X-Forwarded-For` is only believed from
      the load balancer. Today it is honoured whenever `APP_ENV=production`,
      which is correct only behind a proxy that overwrites it.
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
