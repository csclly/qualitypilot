import argparse
import asyncio
from pathlib import Path

from app.evaluation.runner import load_dataset, run_api_evaluation, save_report


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="运行 QualityPilot 知识检索评测")
    parser.add_argument("--dataset", required=True, type=Path, help="评测集 JSON 路径")
    parser.add_argument(
        "--base-url",
        default="http://localhost:8000",
        help="QualityPilot 后端地址",
    )
    parser.add_argument("--top-k", default=5, type=int, help="每个查询返回的分块数")
    parser.add_argument(
        "--search-mode",
        choices=("vector", "keyword", "hybrid"),
        default="vector",
        help="检索模式",
    )
    parser.add_argument("--timeout", default=60.0, type=float, help="单次请求超时秒数")
    parser.add_argument(
        "--request-max-retries",
        default=2,
        type=int,
        help="瞬时 HTTP 错误的最大重试次数",
    )
    parser.add_argument("--output", type=Path, help="可选的 JSON 报告保存路径")
    return parser.parse_args()


async def main() -> None:
    args = parse_args()
    dataset = load_dataset(args.dataset)
    report = await run_api_evaluation(
        dataset,
        base_url=args.base_url,
        top_k=args.top_k,
        search_mode=args.search_mode,
        timeout_seconds=args.timeout,
        request_max_retries=args.request_max_retries,
    )
    if args.output:
        save_report(report, args.output)

    print(f"评测集：{report.dataset_name} {report.dataset_version}")
    print(f"检索模式：{report.search_mode}")
    print(f"查询数：{report.query_count}")
    print(f"Recall@{report.top_k}：{report.recall_at_k:.4f}")
    print(f"MRR：{report.mrr:.4f}")
    print(f"引用正确率@{report.top_k}：{report.citation_accuracy_at_k:.4f}")


if __name__ == "__main__":
    asyncio.run(main())
