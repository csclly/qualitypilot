import asyncio
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from enum import Enum
from typing import Protocol, TypeAlias


BusinessValue: TypeAlias = str | int | float | bool | None


class BusinessSystem(str, Enum):
    MES = "mes"
    QMS = "qms"


class BusinessToolErrorKind(str, Enum):
    TIMEOUT = "timeout"
    AUTHENTICATION = "authentication"
    PERMISSION = "permission"
    UNAVAILABLE = "unavailable"
    INVALID_RESPONSE = "invalid_response"
    UNEXPECTED = "unexpected"


@dataclass(frozen=True, slots=True)
class BusinessRecord:
    system: BusinessSystem
    record_id: str
    record_type: str
    summary: str
    attributes: Mapping[str, BusinessValue] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.record_id.strip():
            raise ValueError("业务记录 ID 不能为空")
        if not self.record_type.strip():
            raise ValueError("业务记录类型不能为空")
        if not self.summary.strip():
            raise ValueError("业务记录摘要不能为空")


@dataclass(frozen=True, slots=True)
class BusinessToolFailure:
    kind: BusinessToolErrorKind
    message: str
    retryable: bool


@dataclass(frozen=True, slots=True)
class BusinessToolResult:
    tool_name: str
    system: BusinessSystem
    records: tuple[BusinessRecord, ...]
    failure: BusinessToolFailure | None = None

    @property
    def succeeded(self) -> bool:
        return self.failure is None


class ReadOnlyBusinessTool(Protocol):
    """MES/QMS 只读查询边界；协议故意不暴露写入或执行方法。"""

    @property
    def name(self) -> str: ...

    @property
    def system(self) -> BusinessSystem: ...

    async def query(
        self,
        question: str,
        *,
        limit: int,
    ) -> Sequence[BusinessRecord]: ...


class BusinessToolError(RuntimeError):
    def __init__(
        self,
        message: str,
        *,
        kind: BusinessToolErrorKind,
        retryable: bool,
    ) -> None:
        super().__init__(message)
        self.kind = kind
        self.retryable = retryable


class BusinessToolAuthenticationError(BusinessToolError):
    def __init__(self, message: str = "业务系统认证失败") -> None:
        super().__init__(
            message,
            kind=BusinessToolErrorKind.AUTHENTICATION,
            retryable=False,
        )


class BusinessToolPermissionError(BusinessToolError):
    def __init__(self, message: str = "业务系统拒绝只读查询") -> None:
        super().__init__(
            message,
            kind=BusinessToolErrorKind.PERMISSION,
            retryable=False,
        )


class BusinessToolUnavailableError(BusinessToolError):
    def __init__(self, message: str = "业务系统暂时不可用") -> None:
        super().__init__(
            message,
            kind=BusinessToolErrorKind.UNAVAILABLE,
            retryable=True,
        )


class BusinessToolResponseError(BusinessToolError):
    def __init__(self, message: str = "业务系统响应不符合契约") -> None:
        super().__init__(
            message,
            kind=BusinessToolErrorKind.INVALID_RESPONSE,
            retryable=False,
        )


async def collect_business_context(
    question: str,
    tools: Sequence[ReadOnlyBusinessTool],
    *,
    limit_per_tool: int = 10,
    timeout_seconds: float = 5.0,
) -> tuple[BusinessToolResult, ...]:
    if not question.strip():
        raise ValueError("业务工具查询问题不能为空")
    if limit_per_tool <= 0:
        raise ValueError("单工具结果上限必须大于 0")
    if timeout_seconds <= 0:
        raise ValueError("业务工具超时时间必须大于 0")

    names = [tool.name for tool in tools]
    if any(not name.strip() for name in names):
        raise ValueError("业务工具名称不能为空")
    if len(names) != len(set(names)):
        raise ValueError("业务工具名称不能重复")

    results = await asyncio.gather(
        *(
            _query_tool(
                tool,
                question,
                limit=limit_per_tool,
                timeout_seconds=timeout_seconds,
            )
            for tool in tools
        )
    )
    return tuple(results)


async def _query_tool(
    tool: ReadOnlyBusinessTool,
    question: str,
    *,
    limit: int,
    timeout_seconds: float,
) -> BusinessToolResult:
    try:
        records = tuple(
            await asyncio.wait_for(
                tool.query(question, limit=limit),
                timeout=timeout_seconds,
            )
        )
        _validate_records(tool, records, limit=limit)
        return BusinessToolResult(
            tool_name=tool.name,
            system=tool.system,
            records=records,
        )
    except TimeoutError:
        failure = BusinessToolFailure(
            kind=BusinessToolErrorKind.TIMEOUT,
            message="业务系统只读查询超时",
            retryable=True,
        )
    except BusinessToolError as exc:
        failure = BusinessToolFailure(
            kind=exc.kind,
            message=str(exc),
            retryable=exc.retryable,
        )
    except Exception:
        failure = BusinessToolFailure(
            kind=BusinessToolErrorKind.UNEXPECTED,
            message="业务工具发生未分类错误",
            retryable=False,
        )
    return BusinessToolResult(
        tool_name=tool.name,
        system=tool.system,
        records=(),
        failure=failure,
    )


def _validate_records(
    tool: ReadOnlyBusinessTool,
    records: tuple[BusinessRecord, ...],
    *,
    limit: int,
) -> None:
    if len(records) > limit:
        raise BusinessToolResponseError("业务工具返回数量超过请求上限")
    if any(record.system != tool.system for record in records):
        raise BusinessToolResponseError("业务工具返回了其他系统的记录")
    record_ids = [record.record_id for record in records]
    if len(record_ids) != len(set(record_ids)):
        raise BusinessToolResponseError("业务工具返回了重复记录 ID")


class InMemoryReadOnlyBusinessTool:
    """用于测试和本地演示的只读假工具，不连接真实业务系统。"""

    def __init__(
        self,
        *,
        name: str,
        system: BusinessSystem,
        responses: Mapping[str, Sequence[BusinessRecord]] | None = None,
        delay_seconds: float = 0,
        error: BusinessToolError | None = None,
    ) -> None:
        if not name.strip():
            raise ValueError("业务工具名称不能为空")
        if delay_seconds < 0:
            raise ValueError("假工具延迟不能小于 0")
        self._name = name
        self._system = system
        self._responses = dict(responses or {})
        self._delay_seconds = delay_seconds
        self._error = error
        self.calls: list[tuple[str, int]] = []

    @property
    def name(self) -> str:
        return self._name

    @property
    def system(self) -> BusinessSystem:
        return self._system

    async def query(
        self,
        question: str,
        *,
        limit: int,
    ) -> Sequence[BusinessRecord]:
        self.calls.append((question, limit))
        if self._delay_seconds:
            await asyncio.sleep(self._delay_seconds)
        if self._error is not None:
            raise self._error
        return tuple(self._responses.get(question, ()))[:limit]
