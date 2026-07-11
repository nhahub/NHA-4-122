import uuid
from sqlalchemy import Column, String, Text, Integer, DateTime, ForeignKey, Index, CheckConstraint
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.sql import func
from sqlalchemy.orm import relationship
from core.database import Base


class Message(Base):
    __tablename__ = "messages"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    session_id = Column(UUID(as_uuid=True), ForeignKey("sessions.id", ondelete="CASCADE"), nullable=False)
    # 'assistant' aligns with HuggingFace tokenizer.apply_chat_template() contract.
    role = Column(String(10), nullable=False)
    content = Column(Text, nullable=False)
    # Nullable by design — token count for assistant messages is only known after
    # streaming completes. Must be backfilled immediately; never left null permanently.
    token_count = Column(Integer, nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())

    # Relationships
    session = relationship("Session", back_populates="messages")
    feedback = relationship("MessageFeedback", back_populates="message", uselist=False, cascade="all, delete-orphan")
    files = relationship("File", back_populates="message")

    # Indexes & constraints
    __table_args__ = (
        # Primary read pattern: all messages in a session, chronological order.
        Index("ix_messages_session_created", "session_id", "created_at"),
        # Enforced at DB level — a service bug passing an invalid role fails here,
        # not silently in production.
        CheckConstraint("role IN ('user', 'assistant', 'tool')", name="ck_messages_role"),
    )
