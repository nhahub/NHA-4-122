from __future__ import annotations

import json
import time
import uuid
from typing import AsyncGenerator

from fastapi import HTTPException, status
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from core.metrics import (
    inference_duration_seconds,
    time_to_first_token_seconds,
    tokens_generated_total,
    stream_errors_total,
)

from llm.client import LLMClient
from llm.prompt import count_tokens, format_chat_prompt
from models.file import File
from models.message import Message
from models.session import Session
from models.user import User
from schemas.enums import RoleEnum

import logging

_logger = logging.getLogger("core.sse")

# Tag boundaries the orchestrator watches for in the token stream.
_TOOL_CALL_OPEN = "<tool_call>"
_TOOL_CALL_CLOSE = "</tool_call>"


async def get_owned_session(
    session_id: uuid.UUID,
    current_user: User,
    db: AsyncSession,
) -> Session:
    """
    Fetch a session by ID and verify it belongs to the authenticated user.
    Raises 404 if not found or not owned.
    """
    result = await db.execute(
        select(Session).where(
            Session.id == session_id,
            Session.user_id == current_user.id,
        )
    )
    session = result.scalar_one_or_none()
    if session is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Session not found.")
    return session


def build_sse_event(event: str, data: dict) -> str:
    """Format an SSE string from an event name and JSON payload."""
    return f"event: {event}\ndata: {json.dumps(data)}\n\n"


async def sse_generator(
    db: AsyncSession,
    client: LLMClient,
    messages: list[dict],          # Raw message dicts — format_chat_prompt() called internally
    tools: list[dict] | None,      # ACTIVE_TOOL_SCHEMAS or None to disable tool calling
    session: Session,
    user_message_id: uuid.UUID,
    assistant_message_id: uuid.UUID,
    session_id: uuid.UUID,
) -> AsyncGenerator[str, None]:
    """
    Async generator that drives the SSE stream for a single chat turn.

    When tools is not None, the generator operates as an orchestration loop:
    - Streams tokens looking for a <tool_call>...</tool_call> block.
    - On detection: dispatches the tool, persists the tool-role message,
      emits tool_call SSE events, creates the confirmation Message, sets
      File.message_id (two-phase commit), then ends the stream.
    - If no tool call is detected: behaves identically to the original
      single-pass streamer.
    """
    from datetime import datetime, timezone
    from tools.registry import dispatch

    # 1. Signal start
    yield build_sse_event("message_start", {
        "user_message_id": str(user_message_id),
        "assistant_message_id": str(assistant_message_id),
    })

    # 2. Build the prompt with tools injected into the template
    prompt = format_chat_prompt(messages=messages, tools=tools)

    full_response: list[str] = []
    start_time = time.monotonic()
    first_token_time: float | None = None

    # Buffer used to accumulate text once we see <tool_call> until </tool_call>
    tool_buffer: str = ""
    in_tool_call: bool = False
    tool_call_detected: bool = False

    try:
        async for token in client.generate_stream(prompt):
            if first_token_time is None:
                first_token_time = time.monotonic()
                time_to_first_token_seconds.observe(first_token_time - start_time)

            normalized = token.replace("\\n", "\n").replace("\\t", "\t")

            if not in_tool_call:
                # Accumulate to detect the opening tag across token boundaries
                tool_buffer += normalized
                open_idx = tool_buffer.find(_TOOL_CALL_OPEN)

                if open_idx == -1:
                    # No opening tag yet - flush safe prefix and keep a small tail
                    # in the buffer in case the tag spans tokens.
                    safe_len = max(0, len(tool_buffer) - len(_TOOL_CALL_OPEN))
                    if safe_len > 0:
                        safe_text = tool_buffer[:safe_len]
                        full_response.append(safe_text)
                        yield build_sse_event("token", {"token": safe_text})
                        tool_buffer = tool_buffer[safe_len:]
                else:
                    # Flush everything before the opening tag as normal tokens
                    pre_text = tool_buffer[:open_idx]
                    if pre_text:
                        full_response.append(pre_text)
                        yield build_sse_event("token", {"token": pre_text})
                    # Switch into tool-call accumulation mode
                    tool_buffer = tool_buffer[open_idx + len(_TOOL_CALL_OPEN):]
                    in_tool_call = True
                    tool_call_detected = True
            else:
                # Inside a tool call block - accumulate until closing tag.
                # Note: _TOOL_CALL_STOP in client.py stops generation AT
                # </tool_call>, so we may receive the close tag here or the
                # stream may simply end. Handle both cases.
                tool_buffer += normalized
                if _TOOL_CALL_CLOSE in tool_buffer:
                    close_idx = tool_buffer.find(_TOOL_CALL_CLOSE)
                    tool_buffer = tool_buffer[:close_idx]
                    break  # Stream ended at the tool call boundary

    except Exception as exc:
        stream_errors_total.inc()
        _logger.error("Inference error: %s", exc, exc_info=True)
        yield build_sse_event("error", {"detail": "Generation failed. Please retry."})
        return

    # --- Branch: tool call was detected ---
    if tool_call_detected:
        raw_json = tool_buffer.strip()

        # Parse tool name and args from the JSON the model emitted
        try:
            tool_payload = json.loads(raw_json) if raw_json else {}
            tool_name: str = tool_payload.get("name", "")
            tool_args: dict = tool_payload.get("arguments", tool_payload.get("parameters", {}))
            if isinstance(tool_args, str):
                tool_args = json.loads(tool_args)
        except (json.JSONDecodeError, ValueError):
            _logger.warning("Could not parse tool call JSON: %r", raw_json)
            tool_name = "generate_incident_report"
            tool_args = {}

        _logger.info("Tool call detected: %s args=%s", tool_name, tool_args)
        yield build_sse_event("tool_call", {"tool": tool_name, "status": "running"})

        # Dispatch - session_id always comes from the trusted request context,
        # never from the LLM-generated args (IDOR defense).
        tool_result = await dispatch(
            name=tool_name,
            args=tool_args,
            session_id=session_id,
            db=db,
        )

        # Persist the tool-role message so the model is aware of the report
        # on all subsequent turns (history query in messages.py has no role filter).
        # The Qwen3 tokenizer natively supports role="tool" in apply_chat_template.
        tool_content = json.dumps({
            "tool_name": tool_name,
            **tool_result,
        })
        tool_msg = Message(
            session_id=session_id,
            role=RoleEnum.tool.value,
            content=tool_content,
            token_count=count_tokens(tool_content),
        )
        db.add(tool_msg)
        await db.flush()  # Get the id without committing yet

        # Build the fixed templated confirmation string (no loop-back generation).
        # FLAG-04: wording confirmed, no emoji.
        filename = tool_result.get("filename", "report.md")
        confirmation_text = f"Incident report generated — {filename}"

        # Create the confirmation Message row FIRST (FLAG-01 resolution: Option A).
        # The File.message_id is set in the UPDATE below, after we have the message id.
        confirmation_msg = Message(
            id=assistant_message_id,
            session_id=session_id,
            role=RoleEnum.assistant.value,
            content=confirmation_text,
            token_count=count_tokens(confirmation_text),
        )
        db.add(confirmation_msg)
        await db.flush()

        # Two-phase commit: link File row to the confirmation Message (FLAG-01).
        file_id = tool_result.get("file_id")
        if file_id and tool_result.get("status") == "ok":
            await db.execute(
                update(File)
                .where(File.id == uuid.UUID(file_id))
                .values(message_id=assistant_message_id)
            )

        session.updated_at = datetime.now(timezone.utc)
        await db.commit()

        inference_duration_seconds.observe(time.monotonic() - start_time)

        # Emit the completion events.
        # FLAG-04: file_id travels on tool_call:done only, NOT on message_end.
        # This avoids mutating the existing message_end contract.
        yield build_sse_event("tool_call", {
            "tool": tool_name,
            "status": "done",
            "filename": filename,
            "file_id": file_id,
        })

        # Stream the confirmation text as a token so the frontend renders it
        # in the chat bubble before message_end arrives.
        yield build_sse_event("token", {"token": confirmation_text})

        yield build_sse_event("message_end", {
            "assistant_message_id": str(assistant_message_id),
            "token_count": count_tokens(confirmation_text),
            "finish_reason": "tool",
        })
        return

    # --- Branch: normal response (no tool call) ---
    # Flush any remaining safe buffer content that was held back
    if tool_buffer:
        full_response.append(tool_buffer)
        yield build_sse_event("token", {"token": tool_buffer})

    complete_content = "".join(full_response)
    assistant_token_count = count_tokens(complete_content) if complete_content else 0
    finish_reason = client.last_finish_reason

    assistant_msg = Message(
        id=assistant_message_id,
        session_id=session_id,
        role=RoleEnum.assistant.value,
        content=complete_content,
        token_count=assistant_token_count,
    )
    db.add(assistant_msg)
    session.updated_at = datetime.now(timezone.utc)
    await db.commit()

    inference_duration_seconds.observe(time.monotonic() - start_time)
    tokens_generated_total.inc(assistant_token_count)

    yield build_sse_event("message_end", {
        "assistant_message_id": str(assistant_message_id),
        "token_count": assistant_token_count,
        "finish_reason": finish_reason,
    })
