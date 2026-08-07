import asyncio
from collections.abc import Callable

from app.agent.state import AgentEvidence, AgentRecommendation
from app.services.generation.errors import (
    GenerationAPIError,
    GenerationServiceError,
    GenerationTransportError,
)
from app.services.generation.qwen import QwenStructuredRecommendationGenerator


GenerationProviderFactory = Callable[[], QwenStructuredRecommendationGenerator]


class EvidenceBasedDraftGenerator:
    """在尚未选定生成模型前，生成可追溯且必须审批的规则草稿。"""

    async def generate(
        self,
        question: str,
        evidence: list[AgentEvidence],
    ) -> AgentRecommendation:
        if not evidence:
            return {
                "summary": "知识库中没有检索到可用证据，暂不能形成处置建议。",
                "suggested_actions": [],
                "risk_notes": [
                    "需要补充知识文档或现场数据后重新分析。",
                    "当前草稿不得触发生产操作。",
                ],
                "citations": [],
                "generation_mode": "deterministic_fallback",
            }

        suggested_actions = [
            (
                f"核查《{item['document_title']}》第 "
                f"{item['chunk_index'] + 1} 个分块，并结合现场数据确认。"
            )
            for item in evidence[:3]
        ]
        return {
            "summary": (
                f"针对“{question}”已整理 {len(evidence)} 条知识库证据，"
                "以下内容是等待人工确认的分析草稿。"
            ),
            "suggested_actions": suggested_actions,
            "risk_notes": [
                "当前草稿使用确定性规则整理证据。",
                "证据仅来自知识库，不包含实时 MES/QMS 生产数据。",
                "草稿通过人工审批前不得触发工单或生产参数变更。",
            ],
            "citations": [item["chunk_id"] for item in evidence[:3]],
            "generation_mode": "deterministic_fallback",
        }


class ResilientRecommendationGenerator:
    """优先使用结构化模型，失败时返回明确标记的安全规则草稿。"""

    def __init__(
        self,
        provider_factory: GenerationProviderFactory,
        *,
        max_retries: int,
        retry_base_delay_seconds: float,
        fallback: EvidenceBasedDraftGenerator | None = None,
    ) -> None:
        self._provider_factory = provider_factory
        self._max_retries = max_retries
        self._retry_base_delay_seconds = retry_base_delay_seconds
        self._fallback = fallback or EvidenceBasedDraftGenerator()

    async def generate(
        self,
        question: str,
        evidence: list[AgentEvidence],
    ) -> AgentRecommendation:
        if not evidence:
            return await self._fallback.generate(question, evidence)

        provider: QwenStructuredRecommendationGenerator | None = None
        try:
            provider = self._provider_factory()
            retry_count = 0
            while True:
                try:
                    return await provider.generate(question, evidence)
                except GenerationServiceError as exc:
                    if retry_count >= self._max_retries or not _is_retryable(exc):
                        raise
                    delay = self._retry_base_delay_seconds * (2**retry_count)
                    retry_count += 1
                    if delay > 0:
                        await asyncio.sleep(delay)
        except GenerationServiceError as exc:
            draft = await self._fallback.generate(question, evidence)
            draft["risk_notes"].insert(0, _fallback_reason(exc))
            return draft
        finally:
            if provider is not None:
                await provider.aclose()


def _is_retryable(exc: GenerationServiceError) -> bool:
    if isinstance(exc, GenerationAPIError):
        return exc.retryable
    return isinstance(exc, GenerationTransportError)


def _fallback_reason(exc: GenerationServiceError) -> str:
    if isinstance(exc, GenerationAPIError):
        category = "远端模型服务错误"
    elif isinstance(exc, GenerationTransportError):
        category = "模型网络或超时错误"
    else:
        category = "模型配置或响应校验错误"
    return f"{category}，本次已降级为确定性草稿，必须人工复核。"
