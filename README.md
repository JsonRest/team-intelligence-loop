# Team Intelligence Loop

A multi-agent AI system that replaces the daily standup meeting with a 3-minute async intelligence loop. Team members submit brief text updates via a custom web dashboard; the system extracts structured insights, detects cross-person dependencies, auto-schedules targeted 1:1s via Google Calendar MCP, delivers a synthesized digest to every inbox via Gmail MCP, and builds a growing decision journal — all stored in AlloyDB with pgvector semantic search.

**Stack:** Google ADK · Vertex AI (Gemini 2.5 Flash Lite) · workspace-mcp (Calendar + Gmail) · AlloyDB + pgvector · FastAPI · Cloud Run  
**GCP Project:** `genaiacademy-491713`

---

## Architecture

```
Browser / Judge
      ↓
til-frontend (Cloud Run)     ← judges visit this URL
  FastAPI BFF · custom dashboard · /api/* data endpoints
   ↙ POST /run                           ↘ direct data reads
  ↙                                       ↘
til-agent (Cloud Run · ADK)           AlloyDB · pgvector
Orchestrator + 4 sub-agents                5 tables
  ↓ Parser · Blocker
  ↓ Scheduler → Google Calendar MCP (workspace-mcp-til)
  ↓ Synthesizer → Gmail MCP (workspace-mcp-til)
        ↓
  Team digest output
```

**Three Cloud Run services:**
- `workspace-mcp-til` — Calendar + Gmail MCP server (always-on)
- `til-agent` — ADK orchestrator pipeline
- `til-frontend` — custom web dashboard; the URL you submit to judges

---

## Prerequisites

```bash
gcloud --version && uv --version
psql --version        # brew install libpq  (Mac)
python3 --version     # 3.11+ recommended
```

Enable APIs:

```bash
gcloud config set project genaiacademy-491713

gcloud services enable \
  alloydb.googleapis.com aiplatform.googleapis.com \
  calendar-json.googleapis.com gmail.googleapis.com \
  run.googleapis.com cloudbuild.googleapis.com \
  artifactregistry.googleapis.com secretmanager.googleapis.com
```

---

## 1 · workspace-mcp Setup (Google Calendar + Gmail via MCP)

### 1a — Create OAuth credentials (one-time)

1. GCP Console → **APIs & Services → Credentials**
2. **+ Create Credentials → OAuth 2.0 Client ID → Desktop App**
3. Name it `workspace-mcp-til` → **Create**
4. Go to **Data Access** → **Add or Remove Scopes** → add:
   - `https://www.googleapis.com/auth/calendar`
   - `https://www.googleapis.com/auth/gmail.send`
5. Go to **Audience** → **Test users** → add your email
6. Download the JSON file → rename to `client_secret.json` → move to repo root
7. Add to `.env`:

```
GOOGLE_WORKSPACE_CLIENT_ID=your-id.apps.googleusercontent.com
GOOGLE_WORKSPACE_CLIENT_SECRET=GOCSPX-your-secret
```

### 1b — Get refresh token for Cloud Run (one-time)

```bash
uv run python3 workspace-mcp-service/get_refresh_token.py
# Browser opens → grant Calendar + Gmail access → token printed
# Add GOOGLE_WORKSPACE_REFRESH_TOKEN to .env
# Also add the GOOGLE_OAUTH_* aliases (same values, different names for local dev):
# GOOGLE_OAUTH_CLIENT_ID=<same as GOOGLE_WORKSPACE_CLIENT_ID>
# GOOGLE_OAUTH_CLIENT_SECRET=<same as GOOGLE_WORKSPACE_CLIENT_SECRET>
# GOOGLE_OAUTH_REFRESH_TOKEN=<same as GOOGLE_WORKSPACE_REFRESH_TOKEN>
```

Add `client_secret*.json` to `.gitignore` — it contains OAuth secrets.

### 1c — Start MCP server (local dev only — skip for Cloud Run)

Open a **dedicated terminal** and keep it running:

```bash
source .env
PORT=8081 uvx workspace-mcp \
  --tools gmail calendar \
  --transport streamable-http \
  --single-user
```

> Note: workspace-mcp local dev requires a one-time browser OAuth flow to cache credentials in `~/.google_workspace_mcp/credentials/`. For Cloud Run, the refresh token is injected via Secret Manager — no browser auth needed.

---

## 2 · AlloyDB Setup

### 2a — Create cluster and instance

```bash
# Create cluster (no --database-flags here — that goes on the instance)
gcloud alloydb clusters create til-cluster \
  --region=us-central1 \
  --password=YOUR_SECURE_PASSWORD

# Create instance WITH the complexity flag (required before enabling public IP)
gcloud alloydb instances create til-primary \
  --cluster=til-cluster --region=us-central1 \
  --instance-type=PRIMARY --cpu-count=2 \
  --database-flags=password.enforce_complexity=on

# Enable public IP
gcloud alloydb instances update til-primary \
  --cluster=til-cluster --region=us-central1 \
  --assign-inbound-public-ip=ASSIGN_IPV4
```

### 2b — Start Auth Proxy (Mac Apple Silicon)

```bash
curl -o alloydb-auth-proxy \
  https://storage.googleapis.com/alloydb-auth-proxy/v1.13.1/alloydb-auth-proxy.darwin.arm64
chmod +x alloydb-auth-proxy

# Keep this terminal open
./alloydb-auth-proxy \
  "projects/genaiacademy-491713/locations/us-central1/clusters/til-cluster/instances/til-primary" \
  --public-ip --port=5432
```

### 2c — Create database and run schema

```bash
psql -h 127.0.0.1 -p 5432 -U postgres -c "CREATE DATABASE til_db;"
psql -h 127.0.0.1 -p 5432 -U postgres -d til_db -f schema.sql
```

If the seed INSERT fails with a syntax error, run it manually in psql:

```sql
INSERT INTO team_members (name, email, calendar_id) VALUES
    ('Jesse',   'jsliamzon@gmail.com',      'jsliamzon@gmail.com'),
    ('Angela',  'angela.teng@globe.com.ph', 'angela.teng@globe.com.ph'),
    ('Nikko',   'nikko.yabut@globe.com.ph', 'nikko.yabut@globe.com.ph');
```

---

## 3 · Local Development

```bash
git clone https://github.com/JsonRest/team-intelligence-loop.git
cd team-intelligence-loop
cp .env.example .env
# Fill in all values — see Section 5 for full variable list
```

```bash
uv venv && source .venv/bin/activate
uv pip install -r til_agent/requirements.txt
uv pip install -r requirements-frontend.txt
gcloud auth application-default login
gcloud auth application-default set-quota-project genaiacademy-491713
```

### Running locally (4 terminals)

```bash
# Terminal 1 — AlloyDB Auth Proxy
./alloydb-auth-proxy "projects/genaiacademy-491713/..." --public-ip --port=5432

# Terminal 2 — workspace-mcp server (local dev only)
source .env
PORT=8081 uvx workspace-mcp --tools gmail calendar --transport streamable-http --single-user

# Terminal 3 — ADK agent
source .env
uv run adk web   # runs til-agent on http://localhost:8000

# Terminal 4 — Frontend dashboard
source .env
uv run uvicorn server:app --port 8080
# Open http://localhost:8080
```

---

## 4 · Deploy to Cloud Run

Deploy in this order: workspace-mcp first, then til-agent, then til-frontend.

### 4a — Deploy workspace-mcp service (always-on)

```bash
bash workspace-mcp-service/deploy.sh
# → stores refresh token in Secret Manager
# → builds and pushes Docker image
# → deploys workspace-mcp-til to Cloud Run with min-instances=1
# → prints URL: https://workspace-mcp-til-XXXX.us-central1.run.app
```

Update `.env` with the printed URL:

```
WORKSPACE_MCP_URL=https://workspace-mcp-til-XXXX.us-central1.run.app/mcp
```

### 4b — Deploy til-agent (ADK orchestrator)

```bash
uv run adk deploy cloud_run \
  --project=genaiacademy-491713 \
  --region=us-central1 \
  --service_name=til-agent \
  til_agent \
  -- \
  --min-instances=1 \
  --set-env-vars="WORKSPACE_MCP_URL=https://workspace-mcp-til-XXXX.us-central1.run.app/mcp,ALLOYDB_INSTANCE_URI=projects/genaiacademy-491713/locations/us-central1/clusters/til-cluster/instances/til-primary,DB_USER=postgres,DB_PASSWORD=YOUR_PASSWORD,DB_NAME=til_db,MODEL=gemini-2.5-flash-lite,GOOGLE_CLOUD_PROJECT=genaiacademy-491713,GOOGLE_CLOUD_LOCATION=us-central1,GOOGLE_GENAI_USE_VERTEXAI=True"
# Note: do NOT use --with_ui
```

### 4c — Deploy til-frontend (custom dashboard) ← judges visit this URL

```bash
gcloud run deploy til-frontend \
  --source . \
  --region=us-central1 \
  --project=genaiacademy-491713 \
  --allow-unauthenticated \
  --min-instances=1 \
  --set-env-vars="ADK_API_URL=https://til-agent-XXXX.us-central1.run.app,\
ALLOYDB_INSTANCE_URI=projects/genaiacademy-491713/locations/us-central1/clusters/til-cluster/instances/til-primary,\
DB_USER=postgres,DB_PASSWORD=YOUR_PASSWORD,DB_NAME=til_db"
```

**Submit the `til-frontend` URL to judges.** This is the dashboard they will test.

---

## 5 · Environment Variables

| Variable | Service | Description |
|---|---|---|
| `GOOGLE_CLOUD_PROJECT` | til-agent | GCP project ID |
| `GOOGLE_CLOUD_LOCATION` | til-agent | Region (us-central1) |
| `GOOGLE_GENAI_USE_VERTEXAI` | til-agent | Must be `True` |
| `MODEL` | til-agent | `gemini-2.5-flash-lite` (quota-safe) |
| `ALLOYDB_INSTANCE_URI` | Both | Full AlloyDB instance URI |
| `DB_USER` / `DB_PASSWORD` / `DB_NAME` | Both | AlloyDB credentials |
| `GOOGLE_WORKSPACE_CLIENT_ID` | workspace-mcp | Desktop App OAuth client ID (Cloud Run env var name) |
| `GOOGLE_WORKSPACE_CLIENT_SECRET` | workspace-mcp | Desktop App OAuth secret (Cloud Run env var name) |
| `GOOGLE_WORKSPACE_REFRESH_TOKEN` | workspace-mcp | Long-lived OAuth refresh token (stored in Secret Manager) |
| `GOOGLE_OAUTH_CLIENT_ID` | local dev | Same value as WORKSPACE — name workspace-mcp reads locally |
| `GOOGLE_OAUTH_CLIENT_SECRET` | local dev | Same value as WORKSPACE — name workspace-mcp reads locally |
| `GOOGLE_OAUTH_REFRESH_TOKEN` | local dev | Same value as WORKSPACE — name workspace-mcp reads locally |
| `MCP_SINGLE_USER_MODE` | local dev | Set `true` for local single-user workspace-mcp |
| `WORKSPACE_MCP_URL` | til-agent | URL of the workspace-mcp service + `/mcp` path |
| `ADK_API_URL` | til-frontend | URL of the til-agent service |
| `TEAM_EMAILS` | til-agent | Comma-separated recipient emails (no spaces) |

---

## 6 · Repo Structure

```
team-intelligence-loop/
├── til_agent/                ← ADK agent package
│   ├── __init__.py           ← lazy import guard (frontend-safe)
│   ├── agent.py              ← orchestrator + 4 sub-agents + McpToolset
│   ├── database.py           ← AlloyDB tools using pg8000 driver
│   ├── google_tools.py       ← direct API fallback (not used in MCP mode)
│   ├── prompts.py            ← system prompts for all 5 agents
│   └── requirements.txt      ← MUST be inside the package folder
├── frontend/
│   └── index.html            ← dashboard (3 views: Digest · Submit · Decisions)
├── workspace-mcp-service/
│   ├── Dockerfile
│   ├── get_refresh_token.py
│   └── deploy.sh
├── server.py                 ← FastAPI BFF (serves dashboard + /api/* endpoints)
├── Dockerfile                ← for til-frontend Cloud Run
├── requirements-frontend.txt
├── schema.sql
├── .env.example
├── .gitignore
└── README.md
```

---

## 7 · Known Issues and Fixes

| Issue | Fix |
|---|---|
| `--database-flags` rejected on cluster create | Move flag to `gcloud alloydb instances create` instead |
| `Driver 'psycopg2' is not supported` | alloydb-connector 1.12.1+ requires pg8000 — use `[pg8000]` extra |
| `Type uuid.UUID not serialisable` | pg8000 returns UUID objects — add `uuid.UUID` to `_serialize()` in database.py |
| `No root_agent found for 'til_orchestrator'` | Agent folder is `til_agent` — update `server.py` app_name references |
| `MCPToolset is deprecated` | Use `McpToolset` (new capitalization) from same import path |
| `gemini-2.5-flash` quota error | Use `MODEL=gemini-2.5-flash-lite` |
| `429 RESOURCE_EXHAUSTED` | Wait 2–3 minutes between test submissions — per-minute quota |
| Auth Proxy fails on public IP | Set `password.enforce_complexity=on` on instance create before enabling public IP |
| `alloydb-auth-proxy` permission denied | `chmod +x alloydb-auth-proxy` |
| ADK session required before `/run` | `server.py` handles session creation automatically |
| til-frontend can't import til_agent | `__init__.py` uses lazy import — ADK not required in frontend container |
| `ValueError: a coroutine was expected, got None` on Cloud Run startup | `google-workspace-mcp 2.0.1` has a breaking change — pin to `==1.17.3` in Dockerfile |
| `Permission denied on secret` during Cloud Run deploy | Grant Secret Accessor role: `gcloud secrets add-iam-policy-binding workspace-mcp-refresh-token --member="serviceAccount:PROJECT_NUMBER-compute@developer.gserviceaccount.com" --role="roles/secretmanager.secretAccessor"` |
| Cold start delays during eval | Deploy all services with `--min-instances=1` |
| workspace-mcp OAuth 2.1 + single-user conflict | Use only `--single-user` flag locally, no `MCP_ENABLE_OAUTH21` |

---

## 8 · Clean Up

```bash
gcloud run services delete til-frontend --region=us-central1 --quiet
gcloud run services delete til-agent --region=us-central1 --quiet
gcloud run services delete workspace-mcp-til --region=us-central1 --quiet
gcloud alloydb clusters delete til-cluster --region=us-central1 --quiet
gcloud secrets delete workspace-mcp-refresh-token --quiet
```
