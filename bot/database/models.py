from sqlalchemy import Column, BigInteger, Integer, String, Boolean, Text, DateTime, ForeignKey, func
from sqlalchemy.orm import DeclarativeBase, relationship


class Base(DeclarativeBase):
    pass


class User(Base):
    __tablename__ = "users"

    id = Column(BigInteger, primary_key=True, autoincrement=True)
    telegram_id = Column(BigInteger, unique=True, nullable=False, index=True)
    username = Column(String(255))
    first_name = Column(String(255))
    last_name = Column(String(255))
    language_code = Column(String(10))

    is_bot = Column(Boolean, default=False, nullable=False)
    is_blocked = Column(Boolean, default=False, nullable=False)
    is_active = Column(Boolean, default=True, index=True, nullable=False)

    created_at = Column(DateTime(timezone=True), server_default=func.now(), index=True, nullable=False)
    last_interaction = Column(DateTime(timezone=True), server_default=func.now(), index=True, nullable=False)
    blocked_at = Column(DateTime(timezone=True))

    broadcasts = relationship("Broadcast", back_populates="creator")
    broadcast_deliveries = relationship("BroadcastDelivery", back_populates="user")


class Channel(Base):
    __tablename__ = "channels"

    id = Column(Integer, primary_key=True, autoincrement=True)
    channel_id = Column(BigInteger, unique=True, nullable=False)
    channel_username = Column(String(255), unique=True, nullable=False)
    channel_title = Column(String(255), nullable=False)
    button_text = Column(String(255), nullable=False)
    button_url = Column(String(512), nullable=False)
    is_active = Column(Boolean, default=True, nullable=False)
    priority = Column(Integer, default=0, nullable=False)
    added_by = Column(BigInteger, ForeignKey("users.telegram_id"))
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)


class Broadcast(Base):
    __tablename__ = "broadcasts"

    id = Column(BigInteger, primary_key=True, autoincrement=True)
    created_by = Column(BigInteger, ForeignKey("users.telegram_id", ondelete="SET NULL"), index=True)
    mode = Column(String(20), nullable=False)
    content_preview = Column(Text)
    status = Column(String(20), default="created", index=True, nullable=False)
    total_targets = Column(Integer, default=0, nullable=False)
    success_count = Column(Integer, default=0, nullable=False)
    failed_count = Column(Integer, default=0, nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    started_at = Column(DateTime(timezone=True))
    finished_at = Column(DateTime(timezone=True))

    creator = relationship("User", back_populates="broadcasts")
    deliveries = relationship("BroadcastDelivery", back_populates="broadcast", cascade="all, delete-orphan")


class BroadcastDelivery(Base):
    __tablename__ = "broadcast_deliveries"

    id = Column(BigInteger, primary_key=True, autoincrement=True)
    broadcast_id = Column(BigInteger, ForeignKey("broadcasts.id", ondelete="CASCADE"), index=True, nullable=False)
    user_id = Column(BigInteger, ForeignKey("users.telegram_id", ondelete="SET NULL"), index=True)
    status = Column(String(20), nullable=False)
    error = Column(Text)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    broadcast = relationship("Broadcast", back_populates="deliveries")
    user = relationship("User", back_populates="broadcast_deliveries")
