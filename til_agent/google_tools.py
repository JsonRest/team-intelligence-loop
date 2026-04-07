"""
Google Calendar and Gmail tools for Team Intelligence Loop.

These are implemented as direct Google API calls.
To swap in MCPToolset once a public MCP endpoint is available:

    from google.adk.tools.mcp_tool.mcp_toolset import MCPToolset, StreamableHTTPConnectionParams

    calendar_mcp = MCPToolset(
        connection_params=StreamableHTTPConnectionParams(
            url="https://gcal.mcp.claude.com/mcp",
            headers={"Authorization": f"Bearer {os.getenv('GCAL_MCP_TOKEN')}"},
            timeout=30,
        )
    )

    gmail_mcp = MCPToolset(
        connection_params=StreamableHTTPConnectionParams(
            url="https://gmail.mcp.claude.com/mcp",
            headers={"Authorization": f"Bearer {os.getenv('GMAIL_MCP_TOKEN')}"},
            timeout=30,
        )
    )

Then replace the individual tool functions with the MCPToolset instances in agent.py.
"""
import base64
import json
import os
from datetime import datetime, timedelta
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

from google.auth import default
from googleapiclient.discovery import build

_CALENDAR_SCOPES = ["https://www.googleapis.com/auth/calendar"]
_GMAIL_SCOPES    = ["https://www.googleapis.com/auth/gmail.send"]


def _calendar_service():
    creds, _ = default(scopes=_CALENDAR_SCOPES)
    return build("calendar", "v3", credentials=creds)


def _gmail_service():
    creds, _ = default(scopes=_GMAIL_SCOPES)
    return build("gmail", "v1", credentials=creds)


def check_calendar_availability(
    owner_email: str,
    blocker_email: str,
    sprint_day: str,
) -> str:
    """Check free/busy availability for two team members and return candidate 15-min slots.

    Args:
        owner_email: Email of the person who is blocked.
        blocker_email: Email of the person causing the block.
        sprint_day: Sprint day date in YYYY-MM-DD format. Slots are searched
                    for the next 2 business days starting from this date.

    Returns:
        JSON with a list of available slot dicts, each with 'start' and 'end'
        in YYYY-MM-DDTHH:MM:00 format. Falls back to a default slot on API error.
    """
    try:
        service = _calendar_service()
        start_dt = datetime.strptime(sprint_day, "%Y-%m-%d")
        time_min = start_dt.replace(hour=9,  minute=0, second=0).isoformat() + "Z"
        time_max = (start_dt + timedelta(days=2)).replace(hour=17, minute=0, second=0).isoformat() + "Z"

        body = {
            "timeMin": time_min,
            "timeMax": time_max,
            "timeZone": "UTC",
            "items": [{"id": owner_email}, {"id": blocker_email}],
        }
        freebusy = service.freebusy().query(body=body).execute()
        busy_o  = freebusy["calendars"].get(owner_email,  {}).get("busy", [])
        busy_b  = freebusy["calendars"].get(blocker_email, {}).get("busy", [])
        all_busy = busy_o + busy_b

        slots: list = []
        current = start_dt.replace(hour=9, minute=0, second=0)
        search_end = (start_dt + timedelta(days=2)).replace(hour=17, minute=0)

        while current < search_end and len(slots) < 3:
            # Skip over non-business hours for day 2 onwards
            if current.hour >= 17:
                current = (current + timedelta(days=1)).replace(hour=9, minute=0)
                continue

            slot_end = current + timedelta(minutes=15)
            busy = False
            for period in all_busy:
                b_start = datetime.fromisoformat(period["start"].replace("Z", "")).replace(tzinfo=None)
                b_end   = datetime.fromisoformat(period["end"].replace("Z", "")).replace(tzinfo=None)
                if current < b_end and slot_end > b_start:
                    busy = True
                    break

            if not busy:
                slots.append({
                    "start": current.strftime("%Y-%m-%dT%H:%M:00"),
                    "end":   slot_end.strftime("%Y-%m-%dT%H:%M:00"),
                })
            current += timedelta(minutes=30)

        return json.dumps({
            "available_slots": slots,
            "owner_email": owner_email,
            "blocker_email": blocker_email,
        })

    except Exception as e:
        # Graceful fallback: return a default slot for tomorrow at 2pm
        fallback = datetime.strptime(sprint_day, "%Y-%m-%d") + timedelta(days=1)
        fallback = fallback.replace(hour=14, minute=0, second=0)
        return json.dumps({
            "available_slots": [{
                "start": fallback.strftime("%Y-%m-%dT%H:%M:00"),
                "end":   (fallback + timedelta(minutes=15)).strftime("%Y-%m-%dT%H:%M:00"),
            }],
            "note": f"Calendar API unavailable — using default slot. Error: {e}",
        })


def create_calendar_event(
    owner_email: str,
    blocker_email: str,
    title: str,
    start_time: str,
    end_time: str,
) -> str:
    """Create a targeted 1:1 calendar event to resolve a sprint blocker.

    Args:
        owner_email: Email of the blocked person (event organiser).
        blocker_email: Email of the person causing the block (attendee).
        title: Event title, e.g. 'TIL 1:1: Auth PR review — Jesse × Alex'.
        start_time: Event start in ISO format YYYY-MM-DDTHH:MM:00.
        end_time: Event end in ISO format YYYY-MM-DDTHH:MM:00.

    Returns:
        JSON with event_id, event_link, status, title, and start time.
    """
    try:
        service = _calendar_service()
        event = {
            "summary": title,
            "description": (
                "Targeted 1:1 created automatically by Team Intelligence Loop "
                "to resolve a sprint blocker. Duration: 15 minutes."
            ),
            "start": {"dateTime": start_time, "timeZone": "UTC"},
            "end":   {"dateTime": end_time,   "timeZone": "UTC"},
            "attendees": [{"email": owner_email}, {"email": blocker_email}],
            "reminders": {
                "useDefault": False,
                "overrides": [{"method": "popup", "minutes": 10}],
            },
        }
        created = service.events().insert(
            calendarId="primary", body=event, sendUpdates="all"
        ).execute()
        return json.dumps({
            "status":     "created",
            "event_id":   created.get("id"),
            "event_link": created.get("htmlLink"),
            "title":      title,
            "start":      start_time,
            "attendees":  [owner_email, blocker_email],
        })
    except Exception as e:
        return json.dumps({"status": "failed", "error": str(e)})


def send_digest_email(to_emails: str, subject: str, digest_text: str) -> str:
    """Send the team digest email to all members via the Gmail API.

    Args:
        to_emails: Comma-separated string of recipient email addresses.
        subject: Email subject line.
        digest_text: Full plain-text digest to send.

    Returns:
        JSON with send status, Gmail message_id, and recipient count.
    """
    try:
        service = _gmail_service()
        recipients = [e.strip() for e in to_emails.split(",") if e.strip()]

        msg = MIMEMultipart("alternative")
        msg["Subject"] = subject
        msg["From"]    = "me"
        msg["To"]      = ", ".join(recipients)

        # Plain text
        msg.attach(MIMEText(digest_text, "plain"))

        # Minimal HTML version
        html_lines = digest_text.replace("\n", "<br>")
        html = f"""<html><body style="font-family:sans-serif;max-width:640px;margin:0 auto;padding:24px">
<div style="background:#E6F1FB;padding:12px 16px;border-radius:8px;margin-bottom:20px">
  <strong style="color:#042C53">Team Intelligence Loop</strong>
  <span style="color:#185FA5"> · Sprint Digest</span>
</div>
<div style="color:#0F172A;line-height:1.7">{html_lines}</div>
<hr style="margin-top:32px;border:none;border-top:1px solid #E2E8F0">
<p style="font-size:12px;color:#94A3B8">
  Sent by Team Intelligence Loop · Google ADK + Vertex AI + AlloyDB
</p>
</body></html>"""
        msg.attach(MIMEText(html, "html"))

        raw = base64.urlsafe_b64encode(msg.as_bytes()).decode()
        sent = service.users().messages().send(
            userId="me", body={"raw": raw}
        ).execute()

        return json.dumps({
            "status":     "sent",
            "message_id": sent.get("id"),
            "recipients": recipients,
            "count":      len(recipients),
        })
    except Exception as e:
        return json.dumps({"status": "failed", "error": str(e)})
