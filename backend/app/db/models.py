from datetime import datetime, timezone

from sqlalchemy import DateTime, Float, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


_TZ_DATETIME = DateTime(timezone=True)


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True)
    email: Mapped[str] = mapped_column(String(255), unique=True, index=True, nullable=False)
    hashed_password: Mapped[str] = mapped_column(String(255), nullable=False)
    created_at: Mapped[datetime] = mapped_column(_TZ_DATETIME, default=_utcnow)

    broker_credentials: Mapped[list["BrokerCredentialRecord"]] = relationship(
        back_populates="user", cascade="all, delete-orphan"
    )


class BrokerCredentialRecord(Base):
    """Broker credentials, encrypted at rest (see app/secrets_store/encryption.py). The DB only
    ever stores ciphertext - decryption happens in memory, on demand, right before an adapter
    is constructed.
    """

    __tablename__ = "broker_credentials"
    __table_args__ = (UniqueConstraint("user_id", "broker_name", name="uq_user_broker"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    broker_name: Mapped[str] = mapped_column(String(50), nullable=False)
    encrypted_payload: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(_TZ_DATETIME, default=_utcnow)
    updated_at: Mapped[datetime] = mapped_column(_TZ_DATETIME, default=_utcnow, onupdate=_utcnow)

    user: Mapped["User"] = relationship(back_populates="broker_credentials")


class TradeRecord(Base):
    """A persisted paper (or, once wired, live) trade for a logged-in user - the storage layer
    a Trade Journal / Positions view reads from. Anonymous paper-execute calls (no logged-in
    user) are never written here, only ever the in-memory response.

    `exit_time`/`exit_price`/`pnl` stay null for as long as the position is open. Today nothing
    monitors live prices to close a paper-execute position automatically (that only happens
    inside the historical backtest engine's simulation) - closing one is a future write to this
    same row, not a new endpoint yet, so every row currently ends up "open" until that lands.
    """

    __tablename__ = "trades"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    mode: Mapped[str] = mapped_column(String(10), nullable=False, default="PAPER")
    symbol: Mapped[str] = mapped_column(String(50), nullable=False)
    strategy_id: Mapped[str] = mapped_column(String(100), nullable=False)
    direction: Mapped[str] = mapped_column(String(10), nullable=False)
    entry_time: Mapped[datetime] = mapped_column(_TZ_DATETIME, nullable=False)
    entry_price: Mapped[float] = mapped_column(Float, nullable=False)
    quantity: Mapped[int] = mapped_column(Integer, nullable=False)
    stop_loss: Mapped[float] = mapped_column(Float, nullable=False)
    target1: Mapped[float] = mapped_column(Float, nullable=False)
    target2: Mapped[float | None] = mapped_column(Float, nullable=True)
    exit_time: Mapped[datetime | None] = mapped_column(_TZ_DATETIME, nullable=True)
    exit_price: Mapped[float | None] = mapped_column(Float, nullable=True)
    exit_reason: Mapped[str | None] = mapped_column(String(100), nullable=True)
    pnl: Mapped[float | None] = mapped_column(Float, nullable=True)
    charges: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    created_at: Mapped[datetime] = mapped_column(_TZ_DATETIME, default=_utcnow)


class AuditLogRecord(Base):
    """Security-relevant events (register, login, credential stored, broker authenticated, ...).
    Per the platform's audit-trail requirement: every credential/auth action leaves a row here.
    """

    __tablename__ = "audit_logs"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    event: Mapped[str] = mapped_column(String(100), nullable=False)
    detail: Mapped[str] = mapped_column(Text, nullable=False, default="")
    created_at: Mapped[datetime] = mapped_column(_TZ_DATETIME, default=_utcnow)
