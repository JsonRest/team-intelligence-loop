"""
Team Intelligence Loop — Frontend Server (BFF pattern)

Serves the dashboard HTML and provides REST endpoints:
  GET  /                     → dashboard HTML
  GET  /api/team             → team members from AlloyDB
  GET  /api/standups/{date}  → standups + parsed items from AlloyDB
  GET  /api/blockers/{date}  → active blockers from AlloyDB
  GET  /api/decisions        → recent decisions from AlloyDB
  POST /api/standup          → submit standup → calls ADK agent API

Deploy:
  gcloud run deploy til-frontend \\
    --source . --region us-central1 \\
    --set-env-vars="ADK_API_URL=https://til-agent-XXXX.us-central1.run.app"
"""
import json
import os
import uuid
from pathlib import Path

import httpx
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse
from pydantic import BaseModel

load_dotenv()

from til_agent.database import (
    get_active_blockers  as _blockers,
    get_parsed_items     as _items,
    get_recent_decisions as _decisions,
    get_standups_for_day as _standups,
    get_team_members     as _team,
)

app = FastAPI(title="Team Intelligence Loop Dashboard")
app.add_middleware(
    CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"]
)

ADK_API_URL  = os.getenv("ADK_API_URL",  "http://localhost:8000")
FRONTEND_DIR = Path(__file__).parent / "frontend"


# ── Static ─────────────────────────────────────────────────────

@app.get("/", response_class=HTMLResponse)
async def index():
    return (FRONTEND_DIR / "index.html").read_text()


@app.get("/health")
async def health():
    return {"status": "ok"}


# ── Data endpoints ─────────────────────────────────────────────

@app.get("/api/team")
async def get_team():
    try:
        return json.loads(_team())
    except Exception as e:
        raise HTTPException(500, str(e))


@app.get("/api/standups/{sprint_day}")
async def get_standups(sprint_day: str):
    try:
        data = json.loads(_standups(sprint_day))
        data["items"] = json.loads(_items(sprint_day)).get("items", [])
        return data
    except Exception as e:
        raise HTTPException(500, str(e))


@app.get("/api/blockers/{sprint_day}")
async def get_blockers(sprint_day: str):
    try:
        return json.loads(_blockers(sprint_day))
    except Exception as e:
        raise HTTPException(500, str(e))


@app.get("/api/decisions")
async def get_decisions():
    try:
        return json.loads(_decisions(10))
    except Exception as e:
        raise HTTPException(500, str(e))


# ── Agent interaction ──────────────────────────────────────────

class StandupRequest(BaseModel):
    member_email: str
    sprint_day: str
    yesterday: str
    today: str
    blocker: str = ""


@app.post("/api/standup")
async def submit_standup(req: StandupRequest):
    safe_name  = req.member_email.split("@")[0].replace(".", "_")
    user_id    = f"u_{safe_name}"
    session_id = f"s_{uuid.uuid4().hex[:8]}"

    lines = [f"Yesterday: {req.yesterday}", f"Today: {req.today}"]
    if req.blocker.strip():
        lines.append(f"Blockers: {req.blocker}")

    message = (
        f"Process this standup for {req.member_email} on {req.sprint_day}:\n\n"
        + "\n".join(lines)
    )

    try:
        async with httpx.AsyncClient(timeout=180.0) as client:
            # 1 — create ADK session
            r = await client.post(
                f"{ADK_API_URL}/apps/til_agent/users/{user_id}/sessions/{session_id}",
                json={"state": {}},
            )
            r.raise_for_status()

            # 2 — run the orchestrator pipeline
            r = await client.post(
                f"{ADK_API_URL}/run",
                json={
                    "app_name":   "til_agent",
                    "user_id":    user_id,
                    "session_id": session_id,
                    "new_message": {
                        "role":  "user",
                        "parts": [{"text": message}],
                    },
                },
            )
            r.raise_for_status()
            return {"status": "submitted", "session_id": session_id, "result": r.json()}

    except httpx.HTTPStatusError as e:
        raise HTTPException(status_code=e.response.status_code, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
