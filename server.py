"""
Team Intelligence Loop — Frontend Server (BFF pattern)

Serves the dashboard HTML and provides REST endpoints:
  GET  /                     → dashboard HTML
  GET  /api/team             → team members from AlloyDB
  GET  /api/standups/{date}  → standups + parsed items from AlloyDB
  GET  /api/blockers/{date}  → active blockers from AlloyDB
  GET  /api/decisions        → recent decisions from AlloyDB
  POST /api/standup          → submit standup → calls ADK agent API

After the ADK pipeline runs, server.py ALSO directly:
  - stores standup + parsed items via database tools
  - detects blockers from parsed items and stores them
  - sends the digest email via google_tools

This guarantees delivery regardless of whether the model calls tools.
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
    store_standup        as _store_standup,
    store_parsed_items   as _store_parsed_items,
    store_blocker        as _store_blocker,
    update_blocker_status as _update_blocker_status,
    get_standups_for_day,
    get_parsed_items,
    get_team_members,
)
from til_agent.google_tools import (
    send_digest_email,
    check_calendar_availability,
    create_calendar_event,
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


# ── Guaranteed post-pipeline steps ────────────────────────────

def _guarantee_blocker_detection(sprint_day: str):
    """
    After the agent pipeline runs, directly detect blockers from parsed_items
    and store any that haven't been stored yet. This guarantees blocker
    detection regardless of whether the model called store_blocker.
    """
    try:
        items_data = json.loads(get_parsed_items(sprint_day))
        items = items_data.get("items", [])
        team_data = json.loads(get_team_members())
        members = team_data.get("members", [])

        # Build name → email map (lowercase for matching)
        name_to_email = {m["name"].lower(): m["email"] for m in members}
        email_to_name = {m["email"]: m["name"] for m in members}

        # Find all blocker items
        blockers_stored = 0
        for item in items:
            if item.get("category") != "blocker":
                continue
            blocker_text = item.get("content", "")
            owner_email = item.get("email", "")
            if not blocker_text or not owner_email:
                continue

            # Try to match a team member name in the blocker text
            blocked_by_email = None
            for name, email in name_to_email.items():
                if name in blocker_text.lower() and email != owner_email:
                    blocked_by_email = email
                    break

            if blocked_by_email:
                # Skip if identical blocker already exists today
                existing = json.loads(_blockers(sprint_day))
                already_exists = any(
                    b.get("description", "").lower() == blocker_text.lower()
                    for b in existing.get("blockers", [])
                )
                if already_exists:
                    continue
                result = json.loads(_store_blocker(
                    owner_email, blocked_by_email, blocker_text, sprint_day
                ))
                if result.get("blocker_id"):
                    blockers_stored += 1
                    print(f"[TIL] Stored blocker: {owner_email} ← {blocked_by_email}", flush=True)

        return blockers_stored
    except Exception as e:
        print(f"[TIL] Blocker detection error: {e}", flush=True)
        return 0


def _guarantee_calendar_events(sprint_day: str):
    """
    After blocker detection, directly book 1:1 calendar events for each
    active blocker that hasn't been scheduled yet.
    """
    try:
        blockers_data = json.loads(_blockers(sprint_day))
        blockers = blockers_data.get("blockers", [])

        events_created = 0
        for b in blockers:
            if b.get("status") != "active":
                continue
            owner_email   = b.get("owner_email", "")
            blocker_email = b.get("blocker_email", "")
            if not owner_email or not blocker_email:
                continue

            owner_name   = b.get("owner_name", owner_email)
            blocker_name = b.get("blocker_name", blocker_email)

            # Find available slot
            avail = json.loads(check_calendar_availability(
                owner_email, blocker_email, sprint_day
            ))
            slots = avail.get("available_slots", [])
            if not slots:
                continue

            slot = slots[0]
            title = (
                f"TIL 1:1: {b.get('description', 'Blocker')[:40]} "
                f"— {owner_name} x {blocker_name}"
            )
            result = json.loads(create_calendar_event(
                owner_email, blocker_email,
                title, slot["start"], slot["end"]
            ))
            if result.get("status") == "created":
                events_created += 1
                _update_blocker_status(b["id"], "scheduled",
                    f"1:1 booked: {slot['start']}")
                print(f"[TIL] Calendar event created: {title}", flush=True)
            else:
                print(f"[TIL] Calendar event failed: {result}", flush=True)

        return events_created
    except Exception as e:
        print(f"[TIL] Calendar event error: {e}", flush=True)
        return 0


def _guarantee_digest_email(sprint_day: str):
    """
    After the agent pipeline runs, directly send the digest email.
    This guarantees email delivery regardless of whether the model
    called send_digest_email.
    """
    try:
        standups_data = json.loads(get_standups_for_day(sprint_day))
        standups = standups_data.get("standups", [])
        if not standups:
            print(f"[TIL] No standups for {sprint_day}, skipping digest", flush=True)
            return False

        team_data = json.loads(get_team_members())
        members = team_data.get("members", [])
        items_data = json.loads(get_parsed_items(sprint_day))
        items = items_data.get("items", [])
        blockers_data = json.loads(_blockers(sprint_day))
        blockers = blockers_data.get("blockers", [])

        # Build digest text — deduplicate: keep most recent standup per member
        seen_emails = set()
        unique_standups = []
        for s in sorted(standups, key=lambda x: x.get("submitted_at", ""), reverse=True):
            if s.get("email") not in seen_emails:
                seen_emails.add(s.get("email"))
                unique_standups.append(s)

        lines = [
            f"SPRINT DIGEST — {sprint_day}",
            "Team Intelligence Loop",
            "=" * 50,
            "",
            "TEAM STATUS",
        ]

        for s in unique_standups:
            name = s.get("name", s.get("email", "Unknown"))
            member_items = [i for i in items if i.get("email") == s.get("email")]
            yesterday = next((i["content"] for i in member_items if i["category"] == "yesterday"), "")
            today = next((i["content"] for i in member_items if i["category"] == "today"), "")
            blocker = next((i["content"] for i in member_items if i["category"] == "blocker"), "")
            lines.append(f"\n{name}")
            if yesterday:
                lines.append(f"  ✓ {yesterday}")
            if today:
                lines.append(f"  → {today}")
            if blocker:
                lines.append(f"  ⚠ BLOCKED: {blocker}")

        lines.append("\nBLOCKERS & RESOLUTIONS")
        if blockers:
            for b in blockers:
                lines.append(f"  ⚠ {b.get('description', '')}")
        else:
            lines.append("  ✓ No cross-person blockers today")

        lines.append("\n" + "=" * 50)
        lines.append("Sent by Team Intelligence Loop · Google ADK + Vertex AI + AlloyDB")

        digest_text = "\n".join(lines)
        to_emails = ",".join(m["email"] for m in members)
        subject = f"Sprint Digest {sprint_day} | Team Intelligence Loop"

        result = json.loads(send_digest_email(to_emails, subject, digest_text))
        if result.get("status") == "sent":
            print(f"[TIL] Digest sent to {result.get('count')} recipients", flush=True)
            return True
        else:
            print(f"[TIL] Digest send failed: {result}", flush=True)
            return False
    except Exception as e:
        print(f"[TIL] Digest email error: {e}", flush=True)
        return False


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

    adk_result = None
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
            adk_result = r.json()

    except Exception as e:
        print(f"[TIL] ADK pipeline error: {e}", flush=True)
        # Continue to guarantee steps even if ADK fails

    # 3 — Guaranteed steps: run regardless of ADK outcome
    # Only store directly if ADK didn't already store a standup for this member/day
    try:
        raw_text = "\n".join(lines)
        existing = json.loads(get_standups_for_day(req.sprint_day))
        # Only skip if exact same content already stored (prevent ADK double-store)
        already_stored = any(
            s.get("email") == req.member_email and
            s.get("raw_text", "").strip() == raw_text.strip()
            for s in existing.get("standups", [])
        )
        if not already_stored:
            store_result = json.loads(_store_standup(req.member_email, req.sprint_day, raw_text))
            standup_id = store_result.get("standup_id")
            if standup_id:
                _store_parsed_items(
                    standup_id, req.yesterday, req.today,
                    req.blocker, req.member_email
                )
    except Exception as e:
        print(f"[TIL] Direct store error: {e}", flush=True)

    # 4 — Detect and store blockers
    blockers_found = _guarantee_blocker_detection(req.sprint_day)

    # 5 — Create calendar events for unscheduled blockers
    events_created = _guarantee_calendar_events(req.sprint_day)

    # 6 — Send digest email
    email_sent = _guarantee_digest_email(req.sprint_day)

    return {
        "status": "submitted",
        "session_id": session_id,
        "result": adk_result,
        "guaranteed": {
            "blockers_stored": blockers_found,
            "calendar_events": events_created,
            "email_sent": email_sent,
        }
    }
