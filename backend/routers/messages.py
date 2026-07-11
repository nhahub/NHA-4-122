"""
routers/messages.py — Messaging and feedback endpoints.

POST  /sessions/{session_id}/messages          — Send a message; stream the AI response via SSE.
POST  /messages/{message_id}/feedback          — Submit a vote (first time).
PATCH /messages/{message_id}/feedback          — Change an existing vote.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import StreamingResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from core.config import settings
from core.database import get_db
from core.dependencies import get_current_user
from core.metrics import busy_rejections_total
from core.utils import sse_generator
from llm.client import LLMClient
from llm.context import fit_history
from llm.prompt import count_tokens
from models.feedback import MessageFeedback
from models.message import Message
from models.session import Session
from models.user import User
from schemas.enums import RoleEnum
from schemas.feedback import FeedbackIn, FeedbackOut
from schemas.message import MessageIn
from tools.schemas import ACTIVE_TOOL_SCHEMAS

router = APIRouter()

# ── Endpoints ─────────────────────────────────────────────────────────────────

@router.post("/sessions/{session_id}/messages")
async def send_message(
    session_id: uuid.UUID,
    body: MessageIn,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> StreamingResponse:
    """
    The critical path endpoint. Accepts a user message, persists it, builds the
    Qwen3 prompt, and streams the model response back as SSE.
    """
    # 1. Check model busy — MUST happen before any DB write
    client = LLMClient.get()
    if client.is_busy:
        busy_rejections_total.inc()
        raise HTTPException(
            status_code=status.HTTP_423_LOCKED,
            detail="The model is currently processing another request. Please wait and retry.",
        )

    # 2. Validate session ownership
    session_result = await db.execute(
        select(Session).where(
            Session.id == session_id,
            Session.user_id == current_user.id,
        )
    )
    session = session_result.scalar_one_or_none()
    if session is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Session not found.")

    # 3. Token-count the user message and persist it
    token_count = count_tokens(body.content)
    user_msg = Message(
        session_id=session_id,
        role=RoleEnum.user.value,
        content=body.content,
        token_count=token_count,
    )
    db.add(user_msg)

    # 4. Auto-title: set session title from the first ~40 chars of the first message
    if session.title is None:
        title_raw = body.content[:40]
        if len(body.content) > 40:
            last_space = title_raw.rfind(" ")
            if last_space > 0:
                title_raw = title_raw[:last_space]
            session.title = title_raw + "..."
        else:
            session.title = title_raw

    await db.commit()
    await db.refresh(user_msg)

    # 5. Fetch full history and trim to token budget
    history_result = await db.execute(
        select(Message)
        .where(Message.session_id == session_id)
        .order_by(Message.created_at.asc())
    )
    all_messages = list(history_result.scalars().all())
    trimmed = fit_history(all_messages, settings.chat_history_token_budget)

    # 6. Build raw message list for the orchestration loop
    messages_for_template = [
        {"role": msg.role, "content": msg.content}
        for msg in trimmed
    ]

    # 7. Reserve the assistant message ID and begin streaming
    assistant_id = uuid.uuid4()

    return StreamingResponse(
        sse_generator(
            db=db,
            client=client,
            messages=messages_for_template,
            tools=ACTIVE_TOOL_SCHEMAS,
            session=session,
            user_message_id=user_msg.id,
            assistant_message_id=assistant_id,
            session_id=session_id,
        ),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",  # Disables Nginx buffering for SSE
        },
    )


@router.post(
    "/messages/{message_id}/feedback",
    response_model=FeedbackOut,
    status_code=status.HTTP_201_CREATED,
)
async def submit_feedback(
    message_id: uuid.UUID,
    body: FeedbackIn,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> MessageFeedback:
    """Submit a vote for an assistant message (first time only)."""
    # Validate: message must exist, belong to current user's session, and be from the assistant
    msg_result = await db.execute(
        select(Message)
        .join(Session, Message.session_id == Session.id)
        .where(
            Message.id == message_id,
            Session.user_id == current_user.id,
            Message.role == RoleEnum.assistant.value,
        )
    )
    message = msg_result.scalar_one_or_none()
    if message is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Assistant message not found or you do not have access to it.",
        )

    # Check for existing feedback (409 if already voted)
    existing = await db.execute(
        select(MessageFeedback).where(MessageFeedback.message_id == message_id)
    )
    if existing.scalar_one_or_none() is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Feedback already exists. Use PATCH to change your vote.",
        )

    feedback = MessageFeedback(message_id=message_id, vote=body.vote.value)
    db.add(feedback)
    await db.commit()
    await db.refresh(feedback)
    return feedback


@router.patch("/messages/{message_id}/feedback", response_model=FeedbackOut)
async def update_feedback(
    message_id: uuid.UUID,
    body: FeedbackIn,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> MessageFeedback:
    """Change an existing vote (e.g., switch from upvote to downvote)."""
    # Validate ownership and role in a single query
    msg_result = await db.execute(
        select(Message)
        .join(Session, Message.session_id == Session.id)
        .where(
            Message.id == message_id,
            Session.user_id == current_user.id,
            Message.role == RoleEnum.assistant.value,
        )
    )
    if msg_result.scalar_one_or_none() is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Assistant message not found or you do not have access to it.",
        )

    feedback_result = await db.execute(
        select(MessageFeedback).where(MessageFeedback.message_id == message_id)
    )
    feedback = feedback_result.scalar_one_or_none()
    if feedback is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No feedback found for this message. Use POST to submit a vote first.",
        )

    feedback.vote = body.vote.value
    feedback.updated_at = datetime.now(timezone.utc)
    await db.commit()
    await db.refresh(feedback)
    return feedback
