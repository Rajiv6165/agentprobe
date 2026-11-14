"""Project listing endpoints."""

from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_db
from app.schemas import ProjectListResponse
from app.services.auth import verify_api_key
from app.services.project_service import list_projects

router = APIRouter(prefix="/v1/projects", tags=["Projects"])


@router.get("", response_model=ProjectListResponse, summary="List all projects")
async def get_projects(
    db: AsyncSession = Depends(get_db),
    _auth: str | None = Depends(verify_api_key),
) -> ProjectListResponse:
    """Retrieve all projects along with aggregate run counts."""
    projects = await list_projects(db)
    return ProjectListResponse(projects=projects)

