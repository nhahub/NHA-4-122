"""
routers/files.py - Authenticated file download endpoint.

GET /api/files/{file_id} - Download a generated report file.

Ownership is verified: the File's session must belong to the authenticated
user. Returns 404 for both not-found and unauthorized cases to avoid
leaking existence information (IDOR defense).
"""
from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import FileResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from core.database import get_db
from core.dependencies import get_current_user
from models.file import File
from models.session import Session
from models.user import User

router = APIRouter()


@router.get("/{file_id}")
async def download_file(
    file_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> FileResponse:
    """
    Download a generated report file by its ID.

    Verifies that the file belongs to a session owned by the authenticated
    user. Returns 404 (not 403) if not found or not owned - this avoids
    leaking whether a file_id exists for another user.
    """
    result = await db.execute(
        select(File)
        .where(File.id == file_id)
        .options(selectinload(File.session))
    )
    file_record = result.scalar_one_or_none()

    if file_record is None or file_record.session.user_id != current_user.id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="File not found.",
        )

    return FileResponse(
        path=file_record.storage_path,
        filename=file_record.original_filename,
        media_type=file_record.mime_type,
    )
