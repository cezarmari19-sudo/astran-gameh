# Astran Game — DSA moderation: implementation & assessment

> **Software alone does not establish DSA compliance.** This report lists what was built, how it was tested, and what still needs
> company information, legal decisions or operating procedures. Nothing here is legal advice. No micro/small-enterprise exemption is assumed.
> Whether Astran is a *hosting service* (Art. 16/17) or an *online platform* (Art. 20–28, e.g. Art. 24(5) transparency database,
> Art. 21 out-of-court bodies) depends on its legal status and size and **must be determined by a lawyer**. GDPR duties (retention, erasure,
> legal basis for processing moderation data) are separate and are not settled by this work.

## Compliance table

| Requirement | Status | Notes |
|---|---|---|
| Art. 11 — Contact point for authorities | **REQUIRES COMPANY INFORMATION** | Page, public API and an electronic intake (`POST /api/contact/messages`, channel "authority"/"commission_or_dsc") are built; no contact data is configured. Language(s) and entity details missing. |
| Art. 12 — Contact point for users | **REQUIRES COMPANY INFORMATION** | Same; in-app form is read by people (not automated-only). Email/languages must be configured. |
| Art. 14 — Terms of Service | **REQUIRES LEGAL VERIFICATION** | v1.1 (ro+en) drafted with moderation, reporting, automated-tools, complaints and termination sections; versioning/acceptance logging pre-existed. Text contains `[BRACKETED]` placeholders and is marked *not legally reviewed*. |
| Art. 16 — Notice and action | **PARTIALLY IMPLEMENTED** | Notice form, validation (explanation, location, identity, good faith), precision assessment, receipt, decision, notifier outcome, audit: implemented, service layer tested. HTTP layer & UI not executed here. Response-time targets and applicability need human decision. |
| Art. 17 — Statement of reasons | **PARTIALLY IMPLEMENTED** | Generated, hashed, immutable, visible only to the affected user. Not implemented: email delivery, submission to the EU Transparency Database (Art. 24(5), only if Astran is an online platform), retention policy value. Whether warnings need a statement is flagged for legal review. |
| Art. 20 — Internal complaints | **PARTIALLY IMPLEMENTED** | 6-month window, one complaint per decision, evidence (text + https links), independent human reviewer, maintain/reverse with reasoning: implemented, service layer tested. Applicability (online platform) and Art. 21 body info are missing. |

## Configuration the developer must supply (never invented)

`ASTRAN_LEGAL_ENTITY_NAME`, `ASTRAN_LEGAL_ADDRESS`, `ASTRAN_COMPANY_REGISTRATION`, `ASTRAN_DSA_AUTHORITIES_EMAIL`, `ASTRAN_DSA_AUTHORITIES_LANGUAGES`,
`ASTRAN_DSA_USERS_EMAIL`, `ASTRAN_DSA_USERS_LANGUAGES`, `ASTRAN_TRUSTED_FLAGGERS_EMAIL`, `ASTRAN_EU_LEGAL_REPRESENTATIVE` (Art. 13, non-EU providers only),
`ASTRAN_ODS_BODIES`, `MODERATION_RETENTION_POLICY`. `GET /api/contact/info` returns `missing_fields` and the variable names.
Roles: `users.is_platform_admin` (existing) = admin; `users.is_moderator` = moderator (granted by an admin via `POST /api/moderation/admin/moderators`).

## Files created
- backend/astran_sandbox/moderation_service.py — logic (reports, notices, decisions, SoR, appeals, audit chain, contact)
- backend/astran_sandbox/moderation_routes.py — FastAPI routes
- backend/astran_sandbox/test_moderation.py — 42 tests (3 HTTP tests skip without fastapi)
- backend/legal/terms_of_service/1.1.ro.txt, 1.1.en.txt
- frontend/src/api/moderation.ts, src/i18n/modStrings.ts (+ modStrings.test.mts), src/components/modUi.tsx, src/components/ReportSheet.tsx
- frontend/app/moderation/{index,reports,notifications,decisions,appeals,contact}.tsx, decision/[id].tsx
- frontend/app/moderation/admin/{index,appeals,audit}.tsx, admin/report/[id].tsx, admin/appeal/[id].tsx

## Files modified
- backend/server.py — imports; `ReportBody.category` (optional); `POST /reports` now registers via the moderation service and returns `{report_id, receipt}` (still contains `report_id`); index creation on startup; routers; suspension gate in `get_current_user`; WebSocket resolver rejects suspended accounts; owner cannot re-publish moderated games.
- backend/astran_sandbox/dm_store.py, group_routes.py — moderated messages hidden from reads (`moderation_hidden`).
- backend/legal/manifest.json — adds ToS 1.1 as current (`requires_reacceptance: true`).
- frontend/app/settings.tsx (Terms / Moderation / Contact rows), app/(tabs)/index.tsx (footer links), app/game/[id].tsx (Report button).
- Registration already linked the Terms through `ConsentCheckboxes` (unchanged).

## Database
No destructive migration. New collections (created lazily; `moderation_service.ensure_indexes` runs at startup, idempotent): `report_evidence`,
`report_internal_notes`, `moderation_decisions`, `statements_of_reasons`, `moderation_corrections`, `moderation_appeals`, `moderation_notifications`,
`moderation_audit_log` (unique `seq`, hash-chained), `contact_messages`. Existing `reports` documents are not rewritten: legacy `status: "open"` is read as `pending`,
legacy `context` is used as evidence. New optional fields on `reports`: `report_kind`, `category`, `reported_user_id`, `assigned_to`, `notice`, `reviewed_at`, `decided_at`.
New optional `users` fields: `is_moderator`, `moderation_warning_count`, `moderation_suspended(_until)`. No multi-document transactions (needs a replica set; not used elsewhere in the repo):
writes are ordered, a decision is claimed atomically, and failures leave a `failed` decision plus an audit event.

## Endpoints (all under /api)
User: `POST /reports` (existing, extended), `POST /moderation/notices`, `GET /moderation/me/{reports,reports/{id},notifications,decisions,decisions/{id},appeals,appeals/{id},role}`,
`POST /moderation/me/notifications/{id}/read`, `POST /moderation/me/decisions/{id}/appeals`.
Staff: `GET /moderation/admin/{reports,reports/{id},audit,audit/verify,appeals,appeals/{id},contact-messages}`,
`POST /moderation/admin/reports/{id}/{assign,status,notes,assessment,decisions}`, `POST /moderation/admin/decisions/{id}/corrections`,
`POST /moderation/admin/appeals/{id}/{start-review,resolve}`, `POST /moderation/admin/moderators` (admin only).
Public: `GET /contact/info`, `POST /contact/messages` (rate-limited by IP). Existing `/legal/*` serves the Terms.

## Security
Role read from the DB-loaded user on every request; IDOR → 404 for foreign reports/decisions/appeals; internal notes and moderator ids never in user views (tested);
strict request models (`extra=forbid`); rate limits on every write; evidence = text snapshot + https links only (no uploads); permanent suspension admin-only; staff/self protected;
the original decision-maker cannot review the appeal; audit log is insert-only with a verifiable hash chain; suspended accounts keep access only to decisions/appeals/legal/data-export.

## Tests executed
- `python -m unittest astran_sandbox.test_moderation`: 42 run, 39 pass, 3 skipped (HTTP tests need fastapi/httpx — **not installed here, no network; they have not run**).
- Full backend suite: 468 run; the same 20 failures/errors occur on the untouched upload (missing fastapi/websockets/sandbox binaries); no new failures.
- Frontend: `modStrings.test.mts` 5/5; existing `dmStore` 12/12 and `chatStore` 11/11 pass. **TypeScript was not type-checked and the UI was not rendered** (no node_modules / no network).
- `py_compile` of all changed Python files OK.

## Known problems and remaining work
1. **Pre-existing, not fixed:** in the uploaded archive `legal/privacy_policy/*` files are absent and the ToS 1.0 hashes do not match `manifest.json`; the registry fails closed, so the server will not start from this snapshot as-is. Restore the files from your repository (do not re-hash a published version).
2. Switching ToS "current" to 1.1 asks every user to re-accept a **draft**. Set `"current": "1.0"` in `manifest.json` until the text is approved.
3. Existing in-game chat and DM reports (`dm_core`, `chat_core`) still insert their own documents: they appear in the dashboard but get no receipt/notification.
4. Moderated group messages are hidden in the group chat list and DM history only; check other read paths (realtime history) before relying on it.
5. Needs a human decision: Art. 16 response times, moderator training/staffing (≥2 moderators are required for independent appeal review), trusted-flagger procedure, Art. 24(5) submissions, retention periods, GDPR erasure vs. moderation records and notice-submitter emails, email delivery of statements, ToS legal review, ODS bodies, and whether Astran is a hosting service/online platform.
6. Not built: email notifications, file evidence, authority orders (Arts. 9–10) handling beyond the contact intake, transparency reports (Arts. 15/24).
