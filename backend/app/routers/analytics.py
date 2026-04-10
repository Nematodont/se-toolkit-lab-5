"""Router for analytics endpoints.

Each endpoint performs SQL aggregation queries on the interaction data
populated by the ETL pipeline. All endpoints require a `lab` query
parameter to filter results by lab (e.g., "lab-01").
"""

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, case, text
from sqlmodel import col, select
from sqlmodel.ext.asyncio.session import AsyncSession

from app.database import get_session
from app.models.item import ItemRecord
from app.models.interaction import InteractionLog
from app.models.learner import Learner

router = APIRouter()


@router.get("/scores")
async def get_scores(
    lab: str = Query(..., description="Lab identifier, e.g. 'lab-01'"),
    session: AsyncSession = Depends(get_session),
):
    """Score distribution histogram for a given lab."""
    # Convert lab param to title pattern: "lab-04" → "%Lab 04%"
    title_pattern = lab.replace("-", " ").replace("lab ", "Lab ", 1)
    title_pattern = f"%{title_pattern}%"

    # Find the lab
    lab_stmt = select(ItemRecord).where(
        col(ItemRecord.type) == "lab",
        col(ItemRecord.title).like(title_pattern),
    )
    lab_result = await session.exec(lab_stmt)
    lab_item = lab_result.first()
    if not lab_item:
        return [
            {"bucket": "0-25", "count": 0},
            {"bucket": "26-50", "count": 0},
            {"bucket": "51-75", "count": 0},
            {"bucket": "76-100", "count": 0},
        ]

    # Find tasks belonging to this lab
    tasks_stmt = select(ItemRecord.id).where(
        col(ItemRecord.type) == "task",
        col(ItemRecord.parent_id) == lab_item.id,
    )
    task_ids = [row for row in (await session.exec(tasks_stmt)).all()]

    # Query interactions with scores for these tasks
    bucket_expr = case(
        (col(InteractionLog.score) <= 25, "0-25"),
        (col(InteractionLog.score) <= 50, "26-50"),
        (col(InteractionLog.score) <= 75, "51-75"),
        else_="76-100",
    )

    query = (
        select(bucket_expr.label("bucket"), func.count().label("count"))
        .where(
            col(InteractionLog.item_id).in_(task_ids),
            col(InteractionLog.score).isnot(None),
        )
        .group_by("bucket")
    )

    results = await session.exec(query)
    counts = {row[0]: row[1] for row in results.all()}

    return [
        {"bucket": "0-25", "count": counts.get("0-25", 0)},
        {"bucket": "26-50", "count": counts.get("26-50", 0)},
        {"bucket": "51-75", "count": counts.get("51-75", 0)},
        {"bucket": "76-100", "count": counts.get("76-100", 0)},
    ]


@router.get("/pass-rates")
async def get_pass_rates(
    lab: str = Query(..., description="Lab identifier, e.g. 'lab-01'"),
    session: AsyncSession = Depends(get_session),
):
    """Per-task pass rates for a given lab."""
    title_pattern = lab.replace("-", " ").replace("lab ", "Lab ", 1)
    title_pattern = f"%{title_pattern}%"

    lab_stmt = select(ItemRecord).where(
        col(ItemRecord.type) == "lab",
        col(ItemRecord.title).like(title_pattern),
    )
    lab_result = await session.exec(lab_stmt)
    lab_item = lab_result.first()
    if not lab_item:
        return []

    tasks_stmt = select(ItemRecord).where(
        col(ItemRecord.type) == "task",
        col(ItemRecord.parent_id) == lab_item.id,
    ).order_by(col(ItemRecord.title))
    tasks = (await session.exec(tasks_stmt)).all()

    result = []
    for task in tasks:
        query = (
            select(
                func.round(func.avg(col(InteractionLog.score)), 1).label("avg_score"),
                func.count().label("attempts"),
            )
            .where(col(InteractionLog.item_id) == task.id)
        )
        row = (await session.exec(query)).first()
        if row and row[1] > 0:
            result.append({
                "task": task.title,
                "avg_score": float(row[0]),
                "attempts": row[1],
            })
        else:
            result.append({
                "task": task.title,
                "avg_score": 0.0,
                "attempts": 0,
            })

    return result


@router.get("/timeline")
async def get_timeline(
    lab: str = Query(..., description="Lab identifier, e.g. 'lab-01'"),
    session: AsyncSession = Depends(get_session),
):
    """Submissions per day for a given lab."""
    title_pattern = lab.replace("-", " ").replace("lab ", "Lab ", 1)
    title_pattern = f"%{title_pattern}%"

    lab_stmt = select(ItemRecord).where(
        col(ItemRecord.type) == "lab",
        col(ItemRecord.title).like(title_pattern),
    )
    lab_result = await session.exec(lab_stmt)
    lab_item = lab_result.first()
    if not lab_item:
        return []

    tasks_stmt = select(ItemRecord.id).where(
        col(ItemRecord.type) == "task",
        col(ItemRecord.parent_id) == lab_item.id,
    )
    task_ids = [row for row in (await session.exec(tasks_stmt)).all()]

    query = (
        select(
            func.date(col(InteractionLog.created_at)).label("date"),
            func.count().label("submissions"),
        )
        .where(col(InteractionLog.item_id).in_(task_ids))
        .group_by("date")
        .order_by(text("date"))
    )

    results = await session.exec(query)
    return [
        {"date": row[0], "submissions": row[1]}
        for row in results.all()
    ]


@router.get("/groups")
async def get_groups(
    lab: str = Query(..., description="Lab identifier, e.g. 'lab-01'"),
    session: AsyncSession = Depends(get_session),
):
    """Per-group performance for a given lab."""
    title_pattern = lab.replace("-", " ").replace("lab ", "Lab ", 1)
    title_pattern = f"%{title_pattern}%"

    lab_stmt = select(ItemRecord).where(
        col(ItemRecord.type) == "lab",
        col(ItemRecord.title).like(title_pattern),
    )
    lab_result = await session.exec(lab_stmt)
    lab_item = lab_result.first()
    if not lab_item:
        return []

    tasks_stmt = select(ItemRecord.id).where(
        col(ItemRecord.type) == "task",
        col(ItemRecord.parent_id) == lab_item.id,
    )
    task_ids = [row for row in (await session.exec(tasks_stmt)).all()]

    query = (
        select(
            col(Learner.student_group).label("group"),
            func.round(func.avg(col(InteractionLog.score)), 1).label("avg_score"),
            func.count(func.distinct(col(Learner.id))).label("students"),
        )
        .join(Learner, col(Learner.id) == col(InteractionLog.learner_id))
        .where(col(InteractionLog.item_id).in_(task_ids))
        .group_by(col(Learner.student_group))
        .order_by(col(Learner.student_group))
    )

    results = await session.exec(query)
    return [
        {"group": row[0], "avg_score": float(row[1]), "students": row[2]}
        for row in results.all()
    ]
