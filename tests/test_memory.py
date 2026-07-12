import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import select

from kid_terminal.app import cleanup, memory_context, store_turn
from kid_terminal.db import SessionLocal, init_db
from kid_terminal.models import Conversation, Device, LongTermMemory, Message


async def test_default_memory_is_redacted_and_long_term_is_opt_in():
    await init_db()
    device_id = str(uuid.uuid4())
    conversation_id = str(uuid.uuid4())
    async with SessionLocal() as db:
        db.add(
            Device(
                id=device_id,
                name="memory-test",
                token_hash=uuid.uuid4().hex,
                token_expires_at=datetime.now(UTC) + timedelta(days=1),
            )
        )
        await db.commit()
        for index in range(5):
            await store_turn(
                db,
                device_id,
                conversation_id,
                f"问题{index}，电话13800138000",
                f"回答{index}",
            )
        conversation = await db.get(Conversation, conversation_id)
        assert conversation and not conversation.summary
        messages = await db.scalars(
            select(Message).where(Message.conversation_id == conversation_id)
        )
        assert all("13800138000" not in item.content for item in messages)
        db.add(
            LongTermMemory(
                device_id=device_id,
                content="已经过期的记忆",
                expires_at=datetime.now(UTC) - timedelta(seconds=1),
            )
        )
        db.add(
            LongTermMemory(
                device_id=device_id,
                content="家长主动保存的记忆",
                expires_at=datetime.now(UTC) + timedelta(hours=1),
            )
        )
        await db.commit()
        context = await memory_context(db, device_id, conversation)
        assert "家长主动保存的记忆" in context
        assert "已经过期" not in context
        memories = await db.scalars(
            select(LongTermMemory).where(LongTermMemory.device_id == device_id)
        )
        assert len(list(memories)) == 2


async def test_cleanup_removes_expired_conversation_messages() -> None:
    await init_db()
    device_id = str(uuid.uuid4())
    conversation_id = str(uuid.uuid4())
    old = datetime.now(UTC) - timedelta(days=2)
    async with SessionLocal() as db:
        db.add(
            Device(
                id=device_id,
                name="retention-test",
                token_hash=uuid.uuid4().hex,
                token_expires_at=datetime.now(UTC) + timedelta(days=1),
            )
        )
        db.add(
            Conversation(
                id=conversation_id,
                device_id=device_id,
                updated_at=old,
            )
        )
        db.add(
            Message(
                conversation_id=conversation_id, role="user", content="过期问题", created_at=old
            )
        )
        await db.commit()
        result = await cleanup(db)
        assert result["messages"] >= 1
        assert result["conversations"] >= 1
        assert await db.get(Conversation, conversation_id) is None
