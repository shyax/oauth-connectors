# OAuth Connector Platform

A production-oriented backend that abstracts OAuth 2.0 across providers, manages the full token lifecycle, executes connector actions reliably under real failure conditions, and exposes a unified API layer — all in a multi-tenant architecture.

Built to demonstrate the exact depth of correctness that enterprise connector systems require: token refresh race conditions, idempotent retry budgets that separate rate limits from failures, cursor-based incremental sync, and a provider-agnostic execution engine that scales without touching core infrastructure.

**Stack:** Python · FastAPI · PostgreSQL · Redis · RQ · Docker Compose  
**Providers implemented:** Google Drive · Slack  
**Adding a new provider:** one file, zero changes to core

---

## Architecture

```
┌─────────────────────────────────────────────────────────────────────────┐
│                            External World                                │
│                                                                          │
│   Browser / Client          Google OAuth          Slack Events           │
│         │                       │                      │                 │
└─────────┼───────────────────────┼──────────────────────┼─────────────────┘
          │                       │                      │
          ▼                       ▼                      ▼
┌─────────────────────────────────────────────────────────────────────────┐
│                            FastAPI (API Layer)                           │
│                                                                          │
│  POST /connect/{provider}     GET /callback     POST /webhooks/slack    │
│  GET  /files                  POST /messages    GET  /integrations      │
│                                                 GET  /jobs              │
│                    Correlation-ID middleware                             │
└───────────────┬──────────────────────────┬──────────────────────────────┘
                │                          │
                ▼                          ▼
┌──────────────────────────┐   ┌───────────────────────────────────────────┐
│       Auth Layer         │   │             Execution Layer               │
│                          │   │                                           │
│  oauth_google.py         │   │  enqueue_once()  ──► default_queue       │
│  oauth_slack.py          │   │    └─ idempotency_key (DB unique)         │
│  token_manager.py        │   │    └─ IntegrityError race guard          │
│                          │   │                                           │
│  Redis SET NX lock per   │   │  Job runners: sync / action / webhook    │
│  integration_id          │   │    └─ RateLimitError  → retry_queue      │
│  Double-check inside     │   │         (no retry_count increment)       │
│  lock (check-then-act)   │   │    └─ Exception       → retry_queue      │
│  invalid_grant → revoked │   │         (2^n backoff + jitter)           │
│                          │   │    └─ max_retries hit  → dead_letter     │
└──────────────────────────┘   └──────────────────┬────────────────────────┘
                                                   │
                ┌──────────────────────────────────┘
                ▼
┌───────────────────────────────────────────────────────────────────────────┐
│                          Connector Layer                                   │
│                                                                            │
│  BaseConnector (ABC)                                                       │
│    execute(action, payload) → dict                                         │
│    handle_rate_limit(response) → RateLimitInfo                            │
│    get_access_token() → str  (refresh-on-use, transparent to caller)      │
│                                                                            │
│  GoogleDriveConnector          SlackConnector                             │
│    incremental_sync              send_message                             │
│    list_files (paginated)        process_event                            │
│    SyncCursor persistence        token_revoked detection                  │
│    403 rateLimitExceeded         ok=false + ratelimited body              │
└────────────────────────────────┬──────────────────────────────────────────┘
                                 │
                ┌────────────────┘
                ▼
┌──────────────────────────────────────────────────────────────────────────┐
│                        Normalization Layer                                │
│                                                                           │
│  google_file_to_external()   slack_message_to_external()                 │
│  ExternalFile schema         ExternalMessage schema                       │
│                                                                           │
│  Provider schemas never leak past this boundary.                         │
│  external_objects stores normalized form — API reads are plain selects.  │
└──────────────────────────────────────────────────────────────────────────┘
                                 │
                ┌────────────────┘
                ▼
┌────────────────────────────────────────────────────────────────────────┐
│                         PostgreSQL                                      │
│                                                                         │
│  integrations      connector_jobs         external_objects             │
│  (encrypted        (idempotency_key       (UNIQUE tenant+source+id     │
│   tokens, status,   UNIQUE, retry_count,   upsert target,              │
│   expires_at)       job_type, payload)     JSONB data)                 │
│                                                                         │
│  sync_cursors                                                           │
│  (page_token or timestamp per integration, separate table)             │
└────────────────────────────────────────────────────────────────────────┘
```

---

## Quick Start

```bash
# 1. Clone and configure
git clone <repo>
cp .env.example .env
# Fill in ENCRYPTION_KEY, GOOGLE_*, SLACK_* in .env
# Generate key: python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"

# 2. Start the stack
docker-compose up --build

# 3. Run migrations
docker-compose exec api alembic upgrade head

# 4. Seed tenants and mock integrations
docker-compose exec api python scripts/seed.py
```

API is live at `http://localhost:8000`. Docs at `http://localhost:8000/docs`.

---

## OAuth Lifecycle

This is the hardest part of any connector system and the part most implementations get wrong. Here's exactly how this one handles it.

### Google: offline access + token rotation edge case

```
Client                     API                    Google
  │                          │                       │
  │  GET /connect/google     │                       │
  │─────────────────────────►│                       │
  │                          │  redirect (state JWT) │
  │◄─────────────────────────│                       │
  │                          │                       │
  │  GET /connect/google/callback?code=...           │
  │─────────────────────────►│                       │
  │                          │  POST /token          │
  │                          │──────────────────────►│
  │                          │  {access_token,       │
  │                          │   refresh_token,      │
  │                          │   expires_in}         │
  │                          │◄──────────────────────│
  │                          │                       │
  │  {integration_id}        │  encrypt + store      │
  │◄─────────────────────────│                       │
```

`access_type=offline&prompt=consent` is required on every authorization request. Without `prompt=consent`, Google only returns a `refresh_token` on the **first** grant for a given client — a silent failure that surfaces hours later when the access token expires.

Token refresh also handles Google's rotation edge case: a token refresh response may contain a new `refresh_token`. The token manager always persists it when present. An implementation that ignores this will fail within weeks on accounts where rotation is enabled.

### Slack: bot tokens and the revocation path

Slack bot tokens don't expire. `expires_at` is `NULL` and `needs_refresh()` returns `False` unconditionally. The revocation path is different: it's detected at API response time (`token_revoked`, `invalid_auth`, `account_inactive` in the response body), not by checking a clock. When detected, the integration is marked `revoked` inline and the job fails cleanly — no retry.

### Refresh race condition prevention

When multiple workers process jobs for the same integration simultaneously, naive implementations cause a token refresh storm: every worker detects expiry and calls the provider in parallel, burning refresh token grants and hitting rate limits.

This implementation uses a Redis `SET NX PX` lock per `integration_id`:

```python
# token_manager.py:39
acquired = redis_conn.set(lock_key, "1", px=REFRESH_LOCK_TTL_MS, nx=True)
if not acquired:
    time.sleep(0.5)
    return db.get(Integration, integration_id)  # read freshly-refreshed token
```

The lock TTL (30s) bounds the worst case: if a worker crashes mid-refresh, the lock expires and the next worker can recover. Inside the lock, we re-check `needs_refresh()` before calling the provider — this is the **check-then-act** pattern that prevents redundant refreshes when multiple callers stack up on the lock.

---

## Retry & Rate Limit Design

### The key distinction: rate limits are not failures

Most retry implementations conflate rate limits with exceptions and apply the same backoff budget to both. This is wrong and causes jobs to hit the dead-letter queue on a busy quota day.

```python
# execution/rate_limit.py:24
# Rate limit retries do NOT increment retry_count — this is expected provider behavior
retry_queue.enqueue_in(timedelta(seconds=delay), _run_job_by_id, str(job.id))
```

Only exceptions burn the retry budget. Rate limits just reschedule.

### Exponential backoff sequence

```
Attempt 1 failure  →  delay = 2^1 + jitter  =  ~2s
Attempt 2 failure  →  delay = 2^2 + jitter  =  ~4s
Attempt 3 failure  →  delay = 2^3 + jitter  =  ~8s
Attempt 4 failure  →  delay = 2^4 + jitter  =  ~16s
Attempt 5 failure  →  delay = 2^5 + jitter  =  ~32s
Attempt 6          →  status = dead, enqueue to dead_letter_queue
```

Jitter (`random.uniform(0, 1)` on exceptions, `random.uniform(0, 5)` on rate limits) prevents thundering herd when a downstream outage recovers and all paused jobs reschedule simultaneously.

### Provider-specific rate limit detection

**Google Drive:**

```python
# connectors/google_drive.py:27
if response.status_code == 403:
    errors = response.json().get("error", {}).get("errors", [])
    reasons = {e.get("reason") for e in errors}
    if reasons & {"rateLimitExceeded", "userRateLimitExceeded"}:
        return RateLimitInfo(is_rate_limited=True, retry_after_seconds=60)
```

Google uses `403` for quota errors (not `429`), and the status code alone is insufficient — a `403` could be a permissions error. The `reason` field inside the error body is the authoritative signal.

**Slack:**

```python
# connectors/slack.py:35
if response.status_code == 200:
    body = response.json()
    if not body.get("ok") and body.get("error") == "ratelimited":
        retry_after = int(response.headers.get("Retry-After", 60))
        return RateLimitInfo(is_rate_limited=True, retry_after_seconds=retry_after)
```

Slack returns rate limit signals inside `200` responses with `ok=false`. Checking only HTTP status codes misses this entirely.

---

## Idempotency

Jobs are deduplicated at two levels:

**1. Database unique constraint** — `connector_jobs.idempotency_key` has a `UNIQUE` constraint. Even if two processes attempt to insert simultaneously, one gets an `IntegrityError` which is caught and treated as a dedup signal. Redis-only deduplication would be lost on cache flush.

**2. Status-aware logic** — a job in `pending`, `running`, or `success` state blocks re-enqueue. A `failed` job within its retry budget can be re-enqueued (the caller retried explicitly).

```
idempotency_key construction:
  sync jobs    →  "sync:{integration_id}:{date}"       (one per day)
  action jobs  →  caller-supplied UUID or input hash
  webhook jobs →  "webhook:{slack_event_id}"            (Slack guarantees uniqueness)
```

---

## Incremental Sync

Google Drive sync is cursor-based, not timestamp-based. Timestamps require clock synchronization and miss renames/moves with unchanged `modifiedTime`. Page tokens are the correct primitive.

```
First sync:    no cursor → fetch from beginning, persist pageToken after each page
Mid-sync:      cursor from sync_cursors table → resume from last committed page
Sync complete: cursor updated to timestamp for next delta reference
```

The cursor lives in a dedicated `sync_cursors` table — one row per integration with a `UNIQUE` constraint on `integration_id`. Embedding it as JSONB on the `integrations` row would create update contention when sync and token refresh run concurrently.

Sync objects are upserted via `db.merge()` against the `UNIQUE (tenant_id, source, external_id)` constraint. A restart mid-sync is safe: already-processed objects are overwritten with identical normalized data, and the cursor resumes from the last committed page.

---

## Webhook Processing

```
Slack → POST /webhooks/slack/events
          │
          ├─ 1. Read raw body (before any parsing)
          ├─ 2. Verify HMAC-SHA256 signature (X-Slack-Signature vs signing secret)
          ├─ 3. Reject if timestamp > 5 minutes old (replay protection)
          ├─ 4. Handle url_verification challenge (synchronous, immediate)
          ├─ 5. enqueue_once(idempotency_key="webhook:{event_id}")
          └─ 6. Return HTTP 200 immediately
                   │
                   └─ Worker picks up run_webhook_job async
                        └─ SlackConnector.execute("process_event", payload)
```

The signature must be verified against the **raw bytes** of the request body before any JSON parsing. Parsing first would allow body manipulation attacks. Slack's `event_id` is the idempotency key — Slack guarantees uniqueness per event and will retry delivery if it doesn't receive a `200` within 3 seconds.

---

## Failure Mode Reference

| Scenario | Detection | Response |
|---|---|---|
| Token expired | `expires_at < now + 5min buffer` | Refresh transparently before execute |
| Refresh returns `invalid_grant` | HTTP 400 body on token endpoint | Mark integration `revoked`, stop retrying |
| Google rate limit | HTTP 403 + `rateLimitExceeded` reason | Backoff with jitter, **no retry_count increment** |
| Slack rate limit | HTTP 429 or `ok=false + ratelimited` | Respect `Retry-After`, **no retry_count increment** |
| Transient exception | Any uncaught exception in job runner | Exponential backoff, max 5 retries |
| Max retries exceeded | `retry_count >= max_retries` | Status `dead`, written to dead_letter_queue |
| Duplicate webhook | Same `event_id` arrives twice | idempotency_key blocks re-enqueue |
| Concurrent refresh | Second worker can't acquire Redis lock | Wait 500ms, read freshly-refreshed token |
| Worker crash mid-job | RQ job stuck in `started` | RQ heartbeat expires, job re-enqueued automatically |
| Integration revoked at API call | `token_revoked` in Slack response body | Mark `revoked` inline, fail job without retry |

---

## Extending to a New Provider

Adding a connector requires exactly two files. Nothing in the core — queue, retry logic, idempotency, normalization schemas, or the API layer — changes.

**Step 1:** `app/auth/oauth_{provider}.py`

```python
def {provider}_authorization_url(state: str) -> str: ...
def {provider}_exchange_code(code: str) -> dict: ...   # returns {access_token, refresh_token, expires_at, scopes}
def {provider}_refresh(refresh_token: str) -> dict: ...
```

**Step 2:** `app/connectors/{provider}.py`

```python
class MyProviderConnector(BaseConnector):
    def execute(self, action: str, payload: dict) -> dict:
        # dispatch to action-specific methods

    def handle_rate_limit(self, response) -> RateLimitInfo:
        # provider-specific rate limit detection
```

**Step 3:** Register in two places:

```python
# app/auth/router.py — add to provider allowlist
# app/connectors/__init__.py — add to get_connector() factory
```

That's it. Every retry, backoff, dead-letter, idempotency, and observability behavior is inherited automatically.

---

## Data Model

```
integrations
  id               UUID PK
  tenant_id        UUID  — strict row-level isolation
  provider         VARCHAR — 'google', 'slack'
  access_token     TEXT  — Fernet-encrypted, never logged
  refresh_token    TEXT  — nullable, Fernet-encrypted
  expires_at       TIMESTAMPTZ — NULL for non-expiring (Slack)
  scopes           TEXT[]
  status           ENUM (active | revoked | error)

connector_jobs
  id               UUID PK
  idempotency_key  VARCHAR UNIQUE — dedup anchor
  job_type         ENUM (sync | action | webhook)
  status           ENUM (pending | running | failed | success | dead)
  retry_count      INT   — only incremented on exceptions, not rate limits
  max_retries      INT
  payload          JSONB
  error_detail     TEXT
  scheduled_at     TIMESTAMPTZ — set by backoff, read by worker scheduler

external_objects
  UNIQUE (tenant_id, source, external_id) — safe upsert target
  data             JSONB — normalized form, never raw provider payload

sync_cursors
  integration_id   UUID UNIQUE — one cursor per integration
  cursor_type      ENUM (page_token | timestamp)
  value            TEXT
```

---

## Observability

All log output is structured JSON via `structlog`. Every log line carries `correlation_id` (injected by middleware on inbound requests, generated fresh for worker jobs) and `tenant_id`.

**Events always logged:**

```
token_refresh_started / token_refresh_succeeded / token_refresh_failed
job_enqueued / job_started / job_succeeded / job_failed / job_dead
job_deduplicated / job_deduplicated_race
rate_limit_detected / rate_limit_retry_scheduled
job_retry_scheduled
integration_revoked
webhook_received / webhook_enqueued / webhook_no_integration
```

Tokens are never logged. `error_detail` is truncated to 500 characters before persistence.

---

## Security

| Concern | Implementation |
|---|---|
| Token storage | Fernet symmetric encryption (`cryptography` library). Key lives in env, never in code or DB |
| Token logging | `encrypt_token` / `decrypt_token` are the only call sites — no plaintext ever reaches a logger |
| Webhook authenticity | HMAC-SHA256 over raw request body + timestamp. Replay window: 5 minutes |
| Tenant isolation | Every DB query is scoped by `tenant_id`. No cross-tenant joins exist |
| OAuth state forgery | State token stored in Redis with 10-minute TTL, deleted on first use (one-time token) |
| Refresh token exposure | Only decrypted inside `token_manager.py`, passed directly to the provider HTTP call |

---

## Project Structure

```
app/
├── auth/
│   ├── token_manager.py      # Redis lock, encrypt/decrypt, refresh lifecycle
│   ├── oauth_google.py       # Google authorization URL, code exchange, refresh
│   ├── oauth_slack.py        # Slack bot token flow, scope validation
│   └── router.py             # /connect/{provider} + /callback
├── connectors/
│   ├── base.py               # BaseConnector ABC, RateLimitInfo, error types
│   ├── google_drive.py       # Incremental sync, pagination, 403 detection
│   └── slack.py              # send_message, event processing, revocation
├── execution/
│   ├── queue.py              # default / retry / dead_letter queues
│   ├── idempotency.py        # enqueue_once with DB-level dedup + race guard
│   ├── rate_limit.py         # Jitter backoff, retry without budget consumption
│   ├── worker.py             # RQ worker entry point with scheduler
│   └── jobs/
│       ├── sync_job.py       # Google Drive sync runner
│       ├── action_job.py     # Generic action runner (send_message etc.)
│       ├── webhook_job.py    # Slack event processing runner
│       └── retry_job.py      # Backoff logic, dead-letter routing
├── models/
│   ├── integration.py
│   ├── connector_job.py
│   └── external_object.py    # ExternalObject + SyncCursor
├── normalization/
│   ├── schemas.py            # ExternalFile, ExternalMessage (Pydantic)
│   └── adapters.py           # Provider raw → unified schema
├── webhooks/
│   └── slack.py              # Signature verification, async enqueue, 200 fast
├── observability/
│   └── logging.py            # structlog config, correlation_id, context binding
├── config.py                 # pydantic-settings
├── db.py                     # SQLAlchemy engine, session, db_session() context manager
└── main.py                   # FastAPI app factory, correlation middleware
alembic/
└── versions/0001_initial_schema.py
```
