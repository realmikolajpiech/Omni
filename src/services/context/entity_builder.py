"""Entity Builder — extract entities from calendar, email, and activity data.

Bridges existing productivity.py AppleScript calls with the KnowledgeGraph,
creating Person, Event, and Email entities with proper relationships.
"""

import logging
import re
import time

from src.services.context.knowledge_graph import KnowledgeGraph

_log = logging.getLogger(__name__)

# Regex for extracting structured fields from productivity.py output
_EVENT_BLOCK_RE = re.compile(
    r"Calendar:\s*(.+?)\n"
    r"Event:\s*(.+?)\n"
    r"Start:\s*(.+?)\n"
    r"End:\s*(.+?)\n"
    r"Description:\s*(.*?)\n"
    r"-+",
    re.DOTALL,
)

_EMAIL_BLOCK_RE = re.compile(
    r"From:\s*(.+?)\n"
    r"Subject:\s*(.+?)\n"
    r"Date:\s*(.+?)\n"
    r"-+",
    re.DOTALL,
)

# Extract "Name <email>" or plain email
_EMAIL_ADDR_RE = re.compile(r"([^<]+?)\s*<([^>]+)>|(\S+@\S+\.\S+)")


def build_entities_from_calendar(kg: KnowledgeGraph) -> int:
    """Parse calendar events and create Event + Person entities.

    Returns count of new/updated entities.
    """
    try:
        from src.services.system.productivity import get_calendar_events
        raw = get_calendar_events(days=3)
    except Exception as e:
        _log.warning("[context] Calendar fetch failed: %s", e)
        return 0

    if not raw or raw.startswith("Error") or raw == "No upcoming events found.":
        return 0

    count = 0
    for m in _EVENT_BLOCK_RE.finditer(raw):
        cal_name, title, start_str, end_str, description = m.groups()

        # Create event entity
        event_uri = f"cal:{cal_name}:{title}:{start_str}"
        event_id = kg.upsert_entity(
            "event",
            title.strip(),
            uri=event_uri,
            metadata={
                "calendar": cal_name.strip(),
                "start": start_str.strip(),
                "end": end_str.strip(),
                "description": description.strip(),
            },
        )
        count += 1

        # Extract people from description (email addresses, @mentions)
        people = extract_people_from_text(description)
        for person_name, person_email in people:
            person_id = kg.upsert_entity(
                "person",
                person_name,
                uri=person_email,
                metadata={"source": "calendar"},
            )
            kg.add_relationship(person_id, event_id, "attendee_of")
            count += 1

    _log.debug("[context] Built %d entities from calendar", count)
    return count


def build_entities_from_emails(kg: KnowledgeGraph) -> int:
    """Parse recent emails and create Email + Person entities.

    Returns count of new/updated entities.
    """
    try:
        from src.services.system.productivity import get_unread_emails
        raw = get_unread_emails(limit=10)
    except Exception as e:
        _log.warning("[context] Email fetch failed: %s", e)
        return 0

    if not raw or raw.startswith("Error") or raw == "No unread emails.":
        return 0

    count = 0
    for m in _EMAIL_BLOCK_RE.finditer(raw):
        sender_raw, subject, date_str = m.groups()

        # Parse sender
        person_name, person_email = _parse_sender(sender_raw.strip())

        # Create person entity
        if person_email:
            person_id = kg.upsert_entity(
                "person",
                person_name or person_email,
                uri=person_email,
                metadata={"source": "email"},
            )
            count += 1
        else:
            person_id = kg.upsert_entity(
                "person",
                person_name or sender_raw.strip(),
                metadata={"source": "email"},
            )
            count += 1

        # Create email entity
        email_uri = f"email:{subject}:{date_str}"
        email_id = kg.upsert_entity(
            "email",
            subject.strip(),
            uri=email_uri,
            metadata={
                "sender": sender_raw.strip(),
                "date": date_str.strip(),
            },
        )
        count += 1

        # Relationship: email sent by person
        kg.add_relationship(email_id, person_id, "sent_by")

    _log.debug("[context] Built %d entities from emails", count)
    return count


def build_entities_from_activity(kg: KnowledgeGraph, activity_batch: list[dict]) -> None:
    """Convert raw activity observations into File/URL/App entities
    and create co_active relationships for entities seen together."""
    entity_ids = []
    for entry in activity_batch:
        app_name = entry.get("app_name", "")
        file_path = entry.get("file_path")

        if file_path:
            import os
            eid = kg.upsert_entity(
                "file",
                os.path.basename(file_path),
                uri=file_path,
                metadata={"app": app_name},
            )
        else:
            eid = kg.upsert_entity("app", app_name, uri=f"app:{app_name}")

        entity_ids.append(eid)

    # Co-active relationships
    unique_ids = list(dict.fromkeys(entity_ids))
    for i, a in enumerate(unique_ids):
        for b in unique_ids[i + 1:]:
            kg.add_relationship(a, b, "co_active")


def extract_people_from_text(text: str) -> list[tuple[str, str | None]]:
    """Extract people references from text.

    Returns list of (name, email_or_None).
    Handles patterns:
      - "Name <email@example.com>"
      - bare email addresses
      - @mentions (Twitter-style)
    """
    if not text:
        return []

    results = []
    seen = set()

    for m in _EMAIL_ADDR_RE.finditer(text):
        name_part, email_part, bare_email = m.groups()
        if email_part:
            email = email_part.strip()
            name = (name_part or email).strip()
        elif bare_email:
            email = bare_email.strip()
            name = email
        else:
            continue

        if email not in seen:
            seen.add(email)
            results.append((name, email))

    return results


def _parse_sender(sender: str) -> tuple[str, str | None]:
    """Parse 'Name <email>' or 'email@domain.com' into (name, email)."""
    m = re.match(r"(.+?)\s*<([^>]+)>", sender)
    if m:
        return m.group(1).strip(), m.group(2).strip()
    if "@" in sender:
        return sender, sender
    return sender, None


def run_periodic_entity_build(kg: KnowledgeGraph) -> int:
    """Run a full entity build pass (calendar + email).

    Intended to be called every ~10 minutes from the suggestion engine
    or observer.  Returns total entities created/updated.
    """
    total = 0
    total += build_entities_from_calendar(kg)
    total += build_entities_from_emails(kg)
    return total
