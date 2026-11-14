"""Business logic for project operations."""

from __future__ import annotations

import uuid

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Project, Run
from app.schemas import ProjectResponse


async def list_projects(db: AsyncSession) -> list[ProjectResponse]:
    """Retrieve all projects along with their total run count."""
    stmt = (
        select(Project, func.count(Run.id).label("run_count"))
        .outerjoin(Run, Project.id == Run.project_id)
        .group_by(Project.id)
        .order_by(Project.created_at.desc())
    )
    result = await db.execute(stmt)
    rows = result.all()

    return [
        ProjectResponse(
            id=proj.id,
            name=proj.name,
            created_at=proj.created_at,
            run_count=count,
        )
        for proj, count in rows
    ]


async def get_or_create_project(db: AsyncSession, project_name: str) -> Project:
    """Retrieve existing project by name or insert a new one if not found."""
    stmt = select(Project).where(Project.name == project_name)
    result = await db.execute(stmt)
    project = result.scalar_one_or_none()

    if project is None:
        project = Project(id=uuid.uuid4(), name=project_name)
        db.add(project)
        await db.flush()

    return project

