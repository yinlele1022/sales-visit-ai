"""Robust sales-visit extraction pipeline with fallback, introspection, and guardrails."""

import json
import os
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

from openai import OpenAI
from pydantic import ValidationError

from config import API_KEYS, BASE_URLS, MODELS
from prompts import build_extraction_prompt, build_validation_prompt
from schema import SalesVisitRecord, SALES_VISIT_JSON_SCHEMA


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

FALLBACK_CHAIN = ["deepseek", "qwen"]
MAX_RETRIES = 3
RETRY_DELAY_SECONDS = 1.0
MAX_INTROSPECTION_ROUNDS = 2


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------

@dataclass
class PipelineResult:
    """Final result of the robust pipeline."""

    extracted: dict[str, Any]
    passed: bool
    provider_used: str
    fallback_used: bool
    introspection_rounds: int
    errors: list[dict] = field(default_factory=list)
    confidence: dict[str, float] = field(default_factory=dict)
    human_review_required: bool = False
    human_reason: str = ""
    original_transcript: str = ""
    elapsed_seconds: float = 0.0


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

class ExtractionError(Exception):
    """Raised when no provider can produce a valid extraction."""


class SchemaValidationError(Exception):
    """Raised when extracted output does not conform to schema."""


class HumanReviewRequiredError(Exception):
    """Raised when the pipeline cannot guarantee accuracy and needs human."""


class NoProviderAvailableError(Exception):
    """Raised when no API keys are configured."""


def _strip_markdown_code_block(text: str) -> str:
    text = text.strip()
    if text.startswith("```json"):
        text = text[7:]
    if text.startswith("```"):
        text = text[3:]
    if text.endswith("```"):
        text = text[:-3]
    return text.strip()


def _enforce_schema(raw: dict) -> dict:
    """Validate and normalize against Pydantic schema."""
    try:
        record = SalesVisitRecord(**raw)
        return record.model_dump()
    except ValidationError as e:
        raise SchemaValidationError(f"Schema validation failed: {e}") from e


def _build_client(provider: str) -> OpenAI:
    key = API_KEYS.get(provider)
    if not key:
        raise NoProviderAvailableError(f"Missing API key for provider '{provider}'")
    return OpenAI(api_key=key, base_url=BASE_URLS.get(provider))


# ---------------------------------------------------------------------------
# Retry / fallback LLM caller
# ---------------------------------------------------------------------------

def _call_with_retry(
    client: OpenAI,
    model: str,
    messages: list[dict],
    response_format: Optional[dict] = None,
    temperature: float = 0.0,
    max_retries: int = MAX_RETRIES,
) -> str:
    """Call LLM with exponential backoff. Raises last exception if all fail."""
    last_exception = None
    for attempt in range(max_retries):
        try:
            kwargs = {"model": model, "messages": messages, "temperature": temperature}
            if response_format:
                kwargs["response_format"] = response_format
            response = client.chat.completions.create(**kwargs)
            return response.choices[0].message.content
        except Exception as e:
            last_exception = e
            if attempt < max_retries - 1:
                time.sleep(RETRY_DELAY_SECONDS * (2 ** attempt))
    raise last_exception


def _extract_with_provider(
    provider: str,
    transcript: str,
    context: Optional[dict],
    strict: bool = True,
    history_context: Optional[str] = None,
) -> dict:
    """Extract using a specific provider. Raises on failure."""
    client = _build_client(provider)
    model = MODELS.get(provider)
    prompt = build_extraction_prompt(transcript, context=context, history_context=history_context)

    system_msg = "你是一位资深的销售运营分析师。严格按规则输出 JSON。"
    messages = [
        {"role": "system", "content": system_msg},
        {"role": "user", "content": prompt},
    ]

    if strict:
        try:
            response_format = {
                "type": "json_schema",
                "json_schema": {
                    "name": SALES_VISIT_JSON_SCHEMA["name"],
                    "schema": SALES_VISIT_JSON_SCHEMA["schema"],
                    "strict": True,
                },
            }
            content = _call_with_retry(client, model, messages, response_format=response_format)
        except Exception:
            # Retry with plain JSON instruction
            messages[0]["content"] = "你是一位资深的销售运营分析师。严格按规则输出 JSON，不要任何解释。"
            content = _call_with_retry(client, model, messages, response_format={"type": "json_object"})
    else:
        content = _call_with_retry(client, model, messages, response_format={"type": "json_object"})

    content = _strip_markdown_code_block(content)
    raw = json.loads(content)
    raw["original_transcript"] = transcript
    return _enforce_schema(raw)


def extract_with_fallback(
    transcript: str,
    context: Optional[dict] = None,
    providers: Optional[list[str]] = None,
    history_context: Optional[str] = None,
) -> tuple[dict, str, bool]:
    """Try providers in order; return (extracted, provider_used, fallback_used)."""
    providers = providers or FALLBACK_CHAIN
    last_error = None
    for idx, provider in enumerate(providers):
        if not API_KEYS.get(provider):
            continue
        try:
            extracted = _extract_with_provider(provider, transcript, context, strict=(idx == 0), history_context=history_context)
            return extracted, provider, idx > 0
        except Exception as e:
            last_error = e
            continue
    raise ExtractionError(f"All providers failed. Last error: {last_error}")


# ---------------------------------------------------------------------------
# Introspection / self-correction
# ---------------------------------------------------------------------------

def _introspection_prompt(transcript: str, extracted: dict, context: Optional[dict]) -> str:
    ctx_lines = []
    if context:
        if context.get("sales_name"):
            ctx_lines.append(f"- 销售姓名: {context['sales_name']}")
        if context.get("reference_date"):
            ctx_lines.append(f"- 记录日期（今天）: {context['reference_date']}")
        if context.get("visit_time"):
            ctx_lines.append(f"- 拜访时间（已知）: {context['visit_time']}")
    ctx_text = "\n".join(ctx_lines) if ctx_lines else "（无额外上下文）"

    return f"""你是一位严格的销售数据质量审计员。请对以下【提取结果】进行深度省察，检查是否有幻觉、遗漏或逻辑错误。

只检查以下问题，并输出 JSON 对象：
{{
  "passed": true/false,
  "confidence_score": 0.0-1.0,
  "field_confidence": {{
    "sales_name": 0.0-1.0,
    "customer": 0.0-1.0,
    "contact_person": 0.0-1.0,
    "visit_time": 0.0-1.0,
    "topic": 0.0-1.0,
    "customer_interest": 0.0-1.0,
    "key_concerns": 0.0-1.0,
    "competitors_mentioned": 0.0-1.0,
    "next_steps": 0.0-1.0,
    "risk_level": 0.0-1.0,
    "risk_reason": 0.0-1.0,
    "resources_needed": 0.0-1.0,
    "relationship_suggestion": 0.0-1.0,
    "strategic_insight": 0.0-1.0
  }},
  "errors": [
    {{"error_type": "幻觉|遗漏|逻辑", "field": "字段名", "reason": "具体原因"}}
  ],
  "suggested_fix": "如果需要修正，给出修正后的整个 JSON；如果不需要，写 null"
}}

判断标准（严格但合理）：
1. 幻觉：提取结果中有内容在原文或上下文中找不到明确依据。例如：原文未提预算但出现预算数字；原文未提竞对但出现竞对名称。这是唯一必须拦截的错误类型。
2. 遗漏：原文或上下文中明确提到的关键信息（公司名、联系人、时间承诺、竞对、下一步、客户疑虑）提取结果里没有。注意：泛泛的兴趣（如"有点兴趣"）不算明确的 customer_interest；未承诺具体行动则 next_steps 为空是合理的；文本稀疏时大量字段未知也是合理的，不算遗漏。
3. 逻辑错误：字段之间存在明显矛盾。例如：风险等级为低但关键疑虑包含"停测""罚款"等严重信号；风险等级为高但无关键疑虑和竞对。风险等级本身是主观判断，只要文本支持即可，不要仅因个人判断不同而标错。
4. 置信度：0 表示完全不确定，1 表示完全确定。如果原文信息模糊或缺失，对应字段置信度应较低；如果原文明确，置信度应较高。

已知上下文：
{ctx_text}

【原始记录】
{transcript}

【提取结果】
{json.dumps(extracted, ensure_ascii=False, indent=2)}

只输出 JSON 对象，不要其他文字。"""


def _introspect(
    provider: str,
    transcript: str,
    extracted: dict,
    context: Optional[dict],
) -> dict:
    """Run LLM introspection and return structured audit result."""
    client = _build_client(provider)
    model = MODELS.get(provider)
    prompt = _introspection_prompt(transcript, extracted, context)
    content = _call_with_retry(
        client,
        model,
        [
            {"role": "system", "content": "你是一位严格的数据质量审计员。只输出 JSON 对象。"},
            {"role": "user", "content": prompt},
        ],
        response_format={"type": "json_object"},
    )
    content = _strip_markdown_code_block(content)
    try:
        return json.loads(content)
    except json.JSONDecodeError:
        return {
            "passed": False,
            "confidence_score": 0.0,
            "field_confidence": {},
            "errors": [{"error_type": "解析错误", "field": "introspection", "reason": "LLM 省察输出非 JSON"}],
            "suggested_fix": None,
        }


def _apply_fix(extracted: dict, suggested_fix: Any) -> dict:
    """Apply suggested fix if it is a valid schema-compliant dict."""
    if not suggested_fix or not isinstance(suggested_fix, dict):
        return extracted
    try:
        # Preserve original transcript and any missing required fields
        merged = extracted.copy()
        merged.update(suggested_fix)
        merged["original_transcript"] = extracted.get("original_transcript", "")
        fixed = _enforce_schema(merged)
        return fixed
    except SchemaValidationError:
        return extracted


def introspection_loop(
    transcript: str,
    extracted: dict,
    context: Optional[dict],
    provider: str,
    max_rounds: int = MAX_INTROSPECTION_ROUNDS,
) -> tuple[dict, int, dict, list[dict]]:
    """Run self-correction rounds. Return (final_extracted, rounds, confidence, audit_errors)."""
    current = extracted
    final_audit = {"passed": True, "field_confidence": {}, "errors": []}
    for round_idx in range(max_rounds):
        audit = _introspect(provider, transcript, current, context)
        final_audit = audit
        if audit.get("passed"):
            return current, round_idx, audit.get("field_confidence", {}), []
        suggested_fix = audit.get("suggested_fix")
        if suggested_fix and suggested_fix is not None:
            current = _apply_fix(current, suggested_fix)
        else:
            break
    audit_errors = final_audit.get("errors", []) or []
    return current, max_rounds, final_audit.get("field_confidence", {}), audit_errors


# ---------------------------------------------------------------------------
# Business rule guardrails
# ---------------------------------------------------------------------------

def business_rule_check(extracted: dict) -> list[dict]:
    """Apply deterministic business rules that LLM may violate."""
    errors = []
    risk = extracted.get("risk_level")
    concerns = extracted.get("key_concerns", []) or []
    competitors = extracted.get("competitors_mentioned", []) or []
    next_steps = extracted.get("next_steps", []) or []

    # Risk consistency
    if risk == "低":
        if len(concerns) >= 3:
            errors.append({"error_type": "逻辑", "field": "risk_level", "reason": "风险等级为低但关键疑虑过多"})
        if any(k in str(c) for c in concerns for k in ["停测", "罚款", "淘汰", "退货", "投诉"]):
            errors.append({"error_type": "逻辑", "field": "risk_level", "reason": "风险等级为低但关键疑虑包含严重信号"})
    elif risk == "高":
        if not concerns and not competitors:
            errors.append({"error_type": "逻辑", "field": "risk_level", "reason": "风险等级为高但无关键疑虑和竞对"})

    # Next steps must be actionable
    for step in next_steps:
        if not isinstance(step, dict):
            continue
        action = str(step.get("action", "")).strip()
        if not action:
            errors.append({"error_type": "格式", "field": "next_steps.action", "reason": "action 为空"})
        elif len(action) < 5:
            errors.append({"error_type": "格式", "field": "next_steps.action", "reason": f"action 过短，缺乏可执行性: {action}"})

    return errors


# ---------------------------------------------------------------------------
# Generalization / OOD guards
# ---------------------------------------------------------------------------

def _load_golden_cases() -> list[dict]:
    path = Path(__file__).parent / "golden_cases.json"
    if not path.exists():
        return []
    return json.loads(path.read_text(encoding="utf-8"))


def _jaccard_similarity(a: set, b: set) -> float:
    if not a and not b:
        return 1.0
    union = a | b
    if not union:
        return 1.0
    return len(a & b) / len(union)


def generalization_guard(transcript: str, extracted: dict) -> list[dict]:
    """Detect out-of-distribution or overfitting signals."""
    errors = []
    cases = _load_golden_cases()

    # 1. Transcript too short / empty
    if len(transcript.strip()) < 25:
        errors.append({"error_type": "泛化", "field": "input", "reason": "输入文本过短（<25字），无法提取有效信息"})

    # 2. Output too similar to any single golden example (overfitting indicator)
    extracted_json = json.dumps(extracted, ensure_ascii=False, sort_keys=True)
    extracted_tokens = set(extracted_json.split())
    similarities = []
    for case in cases:
        gold_json = json.dumps(case.get("gold", {}), ensure_ascii=False, sort_keys=True)
        gold_tokens = set(gold_json.split())
        similarities.append(_jaccard_similarity(extracted_tokens, gold_tokens))
    if similarities and max(similarities) > 0.98:
        errors.append({"error_type": "泛化", "field": "output", "reason": "输出与某条黄金样本几乎完全一致，可能存在过拟合，需人工复核"})

    # Note: 文本稀疏导致大量字段未知是正常情况，不应视为 OOD。

    return errors


# ---------------------------------------------------------------------------
# Main robust pipeline
# ---------------------------------------------------------------------------

def process_with_guardrails(
    transcript: str,
    context: Optional[dict] = None,
    providers: Optional[list[str]] = None,
    require_human_threshold: float = 0.3,
    enable_introspection: bool = True,
    enable_feishu: bool = True,
    logger: Optional[Any] = None,
    history_context: Optional[str] = None,  # NEW: 历史记忆上下文
) -> PipelineResult:
    """Run the full robust pipeline and optionally log to an audit logger."""
    start_time = time.perf_counter()
    result = PipelineResult(
        extracted={},
        passed=False,
        provider_used="",
        fallback_used=False,
        introspection_rounds=0,
        errors=[],
        confidence={},
        human_review_required=False,
        human_reason="",
        original_transcript=transcript,
    )
    feishu_status = "未发送"

    try:
        # Step 1: Extract with fallback (with history context support)
        if history_context:
            # Inject history into context for prompt building
            if context is None:
                context = {}
            context["history_context"] = history_context
        
        extracted, provider, fallback_used = extract_with_fallback(transcript, context, providers, history_context=history_context)
        result.extracted = extracted
        result.provider_used = provider
        result.fallback_used = fallback_used
    except Exception as e:
        result.human_review_required = True
        result.human_reason = f"所有模型提取失败: {e}"
        result.errors.append({"error_type": "提取失败", "field": "pipeline", "reason": str(e)})
        if enable_feishu:
            feishu_status = _notify_feishu(transcript, {}, result.errors, result.human_reason)
        result.elapsed_seconds = round(time.perf_counter() - start_time, 3)
        if logger:
            _log_result(logger, transcript, context, result, feishu_status)
        return result

    # Step 2: LLM introspection / self-correction (informational, not blocking)
    if enable_introspection:
        try:
            extracted, rounds, confidence, audit_errors = introspection_loop(
                transcript, extracted, context, provider
            )
            result.extracted = extracted
            result.introspection_rounds = rounds
            result.confidence = confidence
            if audit_errors:
                result.extracted["_introspection_warnings"] = audit_errors
        except Exception as e:
            result.errors.append({"error_type": "省察失败", "field": "introspection", "reason": str(e)})

    # Step 3: Business rule guard
    biz_errors = business_rule_check(extracted)
    result.errors.extend(biz_errors)

    # Step 4: Generalization guard
    gen_errors = generalization_guard(transcript, extracted)
    result.errors.extend(gen_errors)

    # Step 5: Confidence threshold (informational only, not a hard blocker)
    if result.confidence:
        result.extracted["_confidence"] = result.confidence

    # Step 6: Final gate
    if result.errors:
        result.human_review_required = True
        result.human_reason = "; ".join(e.get("reason", "") for e in result.errors[:3])
        if enable_feishu:
            feishu_status = _notify_feishu(transcript, extracted, result.errors, result.human_reason)
    else:
        result.passed = True
        feishu_status = "无需通知"

    result.elapsed_seconds = round(time.perf_counter() - start_time, 3)
    if logger:
        _log_result(logger, transcript, context, result, feishu_status)

    return result


def _log_result(
    logger: Any,
    transcript: str,
    context: Optional[dict],
    result: PipelineResult,
    feishu_status: str,
) -> None:
    """Write a single pipeline result to the audit logger."""
    try:
        from audit_logger import AuditEntry

        entry = AuditEntry(
            transcript=transcript,
            context=context or {},
            extracted=result.extracted,
            provider_used=result.provider_used,
            fallback_used=result.fallback_used,
            passed=result.passed,
            human_review_required=result.human_review_required,
            human_reason=result.human_reason,
            errors=result.errors,
            introspection_rounds=result.introspection_rounds,
            confidence=result.confidence,
            elapsed_seconds=result.elapsed_seconds,
            feishu_status=feishu_status,
        )
        logger.log(entry)
    except Exception as e:
        # Logging must never break the pipeline
        print(f"[Audit logger failed] {e}")


# ---------------------------------------------------------------------------
# Feishu notification (imported from feishu_notifier)
# ---------------------------------------------------------------------------

def _notify_feishu(
    transcript: str,
    extracted: dict,
    errors: list[dict],
    human_reason: str,
) -> str:
    """Lazy import to avoid circular dependency / missing config. Returns status string."""
    try:
        from feishu_notifier import FeishuNotifier

        notifier = FeishuNotifier()
        resp = notifier.send_human_review_card(transcript, extracted, errors, human_reason)
        if resp.get("code") == 0:
            if notifier.webhook_url:
                return "飞书卡片已发送"
            return "mock已记录"
        return f"发送失败: {resp}"
    except Exception as e:
        # Never crash the pipeline because of notification failure
        print(f"[Feishu notification skipped] {e}")
        return f"通知异常: {e}"
