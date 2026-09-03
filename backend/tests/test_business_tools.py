import pytest

from app.agent.business_tools import (
    BusinessRecord,
    BusinessSystem,
    BusinessToolAuthenticationError,
    BusinessToolErrorKind,
    BusinessToolUnavailableError,
    InMemoryReadOnlyBusinessTool,
    collect_business_context,
    to_agent_business_context,
)


def _record(
    system: BusinessSystem,
    record_id: str,
) -> BusinessRecord:
    return BusinessRecord(
        system=system,
        record_id=record_id,
        record_type="quality_event",
        summary=f"记录 {record_id}",
        attributes={"line": "SMT-01"},
    )


async def test_collects_mes_and_qms_records_in_tool_order() -> None:
    question = "查询批次 LOT-001 的质量记录"
    mes_record = _record(BusinessSystem.MES, "MES-1")
    qms_record = _record(BusinessSystem.QMS, "QMS-1")
    mes = InMemoryReadOnlyBusinessTool(
        name="mes-reader",
        system=BusinessSystem.MES,
        responses={question: [mes_record]},
        delay_seconds=0.01,
    )
    qms = InMemoryReadOnlyBusinessTool(
        name="qms-reader",
        system=BusinessSystem.QMS,
        responses={question: [qms_record]},
    )

    results = await collect_business_context(question, [mes, qms], limit_per_tool=3)

    assert [result.tool_name for result in results] == ["mes-reader", "qms-reader"]
    assert results[0].records == (mes_record,)
    assert results[1].records == (qms_record,)
    assert all(result.succeeded for result in results)
    assert mes.calls == [(question, 3)]
    assert qms.calls == [(question, 3)]


async def test_one_tool_timeout_does_not_discard_other_results() -> None:
    slow_mes = InMemoryReadOnlyBusinessTool(
        name="slow-mes",
        system=BusinessSystem.MES,
        delay_seconds=0.05,
    )
    qms_record = _record(BusinessSystem.QMS, "QMS-2")
    fast_qms = InMemoryReadOnlyBusinessTool(
        name="fast-qms",
        system=BusinessSystem.QMS,
        responses={"问题": [qms_record]},
    )

    results = await collect_business_context(
        "问题",
        [slow_mes, fast_qms],
        timeout_seconds=0.01,
    )

    assert results[0].failure is not None
    assert results[0].failure.kind is BusinessToolErrorKind.TIMEOUT
    assert results[0].failure.retryable is True
    assert results[1].records == (qms_record,)


@pytest.mark.parametrize(
    ("error", "kind", "retryable"),
    [
        (
            BusinessToolAuthenticationError(),
            BusinessToolErrorKind.AUTHENTICATION,
            False,
        ),
        (
            BusinessToolUnavailableError(),
            BusinessToolErrorKind.UNAVAILABLE,
            True,
        ),
    ],
)
async def test_classifies_known_tool_errors(
    error: Exception,
    kind: BusinessToolErrorKind,
    retryable: bool,
) -> None:
    tool = InMemoryReadOnlyBusinessTool(
        name="failing-tool",
        system=BusinessSystem.MES,
        error=error,
    )

    result = (await collect_business_context("问题", [tool]))[0]

    assert result.failure is not None
    assert result.failure.kind is kind
    assert result.failure.retryable is retryable
    assert result.records == ()


async def test_rejects_cross_system_records_as_invalid_response() -> None:
    tool = InMemoryReadOnlyBusinessTool(
        name="mes-reader",
        system=BusinessSystem.MES,
        responses={"问题": [_record(BusinessSystem.QMS, "WRONG-1")]},
    )

    result = (await collect_business_context("问题", [tool]))[0]

    assert result.failure is not None
    assert result.failure.kind is BusinessToolErrorKind.INVALID_RESPONSE
    assert result.records == ()


async def test_unexpected_error_is_redacted() -> None:
    class BrokenTool(InMemoryReadOnlyBusinessTool):
        async def query(self, question: str, *, limit: int):
            raise RuntimeError("internal password=secret")

    tool = BrokenTool(name="broken", system=BusinessSystem.QMS)

    result = (await collect_business_context("问题", [tool]))[0]

    assert result.failure is not None
    assert result.failure.kind is BusinessToolErrorKind.UNEXPECTED
    assert "secret" not in result.failure.message


async def test_validates_query_configuration_before_calling_tools() -> None:
    tool = InMemoryReadOnlyBusinessTool(name="reader", system=BusinessSystem.MES)

    with pytest.raises(ValueError, match="问题不能为空"):
        await collect_business_context("  ", [tool])
    with pytest.raises(ValueError, match="结果上限"):
        await collect_business_context("问题", [tool], limit_per_tool=0)
    with pytest.raises(ValueError, match="超时时间"):
        await collect_business_context("问题", [tool], timeout_seconds=0)

    assert tool.calls == []


async def test_rejects_duplicate_tool_names() -> None:
    first = InMemoryReadOnlyBusinessTool(name="reader", system=BusinessSystem.MES)
    second = InMemoryReadOnlyBusinessTool(name="reader", system=BusinessSystem.QMS)

    with pytest.raises(ValueError, match="名称不能重复"):
        await collect_business_context("问题", [first, second])

    assert first.calls == []
    assert second.calls == []


async def test_converts_tool_results_to_plain_checkpoint_dtos() -> None:
    record = _record(BusinessSystem.MES, "MES-DTO-1")
    tool = InMemoryReadOnlyBusinessTool(
        name="mes-reader",
        system=BusinessSystem.MES,
        responses={"问题": [record]},
    )

    records, failures = to_agent_business_context(
        await collect_business_context("问题", [tool])
    )

    assert records == [
        {
            "tool_name": "mes-reader",
            "system": "mes",
            "record_id": "MES-DTO-1",
            "record_type": "quality_event",
            "summary": "记录 MES-DTO-1",
            "attributes": {"line": "SMT-01"},
        }
    ]
    assert failures == []


def test_business_record_rejects_nested_or_excessive_attributes() -> None:
    with pytest.raises(ValueError, match="来源系统"):
        BusinessRecord(
            system="erp",  # type: ignore[arg-type]
            record_id="ERP-BAD-1",
            record_type="batch",
            summary="来源系统无效",
        )

    with pytest.raises(ValueError, match="标量值"):
        BusinessRecord(
            system=BusinessSystem.MES,
            record_id="MES-BAD-1",
            record_type="batch",
            summary="无效嵌套属性",
            attributes={"nested": {"secret": "value"}},  # type: ignore[dict-item]
        )

    with pytest.raises(ValueError, match="属性数量"):
        BusinessRecord(
            system=BusinessSystem.QMS,
            record_id="QMS-BAD-1",
            record_type="event",
            summary="属性过多",
            attributes={f"field_{index}": index for index in range(21)},
        )
