"""Feishu (Lark) notification helper for human-in-the-loop escalation.

Supports two modes:
1. Webhook mode: FEISHU_WEBHOOK_URL set in .env -> sends card message to group bot.
2. Mock mode: no webhook configured -> prints to stdout and logs to a local file.

The card is intentionally concise and action-oriented, so the reviewer can decide
within 10 seconds whether to click through.
"""

import json
import os
import urllib.request
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

load_dotenv(Path(__file__).parent / ".env")


FEISHU_WEBHOOK_URL = os.getenv("FEISHU_WEBHOOK_URL", "")
FEISHU_USER_ID = os.getenv("FEISHU_USER_ID", "")
MOCK_LOG_PATH = Path(__file__).parent / "feishu_mock_notifications.jsonl"


class FeishuNotifier:
    def __init__(self, webhook_url: str | None = None, user_id: str | None = None):
        self.webhook_url = webhook_url or FEISHU_WEBHOOK_URL
        self.user_id = user_id or FEISHU_USER_ID

    def _send_post(self, payload: dict) -> dict:
        """POST JSON to webhook using urllib (no external requests dep)."""
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        req = urllib.request.Request(
            self.webhook_url,
            data=data,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=15) as resp:
            return json.loads(resp.read().decode("utf-8"))

    def _mock_send(self, payload: dict) -> dict:
        """When no webhook is configured, append to local log and print."""
        record = {
            "webhook_url": self.webhook_url or "<not configured>",
            "payload": payload,
        }
        with open(MOCK_LOG_PATH, "a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
        print("[Feishu mock notification] No webhook configured. Logged to", MOCK_LOG_PATH)
        return {"code": 0, "msg": "mock_sent"}

    def _truncate(self, text: str, limit: int) -> str:
        if text and len(text) > limit:
            return text[: limit - 3] + "..."
        return text or ""

    def _format_field_value(self, value, max_items=5) -> str:
        """Format any field value into a human-readable string.
        Handles: str, list[str], list[dict], dict, and None."""
        if not value:
            return "无"
        if isinstance(value, str):
            return value
        if isinstance(value, (list, tuple)):
            parts = []
            for item in value[:max_items]:
                if isinstance(item, str):
                    parts.append(item)
                elif isinstance(item, dict):
                    # Extract the most meaningful text from a dict
                    action = item.get("action") or item.get("content") or item.get("text") or ""
                    if action:
                        parts.append(action)
                    else:
                        parts.append(str(item))
                else:
                    parts.append(str(item))
            return "；".join(parts) if parts else "无"
        if isinstance(value, dict):
            return value.get("action") or value.get("text") or str(value)
        return str(value)

    def _build_confirmation_card(self, extracted: dict, record_id: str, auto_confirmed: bool = False) -> dict:
        """构建确认入库的飞书互动卡片（带按钮）"""
        sales_name = self._format_field_value(extracted.get("sales_name"))
        customer = self._format_field_value(extracted.get("customer"))
        contact = self._format_field_value(extracted.get("contact_person"))
        topic = self._format_field_value(extracted.get("topic"))
        visit_time = self._format_field_value(extracted.get("visit_time"))
        risk_level = self._format_field_value(extracted.get("risk_level"))

        # 风险等级标识
        risk_tag_map = {"高": "🔴 高风险", "中": "🟡 中风险", "低": "🟢 低风险"}
        risk_tag = risk_tag_map.get(risk_level, risk_level)
        risk_emoji = {"高": "🔴", "中": "🟡", "低": "🟢"}.get(risk_level, "")

        # 格式化列表字段（自动处理dict/list/str）
        concerns = self._format_field_value(extracted.get("key_concerns"), max_items=3)

        # 生成三秒摘要
        contact_short = contact if contact != "无" else ""
        contact_part = f"·{contact_short}" if contact_short else ""
        topic_short = topic[:20] if topic != "无" else ""
        concern_summary = concerns[:30] if concerns != "无" else ""
        one_liner = f"{risk_emoji} **{customer}{contact_part}**：{topic_short}。{concern_summary}。**风险：{risk_level}**"
        # 兼容 competitors 和 competitors_mentioned 两种字段名
        competitors_raw = extracted.get("competitors_mentioned")
        if competitors_raw is None:
            competitors_raw = extracted.get("competitors")
        competitors = self._format_field_value(competitors_raw, max_items=3)
        next_steps = self._format_field_value(extracted.get("next_steps"), max_items=3)

        concerns_text = "\n".join([f"- {c}" for c in concerns.split("；") if c.strip()]) if concerns != "无" else "无"
        competitors_text = competitors if competitors != "无" else "无"
        next_steps_text = "\n".join([f"- {s}" for s in next_steps.split("；") if s.strip()]) if next_steps != "无" else "无"

        # 根据是否自动入库调整标题和底部提示
        if auto_confirmed:
            header_title = "销售拜访记录已自动入库"
            header_template = "green"
            footer_text = "高置信度自动入库。如需修改请在网页端查看所有记录。"
        else:
            header_title = "销售拜访记录已生成"
            header_template = "blue"
            footer_text = "请在网页端确认入库。"

        card = {
            "msg_type": "interactive",
            "card": {
                "config": {"wide_screen_mode": True},
                "header": {
                    "title": {"tag": "plain_text", "content": header_title},
                    "template": header_template,
                },
                "elements": [
                    {
                        "tag": "div",
                        "text": {
                            "tag": "lark_md",
                            "content": one_liner,
                        }
                    },
                    {"tag": "hr"},
                    {
                        "tag": "div",
                        "text": {
                            "tag": "lark_md",
                            "content": f"**销售：** {sales_name}　**客户：** {customer}\n"
                                       f"**联系人：** {contact}　**拜访时间：** {visit_time}\n"
                                       f"**核心议题：** {topic}"
                        }
                    },
                    {"tag": "hr"},
                    {
                        "tag": "div",
                        "text": {
                            "tag": "lark_md",
                            "content": f"**风险等级：** {risk_tag}\n"
                                       f"**关键疑虑：**\n{concerns_text}"
                        }
                    },
                    {"tag": "hr"},
                    {
                        "tag": "div",
                        "text": {
                            "tag": "lark_md",
                            "content": f"**竞对动态：** {competitors_text}"
                        }
                    },
                    {"tag": "hr"},
                    {
                        "tag": "div",
                        "text": {
                            "tag": "lark_md",
                            "content": f"**下一步行动：**\n{next_steps_text}"
                        }
                    },
                    {"tag": "hr"},
                    {
                        "tag": "div",
                        "text": {
                            "tag": "lark_md",
                            "content": f"**💡 战略洞察：**\n{self._format_field_value(extracted.get('strategic_insight'))}"
                        }
                    },
                    {"tag": "hr"},
                    {
                        "tag": "note",
                        "elements": [
                            {
                                "tag": "plain_text",
                                "content": footer_text,
                            }
                        ],
                    },
                ]
            }
        }
        return card

    def send_confirmation_card(self, extracted: dict, record_id: str, auto_confirmed: bool = False) -> dict:
        """发送确认入库的飞书互动卡片"""
        payload = self._build_confirmation_card(extracted, record_id, auto_confirmed=auto_confirmed)
        if self.webhook_url:
            try:
                return self._send_post(payload)
            except Exception as e:
                print(f"[Feishu webhook failed] {e}")
                return self._mock_send(payload)
        return self._mock_send(payload)

    def _build_card(self, transcript: str, extracted: dict, errors: list[dict], human_reason: str) -> dict:
        """Build a concise Feishu interactive card payload for human review."""
        # Core summary fields
        sales_name = extracted.get("sales_name") or "未知"
        customer = extracted.get("customer") or "未知"
        contact = extracted.get("contact_person") or "未知"
        topic = extracted.get("topic") or "未知"
        visit_time = extracted.get("visit_time") or "未知"
        risk_level = extracted.get("risk_level") or "未知"

        # Pick top risk reasons / concerns for the reviewer
        concerns = extracted.get("key_concerns", []) or []
        risk_reason = extracted.get("risk_reason") or ""
        concern_summary = "；".join(str(c) for c in concerns[:3]) or "无"

        # Format errors into a short, readable list
        error_lines = [
            f"{i + 1}. {e.get('error_type', '未知')}｜{e.get('field', '-')}｜{e.get('reason', '')}"
            for i, e in enumerate(errors[:3])
        ]
        error_text = "\n".join(error_lines) if error_lines else "（无具体错误信息）"

        mention_text = f"<at user_id=\"{self.user_id}\"></at>" if self.user_id else ""

        # Card body (Feishu interactive card spec)
        elements = [
            {
                "tag": "div",
                "text": {
                    "tag": "lark_md",
                    "content": f"**{mention_text}发现一条需要人工复核的销售记录，请尽快处理。**",
                },
            },
            {"tag": "hr"},
            {
                "tag": "div",
                "text": {
                    "tag": "lark_md",
                    "content": (
                        f"**复核原因：** {self._truncate(human_reason, 120)}\n"
                        f"**销售：** {sales_name}　**客户：** {customer}　**联系人：** {contact}\n"
                        f"**时间：** {visit_time}　**议题：** {self._truncate(topic, 60)}\n"
                        f"**风险等级：** {risk_level}"
                    ),
                },
            },
            {
                "tag": "div",
                "text": {
                    "tag": "lark_md",
                    "content": (
                        f"**关键疑虑：** {self._truncate(concern_summary, 200)}\n"
                        f"**风险原因：** {self._truncate(risk_reason, 200)}"
                    ),
                },
            },
            {"tag": "hr"},
            {
                "tag": "div",
                "text": {
                    "tag": "lark_md",
                    "content": f"**原始记录：**\n{self._truncate(transcript, 400)}",
                },
            },
            {"tag": "hr"},
            {
                "tag": "div",
                "text": {
                    "tag": "lark_md",
                    "content": f"**命中问题：**\n{error_text}",
                },
            },
            {
                "tag": "note",
                "elements": [
                    {
                        "tag": "plain_text",
                        "content": "如确认无误，请直接在 CRM 中修正并归档；如需退回，请回复“重做”。",
                    }
                ],
            },
        ]

        return {
            "msg_type": "interactive",
            "card": {
                "config": {"wide_screen_mode": True},
                "header": {
                    "title": {"tag": "plain_text", "content": "🚨 销售拜访 AI 需要人工复核"},
                    "template": "red",
                },
                "elements": elements,
            },
        }

    def send_human_review_card(
        self,
        transcript: str,
        extracted: dict,
        errors: list[dict],
        human_reason: str,
    ) -> dict:
        payload = self._build_card(transcript, extracted, errors, human_reason)
        if self.webhook_url:
            try:
                return self._send_post(payload)
            except Exception as e:
                # Fallback to mock logging so we don't lose the alert
                print(f"[Feishu webhook failed] {e}")
                return self._mock_send(payload)
        return self._mock_send(payload)

    def send_text(self, text: str) -> dict:
        payload = {"msg_type": "text", "content": {"text": text}}
        if self.webhook_url:
            try:
                return self._send_post(payload)
            except Exception as e:
                print(f"[Feishu webhook failed] {e}")
                return self._mock_send(payload)
        return self._mock_send(payload)

    def send_success_summary(self, summary: dict) -> dict:
        """Send a daily/periodic success summary card (optional)."""
        content = (
            f"**✅ 销售拜访 AI 处理完成**\n"
            f"- 总记录数：{summary.get('total', 0)}\n"
            f"- 自动通过：{summary.get('passed', 0)}\n"
            f"- 需人工复核：{summary.get('human_review', 0)}\n"
            f"- 失败：{summary.get('failed', 0)}"
        )
        payload = {
            "msg_type": "interactive",
            "card": {
                "config": {"wide_screen_mode": True},
                "header": {
                    "title": {"tag": "plain_text", "content": "销售拜访 AI 运行汇总"},
                    "template": "green",
                },
                "elements": [
                    {
                        "tag": "div",
                        "text": {"tag": "lark_md", "content": content},
                    }
                ],
            },
        }
        if self.webhook_url:
            try:
                return self._send_post(payload)
            except Exception as e:
                print(f"[Feishu webhook failed] {e}")
                return self._mock_send(payload)
        return self._mock_send(payload)


if __name__ == "__main__":
    notifier = FeishuNotifier()
    notifier.send_text("测试：销售拜访 AI 飞书通知通道正常。")
