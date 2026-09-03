"""Validate the configured model with synthetic evidence, without database writes.

Run from backend: .venv/bin/python -m scripts.generation_smoke
"""

import asyncio
import json
import sys

from app.agent.state import AgentEvidence
from app.core.config import get_settings
from app.services.generation.errors import GenerationServiceError
from app.services.generation.factory import create_generation_provider


async def main() -> int:
    settings = get_settings()
    evidence: list[AgentEvidence] = [{
        "chunk_id": "00000000-0000-4000-8000-000000000001",
        "document_id": "00000000-0000-4000-8000-000000000002",
        "document_title": "合成联调证据：AOI 短路报点复核",
        "source_uri": "test://generation-smoke",
        "original_filename": None,
        "chunk_index": 0,
        "content": (
            "AOI 短路报点增加不等于真实短路增加。先检查原始图像和程序版本，"
            "结合显微复核与电测结果判断是否存在真实铜桥；缺少 MES/Gerber "
            "时必须说明证据不足，不得确认具体根因。"
        ),
        "score": 1.0,
        "match_type": "keyword",
        "vector_score": None,
        "keyword_score": 1.0,
    }]
    provider = None
    try:
        provider = create_generation_provider(settings)
        draft = await provider.generate(
            "AOI 发现某批 PCB 短路报点突然增加，没有 MES 和 Gerber 数据，应该怎么分析？",
            evidence,
        )
    except GenerationServiceError as exc:
        print(json.dumps({
            "ok": False, "error": type(exc).__name__, "message": str(exc),
        }, ensure_ascii=False))
        return 1
    finally:
        if provider is not None:
            await provider.aclose()
    print(json.dumps({
        "ok": True,
        "provider": settings.generation_provider,
        "model": settings.generation_model,
        "evidence_source": "synthetic",
        "draft": draft,
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
