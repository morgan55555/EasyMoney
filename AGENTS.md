# AGENTS.md — EasyMoney

Instructions for AI agents (and humans) working in this repository. Read this file before writing code. The README describes the product; **this file defines the exact semantics** the implementation must satisfy.

## 1. Non-negotiable invariants

1. **Sum-zero.** For every group at any moment, `Σ balance(user, group) = 0` exactly (to the cent/kopeck).
2. **Whole amount.** For every expense, `Σ share(participant) = expense.amount` exactly. No fractional unit is ever lost or created.
3. **Decimal only.** All monetary values are `Decimal` in Python, `NUMERIC` in SQL. Floats are forbidden in money paths (schema, API, split math, frontend display layer uses integer minor units or strings).
4. **Compute on read.** Balances are never stored in mutable columns; they are SQL aggregates over expenses and confirmed settlements. Anything that changes an expense or settlement is automatically reflected in balances.
5. **Server-side split.** Clients send the split *method* and *inputs* (method, participants, weights or exact amounts); the server computes the exact participant shares and is the only writer of them.
6. **Single source of truth per tab.** Every browser tab of the same user receives the same WebSocket events and the same REST responses; no tab-local server state exists.

## 2. Exact semantics

### 2.1 Splitting an expense amount `A` among `n` participants

Money is expressed in minor units (cents/kopecks). Currency precision comes from a small backend map (default 2; 0 for JPY, KRW, VND; 3 for KWD, BHD, OMR, TND). `floor` and remainder always operate in minor units.

**Equal split.**
1. `base = floor(A / n)`, `r = A − n·base` (so `0 ≤ r < n` minor units).
2. Every participant gets `base`.
3. The `r` leftover units are assigned **one per participant** to the `r` earliest-joined participants.
   - Ordering (stable, deterministic): `membership.joined_at` ascending, tie-break `user_id` ascending. The payer is in the rotation like anyone else.
4. Example: `100.00` among 3 → `33.33 × 3 = 99.99`, `r = 1` → shares `33.34, 33.33, 33.33` in join order. Sum = `100.00`. ✓

**Share-weighted split (weights `w₁…wₙ > 0`).** Largest-remainder method:
1. `raw_i = A · w_i / Σw` (exact Decimal arithmetic, sufficient precision).
2. `base_i = floor(raw_i)` in minor units; leftover `r = A − Σ base_i` (`0 ≤ r < n`).
3. Sort participants by `frac(raw_i) = raw_i − base_i` **descending**; tie-break = earliest-joined order (same as 2.1).
4. The top `r` participants in that order get `+1` minor unit each. Sum = `A`. ✓
5. Example: `A = 10.01`, weights `[1, 1, 1]` → `raw = 3.3366…` each → bases `3.33 × 3 = 9.99`, `r = 2`, remainders equal → earliest two joiners get `+0.01` → `3.34, 3.34, 3.33`.

**Exact amounts.** The client provides one amount per participant; the server **rejects** the request (422) unless `Σ amounts = A` exactly. No recomputation.

Editing an expense's amount or participants: shares are recomputed by the same rules (equal: fresh floor/remainder; weighted: the stored weights are kept and re-applied to the new amount; exact: the client must re-supply amounts summing to the new total). The stored participant rows are always the authoritative exact shares.

### 2.2 Balances

For group `G` and member `M`:

```
balance(M, G) =
    Σ_E∈G (paid by M on E)                        -- M as payer
  − Σ_E∈G (M's share on E)                        -- M as participant
  + Σ_S (confirmed S where S.from = M, S.group = G)
  − Σ_S (confirmed S where S.to   = M, S.group = G)
```

- Only **confirmed** settlements count. Pending/rejected/cancelled never do.
- Soft-deleted expenses are excluded from every sum.
- Consequence: `Σ_M balance(M, G) = 0` holds by construction (each expense nets to 0; each settlement nets to 0).
- **Cross-group view** (`GET /balances/all`): one row per (user, currency) summing the member's balances over all their groups in that currency. No currency conversion.

### 2.3 Pairwise debts & the optimizer

From net balances, build debtors (`balance < 0`, amount `= −balance`) and creditors (`balance > 0`). Suggested transfers:

```
sort debtors by amount desc, creditors by amount desc   (tie-break user_id asc)
i = j = 0
while i < |D| and j < |C|:
    amt = min(D[i].amount, C[j].amount)
    emit (D[i].user, C[j].user, amt)
    D[i].amount -= amt;  C[j].amount -= amt
    if D[i].amount == 0: i += 1
    if C[j].amount == 0: j += 1
```

This yields at most `n−1` transfers whose total equals the total debt. The result is advisory (UI suggestion); actual settlement is only via the settlement flow (2.4).

### 2.4 Settlement lifecycle

States: `pending → confirmed | rejected | cancelled` (all three terminal).

| Transition | Who | Effect |
|---|---|---|
| create `pending` | debtor (any member who owes the recipient in the current pairwise view) | Validated at proposal: `amount > 0` and `amount ≤ pairwise_debt(from → to) − Σ pending proposals (from → to, same group)` (else 422). The sender's still-pending proposals to the same recipient eat into the headroom, so stacked proposals can never exceed the current pairwise debt — the max sendable sum decreases with every active proposal. No balance effect. Both parties see it as *pending* (in-app + WebSocket). |
| `→ confirmed` | recipient only | The amount is applied to balances (from +, to −). Both parties notified. |
| `→ rejected` | recipient only | Terminal. Sender notified. |
| `→ cancelled` | sender only | Terminal. Recipient notified. |

Note: balances move only on confirmation, and they are computed on read, so if the debt shrinks between proposal and confirmation (someone else settled, an expense was edited/deleted), the confirmed payment is still applied in full and may create a **reverse debt** — that is correct: the money really moved. The pending-headroom cap is a proposal-time guard only: it bounds what may be *proposed* (any terminal transition — confirm/reject/cancel — releases the headroom that proposal was holding), never what may be *confirmed* (no re-validation at confirmation).

### 2.5 Editing / deleting expenses

- Edit: payer, amount, date, category, comment, participants, split — any of these (creator or group owner only). Recompute shares per 2.1, write, emit `expense.updated`, push fresh balance snapshot.
- Delete: **soft delete** (`deleted_at`). Excluded from all sums. Emits `expense.deleted`. Feed keeps the row ("Alice deleted an expense"). If the expense had a receipt, the `Attachment` row and the file under `UPLOAD_DIR` are removed with it (row in the same transaction, file post-commit; a missing file is ignored).
- No recalculation job is needed: because of invariant 4, the next read is already correct, and the pushed snapshot makes it immediate for connected tabs.

### 2.6 Notifications (in-app)

`Notification` row (per user) + WebSocket push. Triggers:

| Event | Notified users |
|---|---|
| Expense created | payer ∪ all participants (dedup) |
| Expense updated | same set, computed from the updated expense |
| Expense deleted | same set, computed from the deleted expense (the feed row stays, §2.5; the receipt dies with it, §3.5) |
| Settlement proposed | sender + recipient |
| Settlement confirmed / rejected / cancelled | sender + recipient |
| Member joined / left | all members **except the actor** (a user is never notified about their own join/leave) |
| Group closed / reopened | all members |

Notifications are read via the bell; mark-read is explicit (`POST /notifications/read`). Marking read is a per-user state change that is not group-scoped, so it syncs across tabs via a dedicated `notification.read` WS event (user-scoped fan-out, post-commit): every open tab of the user refetches the bell and clears its unread badge — the read state never depends on the 60 s polling safety net.

### 2.7 Overdue debts & reminder emails

Definitions (all per group `G`, member `M`):

- **Owed expense**: an active (non-deleted) expense `E` in `G` where `M` is a participant and `share(M, E) > paid(M, E)`. Its debt portion is `share − paid`.
- **FIFO attribution**: confirmed settlements sent by `M` in `G` are applied to `M`'s owed expenses in ascending `(E.date, E.id)` order, covering the oldest first. (This is a bookkeeping attribution for aging only; it does not change balances, which are pure aggregates.)
- `M`'s **debt age** = the date of the oldest owed expense of `M` in `G` that is not fully covered by FIFO attribution.
- `M` is **overdue** in `G` ⇔ `balance(M, G) < 0` **and** `now − debt_age > DEBT_OVERDUE_DAYS` (default 7).

Scheduler (APScheduler, hourly):
1. Find all (M, G) overdue.
2. Skip if `REMINDER_INTERVAL_DAYS` (default 7) have not passed since `last_reminder_sent_at(M, G)` (table `debt_reminder(group_id, debtor_id, last_sent_at)`, unique per pair).
3. **Re-verify at send time** (fresh transaction): if `balance(M, G) ≥ 0` or no longer overdue → skip. Never remind about a settled debt.
4. Send one email to `M` listing, per creditor, the current pairwise amounts (from 2.3), the total, and the group name. Amounts are rendered with the currency code (`12.50 USD`) and the email links to the group page (`FRONTEND_URL/groups/{id}`). Mark `last_reminder_sent_at = now`.

Reminders apply to **closed groups too** until settled (the close summary is not a settlement).

**Single-flight across replicas.** With several backend replicas behind a load balancer the hourly scan is guarded by a Redis lock (`easymoney:lock:overdue-reminder-scan`, `SET <instance_id> NX EX SCHEDULER_LOCK_TTL_S`, release = compare-and-delete by owner so a stale lock of a dead replica is never removed) — exactly one replica runs the scan at a time. With `REDIS_URL` unset the lock is a no-op (a single process already guarantees single-flight). The lock fails open on a Redis error: the scan runs, and the `last_reminder_sent_at` interval still caps repeats per (member, group) pair.

### 2.8 Concurrent writes

- Two members adding expenses simultaneously: two independent transactions; both commit (no shared mutable row). Balances are aggregates, so the sum-zero invariant and the "both recorded" requirement hold without locking.
- Optimistic locking is not needed for expenses. The only contended writes are settlement state transitions (single row, guarded by `WHERE state = 'pending'` → 409 if someone else acted first) and group status transitions.

### 2.9 Group close / reopen

- `POST /groups/{id}/close` — **owner only** (`group.created_by`). Sets `status = closed`, `closed_at`. Sends one **final summary email** per member, addressed to that member by name: per-member balance (**all members, zero-balance ones included** — it is a complete statement, not just the outstanding debts), who-owes-whom pairs, suggested transfers (2.3), or "everyone is settled up" if all zero. Amounts are rendered with the currency code (`12.50 USD`) and the email links to the group page (`FRONTEND_URL/groups/{id}`). Emits `group.closed`.
- While closed: `POST /groups/{id}/expenses` and expense receipt upload → **409**. Everything else (settlements, invites, leaving, read endpoints) still works.
- `POST /groups/{id}/reopen` — owner only. `status = active`, `closed_at = null`. Emits `group.reopened`. No email.

### 2.10 Invitations

- `Invitation`: `group_id`, `token` (URL-safe, unique, 128-bit), `kind` (`link` | `email`), `email_to` (email kind only), `created_by`, `expires_at = now + INVITE_EXPIRY_DAYS` (default 7), `status` (`active/used/revoked`), `used_by`, `used_at`.
- **Link invites**: reusable until revoked or expired (`status` stays `active` for everyone's benefit). Any authenticated user who opens `FRONTEND_URL/invite/{token}` joins the group; `used_by`/`used_at` record the most recent joiner for display only. A user already in the group gets 409 "already a member".
- **Email invites**: same token mechanism, single-use for the addressed email. The email contains the invite link. If the recipient has no account, the frontend offers registration; after registering **with that exact email**, accepting the token joins the group (token remains bound to that email).
- Accept endpoint: `POST /invites/accept {token}` → validates (exists, active, not expired, [email matches if email kind], user not already member) → inserts `Membership(joined_at = now)` → emits `member.joined` to all members.
- Revoking an active invite deletes/invalidates it; already-joined members are unaffected.

### 2.11 Auth & multi-tab behavior

- Registration: `email` (unique, case-insensitive), `password` (min 8 chars), `display_name` (defaults to local part of email, editable).
- Login → `access_token` (JWT, 15 min, `sub = user_id`) + `refresh_token` (JWT, 7 days, `jti` persisted in `refresh_tokens` table).
- Refresh rotates: old `jti` revoked, new pair issued. Logout revokes the presented `jti`.
- Multi-tab: the same access token is valid in every tab (stateless). Each tab opens its own WebSocket authenticated by the access token (validated once at handshake; socket dropped if the token expires mid-session — client re-authenticates with a fresh token). Because every event is broadcast to all sockets of the user, and every REST response is computed on read, **tabs never diverge** — this is requirement 7.

### 2.12 Currency handling

- Each group has exactly one `currency` (ISO 4217 code, set at creation, immutable — enforced by schema).
- Precision map (minor digits): default 2; 0 → JPY, KRW, VND; 3 → KWD, BHD, OMR, TND. Applied to: rounding in splits, serialization (always send money as **strings** in the API, e.g. `"33.34"`), and frontend formatting.
- Serialized strings always carry **exactly `minor_digits` decimals**: `JPY 0 → "0"`, `USD 0 → "0.00"`, `KWD 1.5 → "1.500"`. A zero with no decimal part in a zero-digit currency is correct, not a bug.
- No conversion between currencies anywhere, including the cross-group view.

### 2.13 Profiles, verification, admin & mail controls

**User profile.** A `User` carries, beyond `email` / `password_hash` / `display_name`:
- `is_admin: bool` (default `false`) — whether the user may use the admin panel.
- `email_verified: bool` (default `true`) — whether the address is confirmed.
- `preferred_language: "en" | "ru"` (default `"en"`) — the language of that user's emails and the UI language inside the app.
- `avatar_data: text | null` — base64-encoded image (no `data:` prefix).
- `avatar_content_type: text | null` — e.g. `image/png` (matches `avatar_data`).

**Avatars.** Stored as base64 in the DB. `POST /api/me/avatar` (multipart `file`, image/*, ≤ `MAX_AVATAR_MB`) sets them; `DELETE /api/me/avatar` clears them. `GET /api/users/{id}/avatar` streams the image (any authenticated user; 404 when the user has none). User-list payloads carry only a short `avatar_url` (`/api/users/{id}/avatar`, or `null`) to stay light; the avatar is rendered wherever a username appears (header, members, balances, settlements, feed, participants).

**Registration restriction.** `REGISTRATION_OPEN` (default `true`). When `false`, `POST /api/auth/register` succeeds only while the `users` table is empty (the first user); once any user exists it returns **403**. `GET /api/auth/status` (public) returns `{ registration_open: bool }` = `REGISTRATION_OPEN AND (users table empty)`; the frontend hides the register form when it is `false`. The first-ever registrant on a fresh DB always becomes admin (§ below).

**Registration verification.** Active ⇔ `SMTP_ENABLED AND EMAIL_REGISTER_CONFIRMATION`. When active, a new user is created with `email_verified = false`, a `register` email token is issued and mailed, and login works but the **frontend blocks app access** until `email_verified` is true (full-screen verify prompt with resend). When not active, the user is created with `email_verified = true` immediately. Confirmation tokens live in `email_tokens` (`purpose = "register"`); `POST /api/auth/email/confirm {token}` marks the user verified and the token `used`; `POST /api/auth/email/resend` (authenticated) reissues + resends the user's pending token.

**Email-change confirmation (two emails).** Active ⇔ `SMTP_ENABLED AND EMAIL_CHANGE_CONFIRMATION`. `POST /api/me/email/change {new_email}`:
- active → issue **two** tokens and return `{ status: "confirmation_sent" }` (the new email is **not** applied yet):
  1. a `change` **confirm** token, mailed to the **new** address (`target_email` = the new email, TTL `CONFIRMATION_TOKEN_HOURS`). `POST /api/auth/email/confirm {token}` applies the swap and marks it `used`.
  2. a `revert` **"this wasn't me"** token, mailed to the **previous** address (`target_email` = the old email, TTL `EMAIL_CHANGE_REVERT_DAYS`). `POST /api/auth/email/revert {token}` reverts the change and marks it `used`.
- not active → apply the new email immediately and return `{ status: "changed" }`.
The two tokens are linked by `(user_id, purpose)`. The **revert** is a *full* revert and stays live for the whole `EMAIL_CHANGE_REVERT_DAYS` window: while the paired change is still pending it cancels it (revokes the `change` token, no email moves); if the new address has already confirmed the swap, it switches the account email **back** to the previous one (409 if that address is now taken by another user). Confirming a change deliberately leaves the `revert` token pending so the old-address owner can still undo it. Emails are normalized to lowercase; a change to the email already in use is a 409. The target address must be unique among users.

**Password-change confirmation.** Active ⇔ `SMTP_ENABLED AND PASSWORD_CHANGE_CONFIRMATION`. `POST /api/me/password/change {new_password}` (≥ 8 chars; a new password equal to the current one is a 409):
- active → create a `password` email token carrying the argon2 hash of the new password, mail the confirmation link to the user's **current** address, and return `{ status: "confirmation_sent" }` (the password is **not** changed yet). `POST /api/auth/email/confirm {token}` then applies the pending hash and marks the token `used`.
- not active → apply the new password immediately and return `{ status: "changed" }`.
A new change request revokes the previous pending `password` token (the reissue rule below); only the latest requested password can ever be applied.

**Confirmation tokens.** One table `email_tokens` serves all four confirmations (register, email change, email-change revert, password change): `id`, `user_id` (FK, CASCADE), `purpose` (`register` | `change` | `revert` | `password`), `token` (URL-safe, unique, 128-bit), `target_email` (the address the link is mailed to: current email for `register`/`password`, the **new** email for `change`, the **previous** email for `revert`), `password_hash` (nullable, only on `password` tokens — the pending new-password hash, same type as `users.password_hash`), `status` (`pending` | `used` | `revoked`), `expires_at` (default `now() + CONFIRMATION_TOKEN_HOURS`; `revert` tokens use `now() + EMAIL_CHANGE_REVERT_DAYS`). A token is valid while `status = "pending"` and unexpired; confirming/reverting marks it `used`. Reissuing (resend / a new change request) revokes the previous `pending` token. `resend` applies to register tokens only; the change and password flows are reissued by a fresh request.

**Admin model.** The **first-ever registered user** becomes admin (`is_admin = true`); on an existing DB, a migration backfills `is_admin` on the lowest-id user if no admin exists. Admins (any `is_admin` user) may, **without email confirmation**: create users (`POST /api/admin/users`, 409 on duplicate email), and edit any user's `display_name` / `email` / `password` / `is_admin` (`PATCH /api/admin/users/{id}`). Guard: the **last remaining admin cannot be demoted or have their admin bit removed** (lockout protection). Admins get a **statistics** endpoint (`GET /api/admin/stats`).

**Preferred language vs. UI language.** `preferred_language` is a server-side user setting, changed **only** in user settings. It (a) selects the language of that user's outbound emails (EN or RU; recipients without an account, e.g. invitees, default to EN) and (b) **is** the UI language inside the app: on load the authenticated shell adopts `me.preferred_language` (state only, never persisted), so every account's preference takes effect and switching accounts switches the language. There is **no** language switcher in the app header. Outside the app (login / register / invite pages) a language selector (client, localStorage) lets the visitor read the page in either language; it defaults to the browser language (any `ru*` tag → RU, else EN) and the chosen language is sent as `preferred_language` with the registration request. Initial language on any page: the stored selector choice if present, else the browser language, else `en`.

**Theme (dark/light/system).** A client-only preference (`light` | `dark` | `system`, default `system`), persisted in localStorage. `darkMode: "class"`: `light`/`dark` apply/remove a `.dark` class on `<html>`; `system` follows `prefers-color-scheme` (via a `matchMedia` listener). Not stored server-side, never part of the API.

**SMTP master switch.** `SMTP_ENABLED` (default `true`) is the kill switch for all outbound mail: when `false`, `send_mail` is a pure no-op — nothing is sent, nothing logged — **even when `SMTP_HOST` is set**, and all confirmation flows (registration, email change, password change) are skipped (they apply immediately). `SMTP_HOST` unset (with the switch on) → log-only dev mode as before. Every mail path still goes through `services/mail.py`.

### 2.14 Browser (Web) notifications (Web Push)

OS-level notifications are delivered via **Web Push** (a service worker + VAPID keys + a push subscription), so they arrive **even when the app is fully closed** — no WebSocket required. The former in-page `new Notification` path is removed.

- **Two parallel systems.** The in-app system — the bell, the unread badge, and the sound on `notification.created` (§2.6, §3.2) — is **unchanged and always fires**. OS-level notifications are an **additive** layer delivered through Web Push. One failing or absent does not affect the other.
- **Display condition.** An OS notification is shown **only when all** of the following hold: (1) the user's client-only *Browser notifications* preference is **on**; (2) an active push subscription exists for the user (creating one implies the browser push permission is granted); (3) **no** app window is visible — the service worker checks `self.clients.matchAll({ type: 'window' })` and suppresses the OS notification when any app tab has `visibilityState === 'visible'`. When the app is a visible tab, the bell badge + sound already inform the user, so no OS notification is raised (no double-alerting). With no clients at all (app closed) the notification shows.
- **Text.** The service worker and the bell share one text source: the pure `makeDescribe` + `invitedToken` + `notificationTarget` helpers (`frontend/src/lib/notificationText.ts`) and the locale dictionaries via `frontend/src/i18n/core.ts` (a React-free module so the SW bundle can reuse them). So bell and OS notification render identical text. The push payload carries `lang` = the user's **preferred_language** at send time, so the notification renders in the user's language even if the browser UI is in another. Title = the group name from the payload when present, else the app title.
- **Click.** The service worker's `notificationclick` closes the notification, focuses an existing app window (or opens one) and navigates to the same target the bell's View/Join buttons use: `/groups/{group_id}?tab={tab}&focus={focus}` for group-scoped rows, `/invite/{token}` for `invited` rows, else just focus.
- **Preference & permission.** The preference is **client-only** (localStorage, default off, cross-tab synced), never part of the API — like the theme (§2.13). The browser push-permission prompt is raised **only inside the user's Settings-toggle gesture** via `PushManager.subscribe()`; it is never requested on page load or by the service worker. Turning the toggle off unsubscribes (local + server) and clears the preference; the permission itself is never revoked programmatically.
- **Sound is unaffected:** `playNotification()` still runs on every `notification.created`, visible or not.
- **Server side.** A `push_subscriptions` table holds one row per (user, browser push endpoint). Endpoints: `GET /api/push/vapid-public-key` (public; **404 when push is disabled**), `POST /api/push/subscribe` (upsert by `(user_id, endpoint)`), `POST /api/push/unsubscribe` (idempotent delete). The push fires **post-commit** from the notification emit path (§2.6) as a fire-and-forget task with its own DB session; it never blocks or fails the triggering request. It runs once, on the replica that wrote the data (so each notification is pushed exactly once in multi-replica mode), and also when the user has no open sockets. Subscriptions dead at the push service (HTTP 404/410) are pruned; other errors are logged and swallowed. VAPID keys come from env (`VAPID_PUBLIC_KEY`, `VAPID_PRIVATE_KEY`, `VAPID_SUBJECT`); with both keys unset push is **disabled** (public-key endpoint 404, frontend shows the unsupported hint, bell/sound unchanged).
- **Multi-tab (req 7).** The preference is per-browser (localStorage, synced via the `storage` event); the service worker applies the same visibility rule for all windows, so tabs stay consistent.

## 3. Architecture & conventions

### 3.1 Backend layout

```
app/
  main.py            # create_app(), lifespan (redis client, bus, scheduler), mount WS
  core/config.py     # pydantic-settings Settings (all env vars)
  core/security.py   # argon2 hash/verify, JWT create/decode, deps (get_current_user)
  core/deps.py       # get_db, get_current_user, get_group_member, get_group_owner
  core/redis_client.py  # process-cached Redis client (None = local mode, §3.2)
  db/session.py      # async engine/session
  db/base.py         # Declarative base, mixins (id, created_at, updated_at)
  models/*.py        # one module per aggregate; relationships explicit
  schemas/*.py       # request/response Pydantic models; money fields as Decimal or str
  api/*.py           # thin routers: parse/validate → call service → map errors
  services/*.py      # business logic, takes a DB session; the ONLY layer that
                     #   knows split/balance formulas
  ws/bus.py          # EventBus: LocalBus / RedisBus cross-replica fan-out (§3.2)
  ws/user_groups.py  # WS-handshake group-list cache (fail-open, §3.2)
  ws/hook.py         # post-commit emit: group events + the bell, and the Web Push
                     #   fire-and-forget task (services/push.push_notification_row, §2.14)
  workers/scheduler.py  # APScheduler job: overdue scan (2.7), single-flight via lock
  workers/lock.py    # single-flight Redis lock for the scheduler (§2.7)
```

`backend/scripts/generate_vapid.py` prints ready-to-paste
`VAPID_PUBLIC_KEY` / `VAPID_PRIVATE_KEY` lines for `.env` (§2.14).

Rules:
- Routers never import ORM models directly beyond typing; they call services. Services never touch FastAPI request objects.
- Every money-changing operation happens in **one transaction**: state change + feed event + notification rows + (WS push is post-commit, via a registry callback).
- Errors: 401 unauthenticated, 403 not a member / not owner, 404 not found, 409 illegal state (e.g. expense on closed group, settlement transition race, duplicate member), 422 validation (e.g. exact split doesn't sum to total).
- Money in JSON is always a string. `date` fields are ISO-8601. Timestamps stored UTC.
- `JWT_SECRET` is validated at boot (`core/config.py`): empty → auto-generate in `DEBUG`, fail otherwise; a weak secret (shorter than 32 chars or a known dev default) → warn in `DEBUG`, fail otherwise. Production must set a strong secret.

### 3.2 WebSocket protocol

- Endpoint `WS /ws?token=<access token>`. On success the server subscribes the socket to the user's group set (and adds a group dynamically when the user joins one mid-session).
- Server→client messages: `{"type": "<event>", "group_id": ..., "data": {...}}`. `data` always contains everything the client needs to update UI without an extra REST call.

**Event bus (multi-replica).** Fan-out goes through an event bus (`app/ws/bus.py`) rather than straight to the in-process registry: `LocalBus` when `REDIS_URL` is unset (exactly the pre-Redis behaviour) and `RedisBus` when it is — a PUBLISH to the single channel `easymoney:events`, consumed by **every** replica including the publisher (symmetric delivery: a publisher never touches its own registry directly, so one code path serves both modes). Envelope: one JSON per publish carrying the already-serialized wire message, so subscribers forward bytes without re-serializing. Mid-session join/leave are control messages on the same channel — every replica (re)subscribes **its own** sockets; a leaver's tabs stop receiving the group immediately (a deliberate fix of the pre-Redis leaver-tab leak). Delivery is **at-most-once** (Pub/Sub drops messages to absent subscribers); the backstop is the client's REST refetch on reconnect. On a PUBLISH failure the replica logs the error and delivers to its own registry (no event is silently lost in single-replica mode). The WS handshake reads the user's group set through the `easymoney:user_groups:<user_id>` cache (TTL `USER_GROUPS_CACHE_TTL_S`, invalidated on the same post-commit paths that change membership, fail-open to the DB query). The group-balance snapshot served by `GET /groups/{id}/balances` rides the `easymoney:snapshot:<group_id>` cache (TTL `SNAPSHOT_CACHE_TTL_S`, refreshed with the same wire dict that is pushed on every group event — exact invalidation, TTL as safety net). Both caches are display-only: a miss or a Redis error recomputes from the DB, so a stale/absent value can never make the API wrong in a way the client cannot repair.
- Event catalogue (all push a fresh `group_balances` snapshot — per-user balance + pairwise pairs + suggested transfers — unless noted):

| Type | data highlights |
|---|---|
| `expense.created` / `expense.updated` | full expense incl. exact participant shares |
| `expense.deleted` | expense id, description (for the feed) |
| `expense.receipt.updated` | `expense_id` + `attachment` (full `AttachmentOut`, or `null` after removal) |
| `settlement.created` / `settlement.confirmed` / `settlement.rejected` / `settlement.cancelled` | full settlement |
| `member.joined` / `member.left` | member user |
| `group.closed` / `group.reopened` | group |
| `notification.created` | the notification (bell count) |
| `notification.read` | read-state change — no snapshot, no payload; the client refetches the bell (keeps the unread badge in sync across all tabs/windows of the same user, req 7) |

- Client→server: only pings. No client commands (all mutations are REST).
- Reconnect policy (frontend): exponential backoff; on reconnect the client refetches its data (REST) before trusting state.
- The frontend plays a short notification sound (`/alert.mp3`, via `lib/sound.ts`) on every `notification.created` event.

### 3.3 Activity feed

Append-only `ActivityEvent` rows (group, type, actor, ref, payload JSONB, created_at) written in the same transaction as the action. `GET /groups/{id}/activity` — cursor pagination (`created_at, id` descending), default 30/page. Feed event types mirror the WS catalogue plus `group.created`, `member.left`/close/reopen. Soft-deleted expenses still render ("deleted an expense").

### 3.4 Mail

- Transport: `aiosmtplib` when `SMTP_HOST` set; otherwise log-only (dev). Templates are plain text + HTML, one per purpose: invite, overdue reminder, group-close summary.
- HTML bodies are a shared **branded envelope** matching the frontend look: gray-50 page, 600px-wide white card, a header with the app icon (inlined as a base64 data URI) and the two-tone **EasyMoney** wordmark (indigo `#4f46e5` accent), table-based money rows (label left, right-aligned tabular amount), and an indigo CTA button for the action link. Group-scoped mails (reminder, close summary) link to the group page `FRONTEND_URL/groups/{id}`; the invite links to its token URL.
- Every mail path goes through `services/mail.py`; nothing else opens SMTP connections.

### 3.5 Files (receipts)

Receipt bytes live in the **object store** (`app.services.storage`), selected by
`STORAGE_BACKEND`: `s3` (an S3-compatible store — LocalStack in compose, AWS S3 in
production) or `local` (plain disk under `UPLOAD_DIR`, the pre-S3 behaviour and the
default for bare dev / the test suite). The service layer (`expenses.py`) talks only to
the store interface — it never touches a filesystem path or an SDK client (boto3 is
synchronous and always runs via `asyncio.to_thread`). The object **key** is the
`Attachment.filename` value (`<uuid4-hex>.<ext>`); `content_type`/`size` stay on the DB
row (authoritative for the API). `ready()` (bucket create for S3, no-op for local) runs
once in the app lifespan.

- `POST /expenses/{eid}/receipt` (multipart, field `file`): validates size ≤ `MAX_UPLOAD_MB`, content-type image/* or application/pdf, stores the bytes under a fresh `<uuid4-hex>.<ext>` key, writes the `Attachment` row linked to the expense. Replaces any existing receipt (old key removed). Only the expense creator or the group owner (same rule as §2.5; 403 otherwise). Emits `expense.receipt.updated` with the new `attachment`, so every open tab of every member sees the receipt without a reload.
- `DELETE /expenses/{eid}/receipt`: removes the existing receipt — the `Attachment` row and the object (the object is removed post-commit; a missing key is ignored). 404 when the expense has no receipt. Unlike the upload it is allowed while the group is closed (only the upload is blocked, §2.9). Only the expense creator or the group owner (same rule as §2.5; 403 otherwise). No balance effect; emits `expense.receipt.updated` with `attachment: null`.
- `GET /attachments/{id}`: group-members-only, streams the object bytes with correct content-type + `Content-Disposition`; 404 when the row exists but the object is gone.

### 3.6 Frontend layout

```
src/
  api/client.ts      # fetch wrapper: attaches token, refresh-once on 401, typed
  api/endpoints.ts   # one function per endpoint (typed with the OpenAPI types)
  ws/socket.ts       # WS client: connect/reconnect, token refresh, event bus (emits typed events)
  query/queries.ts   # TanStack Query hooks; WS bus invalidates/mutates matching keys
  pages/Auth.tsx, Groups.tsx, Group.tsx, Overview.tsx
  components/…       # ExpenseForm (split editor UI), Balances, Settlements, Feed,
                     #   InvitePanel, NotificationsBell, ReceiptUpload
```

Rules:
- The WS bus is the only writer to the query cache for group data; REST remains the source for initial loads and refreshes. Both converge because the server is the single source of truth.
- Money is rendered from API strings via a `formatMoney(value, currency)` helper (locale-aware, correct minor digits per the precision map). Never parse to float for display arithmetic.
- Every group screen must react to the WS events listed in 3.2 (expense/settlement/balance changes appear without reload — this is how "updated immediately for all group members" is demonstrated).
- Multi-tab: no `BroadcastChannel` or tab-to-tab hacks; the server-side per-user fan-out already covers it.
- Receipts are **never inlined in the expense list**. The list shows icon+text buttons ("View receipt" / "Replace" / "Attach receipt"); "View" opens a modal (image or PDF), and in edit mode the current receipt is shown as a preview with an "Open full size" modal.

### 3.7 Database

- PostgreSQL 16. Alembic for every schema change (no manual DDL). Money columns `NUMERIC(14,3)`; ids `BIGSERIAL`; money-bearing queries use `NUMERIC` arithmetic only.
- Indexes: `expenses(group_id, date)`, `expenses.payer`, `expense_participants(expense_id, user_id)`, `settlements(group_id, state)`, `activity(group_id, created_at desc)`, `notifications(user_id, read_at)`, `memberships(group_id, user_id)` unique, `email_tokens.token` unique, `email_tokens(user_id, purpose)`, `push_subscriptions(user_id, endpoint)` unique + `push_subscriptions.user_id` (§2.14).
- Avatars are `text` (base64, no `data:` prefix) + `avatar_content_type`; never on a money column.
- `Membership.joined_at` is the tie-broken sort key for the split remainder (2.1) — it must be stable; set it in the DB (`now()`), never client-supplied.
- `attachments.filename` doubles as the receipt **object-store key** (§3.5) — there is no separate key column and no `uploads` Docker volume; receipt bytes live in the object store, not in the DB.

## 4. Environment & running

See README "Environment" table (single source). Key ones: `JWT_SECRET`, `DATABASE_URL`, `SMTP_*`, `SMTP_ENABLED`, `EMAIL_REGISTER_CONFIRMATION`, `EMAIL_CHANGE_CONFIRMATION`, `PASSWORD_CHANGE_CONFIRMATION`, `CONFIRMATION_TOKEN_HOURS`, `EMAIL_CHANGE_REVERT_DAYS`, `REGISTRATION_OPEN`, `MAX_AVATAR_MB`, `DEBUG`, `DEBT_OVERDUE_DAYS`, `REMINDER_INTERVAL_DAYS`, `INVITE_EXPIRY_DAYS`, `MAX_UPLOAD_MB`, `FRONTEND_URL`, `STORAGE_BACKEND`, `S3_ENDPOINT_URL`, `S3_BUCKET`, `S3_REGION`, `S3_ACCESS_KEY_ID`, `S3_SECRET_ACCESS_KEY`, `UPLOAD_DIR` (only when `STORAGE_BACKEND=local`), `CORS_ORIGINS`, `REDIS_URL` (empty = local mode: in-process bus, no caches, lock no-op — the pre-Redis behaviour), `SNAPSHOT_CACHE_TTL_S`, `USER_GROUPS_CACHE_TTL_S`, `SCHEDULER_LOCK_TTL_S`, `VAPID_PUBLIC_KEY`, `VAPID_PRIVATE_KEY`, `VAPID_SUBJECT` (empty keys = Web Push disabled, §2.14; generate via `backend/scripts/generate_vapid.py`).

Local dev without Docker (optional): `alembic upgrade head`, `uvicorn app.main:app --reload`, `npm run dev` (Vite proxies `/api`, `/ws` to `:8000`).

## 5. Testing requirements (definition of done)

A change is done when:

1. **Invariant tests pass.** `backend/tests/` must contain and keep green:
   - `test_splits.py`: random property tests — for random A, n, weights: `Σ shares == A`, shares differ by at most 1 minor unit (equal split), remainder goes to the earliest joiners (deterministic order check).
   - `test_balances.py`: for any sequence of expenses/settlements/edits/deletes (randomized), `Σ balances == 0` and per-user balance matches the formula 2.2 recomputed independently.
   - `test_optimizer.py`: suggested transfers settle all debts, count ≤ n−1, each transfer respects current pairwise debts.
   - `test_settlements.py`: state machine transitions, who-can-do-what, 409 on race (two confirms → one wins), pending has no balance effect, confirmed does.
   - `test_reminders.py`: FIFO aging, weekly cap, skip-when-settled, closed-group behavior (use a fake clock + fake mailer).
   - `test_ws_multiuser.py`: two sockets of the same user both receive an event; a member of another group does not.
   - `test_concurrency.py`: two expenses created "simultaneously" (parallel tasks, no shared state) → both present, sum of balances still 0.
2. Every migration is reversible (`alembic downgrade -1` works).
3. `docker compose up --build` from a clean checkout works without manual steps (migrations auto-run, app on :8081).
4. New WS event types require: backend emit + event-catalogue entry here + frontend handler.
5. No money path uses floats (CI: `ruff` rule + grep guard in tests).
6. **Account/admin tests pass.** `backend/tests/test_accounts.py` must cover: first-ever user becomes admin (and the migration backfills the lowest-id user when none exists); registration restriction (`REGISTRATION_OPEN=false` → 403 once a user exists, `GET /api/auth/status` reflects it); register-confirmation on/off (unverified until confirmed vs. verified immediately); email-change confirmation on/off (two mails — confirm to the new address, revert to the previous one — vs. immediate swap) plus the full revert (cancels a pending change, switches back after a confirmed one, 409s); token lifecycle (confirm marks used, resend revokes the previous pending token, expiry); SMTP kill switch (`SMTP_ENABLED=false` → no send, confirmations skipped); avatar set/clear; admin create/patch (no confirmation, last-admin guard). Frontend: theme manager, i18n preferred-language default, Avatar fallback, settings modal, register-form hiding.
7. **Multi-replica tests pass.** `backend/tests/` must contain and keep green — all in-process against fakeredis (no real Redis needed): `test_redis_bus.py` (cross-replica delivery, symmetric publisher delivery, control messages, publish-failure fallback, envelope codec round-trip); `test_multi_instance_flow.py` (end-to-end: a REST write on replica A reaches a socket parked on replica B; join/leave control messages reach both; the leaver's tabs are unsubscribed); `test_scheduler_lock.py` (single-flight: exactly one of two replicas acquires; release only by owner; TTL expiry; fail-open on Redis error); `test_user_groups_cache.py` (hit/miss, invalidation, corrupt-value recovery); `test_snapshot_cache.py` (hit/miss, `refresh_snapshot_cache` on the real service path, money stays correct on the wire).
8. **Web Push tests pass.** `backend/tests/test_push.py` must cover: `GET /api/push/vapid-public-key` 404 when push is disabled (no VAPID keys) and 200 `{key}` when enabled; `POST /api/push/subscribe` upserts on `(user_id, endpoint)` (one row, keys updated on a second call) and `POST /api/push/unsubscribe` is idempotent (204, row gone, repeat still 204); both are 401 without a token; the post-commit fire path (a notifying write, e.g. an expense) delivers exactly one push per subscribed user with a payload carrying `lang` = the user's `preferred_language` and the notification `{id, type, payload, created_at}`, while the `notification.created` WS event is still emitted in parallel; dead subscriptions (push-service HTTP 404/410) are pruned while other errors (e.g. 503) keep the row; and with no sender plugged nothing is sent and nothing errors (today's behaviour).

## 6. Requirements traceability

| # | Requirement | Where it is specified / implemented |
|---|---|---|
| 1 | Separate backend and frontend | `backend/`, `frontend/` services (README §Architecture) |
| 2 | Python backend | FastAPI, Python 3.12 (§3.1) |
| 3 | React frontend | React 18 + TS + Vite (§3.6) |
| 4 | PostgreSQL storage | `postgres:16`, SQLAlchemy async, Alembic (§3.7) |
| 5 | Working account system | §2.11 (register/login/refresh/revocation) |
| 6 | Easy launch & deployment | `docker compose up --build`, auto migrations, .env defaults (README §Quick start) |
| 7 | Two+ tabs of the same user work | §2.11, §3.2 (per-user WS fan-out, stateless REST, on-read compute) |
| 8 | Groups with members, invites by link or email, own currency | §2.9–2.10, §2.12 |
| 9 | Expenses: payer, amount, equal/shares/exact split, category, date, comment, receipt | §2.1, §3.5 |
| 10 | Balances: within a group and across all groups | §2.2 (`/groups/{id}/balances`, `/balances/all`) |
| 11 | Settlements: full/partial, pending until recipient confirms | §2.4 |
| 12 | Group activity feed | §3.3 |
| 13 | Reminders for outstanding debts; close with final summary report | §2.7, §2.9 |
| 14 | Balances recalculated after every expense, immediately for all members | §2.2 + §3.2 (push fresh snapshot on every event) |
| 15 | Optimal settlement suggestion (not pairwise all-to-all) | §2.3 (greedy, ≤ n−1 transfers) |
| 16 | Notification when an expense involving a user is added/edited | §2.6 |
| 17 | Splits without losing fractional units; defined extra-unit rule | §2.1 (equal: earliest joiners; weighted: largest remainder) |
| 18 | Edit/delete expense → correct recalculation incl. processed settlements | §2.5 + invariant 4 (settlements are real money movements, recomputed on read) |
| 19 | Settlement pending until confirmed; visible to both parties | §2.4 |
| 20 | Overdue debt email; ≤ once/week; not sent if settled | §2.7 (FIFO aging, interval table, re-verify at send) |
| 21 | Two concurrent expense adds both recorded; group balance sum = 0 | §2.8 + invariants 1–2 |
| 22 | Closed group: no new expenses, summary email, reopenable | §2.9 |
| 23 | Sound on new notification | §3.2 (`/alert.mp3` via `lib/sound.ts`, played on `notification.created`) |
| 24 | Profile settings: name, avatar, email, preferred language | §2.13, §3.6 (Settings modal; `PATCH /me`, avatar + email endpoints) |
| 25 | Avatars stored as base64 in the DB; shown in header + every username | §2.13 (`users.avatar_data`, `GET /users/{id}/avatar`) |
| 26 | Email change sends a confirmation (to the new address) and a "this wasn't me" revert link (to the previous address); the full revert cancels a pending change or switches the email back | §2.13 (`EMAIL_CHANGE_CONFIRMATION`, `CONFIRMATION_TOKEN_HOURS`, `EMAIL_CHANGE_REVERT_DAYS`, `email_tokens`, `POST /me/email/change`, `POST /auth/email/revert`) |
| 27 | Preferred language drives email language and the in-app UI language (no in-app switcher); the login-page selector defaults to the browser language and is sent as `preferred_language` on registration | §2.13, §3.6 (`preferred_language`, `applyPreferred`, login-page selector) |
| 28 | Dark/light/browser theme (default browser) | §2.13, §3.6 (ThemeManager, `darkMode:"class"`) |
| 29 | Optional email confirmation after registration (separate env from change confirmation) | §2.13 (`EMAIL_REGISTER_CONFIRMATION`, verify gate) |
| 30 | Registration restriction (only first user can register) | §2.13 (`REGISTRATION_OPEN`, `GET /auth/status`) |
| 31 | Admin panel: first user is admin; manage users; statistics | §2.13 (`is_admin`, `GET/PATCH /admin/users`, `GET /admin/stats`) |
| 32 | Disable SMTP entirely via env (no mail, no confirmations) | §2.13 (`SMTP_ENABLED` kill switch in `services/mail.py`) |
| 33 | Password change with optional email confirmation (pending hash rides on the token, applied only on confirm) | §2.13 (`PASSWORD_CHANGE_CONFIRMATION`, `email_tokens.password_hash`, `POST /me/password/change`) |
| 34 | Multi-replica backend behind a load balancer: cross-instance WS fan-out, short-TTL display caches, single-flight reminder scan — Redis is optional, local mode when unset | §2.7 (lock), §3.2 (bus + caches), §4 (`REDIS_URL` + TTLs), `app/ws/bus.py`, `app/ws/user_groups.py`, `app/core/redis_client.py`, `app/workers/lock.py` |
| 35 | Browser (Web) notifications via **Web Push** (service worker + VAPID): works with the app closed; suppressed while any app tab is visible; client-only toggle; permission only on the Settings gesture; click deep-links; the in-app bell + sound run in parallel, unchanged | §2.14, `app/services/push.py`, `app/api/push.py`, `app/models/push_subscription.py`, `app/ws/hook.py`, `frontend/src/sw/sw.ts`, `frontend/src/lib/push.ts`, `frontend/src/lib/browserNotify.tsx`, `frontend/src/components/SettingsModal.tsx` |

## 7. Style

- Python 3.12, `ruff` (format + lint, line length 100), `mypy --strict` on `services/` and `models/`. Type hints everywhere; money is `Decimal`.
- TypeScript strict; no `any` in `api/` and `ws/`.
- Conventional commit messages (`feat:`, `fix:`, `test:`, `docs:`, `chore:`).
- Never edit `migrations/` history; add a new revision instead.
- When in doubt about a rule above, this file wins over intuition. If a requirement seems missing, add it here first, then implement.
