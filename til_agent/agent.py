"""
Team Intelligence Loop — Agent definitions.

Root agent:    til_orchestrator
Sub-agents:    parser_agent, blocker_agent, scheduler_agent, synthesizer_agent

Calendar and Gmail are connected via workspace-mcp (Streamable HTTP MCP server).
Start the server before running the agent:

    uvx workspace-mcp --tools gmail calendar --transport streamable-http --port 8080

ADK connects via MCPToolset pointing at WORKSPACE_MCP_URL (default: http://localhost:8080/mcp).
"""
import os

from dotenv import load_dotenv
from google.adk import Agent
from google.adk.tools.mcp_tool.mcp_toolset import McpToolset, StreamableHTTPConnectionParams

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
from til_agent.prompts import (
    ORCHESTRATOR_PROMPT,
    PARSER_PROMPT,
    BLOCKER_PROMPT,
    SCHEDULER_PROMPT,
    SYNTHESIZER_PROMPT,
)

load_dotenv()

MODEL             = os.getenv("MODEL", "gemini-2.5-flash-lite")
WORKSPACE_MCP_URL = os.getenv("WORKSPACE_MCP_URL", "http://localhost:8080/mcp")

# ── MCP Toolsets ─────────────────────────────────────────────
# Two separate instances to avoid shared connection state across agents.
# Both point at the same workspace-mcp server (which exposes Calendar
# and Gmail tools when started with --tools gmail calendar).

calendar_mcp = McpToolset(
    connection_params=StreamableHTTPConnectionParams(
        url=WORKSPACE_MCP_URL,
        timeout=30,
    )
)

gmail_mcp = McpToolset(
    connection_params=StreamableHTTPConnectionParams(
        url=WORKSPACE_MCP_URL,
        timeout=30,
    )
)

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
        "using Google Calendar via the workspace-mcp MCP server."
    ),
    instruction=SCHEDULER_PROMPT,
    tools=[
        get_active_blockers,
        update_blocker_status,
        calendar_mcp,              # Google Calendar MCP tools
    ],
)

synthesizer_agent = Agent(
    name="synthesizer_agent",
    model=MODEL,
    description=(
        "Generates the team digest from all standup data, sends it to the team via "
        "Gmail MCP, and logs notable decisions to the AlloyDB decision journal."
    ),
    instruction=SYNTHESIZER_PROMPT,
    tools=[
        get_standups_for_day,
        get_active_blockers,
        get_recent_decisions,
        get_team_members,
        store_decision,
        gmail_mcp,                 # Gmail MCP tools
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
