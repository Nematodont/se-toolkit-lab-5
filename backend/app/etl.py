"""ETL pipeline: fetch data from the autochecker API and load it into the database.

The autochecker dashboard API provides two endpoints:
- GET /api/items — lab/task catalog
- GET /api/logs  — anonymized check results (supports ?since= and ?limit= params)

Both require HTTP Basic Auth (email + password from settings).
"""

from datetime import datetime, timezone
from typing import Any

import httpx
from sqlalchemy import desc
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from app.settings import settings


# ---------------------------------------------------------------------------
# Extract — fetch data from the autochecker API
# ---------------------------------------------------------------------------


async def fetch_items() -> list[dict[str, Any]]:
    """Fetch the lab/task catalog from the autochecker API."""
    auth = httpx.BasicAuth(settings.autochecker_email, settings.autochecker_password)
    async with httpx.AsyncClient() as client:
        resp = await client.get(
            f"{settings.autochecker_api_url}/api/items", auth=auth
        )
        if resp.status_code != 200:
            raise RuntimeError(
                f"Failed to fetch items: {resp.status_code} {resp.text}"
            )
        return resp.json()


async def fetch_logs(since: datetime | None = None) -> list[dict[str, Any]]:
    """Fetch check results from the autochecker API with pagination."""
    auth = httpx.BasicAuth(settings.autochecker_email, settings.autochecker_password)
    all_logs: list[dict[str, Any]] = []
    current_since = since

    async with httpx.AsyncClient() as client:
        while True:
            params: dict[str, Any] = {"limit": 500}
            if current_since is not None:
                params["since"] = current_since.isoformat()

            resp = await client.get(
                f"{settings.autochecker_api_url}/api/logs",
                auth=auth,
                params=params,
            )
            if resp.status_code != 200:
                raise RuntimeError(
                    f"Failed to fetch logs: {resp.status_code} {resp.text}"
                )

            data = resp.json()
            page_logs = data.get("logs", [])
            all_logs.extend(page_logs)

            if not data.get("has_more", False) or not page_logs:
                break

            # Use the last log's submitted_at as the next since value
            last_submitted = page_logs[-1].get("submitted_at")
            if last_submitted:
                current_since = datetime.fromisoformat(last_submitted)
            else:
                break

    return all_logs


# ---------------------------------------------------------------------------
# Load — insert fetched data into the local database
# ---------------------------------------------------------------------------


async def load_items(
    items: list[dict[str, Any]], session: AsyncSession
) -> int:
    """Load items (labs and tasks) into the database."""
    from app.models.item import ItemRecord

    new_count = 0

    # Separate labs and tasks
    labs = [it for it in items if it.get("type") == "lab"]
    tasks = [it for it in items if it.get("type") == "task"]

    # Build a mapping from lab short ID (e.g. "lab-01") to DB record
    lab_id_map: dict[str, ItemRecord] = {}

    # Process labs first
    for lab in labs:
        lab_title = lab["title"]
        # Check if already exists
        existing = await session.exec(
            select(ItemRecord).where(
                ItemRecord.type == "lab", ItemRecord.title == lab_title
            )
        )
        found = existing.first()
        if found is None:
            record = ItemRecord(type="lab", title=lab_title)
            session.add(record)
            await session.flush()  # Get the ID
            new_count += 1
            lab_id_map[lab["lab"]] = record
        else:
            lab_id_map[lab["lab"]] = found

    # Process tasks
    for task in tasks:
        lab_short_id = task["lab"]
        parent_lab = lab_id_map.get(lab_short_id)
        if parent_lab is None:
            # Parent lab not found — skip this task
            continue

        task_title = task["title"]
        # Check if task with this title and parent already exists
        existing = await session.exec(
            select(ItemRecord).where(
                ItemRecord.type == "task",
                ItemRecord.title == task_title,
                ItemRecord.parent_id == parent_lab.id,
            )
        )
        found = existing.first()
        if found is None:
            record = ItemRecord(
                type="task",
                title=task_title,
                parent_id=parent_lab.id,
            )
            session.add(record)
            new_count += 1

    await session.commit()
    return new_count


async def load_logs(
    logs: list[dict[str, Any]],
    items_catalog: list[dict[str, Any]],
    session: AsyncSession,
) -> int:
    """Load interaction logs into the database.

    Args:
        logs: Raw log dicts from the API.
        items_catalog: Raw item dicts from fetch_items() — used to map
            short IDs (e.g. "lab-01", "setup") to item titles in the DB.
        session: Database session.
    """
    from app.models.interaction import InteractionLog
    from app.models.item import ItemRecord
    from app.models.learner import Learner

    new_count = 0

    # Build lookup: (lab_short_id, task_short_id | None) -> item title
    title_lookup: dict[tuple[str, str | None], str] = {}
    for item in items_catalog:
        if item.get("type") == "lab":
            key: tuple[str, str | None] = (item["lab"], None)
            title_lookup[key] = item["title"]
        elif item.get("type") == "task":
            key = (item["lab"], item.get("task"))
            title_lookup[key] = item["title"]

    for log in logs:
        # 1. Find or create Learner by external_id
        learner_external_id = log["student_id"]
        learner_result = await session.exec(
            select(Learner).where(Learner.external_id == learner_external_id)
        )
        learner = learner_result.first()
        if learner is None:
            learner = Learner(
                external_id=learner_external_id,
                student_group=log.get("group", ""),
            )
            session.add(learner)
            await session.flush()

        # 2. Find the matching item in the database
        lab_short_id = log.get("lab")
        task_short_id = log.get("task")  # may be None for lab-level logs
        lookup_key = (lab_short_id, task_short_id)
        item_title = title_lookup.get(lookup_key)  # type: ignore[arg-type]

        if item_title is None:
            # No matching item — skip this log
            continue

        item_result = await session.exec(
            select(ItemRecord).where(ItemRecord.title == item_title)
        )
        item = item_result.first()
        if item is None:
            # Item not in DB — skip
            continue

        # 3. Check for idempotent upsert — skip if InteractionLog exists
        log_external_id = log["id"]
        existing_log = await session.exec(
            select(InteractionLog).where(
                InteractionLog.external_id == log_external_id
            )
        )
        if existing_log.first() is not None:
            continue  # Already exists, skip

        # 4. Create InteractionLog
        submitted_at_str = log.get("submitted_at")
        created_at = (
            datetime.fromisoformat(submitted_at_str)
            if submitted_at_str
            else datetime.now(timezone.utc).replace(tzinfo=None)
        )

        interaction = InteractionLog(
            external_id=log_external_id,
            learner_id=learner.id,  # type: ignore[arg-type]
            item_id=item.id,  # type: ignore[arg-type]
            kind="attempt",
            score=log.get("score"),
            checks_passed=log.get("passed"),
            checks_total=log.get("total"),
            created_at=created_at,
        )
        session.add(interaction)
        new_count += 1

    await session.commit()
    return new_count


# ---------------------------------------------------------------------------
# Orchestrator
# ---------------------------------------------------------------------------


async def sync(session: AsyncSession) -> dict[str, int]:
    """Run the full ETL pipeline."""
    from app.models.interaction import InteractionLog

    # Step 1: Fetch items and load them
    raw_items = await fetch_items()
    await load_items(raw_items, session)

    # Step 2: Determine the last synced timestamp
    last_log = await session.exec(
        select(InteractionLog).order_by(
            desc(InteractionLog.__table__.c.created_at)  # type: ignore[arg-type]
        ).limit(1)
    )
    last_record = last_log.first()
    since = last_record.created_at if last_record else None

    # Step 3: Fetch logs since that timestamp and load them
    raw_logs = await fetch_logs(since=since)
    new_records = await load_logs(raw_logs, raw_items, session)

    # Count total interactions in DB
    total_result = await session.exec(select(InteractionLog))
    total_records = len(total_result.all())

    return {"new_records": new_records, "total_records": total_records}
