from app.agent.state import AgentEvidence, AgentRecommendation


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
                "当前版本使用确定性规则整理证据，尚未接入生成模型。",
                "证据仅来自知识库，不包含实时 MES/QMS 生产数据。",
                "草稿通过人工审批前不得触发工单或生产参数变更。",
            ],
        }
