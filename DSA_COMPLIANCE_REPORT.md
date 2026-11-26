# Astran Game — DSA remediation: implementation & assessment (Arts. 9, 10, 15, 16, 17, 20, 24(5))

> **Software alone does not establish DSA compliance, and this is not a certification or legal advice.** This report separates
> *technical implementation* (what the code does, and how it was tested) from *legal applicability* (which obligations bind Astran Game).
> The code **never assumes** Astran's legal status: it reads an explicit operator decision (`ASTRAN_DSA_SERVICE_CLASS`, `ASTRAN_DSA_ENTERPRISE_SIZE`)
> and, while that decision is missing, reports every status-dependent obligation as `undetermined` and activates nothing mandatory automatically.
> GDPR duties (legal basis, retention periods, erasure vs. moderation records) are separate and not settled by this work.

## 1. Legal applicability (documented, to be confirmed by a lawyer)

| Obligation | Who it binds (DSA, Reg. 2022/2065) | Status in code |
|---|---|---|
| Art. 9, 10, 11, 12 | every intermediary service, any size | built; always `applies` |
| Art. 15 transparency reports | every provider **except micro/small enterprises that are not VLOPs** (Art. 15(2)) | `undetermined` until `ASTRAN_DSA_ENTERPRISE_SIZE` is set |
| Art. 16, 17 | hosting services, including online platforms (Section 2 applies to micro/small too) | `undetermined` until `ASTRAN_DSA_SERVICE_CLASS` is set |
| Art. 20 internal complaints | **online platforms only**; Art. 19 exempts micro/small ones | `undetermined` |
| Art. 24(5) Transparency Database | online platforms that are not micro/small (Art. 19), plus onboarding through the Digital Services Coordinator | `undetermined`; **no automatic submission is enabled** |

Reporting period (Implementing Regulation (EU) 2024/2835): calendar year for providers that are not VLOPs, published within two months of period end;
data collection in the harmonised format was to start on 1 July 2025. `dsa_applicability.py` exposes the matrix at `GET /api/dsa/applicability` (staff only).
Art. 19(2) grace period (12 months after outgrowing the exemption) must be checked manually. Whether a user-report-based action under the Terms is "own initiative"
or not is a classification for legal review; reports show both buckets separately.

## 2. Article-by-article status

Nothing is marked IMPLEMENTED: every item below either depends on configuration/credentials that are not set here, or on integrations/HTTP/UI layers that could **not be executed**
in this environment (no FastAPI, no network, no `node_modules`).

| Article | Status | What exists / what is missing |
|---|---|---|
| **9** Orders to act against illegal content | **PARTIALLY IMPLEMENTED** | `authority_orders.py`: unique reference `ORD-YYYY-NNNNN`, issuing authority, legal basis, received/issued/deadline (recorded **only if the order states one**; otherwise "without undue delay" is shown, no deadline invented), identifiers (exact URLs/IDs), territorial scope, private attachment store, completeness validation (Art. 9(2) elements), authenticity check, legal review with four-eyes, execution **through the existing decision engine** (statement of reasons, notification, audit, complaint right), idempotent re-execution after partial failure, optional legally-confirmed non-disclosure (notice withheld, releasable later), response to the authority with tracked delivery, closure, retention purge. Uploading a document never executes anything (tested). Missing: configured contact point data (Art. 11), SMTP, appointed legal officers, authenticated channel for authorities (public intake is *unverified* and takes no attachments), no automatic copy to the DSC. |
| **10** Orders to provide information | **PARTIALLY IMPLEMENTED** | Same intake/validation/review. Information package limited to an explicit whitelist (`user_id, username, display_name, email, created_at, email_verified`); other requested items are refused and listed; credentials, tokens, payment, parental-consent data and private-message content are never provided by this flow (needs separate legal assessment). Package is stored privately and handed over by a person on the authority's secure channel; the system records *confirmation of delivery*, it does not transmit personal data by email. Subject is informed unless non-disclosure was confirmed by legal review. |
| **15** Transparency reports | **PARTIALLY IMPLEMENTED** | `dsa_transparency.py` aggregates real data (notices, categories, trusted-flagger notices, actions, own-initiative/terms decisions, automated detection, restriction types, complaints and outcomes, medians, Art. 9/10 orders with acknowledge/effect times). JSON + CSV, immutable versioned publication (admin), public page/download. Aggregates only; a final guard refuses output containing identifier-like values. Missing: **exact mapping to the official Annex I CSV/XLSX templates** (could not download them; `template.mapping_status` says so), qualitative sections (rules, tools, human resources) need manual input, chat-filter events and out-of-court-body statistics are not collected (listed under `not_collected`, not zero-filled). Applicability undetermined. |
| **16** Notice and action | **PARTIALLY IMPLEMENTED** | Registered users (existing) **and non-registered people** (`POST /moderation/public/notices`): content URL / ID / account username, alleged illegality + explanation, validation, honeypot, per-IP rate limit, per-contact daily cap and 24h dedup (HMAC hashes, purged after retention), reference number + one-time tracking token (only its hash is stored), status page, receipt/outcome by email, trusted-flagger flag (set only by an admin), ToS reports kept separate from notices, nothing is auto-classified as illegal (tested). Missing: SMTP for emails; anonymous notifiers cannot file Art. 20 complaints (no account) — they must use the contact channel; private messages cannot be reported anonymously by design. |
| **17** Statements of reasons | **PARTIALLY IMPLEMENTED** | Persisted, hashed, immutable; now also states who initiated the measure (notice / user report / Astran / authority order), order reference when applicable, automated means, basis, duration, redress. In-app notification plus **email through `dsa_delivery.py`**: status `pending → sent | failed(retry, exponential backoff) → abandoned`, `skipped_no_contact` when no email exists, **never marked sent unless the sender confirms** (tested with failing/throwing senders); reporter identity absent from SoR and email (tested). Missing: working SMTP (unconfigured here; the real sender could not be exercised), retention values. |
| **20** Internal complaints | **PARTIALLY IMPLEMENTED** | Existing 6-month window, one complaint per decision, human independent reviewer; now **maintain / reverse / modify** (modify replaces an account restriction/suspension with a new, shorter restriction and a new statement of reasons; not available for content decisions), reviewer reasoning, timestamps (submitted / review started / resolved), outcome notification in-app and by email including out-of-court-settlement information. No universal deadline is invented: the DSA requires timely, diligent, non-arbitrary handling and informing "without undue delay"; an **optional internal target** `ASTRAN_APPEAL_TARGET_DAYS` can be set. Missing: ODS body information (`ASTRAN_ODS_BODIES`), applicability determination, ≥2 moderators required. |
| **24(5)** Transparency Database | **PARTIALLY IMPLEMENTED** | `dsa_tdb.py`: redacted machine-readable payload, PII redaction + blocking check (`blocked_pii`), unique submission per statement (no duplicates), statuses/timestamps/external references, retries with backoff, `needs_reconciliation` when the service reports a duplicate `puid`, admin overview, JSON export + manual workflow (manual confirmation requires a typed reference and is flagged *not verified with the service*). The API client posts to `<ASTRAN_TDB_API_URL>/statement` with a bearer token, per the official documentation; **nothing is enabled until** `ASTRAN_TDB_ENABLED=1`, `ASTRAN_TDB_MODE`, `ASTRAN_TDB_API_URL`, `ASTRAN_TDB_API_TOKEN` are set **and** the legal status says it is required. **Not tested against the real or sandbox service.** Enumerated values (`DECISION_*`, `STATEMENT_CATEGORY_*`, `SOURCE_*`) are taken from the documentation/knowledge of the schema and must be validated in the Commission sandbox; categories without a safe mapping stay `needs_review`. Free-text reasoning is not sent by default. |

## 3. Configuration and credentials still required (never invented)

Existing: `ASTRAN_LEGAL_ENTITY_NAME`, `ASTRAN_LEGAL_ADDRESS`, `ASTRAN_COMPANY_REGISTRATION`, `ASTRAN_DSA_AUTHORITIES_EMAIL`, `ASTRAN_DSA_AUTHORITIES_LANGUAGES`, `ASTRAN_DSA_USERS_EMAIL`,
`ASTRAN_DSA_USERS_LANGUAGES`, `ASTRAN_TRUSTED_FLAGGERS_EMAIL`, `ASTRAN_EU_LEGAL_REPRESENTATIVE`, `ASTRAN_ODS_BODIES`, `MODERATION_RETENTION_POLICY`, SMTP settings used by `project_security`.
New (all optional; see `backend/.env.example`): `ASTRAN_DSA_SERVICE_CLASS`, `ASTRAN_DSA_ENTERPRISE_SIZE`, `ASTRAN_DSA_VLOP`, `ASTRAN_APPEAL_TARGET_DAYS`, `ASTRAN_DELIVERY_MAX_ATTEMPTS`,
`ASTRAN_ABUSE_HASH_KEY`, `ASTRAN_PUBLIC_NOTICE_DAILY_CAP`, `ASTRAN_ORDERS_DIR`, `ASTRAN_ORDERS_ENC_KEY`, `ASTRAN_ORDERS_FOUR_EYES`, `ASTRAN_ORDERS_RETENTION_DAYS`,
`ASTRAN_ABUSE_RETENTION_DAYS`, `ASTRAN_DELIVERY_RETENTION_DAYS`, `ASTRAN_TDB_*`.
External: SMTP account; onboarding with the Digital Services Coordinator and a token from the Commission (sandbox first) for Art. 24(5); legal officers named by an admin (`POST /api/moderation/admin/flags`);
an out-of-court body list; a lawyer's determination of service class and size; the official Annex I templates for Art. 15; volume encryption or `ASTRAN_ORDERS_ENC_KEY`.
**Privacy Policy section 2.6 must be reviewed** (anonymous notifier data and HMAC hashes, authority orders, delivery queue): the policy files are absent from the uploaded archive, so they were not edited;
the data inventory (`backend/legal/data_inventory.json`) lists the five new collections.

## 4. Files

**Inspected:** `moderation_service.py`, `moderation_routes.py`, `test_moderation.py`, `test_support_fakedb.py`, `server.py` (report flow, startup, routers, `_report_target_exists`, shutdown), `project_security.py` (email),
`email_verification.py`, `legal/data_inventory.json`, `test_privacy_policy.py`, `DSA_COMPLIANCE_REPORT.md` (previous version), frontend `moderation.ts`, `modStrings.ts`, `modUi.tsx`, existing `app/moderation/*`.
Database is **MongoDB (Motor)**, not SQL: "migrations" are versioned index/constraint changes.

**Created (backend):** `dsa_applicability.py`, `dsa_delivery.py`, `dsa_public_notices.py`, `dsa_transparency.py`, `dsa_tdb.py`, `authority_orders.py`, `dsa_migrations.py`, `dsa_routes.py`, `test_dsa_remediation.py`.
**Created (frontend):** `src/api/dsa.ts`, `app/moderation/{report-illegal,transparency}.tsx`, `app/moderation/admin/{orders,tdb,deliveries,transparency,own-initiative}.tsx`, `admin/order/[id].tsx`.
**Modified:** `moderation_service.py` (own-initiative/order cases, `source`, anonymous reporters, trusted-flagger flag, statement fields, delivery/TDB hooks, `modify` outcome, appeal timing, `parse_content_reference`, order audit filtering),
`moderation_routes.py` (`modify`, new report kinds, `orders` flag in `/me/role`), `server.py` (router, migration at startup, delivery/TDB/retention workers, shutdown), `test_support_fakedb.py` (`drop_index`, `drop_collection`, index names),
`legal/data_inventory.json`, `.env.example`, frontend `client.ts` (`publicApiUrl`), `moderation.ts`, `modStrings.ts` (+test families), `app/moderation/{index,admin/index,admin/appeal/[id],decision/[id]}.tsx`.

## 5. Database (migration `0001_dsa_remediation_indexes`)

New collections: `moderation_deliveries`, `tdb_submissions`, `authority_orders`, `authority_order_attachments`, `transparency_reports`, plus `schema_migrations`.
Unique: `delivery_id`, `dedupe_key`, `submission_id`, **`tdb_submissions.sor_id`** (no duplicate submissions), `order_id`, `reference`, `attachment_id`, transparency `report_id`; supporting indexes on status/time,
`reports(report_kind, created_at)`, `reports.submitter.email_hash`, `moderation_decisions(source, created_at)`, `moderation_appeals(status, created_at)`.
`migrate_up` is idempotent and runs at startup; `migrate_down` removes only the indexes (data kept), `destroy_data=True` drops the new collections. No existing document is rewritten:
decisions without `source` are classified at read time from the report kind. There are no multi-document transactions (needs a replica set, unused elsewhere in the repo); writes are ordered, claims are atomic,
and failures leave an explicit status plus an audit event. Audit log is insert-only and hash-chained (`GET /moderation/admin/audit/verify`); it detects tampering but a DB administrator with write access could still truncate the tail — use backups/WORM storage.

## 6. Endpoints (under /api)

Public: `POST /moderation/public/notices`, `POST /moderation/public/notices/status`, `GET /transparency/reports[/{id}[/csv]]`, `POST /authority/intake` (unverified, no attachments), `GET /authority/contact`.
Staff: `POST /moderation/admin/own-initiative`, `GET /moderation/admin/deliveries`, `POST …/deliveries/{id}/retry`, `GET /dsa/applicability`, `GET /dsa/transparency/preview`.
Admin: `POST /moderation/admin/flags`, `POST /dsa/transparency/publish`, `GET /dsa/tdb`, `POST /dsa/tdb/{export, {id}/confirm-manual, {id}/category, {id}/retry}`.
Legal/admin only (`is_legal_officer` or platform admin; ordinary moderators get 403): `GET/POST /dsa/orders`, `GET /dsa/orders/{id}`, `POST …/{supplement, authenticity, legal-review, execute, information, package-delivered, release-notice, responses, close, attachments}`, `GET …/attachments/{aid}`.

## 7. Security notes

Authorization is checked in each service function as well as in the routes (so unit tests prove it): orders/attachments/package are legal-or-admin only, attachment lookups are bound to the order (no IDOR), downloads are `nosniff`/`no-store`.
Attachments: server-generated paths only (client filename never touches the filesystem), 0600 files in a 0700 directory outside any served path, extension + magic/content checks, 10 MB / 20 files limits, SHA-256 verified on read,
optional Fernet encryption at rest (**unencrypted if no key is set; the UI warns**). Execution requires four-eyes (configurable) and an admin or legal+moderator account. Public endpoints are rate-limited by IP and return identical
responses for "missing" and "wrong token". Transparency CSV neutralises spreadsheet formulas. Inputs use strict models (`extra=forbid`); queries are parameterised Mongo filters (no string-built queries; usernames are regex-escaped).

## 8. Tests executed (this environment)

- `python -m unittest astran_sandbox.test_dsa_remediation`: **69 run, 68 pass, 1 skipped** (real SMTP sender test needs `project_security` dependencies).
  Covers: applicability matrix; delivery success/failure/exception/backoff/abandon/manual retry/no-email/idempotency/no reporter identity/no email in audit; public notices (persistence, no auto-illegal, validation, URL/ID/account, private messages refused,
  duplicates, honeypot, daily cap, token isolation, abuse-metadata purge, trusted-flagger authority); own-initiative statements; complaint modify/reverse/reviewer independence/no invented deadline; TDB payload PII, category review, submit/confirm semantics,
  retry/abandon, duplicate puid, export and manual confirmation, admin-only; orders (authorization, incomplete/uploaded ≠ executed, four-eyes, execution, idempotency, partial failure, non-disclosure, Art. 10 whitelist, attachments
  privacy/IDOR/tampering/encryption, response milestones only after confirmed delivery, lifecycle and purge, unverified intake, audit tamper evidence and visibility); Art. 15 aggregation, period filter, no identifiers, CSV, publish/versioning;
  migration up/down/re-up and unique constraints.
- `python -m unittest astran_sandbox.test_moderation`: 42 run, 39 pass, 3 skipped (HTTP tests, FastAPI not installed) — unchanged.
- Full backend discovery: 715 run (646 before my changes + 69 new); **the same 28 failures/errors as on the untouched upload** (verified by diffing the failing-test lists; causes are missing fastapi/websockets/binaries and the pre-existing
  consumer-rights/scene-model tests); **no new failures**. `py_compile` of every backend module OK.
- Frontend: `modStrings.test.mts` 5/5 (RO/EN key parity, every literal key exists, new status families), `dmStore` 14/14, `blockedStore` 9/9, `consumerStrings` 4/4, `chatStore` 11/11.
  `runtime.test.mts` fails on the untouched upload too (unresolved `@/src` alias without `node_modules`). All new/changed TSX files were syntax-checked with `tsc`; **no type-check, lint, build or rendering was possible** (no `node_modules`, no network).

## 9. Remaining limitations (honest list)

1. **HTTP layer and UI never executed**: `dsa_routes.py` compiled but its routes were not run (FastAPI absent); screens were only syntax-checked. Run `test_moderation`'s HTTP tests and a manual pass in an environment with dependencies; add route tests for `dsa_routes.py`.
2. Real SMTP and the Transparency Database API were not exercised; both are behind explicit configuration and injectable clients/senders (tested with fakes).
3. Art. 15 output is structured on the Annex I sections but **not mapped to the official field codes**; qualitative sections need people; chat-filter events and ODS statistics are not collected.
4. Anonymous notifiers cannot lodge Art. 20 complaints in-app; `modify` supports account restrictions only.
5. Public intake for authorities is unauthenticated and attachment-free by design; there is no automatic transmission of orders to/from the DSC and no digital-signature verification.
6. Pre-existing and untouched: ToS/privacy registry problems in the uploaded archive (see previous notes), in-game chat/DM reports that bypass the receipt flow, the privacy policy text for the new data.
7. Chat-filter masking events are not logged, so "automated moderation" counts only decisions with `automated_detection` set by moderators.
8. Needs human decisions: service class and size, Art. 16 response-time targets and staffing, trusted-flagger procedure, retention periods, ODS bodies, whether to enable the TDB, Art. 10 scope for message content.
