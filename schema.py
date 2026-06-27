"""Structured output schema for the sales visit extraction agent."""

from typing import Literal, Optional

from pydantic import BaseModel, Field


class NextStep(BaseModel):
    action: Optional[str] = Field(description="具体动作")
    owner: Optional[str] = Field(description="负责人，若未说明则为 null")
    deadline: Optional[str] = Field(description="截止时间，格式 YYYY-MM-DD 或 YYYY-MM-DD HH:MM；若未说明则为 null")


class SalesVisitRecord(BaseModel):
    sales_name: Optional[str] = Field(description="销售姓名，若未说明则为 null")
    customer: Optional[str] = Field(description="客户公司名，若未说明则为 null")
    contact_person: Optional[str] = Field(description="主要联系人及其角色，若未说明则为 null")
    visit_time: Optional[str] = Field(description="拜访时间，格式 YYYY-MM-DD 或 YYYY-MM-DD HH:MM；若未说明则为 null")
    topic: str = Field(description="核心议题，若无法推断则写'未知'")
    customer_interest: list[str] = Field(description="客户兴趣点列表")
    key_concerns: list[str] = Field(description="客户疑虑/挑战")
    competitors_mentioned: list[str] = Field(description="竞争对手名称及动态")
    next_steps: list[NextStep] = Field(description="下一步行动")
    risk_level: Literal["高", "中", "低"] = Field(description="风险等级")
    risk_reason: str = Field(description="风险判断依据")
    resources_needed: list[str] = Field(description="需要的内部支持")
    relationship_suggestion: str = Field(description="客户关系维护建议")
    strategic_insight: str = Field(description="基于本次拜访的战略洞察与销售策略建议，分析客户决策心理、竞对博弈关系和行动优先级")
    original_transcript: str = Field(description="保留原始文本以备复核")


# JSON schema for OpenAI structured output
SALES_VISIT_JSON_SCHEMA = {
    "name": "sales_visit_record",
    "strict": True,
    "schema": {
        "type": "object",
        "properties": {
            "sales_name": {"type": ["string", "null"]},
            "customer": {"type": ["string", "null"]},
            "contact_person": {"type": ["string", "null"]},
            "visit_time": {"type": ["string", "null"]},
            "topic": {"type": "string"},
            "customer_interest": {"type": "array", "items": {"type": "string"}},
            "key_concerns": {"type": "array", "items": {"type": "string"}},
            "competitors_mentioned": {"type": "array", "items": {"type": "string"}},
            "next_steps": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "action": {"type": ["string", "null"]},
                        "owner": {"type": ["string", "null"]},
                        "deadline": {"type": ["string", "null"]}
                    },
                    "required": ["action", "owner", "deadline"],
                    "additionalProperties": False
                }
            },
            "risk_level": {"type": "string", "enum": ["高", "中", "低"]},
            "risk_reason": {"type": "string"},
            "resources_needed": {"type": "array", "items": {"type": "string"}},
            "relationship_suggestion": {"type": "string"},
            "strategic_insight": {"type": "string"},
            "original_transcript": {"type": "string"}
        },
        "required": [
            "sales_name", "customer", "contact_person", "visit_time", "topic",
            "customer_interest", "key_concerns", "competitors_mentioned", "next_steps",
            "risk_level", "risk_reason", "resources_needed", "relationship_suggestion",
            "strategic_insight", "original_transcript"
        ],
        "additionalProperties": False
    }
}
