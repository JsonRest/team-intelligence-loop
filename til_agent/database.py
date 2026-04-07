"""
AlloyDB connection and database tools for Team Intelligence Loop.
All tool functions return JSON strings so ADK agents can parse the results.
"""
import os
import json
import uuid
from datetime import datetime, date
from typing import Optional

import pg8000.native
from google.cloud.alloydb.connector import Connector, IPTypes

# ── Module-level connector (reused across calls) ──────────────
_connector: Optional[Connector] = None


def _get_connector() -> Connector:
    global _connector
    if _connector is None:
        _connector = Connector()
    return _connector


def _get_conn():
    """Open a new pg8000 connection via the AlloyDB connector."""
    return _get_connector().connect(
        os.environ["ALLOYDB_INSTANCE_URI"],
        "pg8000",
        user=os.environ.get("DB_USER", "postgres"),
        password=os.environ["DB_PASSWORD"],
        db=os.environ.get("DB_NAME", "til_db"),
        ip_type=IPTypes.PUBLIC,
    )


def _rows_to_dicts(cursor) -> list:
    """Convert pg8000 cursor rows to list of dicts using column names."""
    if cursor.description is None:
        return []
    cols = [d[0] for d in cursor.description]
    return [dict(zip(cols, row)) for row in cursor.fetchall()]


def _serialize(obj):
    """JSON serialiser for date/datetime/UUID objects."""
    if isinstance(obj, (date, datetime)):
        return obj.isoformat()
    if isinstance(obj, uuid.UUID):
        return str(obj)
    raise TypeError(f"Type {type(obj)} not serialisable")


def _get_embedding(text: str) -> list:
    """Generate a text embedding via Vertex AI text-embedding-004."""
    try:
        import vertexai
        from vertexai.language_models import TextEmbeddingModel

        vertexai.init(
            project=os.environ.get("GOOGLE_CLOUD_PROJECT"),
            location=os.environ.get("GOOGLE_CLOUD_LOCATION", "us-central1"),
        )
        model = TextEmbeddingModel.from_pretrained("text-embedding-004")
        embeddings = model.get_embeddings([text])
        return embeddings[0].values
    except Exception:
        return [0.0] * 768  # silent fallback — semantic search degrades gracefully


# ── Tools ─────────────────────────────────────────────────────

def get_team_members() -> str:
    """Get all team members from AlloyDB.

    Returns:
        JSON with a list of team members including id, name, email, and calendar_id.
    """
    try:
        conn = _get_conn()
        cur = conn.cursor()
        cur.execute("SELECT id, name, email, calendar_id FROM team_members ORDER BY name")
        members = _rows_to_dicts(cur)
        cur.close()
        conn.close()
        return json.dumps({"members": members}, default=_serialize)
    except Exception as e:
        return json.dumps({"error": str(e)})


def store_standup(member_email: str, sprint_day: str, raw_text: str) -> str:
    """Store a raw standup submission to AlloyDB.

    Args:
        member_email: Email address of the team member submitting the standup.
        sprint_day: Sprint day date in YYYY-MM-DD format.
        raw_text: Full raw standup text submitted by the member.

    Returns:
        JSON with standup_id on success, or an error message.
    """
    try:
        conn = _get_conn()
        cur = conn.cursor()
        standup_id = str(uuid.uuid4())
        cur.execute(
            """
            INSERT INTO standups (id, member_id, sprint_day, raw_text)
            SELECT %s, id, %s::date, %s
            FROM team_members WHERE email = %s
            RETURNING id
            """,
            (standup_id, sprint_day, raw_text, member_email),
        )
        row = cur.fetchone()
        conn.commit()
        cur.close()
        conn.close()
        if row:
            return json.dumps({"standup_id": str(row[0]), "status": "stored"})
        return json.dumps({"error": f"Member not found: {member_email}"})
    except Exception as e:
        return json.dumps({"error": str(e)})


def store_parsed_items(
    standup_id: str,
    yesterday: str,
    today: str,
    blocker: str,
    member_email: str,
) -> str:
    """Store structured parsed fields (yesterday/today/blocker) to AlloyDB.

    Args:
        standup_id: UUID of the parent standup record.
        yesterday: Summary of what the member completed yesterday.
        today: Summary of what the member plans to work on today.
        blocker: Description of any blocker, or empty string if none.
        member_email: Email of the member who submitted the standup.

    Returns:
        JSON confirming storage with item count.
    """
    try:
        conn = _get_conn()
        cur = conn.cursor()
        items = [("yesterday", yesterday), ("today", today)]
        if blocker.strip():
            items.append(("blocker", blocker))

        for category, content in items:
            if content.strip():
                cur.execute(
                    """
                    INSERT INTO parsed_items (id, standup_id, category, content, owner_id)
                    SELECT %s, %s, %s, %s, id FROM team_members WHERE email = %s
                    """,
                    (str(uuid.uuid4()), standup_id, category, content, member_email),
                )
        conn.commit()
        cur.close()
        conn.close()
        return json.dumps({"status": "stored", "items_count": len(items)})
    except Exception as e:
        return json.dumps({"error": str(e)})


def get_standups_for_day(sprint_day: str) -> str:
    """Retrieve all standup submissions for a specific sprint day.

    Args:
        sprint_day: Date in YYYY-MM-DD format.

    Returns:
        JSON with list of standup records including member name, email, and raw text.
    """
    try:
        conn = _get_conn()
        cur = conn.cursor()
        cur.execute(
            """
            SELECT s.id, s.raw_text, s.submitted_at, tm.name, tm.email
            FROM standups s
            JOIN team_members tm ON s.member_id = tm.id
            WHERE s.sprint_day = %s::date
            ORDER BY s.submitted_at
            """,
            (sprint_day,),
        )
        standups = _rows_to_dicts(cur)
        cur.close()
        conn.close()
        return json.dumps({"standups": standups, "count": len(standups)}, default=_serialize)
    except Exception as e:
        return json.dumps({"error": str(e)})


def get_parsed_items(sprint_day: str) -> str:
    """Get structured parsed items (yesterday/today/blockers) for a sprint day.

    Args:
        sprint_day: Date in YYYY-MM-DD format.

    Returns:
        JSON with all parsed items grouped by team member and category.
    """
    try:
        conn = _get_conn()
        cur = conn.cursor()
        cur.execute(
            """
            SELECT pi.category, pi.content, tm.name, tm.email, s.id AS standup_id
            FROM parsed_items pi
            JOIN standups s ON pi.standup_id = s.id
            JOIN team_members tm ON pi.owner_id = tm.id
            WHERE s.sprint_day = %s::date
            ORDER BY tm.name, pi.category
            """,
            (sprint_day,),
        )
        items = _rows_to_dicts(cur)
        cur.close()
        conn.close()
        return json.dumps({"items": items, "count": len(items)}, default=_serialize)
    except Exception as e:
        return json.dumps({"error": str(e)})


def store_blocker(
    owner_email: str,
    blocked_by_email: str,
    description: str,
    sprint_day: str,
) -> str:
    """Store a detected cross-person blocker to AlloyDB.

    Args:
        owner_email: Email of the team member who is blocked.
        blocked_by_email: Email of the team member causing the block.
        description: Clear description of the blocker and its impact.
        sprint_day: Sprint day date in YYYY-MM-DD format.

    Returns:
        JSON with blocker_id on success or error message.
    """
    try:
        conn = _get_conn()
        cur = conn.cursor()
        blocker_id = str(uuid.uuid4())
        cur.execute(
            """
            INSERT INTO blockers (id, owner_id, blocked_person_id, description, sprint_day)
            SELECT %s,
                   (SELECT id FROM team_members WHERE email = %s),
                   (SELECT id FROM team_members WHERE email = %s),
                   %s, %s::date
            RETURNING id
            """,
            (blocker_id, owner_email, blocked_by_email, description, sprint_day),
        )
        row = cur.fetchone()
        conn.commit()

        # Generate and store description embedding for future semantic search
        if row:
            try:
                embedding = _get_embedding(description)
                cur.execute(
                    "UPDATE blockers SET description_embedding = %s::vector WHERE id = %s",
                    (str(embedding), blocker_id),
                )
                conn.commit()
            except Exception:
                pass  # embedding is optional — graceful degradation

        cur.close()
        conn.close()
        if row:
            return json.dumps({"blocker_id": str(row[0]), "status": "stored"})
        return json.dumps({"error": "Could not store blocker — check both emails exist in team_members"})
    except Exception as e:
        return json.dumps({"error": str(e)})


def update_blocker_status(blocker_id: str, status: str, resolution: str = "") -> str:
    """Update the status of a blocker record.

    Args:
        blocker_id: UUID of the blocker to update.
        status: New status — 'active', 'scheduled', or 'resolved'.
        resolution: Optional description of how the blocker was resolved.

    Returns:
        JSON confirming the update.
    """
    try:
        conn = _get_conn()
        cur = conn.cursor()
        cur.execute(
            """
            UPDATE blockers
            SET status = %s,
                resolution = %s,
                resolved_at = CASE WHEN %s = 'resolved' THEN NOW() ELSE NULL END
            WHERE id = %s
            """,
            (status, resolution, status, blocker_id),
        )
        conn.commit()
        cur.close()
        conn.close()
        return json.dumps({"blocker_id": blocker_id, "status": status, "updated": True})
    except Exception as e:
        return json.dumps({"error": str(e)})


def get_active_blockers(sprint_day: str) -> str:
    """Get all active and scheduled blockers for a sprint day.

    Args:
        sprint_day: Date in YYYY-MM-DD format.

    Returns:
        JSON with list of blockers including both parties' names and emails.
    """
    try:
        conn = _get_conn()
        cur = conn.cursor()
        cur.execute(
            """
            SELECT b.id, b.description, b.status, b.resolution,
                   o.name  AS owner_name,   o.email  AS owner_email,
                   bp.name AS blocker_name, bp.email AS blocker_email
            FROM blockers b
            JOIN team_members o  ON b.owner_id          = o.id
            JOIN team_members bp ON b.blocked_person_id = bp.id
            WHERE b.sprint_day = %s::date
              AND b.status IN ('active', 'scheduled')
            ORDER BY b.created_at
            """,
            (sprint_day,),
        )
        blockers = _rows_to_dicts(cur)
        cur.close()
        conn.close()
        return json.dumps({"blockers": blockers, "count": len(blockers)}, default=_serialize)
    except Exception as e:
        return json.dumps({"error": str(e)})


def store_decision(
    decision: str,
    rationale: str,
    expected_outcome: str,
    review_date: str,
    sprint_day: str,
) -> str:
    """Store a decision to the decision journal in AlloyDB.

    Args:
        decision: The decision that was made.
        rationale: Why this decision was made.
        expected_outcome: What outcome is expected.
        review_date: When to review the outcome in YYYY-MM-DD format.
        sprint_day: Current sprint day in YYYY-MM-DD format.

    Returns:
        JSON with decision_id on success or error message.
    """
    try:
        conn = _get_conn()
        cur = conn.cursor()
        decision_id = str(uuid.uuid4())
        cur.execute(
            """
            INSERT INTO decisions
                (id, decision, rationale, expected_outcome, review_date, sprint_day)
            VALUES (%s, %s, %s, %s, %s::date, %s::date)
            RETURNING id
            """,
            (decision_id, decision, rationale, expected_outcome, review_date, sprint_day),
        )
        row = cur.fetchone()
        conn.commit()

        # Store content embedding
        try:
            embedding = _get_embedding(f"{decision}. {rationale}")
            cur.execute(
                "UPDATE decisions SET content_embedding = %s::vector WHERE id = %s",
                (str(embedding), decision_id),
            )
            conn.commit()
        except Exception:
            pass

        cur.close()
        conn.close()
        return json.dumps({"decision_id": str(row[0]), "status": "logged"})
    except Exception as e:
        return json.dumps({"error": str(e)})


def get_recent_decisions(limit: int = 5) -> str:
    """Get the most recent decisions from the decision journal.

    Args:
        limit: Maximum number of decisions to return (default 5).

    Returns:
        JSON with list of recent decisions including rationale and outcomes.
    """
    try:
        conn = _get_conn()
        cur = conn.cursor()
        cur.execute(
            """
            SELECT id, decision, rationale, expected_outcome, actual_outcome,
                   review_date, sprint_day, created_at
            FROM decisions
            ORDER BY created_at DESC
            LIMIT %s
            """,
            (limit,),
        )
        decisions = _rows_to_dicts(cur)
        cur.close()
        conn.close()
        return json.dumps({"decisions": decisions}, default=_serialize)
    except Exception as e:
        return json.dumps({"error": str(e)})


def get_semantic_similar_blockers(description: str, limit: int = 3) -> str:
    """Find semantically similar past blockers using pgvector cosine similarity.

    Args:
        description: Blocker description to find similar past instances for.
        limit: Maximum number of similar blockers to return (default 3).

    Returns:
        JSON with list of similar past blockers and their resolutions, or an empty
        list with a note if vector search is unavailable.
    """
    try:
        embedding = _get_embedding(description)
        conn = _get_conn()
        cur = conn.cursor()
        cur.execute(
            """
            SELECT b.description, b.status, b.resolution,
                   o.name  AS owner_name,
                   bp.name AS blocker_name,
                   b.sprint_day,
                   1 - (b.description_embedding <=> %s::vector) AS similarity
            FROM blockers b
            JOIN team_members o  ON b.owner_id          = o.id
            JOIN team_members bp ON b.blocked_person_id = bp.id
            WHERE b.description_embedding IS NOT NULL
            ORDER BY b.description_embedding <=> %s::vector
            LIMIT %s
            """,
            (str(embedding), str(embedding), limit),
        )
        similar = _rows_to_dicts(cur)
        cur.close()
        conn.close()
        return json.dumps({"similar_blockers": similar}, default=_serialize)
    except Exception as e:
        return json.dumps({"similar_blockers": [], "note": f"Semantic search unavailable: {e}"})
