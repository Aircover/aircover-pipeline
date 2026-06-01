# Aircover Pipeline

A Python script that pulls a list of meetings from the Aircover API for a date range and runs a coaching agent template against each one.

## Install

```sh
pip install aircover-pipeline
```

Then either run the CLI directly:

```sh
aircover-pipeline --list-templates
aircover-pipeline --start 2026-01-01 --end 2026-03-31 \
                  --template-id <id> --output-dir ./out
```

Or import the client in your own code:

```python
from aircover_client import AircoverClient
client = AircoverClient(username="...", password="...")
meetings = client.get_meetings(start="2026-01-01", end="2026-03-31")
```

See [Authentication](#authentication) for credential setup (`.env` is recommended).

## What this does

1. Authenticates to the Aircover API.
2. Calls `GET /analytics/` to fetch every meeting in the requested date range (auto-chunked into 3-month windows; results deduplicated).
3. For each meeting, calls `GET /transcript/coaching` with a chosen template id and writes the agent's structured output to a JSON file.

Output structure (under `--output-dir`):

```
out/
├── meetings.json                  # raw meeting list from /analytics
├── agent-outputs/
│   ├── agent_<meeting_id>.json    # one file per processed meeting
│   └── ...
├── failures.json                  # only if any rows failed; lists meeting ids
└── summary.json                   # totals per run
```

## Requirements

- Python 3.10 or newer
- `requests` (installed via `requirements.txt`)
- An Aircover account with API access

## Setup

Three equivalent ways to install. Pick whichever fits your workflow.

**Pip into a virtualenv (most common):**

```sh
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

**Pip install (registers an `aircover-pipeline` command on your PATH):**

```sh
python3 -m venv .venv
source .venv/bin/activate
pip install .
# Now you can run from anywhere:
aircover-pipeline --list-templates
```

**Makefile shortcuts (for those who prefer `make`):**

```sh
make install              # creates .venv and installs deps
make list-templates       # lists templates
make run ARGS='--start 2026-01-01 --end 2026-03-31 \
               --template-id <id> --output-dir ./out'
make dry-run ARGS='--start 2026-01-01 --end 2026-03-31 \
                   --template-id <id> --output-dir ./out'
make clean                # removes venv and output dirs
```

## Authentication

Two ways to authenticate. Pick whichever applies to your Aircover account.

> ⚠️ **Don't pass credentials as CLI flags.** `--username`, `--password`, `--access-token`, and `--refresh-token` work for ad-hoc testing but leak to shell history, process listings, and CI logs. For anything beyond a one-off, put credentials in `.env` (gitignored) or pass them via environment variables.

### Option A — Username + password

Best for accounts that log in directly to Aircover (not via SSO).

```sh
cp .env.example .env
# Edit .env and set:
#   AIRCOVER_USERNAME=your-username@your-company.com
#   AIRCOVER_PASSWORD=your-password
```

The script will read `.env` automatically.

### Option B — Bearer token (SSO-only accounts)

If your Aircover account logs in via SSO (Google, Okta, etc.), `/auth/login` will reject your password. You need to grab a bearer token from the web app's localStorage instead. Three ways, in order of ease:

#### Method 1 — Bookmarklet (recommended)

One-time setup, then one click per terminal session.

1. In Chrome, press **Cmd+Shift+B** (Mac) or **Ctrl+Shift+B** (Windows/Linux) to show the bookmarks bar.
2. Right-click the bookmarks bar → **Add page**.
3. **Name:** `Aircover token`. **URL:** paste this entire line (one continuous URL — wrapping in this file is for display only):

   ```
   javascript:(()=>{const raw=localStorage.getItem('GetStorage');if(!raw)return alert('GetStorage not found. Are you signed in to app.aircover.ai?');try{const o=JSON.parse(raw);const a=typeof o.authInfo==='string'?JSON.parse(o.authInfo):o.authInfo;if(a?.access_token&&a?.refresh_token){const t='export AIRCOVER_ACCESS_TOKEN='+JSON.stringify(a.access_token)+'\nexport AIRCOVER_REFRESH_TOKEN='+JSON.stringify(a.refresh_token);navigator.clipboard.writeText(t).then(()=>alert('Tokens copied to clipboard.'));}else alert('No tokens in authInfo.');}catch(e){alert('Parse failed: '+e.message);}})();
   ```

4. Click **Save**.

**To use:** sign into <https://app.aircover.ai>, then click the **Aircover token** bookmark on the bookmarks bar. An alert confirms "Tokens copied to clipboard." Paste into your terminal — two `export` lines drop in. Hit Enter to set them in the current shell.

> Note: pasting the `javascript:` URL directly into Chrome's address bar does **not** work — Chrome strips the `javascript:` prefix for security. The bookmark route bypasses this.

#### Method 2 — Browser console snippet (no bookmark setup)

1. Sign into <https://app.aircover.ai>.
2. Open DevTools (F12 or Cmd+Opt+I), click the **Console** tab.
3. Paste this and press Enter:

   ```js
   (() => {
     const raw = localStorage.getItem('GetStorage');
     if (!raw) return console.warn('GetStorage not found.');
     try {
       const outer = JSON.parse(raw);
       const auth = typeof outer.authInfo === 'string' ? JSON.parse(outer.authInfo) : outer.authInfo;
       if (auth?.access_token && auth?.refresh_token) {
         console.log('export AIRCOVER_ACCESS_TOKEN=' + JSON.stringify(auth.access_token));
         console.log('export AIRCOVER_REFRESH_TOKEN=' + JSON.stringify(auth.refresh_token));
       } else {
         console.warn('authInfo found but missing tokens.');
       }
     } catch (e) {
       console.error('Parse failed:', e);
     }
   })();
   ```

4. Two `export ...` lines print in the console. Select them with your mouse, **Cmd+C** to copy, paste in terminal.

> The console version doesn't auto-copy to clipboard (Chrome's clipboard API blocks writes when DevTools is the focused panel rather than the page). The bookmarklet version doesn't hit this restriction.

#### Method 3 — Manual extraction (deep fallback)

If both scripts fail (e.g., a browser other than Chrome with a different storage layout):

1. In DevTools, go to **Application → Local Storage → `https://app.aircover.ai`**.
2. Click the `GetStorage` row. The value is a JSON string with an `authInfo` field nested inside.
3. Paste the value into a JSON formatter (e.g., jsonformatter.org) and locate `authInfo.access_token` and `authInfo.refresh_token`.
4. Set them in your shell.

#### Once you have the tokens

Either export them in your shell:

```sh
export AIRCOVER_ACCESS_TOKEN='eyJhbGciOi...'
export AIRCOVER_REFRESH_TOKEN='eyJhbGciOi...'
```

Or put them in `.env` (gitignored) for persistence across terminal sessions:

```
AIRCOVER_ACCESS_TOKEN=eyJhbGciOi...
AIRCOVER_REFRESH_TOKEN=eyJhbGciOi...
```

Or pass them on the command line via `--access-token` / `--refresh-token` (not recommended — leaks to shell history).

The refresh token typically lives ~60 days. The access token lives ~1 hour and is auto-refreshed by the script as long as the refresh token is set.


### Security

`.env` is in `.gitignore` and will not be committed. The refresh token is long-lived and grants the same permissions as a full login session — treat it like a password.

## Usage

### 1. Find a coaching template id

```sh
python aircover_pipeline.py --list-templates
```

Output:

```
Available coaching templates (3):
  My QBR Template
    id: WVrzuxuavDAQttxogZaHfZ

  Customer Health Check
    id: a1B2c3D4e5F6g7H8i9J0kL
  ...
```

### 2. Run the pipeline

```sh
python aircover_pipeline.py \
  --start 2026-01-01 --end 2026-03-31 \
  --template-id WVrzuxuavDAQttxogZaHfZ \
  --output-dir ./out
```

### 3. Test on a small batch first

```sh
python aircover_pipeline.py \
  --start 2026-01-01 --end 2026-03-31 \
  --template-id WVrzuxuavDAQttxogZaHfZ \
  --output-dir ./out \
  --limit 5
```

When combined with `--resume`, `--limit N` means "process up to **N new** meetings this run" — already-done rows are skipped first, then the limit is applied to what remains. So `--limit 10 --resume` always processes up to 10 new meetings, regardless of how many were already finished in prior runs.

### 4. Resume after a crash

If the run stops partway through (timeout, network blip, etc.), re-run with `--resume`. It will skip any meetings that already have an output file and only process the remaining ones. Atomic writes mean a half-written file from a crash will not be mistaken for completed work.

```sh
python aircover_pipeline.py ... --resume
```

### 5. Filter the meeting list

```sh
# Drop meetings whose deal_id starts with one of these domains
--exclude-domains example.com,internal.test

# Keep only meetings whose notes_sent_to includes one of these emails
--filter-emails alice@your-company.com,bob@your-company.com

# Keep only meetings whose team_ids includes at least one of these team IDs
--filter-teams 1,3,7
```

### 6. Point at a different API environment

By default the script talks to `https://api.aircover.ai`. Override via `--base-url` or `AIRCOVER_BASE_URL` for staging/sandbox environments:

```sh
# CLI
python aircover_pipeline.py --base-url https://stageapi.aircover.ai ...

# Or in .env
AIRCOVER_BASE_URL=https://stageapi.aircover.ai
```

Useful if Aircover provisioned you a staging tenant for testing integrations before going live against production data.

### 7. Print version

```sh
python aircover_pipeline.py --version
# → aircover-pipeline 1.0.3
```

Same value is recorded in every `summary.json` as `package_version` for audit trails.

### 8. Dry-run preview

Pull the meeting list and apply filters, but skip the coaching calls. Lets you see how many meetings would be processed before committing the time.

```sh
python aircover_pipeline.py \
  --start 2026-01-01 --end 2026-03-31 \
  --template-id <id> --output-dir ./out \
  --dry-run
```

Output: `meetings.json` and `summary.json` (with `dry_run: true`). No `agent-outputs/`.

## Output schema

The output files follow a stable schema. The `schema_version` field in `summary.json` will be bumped if the field set or semantics change in a breaking way. As of `1.0`:

### `meetings.json`

```json
[
  {
    "id": "meeting_id_here",
    "deal_id": "<prospect-domain>/<id>",
    "team_ids": "12, 34",
    "date": "2026-03-15",
    "notes_sent_to": "alice@example.com, bob@example.com"
  }
]
```

### `agent-outputs/agent_<meeting_id>.json`

```json
{
  "meeting_id": "...",
  "deal_id": "...",
  "date": "2026-03-15",
  "template_id": "WVrzuxuavDAQttxogZaHfZ",
  "properties": {
    "<property_id>": {
      "title": "Pain Points",
      "result": "Customer mentioned ...",
      "score": 4,
      "max_score": 5
    },
    ...
  }
}
```

### `summary.json`

```json
{
  "schema_version": "1.0",
  "package_version": "1.0.3",
  "ran_at": "2026-05-31T16:30:00+00:00",
  "start": "2026-01-01",
  "end": "2026-03-31",
  "template_id": "...",
  "base_url": "https://api.aircover.ai",
  "filters": {
    "exclude_domains": null,
    "filter_emails": null,
    "filter_teams": null,
    "limit": null
  },
  "meetings_in_window": 120,
  "processed_this_run": 120,
  "success": 115,
  "failed": 5,
  "skipped_resume": 0
}
```

When `--dry-run` is used, `summary.json` also includes `"dry_run": true` and omits the `success` / `failed` / `processed_this_run` fields.

### Formal JSON Schemas

Machine-readable JSON Schema files for each output type are in [`schemas/`](schemas/):

| File | Validates |
|------|-----------|
| [`schemas/meetings-1.0.json`](schemas/meetings-1.0.json) | `meetings.json` |
| [`schemas/agent-output-1.0.json`](schemas/agent-output-1.0.json) | `agent-outputs/agent_<meeting_id>.json` |
| [`schemas/summary-1.0.json`](schemas/summary-1.0.json) | `summary.json` |
| [`schemas/failures-1.0.json`](schemas/failures-1.0.json) | `failures.json` |

Use them to validate output programmatically in your downstream pipeline. Example with Python's `jsonschema` package:

```sh
pip install jsonschema
```

```python
import json
from jsonschema import validate

with open("out/meetings.json") as f:
    meetings = json.load(f)
with open("schemas/meetings-1.0.json") as f:
    schema = json.load(f)

validate(meetings, schema)  # raises ValidationError on schema mismatch
```

The `schema_version` field in `summary.json` indicates which schema family this run conforms to. Breaking changes to the output structure will increment this version and ship new `*-2.0.json` schemas alongside the old ones, so you can migrate on your own timeline.

### `failures.json` (only when any rows failed)

```json
[
  {"meeting_id": "...", "deal_id": "...", "date": "..."},
  ...
]
```

## Exit codes

| Code | Meaning |
|------|---------|
| 0    | All attempted rows succeeded (or no meetings to process) |
| 1    | Partial failure — some rows succeeded, some failed; see `failures.json` |
| 2    | Auth/setup error, all `/analytics/` chunks failed, OR every attempted row failed |
| 3    | Uncaught exception / interrupted (Ctrl+C) |

Useful for cron / CI integration: a `0` is clean, a `1` means investigate failures.json, a `2` means look at logs and fix something before re-running, a `3` means the script crashed — file a bug.

## Common issues

| Symptom | Likely cause |
|---------|--------------|
| `401 Unauthorized` on every meeting | Access token expired and no refresh token. Re-grab tokens from web app or use username/password auth. |
| Empty `meetings.json` even with valid date range | Your account doesn't have access to any meetings in that window. Check the date range and your account's permissions. |
| `404 Not Found` on `/analytics/` | Your account doesn't have access to that endpoint. Talk to your Aircover contact. |
| `HTTP 500 {"errors":["Unable to read deal"]}` for some rows | Meeting exists but has no Salesforce deal linked. Expected for internal meetings or pre-SFDC meetings; safe to ignore. |
| Per-row `Coaching call failed` log lines | Either the meeting has no transcript, or your token can't access that meeting's organization. The row is recorded in `failures.json`. |

## Verbose logging

Pass `--verbose` to enable debug-level logs. Useful for diagnosing per-meeting failures.

## Running tests

```sh
make test
# or directly:
pip install -e ".[test]"
pytest tests/ -v
```

71 unit tests cover credential loading (env vars / .env file / kwargs precedence), token refresh + re-login fallback, 401 retry, 5xx/429 retry with `Retry-After` honoring, malformed-JSON tolerance on every endpoint, atomic write semantics, date-format validation, meeting response parsing, date-range chunking, distinguishing all-chunks-failed from empty window, the three CLI filter helpers, end-to-end `--limit`/`--resume` ordering, and `summary.json` audit metadata. No real HTTP — all networking is mocked.

## Support

Questions or issues: **support@aircover.ai**.

When reporting a problem, include:

- The exact command you ran (redact tokens / passwords).
- The relevant output (with `--verbose` if a particular row failed).
- The contents of `summary.json` and `failures.json` if relevant.

## License

MIT. See [LICENSE.txt](LICENSE.txt).
