"""Prompt templates for the sales visit extraction agent."""

import json
from pathlib import Path
from typing import Optional


def _load_golden_cases():
    path = Path(__file__).parent / "golden_cases.json"
    return json.loads(path.read_text(encoding="utf-8"))


def _format_concise_examples(cases: list) -> str:
    """Format examples in a compact way for large context."""
    parts = []
    for c in cases:
        ctx = c.get("context", {})
        ctx_text = ""
        if ctx.get("sales_name"):
            ctx_text += f" 销售={ctx['sales_name']}"
        if ctx.get("reference_date"):
            ctx_text += f" 今天={ctx['reference_date']}"
        if ctx.get("visit_time"):
            ctx_text += f" 拜访时间={ctx['visit_time']}"
        parts.append(
            f"--- 样本 {c['sample_id']} ---\n"
            f"上下文:{ctx_text}\n"
            f"原文：{c['original_transcript']}\n"
            f"输出：{json.dumps(c['gold'], ensure_ascii=False)}"
        )
    return "\n\n".join(parts)


def _context_text(context: Optional[dict]) -> str:
    if not context:
        return "（无额外上下文）"
    lines = []
    if context.get("sales_name"):
        lines.append(f"- 销售姓名: {context['sales_name']}")
    if context.get("reference_date"):
        lines.append(f"- 记录日期（今天）: {context['reference_date']}")
    if context.get("visit_time"):
        lines.append(f"- 拜访时间（已知）: {context['visit_time']}")
    if not lines:
        return "（无额外上下文）"
    return "\n".join(lines)


def build_extraction_prompt(
    transcript: str,
    context: Optional[dict] = None,
    history_context: Optional[str] = None  # NEW: 历史记忆上下文
) -> str:
    # Use all 40 golden cases as few-shot examples to maximize alignment with expected output style
    cases = _load_golden_cases()
    examples_text = _format_concise_examples(cases)

    history_section = ""
    if history_context and "无历史记录" not in history_context:
        history_section = f"""

## 📚 历史拜访记录（重要）

{history_context}

**基于以上历史信息，判断本次拜访是否有进展、风险是否变化、下一步是否延续了之前的承诺。**
"""

    return f"""你是一位资深的销售运营分析师，专门负责将销售人员口述的拜访记录转化为结构化的 CRM 数据。

你的任务：从一段原始口语记录中，结合已知上下文和历史拜访记录，提取关键信息并输出为严格的 JSON 对象。

## 已知上下文（本次输入）

{_context_text(context)}{history_section}

## 字段说明

- sales_name: 销售姓名。**如果上下文中已提供，直接使用该值；否则，如果文本未说明，输出"未知"，不要输出 null。**
- customer: 客户公司名。使用文本中的完整/正式称呼。如果文本未说明，输出"未知"，不要输出 null。
- contact_person: 主要联系人及其角色。如果文本未说明，输出"未知"，不要输出 null。
- visit_time: 拜访时间。格式为 YYYY-MM-DD 或 YYYY-MM-DD HH:MM。**如果上下文中已提供拜访时间，直接使用该值；否则，基于上下文中的记录日期和文本中的时间线索推断。若无法确定，输出"未知"，不要输出 null。**
- topic: 核心议题，使用简洁客观的短语，不要加推断或评论。优先使用与示例风格一致的短标签，不要添加"交流"、"讨论"、"会议"、"评审"等后缀（如用"高速 NOA 方案"而非"高速 NOA 方案交流"）。如果无法推断，写"未知"。
- customer_interest: 客户明确表达的兴趣点/需求列表。没有则为空数组。
- key_concerns: 客户明确提出的疑虑、担忧、条件或限制。**包括：客户提出的要求、客户表达的不确定/担忧、客户设置的门槛条件、客户可能自研/替代的风险信号。** 示例："只考虑成熟方案" → "方案成熟度要求高"；"可能想自研一部分" → "对方自研可能性"；"问了功能安全的问题" → "功能安全"。**不要把销售自己的观察或判断（如"疑似套取信息"）填入此处。** 没有则为空数组。
- competitors_mentioned: 提到的竞争对手。没有则为空数组。
- next_steps: 下一步行动。每个元素包含 action（动作）、owner（负责人）、deadline（截止时间）。如果无下一步，则为空数组。**不要把"保持联系""有问题随时找"等模糊社交行为列为 next_step。**
  - action: 必须是销售承诺的"交付物+时限"，不要写成"让谁去做"的过程描述。例如："明天提交初步适配方案" ✓，"明天让系统架构师出初步方案" ✗。
  - owner: 格式为"销售姓名"或"销售姓名/协同角色简称"。如果销售姓名未知，owner 为"销售"。协同角色用简称："系统架构师"→"架构师"，"财务总监"→"财务总监"，"工程部"→"工程部"。**只有协同角色是行动的主要执行者/责任方才加入 owner；如果只是陪同、支持或提供资源，不加入 owner，而放入 resources_needed。** 示例："让系统架构师出方案"→owner="陈敏/架构师"；"跟财务总监商量特批"→owner="李雅/财务总监"；"带算法专家去给陈博演示"→owner="赵刚"（算法专家仅支持，放入 resources_needed）。
- risk_level: 风险等级，只能从"高/中/低"中选择。判断依据：客户明显不满/威胁/重大合同分歧 → 高；客户观望/竞对强劲/条件苛刻 → 中；无明确阻力/兴趣积极 → 低。
- risk_reason: 风险判断依据。必须基于文本事实，不能编造。用1-2句话说明。
- resources_needed: 需要公司内部提供的支持（如"算法专家支持"、"法务支持"）。没有则为空数组。
- relationship_suggestion: 客户关系维护建议。基于文本推导，若无法给出则写"无"。
- strategic_insight: **战略洞察**。基于本次拜访内容，生成一条面向销售 Leader 的决策建议。分析客户的心理锚点、竞对博弈关系、我方行动优先级——不是"记录事实"，而是"放大决策者认知"。示例："客户以博世报价为锚点施压，建议暂不正面竞价，转而强调我方技术响应速度和国产化合规优势——这两项是博世短期内难以匹配的。" 若信息不足以形成洞察，写"无"。
- original_transcript: 保留原始文本。

## 核心规则（必须严格遵守）

1. **不编造**：任何文本中未明确出现的信息，对应字段必须为 null 或空数组。不能虚构客户、时间、预算、行动、竞对。
2. **优先使用上下文**：sales_name、拜访时间、"今天"的基准日期以已知上下文为准。
3. **不推断过度**：只有文本明确暗示或表达的内容才可提取；不要补充常识或销售主观判断。
4. **字段边界清晰**：
   - customer_interest 是客户明确想要/感兴趣的内容；
   - key_concerns 是客户明确表达的担心或问题；
   - next_steps 是双方明确的、可执行的行动项，不包括社交客套。
5. **owner 填写规范**：
   - 若销售姓名已知且仅销售本人负责，owner = 销售姓名。
   - 若文本明确出现协同角色，只有该角色是行动的主要执行者/责任方时，owner = "销售姓名/角色简称"；若只是陪同、支持或提供资源，owner 仍为销售姓名，协同角色放入 resources_needed。
   - 若销售姓名未知但确需销售执行，owner = "销售"，不要写 null。
   - 协同角色简称参考：系统架构师→"架构师"，财务总监保持"财务总监"，工程部→"工程部"，测试团队→"测试团队"，研发→"研发"，售后→"售后"，法务→"法务"。
6. **时间推理**：相对时间（今天/明天/下周三/大后天/两周内）必须基于上下文提供的记录日期推算。若无法确定，设 null。
7. **风险依据风格**：
   - 对于"初步接触"或"初期接触"类记录，如果文本确实没有获得任何客户具体信息（如客户兴趣、内部流程、疑虑、竞对等），risk_reason 写"初期接触，无明确信息。"，不要写"无明确阻力"。
   - 如果文本已体现客户认可、决策链未打通、内部审批等具体内容，risk_reason 必须基于这些事实撰写，不要套用"无明确信息"模板。
8. **风险等级匹配**：risk_reason 必须与 risk_level 一致，不能低风险的记录写出高风险的依据。
9. **保留原文**：original_transcript 必须原样返回输入文本。
10. **输出格式**：仅输出 JSON，不要任何解释、markdown 代码块标记或额外文字。
11. **📊 历史连续性**：如果提供了历史拜访记录，必须考虑客户关系的变化趋势。例如：上次风险为"高"，本次客户态度缓和 → 风险应降低；上次承诺未兑现 → 在本次 key_concerns 中体现。

## 示例（40 条黄金样本）

{examples_text}

---

## 待处理记录

{_context_text(context)}

【原始记录】
{transcript}

【输出】
"""


def build_validation_prompt(transcript: str, extracted: dict, context: Optional[dict] = None) -> str:
    context_info = _context_text(context)
    return f"""你是一位数据质量审计员。你的任务是对比【原始记录】、【已知上下文】和【提取结果】，检查是否存在错误。

只检查以下三类错误：
1. 幻觉错误：提取结果中有内容在原文或上下文中找不到明确依据。
2. 关键遗漏：原文或上下文中明确提到的关键信息（公司名、时间承诺、竞对、下一步、疑虑）提取结果里没有。
3. 逻辑错误：提取结果中字段之间相互矛盾（例如风险等级低但客户威胁停测）。

已知上下文：
{context_info}

输出格式为 JSON 数组，例如：
[
  {{"error_type": "幻觉", "field": "budget", "wrong_content": "500万", "reason": "原文未提预算金额"}},
  {{"error_type": "遗漏", "field": "competitors_mentioned", "reason": "原文提到博世，但结果未出现"}},
  {{"error_type": "逻辑", "field": "risk_level", "reason": "风险等级为低，但关键疑虑包含威胁停测"}}
]

如果没有错误，输出空数组 []。

只输出 JSON，不要任何其他文字。

【原始记录】
{transcript}

【提取结果】
{json.dumps(extracted, ensure_ascii=False, indent=2)}

【输出】
"""
