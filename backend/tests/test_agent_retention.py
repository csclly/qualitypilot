import uuid
from datetime import UTC, datetime, timedelta

from httpx import ASGITransport, AsyncClient
import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from app.agent.retention import (
    AgentRetentionPreview,
    CheckpointArchiveConflictError,
    CheckpointArchiveTooRecentError,
    CheckpointRetentionCandidate,
    SqlAgentRetentionStore,
)
from app.agent.security import (
    API_KEY_AUTH_METHOD,
    RETENTION_OPERATOR_ROLE,
    ApprovalPrincipal,
)
from app.api.routes.agent import get_approval_principal
from app.api.routes.agent_retention import get_agent_retention_store
from app.db import SessionLocal
from app.main import app


def _principal(*, authenticated: bool = True, authorized: bool = True):
    return ApprovalPrincipal(
        actor_id="retention-operator" if authenticated else "unverified",
        roles=frozenset({RETENTION_OPERATOR_ROLE}) if authorized else frozenset(),
        authenticated=authenticated,
        auth_method=API_KEY_AUTH_METHOD if authenticated else None,
    )


async def _insert_checkpoint_bundle(
    thread_id: uuid.UUID,
    *,
    created_at: datetime,
) -> None:
    checkpoint_id = str(uuid.uuid4())
    async with SessionLocal() as session:
        await session.execute(
            text(
                """
                INSERT INTO checkpoints
                    (thread_id, checkpoint_ns, checkpoint_id, type,
                     checkpoint, metadata, created_at)
                VALUES
                    (:thread_id, '', :checkpoint_id, 'json',
                     CAST(:checkpoint AS jsonb), '{}'::jsonb, :created_at)
                """
            ),
            {
                "thread_id": str(thread_id),
                "checkpoint_id": checkpoint_id,
                "checkpoint": (
                    '{"v": 1, "id": "' + checkpoint_id + '", '
                    '"ts": "2000-01-01T00:00:00Z", '
                    '"channel_values": {}, "channel_versions": {}, '
                    '"versions_seen": {}, "pending_sends": []}'
                ),
                "created_at": created_at,
            },
        )
        await session.execute(
            text(
                """
                INSERT INTO checkpoint_blobs
                    (thread_id, checkpoint_ns, channel, version, type, blob)
                VALUES (:thread_id, '', 'state', '1', 'bytes', :blob)
                """
            ),
            {"thread_id": str(thread_id), "blob": b"checkpoint-blob"},
        )
        await session.execute(
            text(
                """
                INSERT INTO checkpoint_writes
                    (thread_id, checkpoint_ns, checkpoint_id, task_id,
                     idx, channel, type, blob, task_path)
                VALUES
                    (:thread_id, '', :checkpoint_id, 'task-1', 0,
                     'state', 'bytes', :blob, '')
                """
            ),
            {
                "thread_id": str(thread_id),
                "checkpoint_id": checkpoint_id,
                "blob": b"checkpoint-write",
            },
        )
        await session.commit()


async def _active_counts(thread_id: uuid.UUID) -> tuple[int, int, int]:
    async with SessionLocal() as session:
        values = []
        for table_name in ("checkpoints", "checkpoint_blobs", "checkpoint_writes"):
            count = await session.scalar(
                text(
                    f"SELECT count(*) FROM {table_name} "
                    "WHERE thread_id = :thread_id"
                ),
                {"thread_id": str(thread_id)},
            )
            values.append(int(count or 0))
    return values[0], values[1], values[2]


async def _cleanup_active(thread_id: uuid.UUID) -> None:
    async with SessionLocal() as session:
        for table_name in ("checkpoint_writes", "checkpoint_blobs", "checkpoints"):
            await session.execute(
                text(f"DELETE FROM {table_name} WHERE thread_id = :thread_id"),
                {"thread_id": str(thread_id)},
            )
        await session.commit()


class FakeRetentionStore:
    def __init__(self) -> None:
        self.calls = 0

    async def preview(self, *, older_than_days, limit, now=None):
        self.calls += 1
        cutoff = datetime(2026, 7, 1, tzinfo=UTC)
        return AgentRetentionPreview(
            cutoff_at=cutoff,
            checkpoint_candidates=[
                CheckpointRetentionCandidate(
                    thread_id=uuid.uuid4(),
                    last_checkpoint_at=cutoff - timedelta(days=1),
                    checkpoint_count=2,
                    blob_count=1,
                    write_count=3,
                )
            ],
            approval_events_before_cutoff=4,
            run_error_events_before_cutoff=5,
            terminal_alerts_before_cutoff=6,
        )


async def test_retention_preview_requires_authentication() -> None:
    store = FakeRetentionStore()
    app.dependency_overrides[get_approval_principal] = lambda: _principal(
        authenticated=False
    )
    app.dependency_overrides[get_agent_retention_store] = lambda: store
    try:
        async with AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://test",
        ) as client:
            response = await client.get("/api/v1/agent/retention/preview")
    finally:
        app.dependency_overrides.pop(get_approval_principal, None)
        app.dependency_overrides.pop(get_agent_retention_store, None)

    assert response.status_code == 401
    assert store.calls == 0


async def test_retention_preview_requires_authorized_role() -> None:
    store = FakeRetentionStore()
    app.dependency_overrides[get_approval_principal] = lambda: _principal(
        authorized=False
    )
    app.dependency_overrides[get_agent_retention_store] = lambda: store
    try:
        async with AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://test",
        ) as client:
            response = await client.get("/api/v1/agent/retention/preview")
    finally:
        app.dependency_overrides.pop(get_approval_principal, None)
        app.dependency_overrides.pop(get_agent_retention_store, None)

    assert response.status_code == 403
    assert store.calls == 0


async def test_retention_preview_returns_only_aggregate_inventory() -> None:
    store = FakeRetentionStore()
    app.dependency_overrides[get_approval_principal] = lambda: _principal()
    app.dependency_overrides[get_agent_retention_store] = lambda: store
    try:
        async with AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://test",
        ) as client:
            response = await client.get(
                "/api/v1/agent/retention/preview?older_than_days=60&limit=10"
            )
    finally:
        app.dependency_overrides.pop(get_approval_principal, None)
        app.dependency_overrides.pop(get_agent_retention_store, None)

    assert response.status_code == 200
    assert response.json()["approval_events_before_cutoff"] == 4
    assert response.json()["run_error_events_before_cutoff"] == 5
    assert response.json()["terminal_alerts_before_cutoff"] == 6
    assert "actor_id" not in response.text
    assert "comment" not in response.text


async def test_archive_endpoint_rejects_mismatched_confirmation() -> None:
    path_id = uuid.uuid4()
    app.dependency_overrides[get_approval_principal] = lambda: _principal()
    app.dependency_overrides[get_agent_retention_store] = lambda: FakeRetentionStore()
    try:
        async with AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://test",
        ) as client:
            response = await client.post(
                f"/api/v1/agent/retention/checkpoints/{path_id}/archive",
                json={"confirm_thread_id": str(uuid.uuid4())},
            )
    finally:
        app.dependency_overrides.pop(get_approval_principal, None)
        app.dependency_overrides.pop(get_agent_retention_store, None)

    assert response.status_code == 409


@pytest.mark.integration
async def test_sql_retention_preview_archive_restore_and_idempotency() -> None:
    thread_id = uuid.uuid4()
    old_time = datetime.now(UTC) - timedelta(days=60)
    await _insert_checkpoint_bundle(thread_id, created_at=old_time)
    try:
        async with SessionLocal() as session:
            store = SqlAgentRetentionStore(session)
            preview = await store.preview(older_than_days=30, limit=100)
            candidate = next(
                item
                for item in preview.checkpoint_candidates
                if item.thread_id == thread_id
            )
            assert (
                candidate.checkpoint_count,
                candidate.blob_count,
                candidate.write_count,
            ) == (1, 1, 1)

            archive, changed = await store.archive_thread(
                thread_id=thread_id,
                older_than_days=30,
                actor_id="retention-operator",
                actor_authenticated=True,
                auth_method=API_KEY_AUTH_METHOD,
            )
            assert changed is True
            assert archive.status == "archived"
            duplicate, duplicate_changed = await store.archive_thread(
                thread_id=thread_id,
                older_than_days=30,
                actor_id="retention-operator",
                actor_authenticated=True,
                auth_method=API_KEY_AUTH_METHOD,
            )
            assert duplicate_changed is False
            assert duplicate.id == archive.id

        assert await _active_counts(thread_id) == (0, 0, 0)

        async with SessionLocal() as restore_session:
            store = SqlAgentRetentionStore(restore_session)
            restored, restored_changed = await store.restore_archive(
                archive_id=archive.id,
                actor_id="retention-operator",
            )
            assert restored_changed is True
            assert restored.status == "restored"
            assert restored.restored_at is not None
            duplicate_restore, duplicate_restore_changed = (
                await store.restore_archive(
                    archive_id=archive.id,
                    actor_id="retention-operator",
                )
            )
            assert duplicate_restore_changed is False
            assert duplicate_restore.id == archive.id

        assert await _active_counts(thread_id) == (1, 1, 1)
    finally:
        await _cleanup_active(thread_id)


@pytest.mark.integration
async def test_sql_archive_rejects_recent_checkpoint() -> None:
    thread_id = uuid.uuid4()
    await _insert_checkpoint_bundle(thread_id, created_at=datetime.now(UTC))
    try:
        async with SessionLocal() as session:
            with pytest.raises(CheckpointArchiveTooRecentError):
                await SqlAgentRetentionStore(session).archive_thread(
                    thread_id=thread_id,
                    older_than_days=30,
                    actor_id="retention-operator",
                    actor_authenticated=True,
                    auth_method=API_KEY_AUTH_METHOD,
                )
        assert await _active_counts(thread_id) == (1, 1, 1)
    finally:
        await _cleanup_active(thread_id)


@pytest.mark.integration
async def test_sql_restore_refuses_to_overwrite_active_checkpoint() -> None:
    thread_id = uuid.uuid4()
    await _insert_checkpoint_bundle(
        thread_id,
        created_at=datetime.now(UTC) - timedelta(days=60),
    )
    async with SessionLocal() as archive_session:
        archive, _ = await SqlAgentRetentionStore(archive_session).archive_thread(
            thread_id=thread_id,
            older_than_days=30,
            actor_id="retention-operator",
            actor_authenticated=True,
            auth_method=API_KEY_AUTH_METHOD,
        )
    await _insert_checkpoint_bundle(thread_id, created_at=datetime.now(UTC))
    try:
        async with SessionLocal() as restore_session:
            with pytest.raises(CheckpointArchiveConflictError, match="拒绝覆盖"):
                await SqlAgentRetentionStore(restore_session).restore_archive(
                    archive_id=archive.id,
                    actor_id="retention-operator",
                )
        assert await _active_counts(thread_id) == (1, 1, 1)
    finally:
        await _cleanup_active(thread_id)


@pytest.mark.integration
async def test_sql_archive_rolls_back_when_delete_phase_fails(monkeypatch) -> None:
    thread_id = uuid.uuid4()
    await _insert_checkpoint_bundle(
        thread_id,
        created_at=datetime.now(UTC) - timedelta(days=60),
    )
    try:
        async with SessionLocal() as session:
            store = SqlAgentRetentionStore(session)

            async def fail_delete(_thread_id: str):
                raise RuntimeError("forced delete failure")

            monkeypatch.setattr(store, "_delete_source_rows", fail_delete)
            with pytest.raises(RuntimeError, match="forced delete failure"):
                await store.archive_thread(
                    thread_id=thread_id,
                    older_than_days=30,
                    actor_id="retention-operator",
                    actor_authenticated=True,
                    auth_method=API_KEY_AUTH_METHOD,
                )
        assert await _active_counts(thread_id) == (1, 1, 1)
        async with SessionLocal() as verify_session:
            archive_count = await verify_session.scalar(
                text(
                    "SELECT count(*) FROM agent_checkpoint_archives "
                    "WHERE thread_id = :thread_id"
                ),
                {"thread_id": str(thread_id)},
            )
            assert archive_count == 0
    finally:
        await _cleanup_active(thread_id)


@pytest.mark.integration
async def test_checkpoint_archive_payload_is_immutable() -> None:
    thread_id = uuid.uuid4()
    await _insert_checkpoint_bundle(
        thread_id,
        created_at=datetime.now(UTC) - timedelta(days=60),
    )
    async with SessionLocal() as session:
        archive, _ = await SqlAgentRetentionStore(session).archive_thread(
            thread_id=thread_id,
            older_than_days=30,
            actor_id="retention-operator",
            actor_authenticated=True,
            auth_method=API_KEY_AUTH_METHOD,
        )
    async with SessionLocal() as mutation_session:
        with pytest.raises(DBAPIError, match="immutable"):
            await mutation_session.execute(
                text(
                    "UPDATE agent_checkpoint_archive_checkpoints "
                    "SET type = 'tampered' WHERE archive_id = :archive_id"
                ),
                {"archive_id": archive.id},
            )
            await mutation_session.commit()
        await mutation_session.rollback()
    async with SessionLocal() as manifest_session:
        with pytest.raises(DBAPIError, match="immutable"):
            await manifest_session.execute(
                text(
                    "UPDATE agent_checkpoint_archives "
                    "SET archived_by = 'tampered' WHERE id = :archive_id"
                ),
                {"archive_id": archive.id},
            )
            await manifest_session.commit()
        await manifest_session.rollback()


@pytest.mark.integration
async def test_retention_api_archives_lists_and_restores_checkpoint() -> None:
    thread_id = uuid.uuid4()
    await _insert_checkpoint_bundle(
        thread_id,
        created_at=datetime.now(UTC) - timedelta(days=60),
    )
    app.dependency_overrides[get_approval_principal] = lambda: _principal()
    try:
        async with AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://test",
        ) as client:
            archived = await client.post(
                f"/api/v1/agent/retention/checkpoints/{thread_id}/archive",
                json={
                    "confirm_thread_id": str(thread_id),
                    "older_than_days": 30,
                },
            )
            assert archived.status_code == 200
            archive_id = archived.json()["archive"]["id"]
            assert archived.json()["changed"] is True
            assert archived.json()["archive"]["status"] == "archived"
            assert await _active_counts(thread_id) == (0, 0, 0)

            listed = await client.get(
                "/api/v1/agent/retention/checkpoint-archives?limit=100"
            )
            assert listed.status_code == 200
            assert archive_id in {item["id"] for item in listed.json()["items"]}

            restored = await client.post(
                f"/api/v1/agent/retention/checkpoint-archives/{archive_id}/restore",
                json={"confirm_archive_id": archive_id},
            )
            assert restored.status_code == 200
            assert restored.json()["changed"] is True
            assert restored.json()["archive"]["status"] == "restored"
    finally:
        app.dependency_overrides.pop(get_approval_principal, None)
        await _cleanup_active(thread_id)

    assert await _active_counts(thread_id) == (0, 0, 0)
