"""
Team Intelligence Loop — Agent definitions.

Root agent:    til_orchestrator
Sub-agents:    parser_agent, blocker_agent, scheduler_agent, synthesizer_agent

Calendar and Gmail are called directly via Google APIs using OAuth refresh token credentials.
Required env vars: GOOGLE_OAUTH_CLIENT_ID, GOOGLE_OAUTH_CLIENT_SECRET, GOOGLE_OAUTH_REFRESH_TOKEN
"""
import os

from dotenv import load_dotenv
from google.adk import Agent

from til_agent.database import (
    get_team_members,
    store_standup,
    store_parsed_items,
    get_standups_for_day,
    get_parsed_items,
    store_blocker,
    update_blocker_status,
    get_active_blockers,
    store_decision,
    get_recent_decisions,
    get_semantic_similar_blockers,
)
from til_agent.google_tools import (
    check_calendar_availability,
    create_calendar_event,
    send_digest_email,
)
from til_agent.prompts import (
    ORCHESTRATOR_PROMPT,
    PARSER_PROMPT,
    BLOCKER_PROMPT,
    SCHEDULER_PROMPT,
    SYNTHESIZER_PROMPT,
)

load_dotenv()

MODEL = os.getenv("MODEL", "gemini-2.5-flash-lite")

# ── Sub-agents ────────────────────────────────────────────────

parser_agent = Agent(
    name="parser_agent",
    model=MODEL,
    description=(
        "Parses raw standup text to extract structured yesterday/today/blocker fields "
        "and stores them to AlloyDB."
    ),
    instruction=PARSER_PROMPT,
    tools=[store_standup, store_parsed_items, get_team_members],
)

blocker_agent = Agent(
    name="blocker_agent",
    model=MODEL,
    description=(
        "Analyzes all standup submissions for a sprint day to detect cross-person "
        "blockers and dependencies. Stores blockers to AlloyDB."
    ),
    instruction=BLOCKER_PROMPT,
    tools=[
        get_standups_for_day,
        get_parsed_items,
        get_team_members,
        store_blocker,
        get_semantic_similar_blockers,
    ],
)

scheduler_agent = Agent(
    name="scheduler_agent",
    model=MODEL,
    description=(
        "Creates targeted 15-minute 1:1 calendar events for each detected blocker pair "
        "using Google Calendar API."
    ),
    instruction=SCHEDULER_PROMPT,
    tools=[
        get_active_blockers,
        update_blocker_status,
        check_calendar_availability,
        create_calendar_event,
    ],
)

synthesizer_agent = Agent(
    name="synthesizer_agent",
    model=MODEL,
    description=(
        "Generates the team digest from all standup data, sends it to the team via "
        "Gmail API, and logs notable decisions to the AlloyDB decision journal."
    ),
    instruction=SYNTHESIZER_PROMPT,
    tools=[
        get_standups_for_day,
        get_active_blockers,
        get_recent_decisions,
        get_team_members,
        store_decision,
        send_digest_email,
    ],
)

# ── Orchestrator (root agent) ─────────────────────────────────

root_agent = Agent(
    name="til_orchestrator",
    model=MODEL,
    description=(
        "Team Intelligence Loop orchestrator. Accepts a standup submission and "
        "coordinates the full pipeline: parse → detect blockers → schedule 1:1s → "
        "synthesize digest + Gmail delivery + decision logging."
    ),
    instruction=ORCHESTRATOR_PROMPT,
    sub_agents=[parser_agent, blocker_agent, scheduler_agent, synthesizer_agent],
    tools=[get_team_members],
)
