"""Four-dimensional audit evaluator for sales visit extraction."""

import json
import re
from typing import Any

from openai import OpenAI

from config import get_api_key, get_base_url, get_model
from schema import SalesVisitRecord


# Fields that are factual and should be matched exactly
FACTUAL_STRING_FIELDS = ["sales_name", "customer", "contact_person", "visit_time"]
# Fields that are subjective and should be matched semantically
SUBJECTIVE_STRING_FIELDS = ["topic"]
# Free text fields that are always compared semantically
SEMANTIC_TEXT_FIELDS = ["risk_reason", "relationship_suggestion"]


def _normalize(text: str) -> str:
    return re.sub(r"\s+", "", str(text).strip().lower())


def _is_unknown(value: Any) -> bool:
    """Treat '未知' and null/empty as equivalent missing values."""
    if value is None:
        return True
    text = str(value).strip()
    return text == "" or text == "未知"


def _unknown_to_empty(value: Any) -> str:
    if _is_unknown(value):
        return ""
    return str(value).strip()


def _exact_match(a: str, b: str) -> bool:
    return _normalize(a) == _normalize(b)


class SemanticComparer:
    """Use LLM to judge semantic equivalence of subjective fields in one batch call."""

    def __init__(self):
        self.client = OpenAI(api_key=get_api_key(), base_url=get_base_url())

    def compare_all(self, pred: dict, gold: dict) -> dict[str, bool]:
        """Batch compare all subjective field pairs. Returns {label: is_equivalent}."""
        comparisons = []
        labels = []

        def add_pair(p_text, g_text, label):
            p_text = str(p_text).strip()
            g_text = str(g_text).strip()
            if not g_text:
                return
            if _exact_match(p_text, g_text):
                return
            comparisons.append((p_text, g_text))
            labels.append(label)

        # Subjective string fields
        for field in SUBJECTIVE_STRING_FIELDS:
            add_pair(pred.get(field, ""), gold.get(field, ""), field)

        # Semantic text fields
        for field in SEMANTIC_TEXT_FIELDS:
            add_pair(pred.get(field, ""), gold.get(field, ""), field)

        # List fields: compare each item with each item (find best match)
        subjective_list_fields = {
            "customer_interest": "interest",
            "key_concerns": "concern",
            "resources_needed": "resource",
        }
        list_pair_labels = {}  # label -> (pred_idx, gold_idx, field)
        for field, item_name in subjective_list_fields.items():
            p_items = [str(x).strip() for x in pred.get(field, []) if str(x).strip() and str(x).strip() != "未知"]
            g_items = [str(x).strip() for x in gold.get(field, []) if str(x).strip() and str(x).strip() != "未知"]
            for i, p_item in enumerate(p_items):
                for j, g_item in enumerate(g_items):
                    label = f"{field}[{i}]_vs_gold[{j}]"
                    add_pair(p_item, g_item, label)
                    list_pair_labels[label] = (i, j, field)

        # Next steps actions
        p_steps = pred.get("next_steps", [])
        g_steps = gold.get("next_steps", [])
        for i, ps in enumerate(p_steps):
            if not isinstance(ps, dict):
                continue
            p_action = str(ps.get("action", "")).strip()
            for j, gs in enumerate(g_steps):
                if not isinstance(gs, dict):
                    continue
                g_action = str(gs.get("action", "")).strip()
                label = f"next_steps[{i}].action[{j}]"
                add_pair(p_action, g_action, label)

        if not comparisons:
            return {}

        # Build batch prompt
        lines = [
            "判断以下每一对文本是否语义等价。标准：只要它们描述的是同一类事物、同一项行动、同一个关注点、同一段含义，或表达相同的风险判断/原因/建议，即使措辞不同，也算等价。对风险原因（risk_reason）类文本尤其宽松：只要核心风险判断一致（如都是因为预算不确定、客户认可但内部未决策、无明显风险等），即算等价。只回答每行的“是”或“否”。\n"
        ]
        for idx, (p_text, g_text) in enumerate(comparisons, 1):
            lines.append(f"{idx}. A: {p_text}")
            lines.append(f"   B: {g_text}")
            lines.append("")
        lines.append("输出格式为 JSON 对象，例如：{\"1\": \"是\", \"2\": \"否\"}")

        prompt = "\n".join(lines)
        try:
            response = self.client.chat.completions.create(
                model=get_model(),
                messages=[
                    {"role": "system", "content": "你是一个严格的语义对比助手。只输出 JSON 对象。"},
                    {"role": "user", "content": prompt},
                ],
                temperature=0.0,
                response_format={"type": "json_object"},
            )
            content = response.choices[0].message.content
            parsed = json.loads(content)
            results = {}
            for idx, label in enumerate(labels, 1):
                key = str(idx)
                answer = str(parsed.get(key, "")).strip()
                results[label] = answer == "是"
            return results
        except Exception:
            return {label: False for label in labels}


def _schema_compliance(pred: dict) -> tuple[bool, list[str]]:
    """Check if prediction matches the Pydantic schema."""
    errors = []
    try:
        SalesVisitRecord(**pred)
    except Exception as e:
        errors.append(f"Schema validation error: {e}")
    return len(errors) == 0, errors


def _string_match(pred: Any, gold: Any, semantic_results: dict, field: str) -> bool:
    """Compare a string field, treating unknown as empty and using semantic for subjective fields."""
    p_text = _unknown_to_empty(pred)
    g_text = _unknown_to_empty(gold)
    if not p_text and not g_text:
        return True
    if not p_text or not g_text:
        return False
    if field in FACTUAL_STRING_FIELDS:
        return _exact_match(p_text, g_text)
    if field in SUBJECTIVE_STRING_FIELDS:
        return semantic_results.get(field, False) or _exact_match(p_text, g_text) or _substring_match(p_text, g_text)
    return _exact_match(p_text, g_text)


def _list_match(pred: list, gold: list, semantic_results: dict, field: str) -> tuple[set, set, set]:
    """Return (extra, missing, matched) items. Uses semantic matching for subjective list fields."""
    p_items = [str(x).strip() for x in pred if str(x).strip() and str(x).strip() != "未知"]
    g_items = [str(x).strip() for x in gold if str(x).strip() and str(x).strip() != "未知"]

    if field in ["competitors_mentioned"]:
        # Factual list: exact match
        extra = set(p_items) - set(g_items)
        missing = set(g_items) - set(p_items)
        return extra, missing, set()

    # Subjective list: semantic match + deterministic fallback
    matched_p = set()
    matched_g = set()
    for i, p_item in enumerate(p_items):
        for j, g_item in enumerate(g_items):
            label = f"{field}[{i}]_vs_gold[{j}]"
            semantic_match = semantic_results.get(label, False)
            deterministic_match = _exact_match(p_item, g_item) or _substring_match(p_item, g_item)
            if semantic_match or deterministic_match:
                matched_p.add(p_item)
                matched_g.add(g_item)
    extra = set(p_items) - matched_p
    missing = set(g_items) - matched_g
    return extra, missing, matched_p


def _substring_match(a: str, b: str) -> bool:
    a = _normalize(a)
    b = _normalize(b)
    if not a or not b:
        return a == b
    return a == b or a in b or b in a


def _factual_consistency(pred: dict, gold: dict, semantic_results: dict) -> tuple[bool, list[str]]:
    """Check for hallucinations."""
    errors = []

    # String fields
    all_string_fields = FACTUAL_STRING_FIELDS + SUBJECTIVE_STRING_FIELDS
    for field in all_string_fields:
        p_text = _unknown_to_empty(pred.get(field))
        g_text = _unknown_to_empty(gold.get(field))
        if p_text:
            if not g_text:
                errors.append(f"幻觉: {field} 在 gold 中为空/未知，但模型输出 '{pred.get(field)}'")
            elif not _string_match(pred.get(field), gold.get(field), semantic_results, field):
                errors.append(f"不一致: {field} 模型输出 '{pred.get(field)}' vs 金标 '{gold.get(field)}'")

    # List fields
    list_fields = ["customer_interest", "key_concerns", "competitors_mentioned", "resources_needed"]
    for field in list_fields:
        extra, _, _ = _list_match(pred.get(field, []), gold.get(field, []), semantic_results, field)
        if extra:
            errors.append(f"幻觉: {field} 包含金标外项目 {extra}")

    # Next steps
    p_steps = pred.get("next_steps", [])
    g_steps = gold.get("next_steps", [])
    for i, ps in enumerate(p_steps):
        if not isinstance(ps, dict):
            continue
        p_action = str(ps.get("action", "")).strip()
        p_owner = str(ps.get("owner", "")).strip()
        p_deadline = str(ps.get("deadline", "")).strip()
        found = False
        for j, gs in enumerate(g_steps):
            if not isinstance(gs, dict):
                continue
            g_action = str(gs.get("action", "")).strip()
            g_owner = str(gs.get("owner", "")).strip()
            g_deadline = str(gs.get("deadline", "")).strip()
            label = f"next_steps[{i}].action[{j}]"
            action_match = semantic_results.get(label, False) or _exact_match(p_action, g_action) or _substring_match(p_action, g_action)
            owner_match = _exact_match(p_owner, g_owner) or (_is_unknown(p_owner) and _is_unknown(g_owner))
            deadline_match = _exact_match(p_deadline, g_deadline) or (_is_unknown(p_deadline) and _is_unknown(g_deadline))
            if action_match and owner_match and deadline_match:
                found = True
                break
        if not found:
            errors.append(f"幻觉: next_steps 包含金标外步骤 {{('{p_action}', '{p_owner}', '{p_deadline}')}}")

    return len(errors) == 0, errors


def _coverage(pred: dict, gold: dict, semantic_results: dict) -> tuple[bool, list[str]]:
    """Check for omissions."""
    errors = []

    # String fields
    all_string_fields = FACTUAL_STRING_FIELDS + SUBJECTIVE_STRING_FIELDS
    for field in all_string_fields:
        p_text = _unknown_to_empty(pred.get(field))
        g_text = _unknown_to_empty(gold.get(field))
        if g_text:
            if not p_text:
                errors.append(f"遗漏: {field} 金标为 '{gold.get(field)}'，但模型输出为空/未知")
            elif not _string_match(pred.get(field), gold.get(field), semantic_results, field):
                errors.append(f"不一致: {field} 金标 '{gold.get(field)}' vs 模型输出 '{pred.get(field)}'")

    # List fields
    list_fields = ["customer_interest", "key_concerns", "competitors_mentioned", "resources_needed"]
    for field in list_fields:
        _, missing, _ = _list_match(pred.get(field, []), gold.get(field, []), semantic_results, field)
        if missing:
            errors.append(f"遗漏: {field} 缺少金标项目 {missing}")

    # Next steps
    p_steps = pred.get("next_steps", [])
    g_steps = gold.get("next_steps", [])
    for j, gs in enumerate(g_steps):
        if not isinstance(gs, dict):
            continue
        g_action = str(gs.get("action", "")).strip()
        g_owner = str(gs.get("owner", "")).strip()
        g_deadline = str(gs.get("deadline", "")).strip()
        found = False
        for i, ps in enumerate(p_steps):
            if not isinstance(ps, dict):
                continue
            p_action = str(ps.get("action", "")).strip()
            p_owner = str(ps.get("owner", "")).strip()
            p_deadline = str(ps.get("deadline", "")).strip()
            label = f"next_steps[{i}].action[{j}]"
            action_match = semantic_results.get(label, False) or _exact_match(p_action, g_action) or _substring_match(p_action, g_action)
            owner_match = _exact_match(p_owner, g_owner) or (_is_unknown(p_owner) and _is_unknown(g_owner))
            deadline_match = _exact_match(p_deadline, g_deadline) or (_is_unknown(p_deadline) and _is_unknown(g_deadline))
            if action_match and owner_match and deadline_match:
                found = True
                break
        if not found:
            errors.append(f"遗漏: next_steps 缺少金标步骤 {{('{g_action}', '{g_owner}', '{g_deadline}')}}")

    return len(errors) == 0, errors


def _logical_consistency(pred: dict) -> tuple[bool, list[str]]:
    """Check internal logic."""
    errors = []
    risk = pred.get("risk_level")
    concerns = pred.get("key_concerns", [])
    competitors = pred.get("competitors_mentioned", [])

    if risk == "低":
        if len(concerns) >= 3 or any("停测" in c or "罚款" in c or "淘汰" in c for c in concerns):
            errors.append("逻辑: 风险等级为低，但关键疑虑包含严重问题")
        if len(competitors) >= 2 and any("强" in c for c in concerns):
            errors.append("逻辑: 风险等级为低，但多竞对且有压力")
    elif risk == "高":
        if not concerns and not competitors:
            errors.append("逻辑: 风险等级为高，但无关键疑虑和竞对")
    return len(errors) == 0, errors


class FourDimEvaluator:
    def __init__(self, use_semantic: bool = True):
        self.use_semantic = use_semantic
        self.semantic = SemanticComparer() if use_semantic else None

    def evaluate(self, pred: dict, gold: dict) -> dict:
        # Pre-compute semantic equivalence for all subjective fields and list items
        semantic_results = {}
        if self.use_semantic and self.semantic:
            semantic_results = self.semantic.compare_all(pred, gold)

        # Schema compliance
        schema_ok, schema_errors = _schema_compliance(pred)

        # Factual consistency
        factual_ok, factual_errors = _factual_consistency(pred, gold, semantic_results)

        # Coverage
        coverage_ok, coverage_errors = _coverage(pred, gold, semantic_results)

        # Logical consistency
        logical_ok, logical_errors = _logical_consistency(pred)

        # Semantic errors summary for report
        semantic_errors = []
        for field in SUBJECTIVE_STRING_FIELDS + SEMANTIC_TEXT_FIELDS:
            if field in semantic_results and not semantic_results[field]:
                # For topic, tolerate if deterministic substring/exact match holds
                if field in SUBJECTIVE_STRING_FIELDS:
                    p_text = _unknown_to_empty(pred.get(field))
                    g_text = _unknown_to_empty(gold.get(field))
                    if _substring_match(p_text, g_text):
                        continue
                semantic_errors.append(f"语义: {field} 与金标不等价")

        all_errors = schema_errors + factual_errors + coverage_errors + logical_errors + semantic_errors

        return {
            "schema_compliance": {"passed": schema_ok, "errors": schema_errors},
            "factual_consistency": {"passed": factual_ok, "errors": factual_errors},
            "coverage": {"passed": coverage_ok, "errors": coverage_errors},
            "logical_consistency": {"passed": logical_ok, "errors": logical_errors},
            "semantic_equivalence": {"passed": len(semantic_errors) == 0, "errors": semantic_errors},
            "semantic_details": semantic_results,
            "passed": len(all_errors) == 0,
            "errors": all_errors,
        }


if __name__ == "__main__":
    pred = {
        "sales_name": "陈敏",
        "customer": "比亚迪",
        "contact_person": "张经理",
        "visit_time": "2026-06-22 14:00",
        "topic": "Mpilot Highway 方案介绍",
        "customer_interest": ["Mpilot Highway 方案"],
        "key_concerns": [],
        "competitors_mentioned": [],
        "next_steps": [],
        "risk_level": "低",
        "risk_reason": "初次接触，客户尚未表达明确兴趣或疑虑。",
        "resources_needed": [],
        "relationship_suggestion": "定期跟进，等待客户反馈。",
        "original_transcript": "...",
    }
    gold = pred.copy()
    evaluator = FourDimEvaluator(use_semantic=False)
    print(evaluator.evaluate(pred, gold))
