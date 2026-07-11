"""
routers/sessions.py — Chat session CRUD endpoints.

GET    /sessions              — List all sessions for the authenticated user (paginated).
POST   /sessions              — Create a new empty session.
GET    /sessions/{session_id} — Fetch a session with its full message + feedback history.
PATCH  /sessions/{session_id} — Rename a session title.
DELETE /sessions/{session_id} — Permanently delete a session (cascades to messages).
"""
from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from core.database import get_db
from core.dependencies import get_current_user
from core.utils import get_owned_session
from models.feedback import MessageFeedback  # noqa: F401 — needed for selectinload
from models.file import File  # noqa: F401 — needed for selectinload
from models.message import Message
from models.session import Session
from models.user import User
from schemas.enums import RoleEnum
from schemas.session import SessionListOut, SessionListItem, SessionOut, SessionRename

router = APIRouter()

# ── Endpoints ─────────────────────────────────────────────────────────────────

@router.get("", response_model=SessionListOut)
async def list_sessions(
    limit: int = 20,
    offset: int = 0,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> SessionListOut:
    """
    Return all sessions for the authenticated user, newest first.
    Never returns message content — only session metadata (fast sidebar load).
    """
    sessions_result = await db.execute(
        select(Session)
        .where(Session.user_id == current_user.id)
        .order_by(Session.updated_at.desc())
        .limit(limit)
        .offset(offset)
    )
    sessions = list(sessions_result.scalars().all())

    total_result = await db.execute(
        select(func.count(Session.id)).where(Session.user_id == current_user.id)
    )
    total: int = total_result.scalar_one()

    return SessionListOut(
        sessions=[SessionListItem.model_validate(s) for s in sessions],
        total=total,
    )


@router.post("", response_model=SessionOut, status_code=status.HTTP_201_CREATED)
async def create_session(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> SessionOut:
    """
    Create a new empty session. Title is null until the first message is sent.
    Called when the user clicks '+ New Chat'.
    """
    session = Session(user_id=current_user.id)
    db.add(session)
    await db.commit()
    await db.refresh(session)
    # Build the response explicitly to avoid lazy-loading the empty messages relationship
    return SessionOut(
        id=session.id,
        title=session.title,
        created_at=session.created_at,
        updated_at=session.updated_at,
        messages=[],
    )


@router.get("/{session_id}", response_model=SessionOut)
async def get_session(
    session_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> Session:
    """
    Fetch the full message history for a session.
    Messages are ordered chronologically (created_at ASC).
    Each assistant message includes its feedback (vote) if one exists.
    """
    result = await db.execute(
        select(Session)
        .options(
            # Eagerly load messages, and for each message eagerly load feedback and files.
            selectinload(Session.messages)
            .selectinload(Message.feedback),
            selectinload(Session.messages)
            .selectinload(Message.files),
        )
        .where(
            Session.id == session_id,
            Session.user_id == current_user.id,
        )
    )
    session = result.scalar_one_or_none()
    if session is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Session not found.")

    # Sort messages chronologically in Python (selectinload doesn't support ordering).
    session.messages.sort(key=lambda m: m.created_at)

    # Filter out tool-role messages before sending to the frontend.
    # They stay in the DB so the LLM sees them in inference context on
    # subsequent turns, but MessageBubble has no handler for this role.
    session.messages = [
        m for m in session.messages if m.role != RoleEnum.tool.value
    ]

    # Stamp file_id onto each message that has a linked file (e.g. a generated report).
    # file_id is not a column on Message — it lives on the File row that back-references
    # this message via File.message_id. Pydantic reads from attributes, so we set it
    # transiently here; it is serialized into MessageOut.file_id on the response.
    for m in session.messages:
        m.file_id = m.files[0].id if m.files else None  # type: ignore[attr-defined]

    return session


@router.patch("/{session_id}", response_model=SessionOut)
async def rename_session(
    session_id: uuid.UUID,
    body: SessionRename,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> SessionOut:
    """Update the title of a session (user edits from the sidebar)."""
    session = await get_owned_session(session_id, current_user, db)
    session.title = body.title
    await db.commit()
    await db.refresh(session)
    return SessionOut(
        id=session.id,
        title=session.title,
        created_at=session.created_at,
        updated_at=session.updated_at,
        messages=[],
    )


@router.delete("/{session_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_session(
    session_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> None:
    """
    Permanently delete a session and all its messages.
    The DB cascade handles messages → message_feedback automatically.
    """
    session = await get_owned_session(session_id, current_user, db)
    await db.delete(session)
    await db.commit()
