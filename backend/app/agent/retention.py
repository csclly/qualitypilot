import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import AgentCheckpointArchive


class CheckpointArchiveNotFoundError(LookupError):
    pass


class CheckpointArchiveTooRecentError(RuntimeError):
    pass


class CheckpointArchiveConflictError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class CheckpointRetentionCandidate:
    thread_id: uuid.UUID
    last_checkpoint_at: datetime
    checkpoint_count: int
    blob_count: int
    write_count: int


@dataclass(frozen=True, slots=True)
class AgentRetentionPreview:
    cutoff_at: datetime
    checkpoint_candidates: list[CheckpointRetentionCandidate]
    approval_events_before_cutoff: int
    run_error_events_before_cutoff: int
    terminal_alerts_before_cutoff: int


@dataclass(frozen=True, slots=True)
class CheckpointArchiveRecord:
    id: uuid.UUID
    thread_id: uuid.UUID
    status: str
    cutoff_at: datetime
    source_last_checkpoint_at: datetime
    checkpoint_count: int
    blob_count: int
    write_count: int
    archived_by: str
    actor_authenticated: bool
    auth_method: str | None
    archived_at: datetime
    restored_by: str | None
    restored_at: datetime | None


class SqlAgentRetentionStore:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def preview(
        self,
        *,
        older_than_days: int,
        limit: int,
        now: datetime | None = None,
    ) -> AgentRetentionPreview:
        _validate_retention_range(older_than_days=older_than_days, limit=limit)
        cutoff_at = (now or datetime.now(UTC)) - timedelta(days=older_than_days)
        result = await self._session.execute(
            text(
                """
                SELECT c.thread_id,
                       max(c.created_at) AS last_checkpoint_at,
                       count(*) AS checkpoint_count,
                       (SELECT count(*) FROM checkpoint_blobs b
                        WHERE b.thread_id = c.thread_id) AS blob_count,
                       (SELECT count(*) FROM checkpoint_writes w
                        WHERE w.thread_id = c.thread_id) AS write_count
                FROM checkpoints c
                GROUP BY c.thread_id
                HAVING max(c.created_at) < :cutoff_at
                ORDER BY max(c.created_at), c.thread_id
                LIMIT :limit
                """
            ),
            {"cutoff_at": cutoff_at, "limit": limit},
        )
        candidates = [
            CheckpointRetentionCandidate(
                thread_id=uuid.UUID(row.thread_id),
                last_checkpoint_at=row.last_checkpoint_at,
                checkpoint_count=row.checkpoint_count,
                blob_count=row.blob_count,
                write_count=row.write_count,
            )
            for row in result
        ]
        approval_count = await self._scalar_count(
            "agent_audit_events",
            "occurred_at",
            cutoff_at,
        )
        error_count = await self._scalar_count(
            "agent_run_error_events",
            "occurred_at",
            cutoff_at,
        )
        terminal_alerts = await self._session.scalar(
            text(
                """
                SELECT count(*) FROM agent_alert_outbox
                WHERE created_at < :cutoff_at
                  AND status IN ('delivered', 'failed')
                """
            ),
            {"cutoff_at": cutoff_at},
        )
        return AgentRetentionPreview(
            cutoff_at=cutoff_at,
            checkpoint_candidates=candidates,
            approval_events_before_cutoff=approval_count,
            run_error_events_before_cutoff=error_count,
            terminal_alerts_before_cutoff=int(terminal_alerts or 0),
        )

    async def list_archives(self, *, limit: int) -> list[CheckpointArchiveRecord]:
        if limit < 1 or limit > 500:
            raise ValueError("归档查询数量必须在 1—500 之间")
        result = await self._session.execute(
            select(AgentCheckpointArchive)
            .order_by(AgentCheckpointArchive.archived_at.desc())
            .limit(limit)
        )
        return [_to_record(item) for item in result.scalars()]

    async def archive_thread(
        self,
        *,
        thread_id: uuid.UUID,
        older_than_days: int,
        actor_id: str,
        actor_authenticated: bool,
        auth_method: str | None,
        now: datetime | None = None,
    ) -> tuple[CheckpointArchiveRecord, bool]:
        _validate_actor(actor_id, actor_authenticated, auth_method)
        _validate_retention_range(older_than_days=older_than_days, limit=1)
        cutoff_at = (now or datetime.now(UTC)) - timedelta(days=older_than_days)
        thread_value = str(thread_id)
        try:
            await self._lock_checkpoint_tables()
            existing = await self._active_archive(thread_value, lock=True)
            if existing is not None:
                await self._session.rollback()
                return existing, False

            source = await self._source_counts(thread_value)
            if source is None:
                raise CheckpointArchiveNotFoundError("未找到活动检查点")
            last_checkpoint_at, checkpoint_count, blob_count, write_count = source
            if last_checkpoint_at >= cutoff_at:
                raise CheckpointArchiveTooRecentError(
                    "检查点尚未达到配置的保留期限"
                )

            archive = AgentCheckpointArchive(
                id=uuid.uuid4(),
                thread_id=thread_value,
                status="archived",
                cutoff_at=cutoff_at,
                source_last_checkpoint_at=last_checkpoint_at,
                checkpoint_count=checkpoint_count,
                blob_count=blob_count,
                write_count=write_count,
                archived_by=actor_id,
                actor_authenticated=actor_authenticated,
                auth_method=auth_method,
            )
            self._session.add(archive)
            await self._session.flush()
            copied = await self._copy_to_archive(archive.id, thread_value)
            expected = (checkpoint_count, blob_count, write_count)
            if copied != expected:
                raise RuntimeError("检查点归档复制计数不一致")
            deleted = await self._delete_source_rows(thread_value)
            if deleted != expected:
                raise RuntimeError("检查点归档删除计数不一致")
            await self._session.commit()
            await self._session.refresh(archive)
            return _to_record(archive), True
        except Exception:
            await self._session.rollback()
            raise

    async def restore_archive(
        self,
        *,
        archive_id: uuid.UUID,
        actor_id: str,
    ) -> tuple[CheckpointArchiveRecord, bool]:
        if not actor_id.strip() or actor_id != actor_id.strip():
            raise ValueError("恢复操作人标识无效")
        try:
            await self._lock_checkpoint_tables()
            result = await self._session.execute(
                select(AgentCheckpointArchive)
                .where(AgentCheckpointArchive.id == archive_id)
                .with_for_update()
            )
            archive = result.scalar_one_or_none()
            if archive is None:
                raise CheckpointArchiveNotFoundError("未找到检查点归档")
            if archive.status == "restored":
                existing = _to_record(archive)
                await self._session.rollback()
                return existing, False
            if await self._source_exists(archive.thread_id):
                raise CheckpointArchiveConflictError(
                    "活动检查点已存在，拒绝覆盖恢复"
                )
            restored = await self._copy_to_source(archive.id, archive.thread_id)
            expected = (
                archive.checkpoint_count,
                archive.blob_count,
                archive.write_count,
            )
            if restored != expected:
                raise RuntimeError("检查点恢复计数不一致")
            archive.status = "restored"
            archive.restored_by = actor_id
            archive.restored_at = datetime.now(UTC)
            await self._session.commit()
            await self._session.refresh(archive)
            return _to_record(archive), True
        except Exception:
            await self._session.rollback()
            raise

    async def _active_archive(
        self,
        thread_id: str,
        *,
        lock: bool,
    ) -> CheckpointArchiveRecord | None:
        statement = select(AgentCheckpointArchive).where(
            AgentCheckpointArchive.thread_id == thread_id,
            AgentCheckpointArchive.status == "archived",
        )
        if lock:
            statement = statement.with_for_update()
        result = await self._session.execute(statement)
        archive = result.scalar_one_or_none()
        return _to_record(archive) if archive is not None else None

    async def _source_counts(
        self,
        thread_id: str,
    ) -> tuple[datetime, int, int, int] | None:
        row = (
            await self._session.execute(
                text(
                    """
                    SELECT max(created_at) AS last_checkpoint_at,
                           count(*) AS checkpoint_count,
                           (SELECT count(*) FROM checkpoint_blobs
                            WHERE thread_id = :thread_id) AS blob_count,
                           (SELECT count(*) FROM checkpoint_writes
                            WHERE thread_id = :thread_id) AS write_count
                    FROM checkpoints WHERE thread_id = :thread_id
                    """
                ),
                {"thread_id": thread_id},
            )
        ).one()
        if row.checkpoint_count == 0:
            return None
        return (
            row.last_checkpoint_at,
            row.checkpoint_count,
            row.blob_count,
            row.write_count,
        )

    async def _source_exists(self, thread_id: str) -> bool:
        value = await self._session.scalar(
            text(
                """
                SELECT EXISTS(
                    SELECT 1 FROM checkpoints WHERE thread_id = :thread_id
                    UNION ALL
                    SELECT 1 FROM checkpoint_blobs WHERE thread_id = :thread_id
                    UNION ALL
                    SELECT 1 FROM checkpoint_writes WHERE thread_id = :thread_id
                )
                """
            ),
            {"thread_id": thread_id},
        )
        return bool(value)

    async def _copy_to_archive(
        self,
        archive_id: uuid.UUID,
        thread_id: str,
    ) -> tuple[int, int, int]:
        parameters = {"archive_id": archive_id, "thread_id": thread_id}
        checkpoints = await self._session.execute(
            text(
                """
                INSERT INTO agent_checkpoint_archive_checkpoints
                    (archive_id, checkpoint_ns, checkpoint_id,
                     parent_checkpoint_id, type, checkpoint, metadata, created_at)
                SELECT :archive_id, checkpoint_ns, checkpoint_id,
                       parent_checkpoint_id, type, checkpoint, metadata, created_at
                FROM checkpoints WHERE thread_id = :thread_id
                """
            ),
            parameters,
        )
        blobs = await self._session.execute(
            text(
                """
                INSERT INTO agent_checkpoint_archive_blobs
                    (archive_id, checkpoint_ns, channel, version, type, blob)
                SELECT :archive_id, checkpoint_ns, channel, version, type, blob
                FROM checkpoint_blobs WHERE thread_id = :thread_id
                """
            ),
            parameters,
        )
        writes = await self._session.execute(
            text(
                """
                INSERT INTO agent_checkpoint_archive_writes
                    (archive_id, checkpoint_ns, checkpoint_id, task_id,
                     idx, channel, type, blob, task_path)
                SELECT :archive_id, checkpoint_ns, checkpoint_id, task_id,
                       idx, channel, type, blob, task_path
                FROM checkpoint_writes WHERE thread_id = :thread_id
                """
            ),
            parameters,
        )
        return checkpoints.rowcount, blobs.rowcount, writes.rowcount

    async def _delete_source_rows(self, thread_id: str) -> tuple[int, int, int]:
        parameters = {"thread_id": thread_id}
        writes = await self._session.execute(
            text("DELETE FROM checkpoint_writes WHERE thread_id = :thread_id"),
            parameters,
        )
        blobs = await self._session.execute(
            text("DELETE FROM checkpoint_blobs WHERE thread_id = :thread_id"),
            parameters,
        )
        checkpoints = await self._session.execute(
            text("DELETE FROM checkpoints WHERE thread_id = :thread_id"),
            parameters,
        )
        return checkpoints.rowcount, blobs.rowcount, writes.rowcount

    async def _copy_to_source(
        self,
        archive_id: uuid.UUID,
        thread_id: str,
    ) -> tuple[int, int, int]:
        parameters = {"archive_id": archive_id, "thread_id": thread_id}
        checkpoints = await self._session.execute(
            text(
                """
                INSERT INTO checkpoints
                    (thread_id, checkpoint_ns, checkpoint_id,
                     parent_checkpoint_id, type, checkpoint, metadata, created_at)
                SELECT :thread_id, checkpoint_ns, checkpoint_id,
                       parent_checkpoint_id, type, checkpoint, metadata, created_at
                FROM agent_checkpoint_archive_checkpoints
                WHERE archive_id = :archive_id
                """
            ),
            parameters,
        )
        blobs = await self._session.execute(
            text(
                """
                INSERT INTO checkpoint_blobs
                    (thread_id, checkpoint_ns, channel, version, type, blob)
                SELECT :thread_id, checkpoint_ns, channel, version, type, blob
                FROM agent_checkpoint_archive_blobs
                WHERE archive_id = :archive_id
                """
            ),
            parameters,
        )
        writes = await self._session.execute(
            text(
                """
                INSERT INTO checkpoint_writes
                    (thread_id, checkpoint_ns, checkpoint_id, task_id,
                     idx, channel, type, blob, task_path)
                SELECT :thread_id, checkpoint_ns, checkpoint_id, task_id,
                       idx, channel, type, blob, task_path
                FROM agent_checkpoint_archive_writes
                WHERE archive_id = :archive_id
                """
            ),
            parameters,
        )
        return checkpoints.rowcount, blobs.rowcount, writes.rowcount

    async def _lock_checkpoint_tables(self) -> None:
        await self._session.execute(
            text(
                "LOCK TABLE checkpoints, checkpoint_blobs, checkpoint_writes "
                "IN SHARE ROW EXCLUSIVE MODE"
            )
        )

    async def _scalar_count(
        self,
        table_name: str,
        timestamp_column: str,
        cutoff_at: datetime,
    ) -> int:
        allowed = {
            ("agent_audit_events", "occurred_at"),
            ("agent_run_error_events", "occurred_at"),
        }
        if (table_name, timestamp_column) not in allowed:
            raise ValueError("不允许的保留期统计字段")
        value = await self._session.scalar(
            text(
                f"SELECT count(*) FROM {table_name} "
                f"WHERE {timestamp_column} < :cutoff_at"
            ),
            {"cutoff_at": cutoff_at},
        )
        return int(value or 0)


def _validate_retention_range(*, older_than_days: int, limit: int) -> None:
    if older_than_days < 1 or older_than_days > 3650:
        raise ValueError("检查点保留天数必须在 1—3650 之间")
    if limit < 1 or limit > 500:
        raise ValueError("保留期预览数量必须在 1—500 之间")


def _validate_actor(
    actor_id: str,
    actor_authenticated: bool,
    auth_method: str | None,
) -> None:
    if (
        not actor_id.strip()
        or actor_id != actor_id.strip()
        or len(actor_id) > 255
    ):
        raise ValueError("归档操作人标识无效")
    if actor_authenticated is not (auth_method is not None):
        raise ValueError("归档身份认证状态与认证方式不一致")


def _to_record(archive: AgentCheckpointArchive) -> CheckpointArchiveRecord:
    return CheckpointArchiveRecord(
        id=archive.id,
        thread_id=uuid.UUID(archive.thread_id),
        status=archive.status,
        cutoff_at=archive.cutoff_at,
        source_last_checkpoint_at=archive.source_last_checkpoint_at,
        checkpoint_count=archive.checkpoint_count,
        blob_count=archive.blob_count,
        write_count=archive.write_count,
        archived_by=archive.archived_by,
        actor_authenticated=archive.actor_authenticated,
        auth_method=archive.auth_method,
        archived_at=archive.archived_at,
        restored_by=archive.restored_by,
        restored_at=archive.restored_at,
    )
