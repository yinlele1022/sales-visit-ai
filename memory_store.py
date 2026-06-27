"""Memory store for the Smart Sales Agent.

This module provides persistent memory across interactions:
- Customer visit history
- Key facts learned about each customer
- Evolution of risk levels and relationship status
"""

import json
from pathlib import Path
from typing import Optional
from datetime import datetime
from collections import defaultdict


class CustomerMemory:
    """Persistent memory for a single customer."""
    
    def __init__(self, customer_name: str):
        self.customer_name = customer_name
        self.visit_history = []  # List of visit summaries
        self.key_facts = {
            "decision_maker": None,  # 决策者
            "budget_status": None,  # 预算状态
            "tech_preference": [],  # 技术偏好
            "pain_points": [],  # 痛点
            "competitors": [],  # 已知竞对
            "promised_actions": [],  # 承诺过的行动
        }
        self.risk_trend = []  # List of (date, risk_level)
        self.last_updated = None
    
    def add_visit(self, visit_data: dict, transcript: str):
        """Add a new visit record and update memory."""
        visit_summary = {
            "timestamp": datetime.now().isoformat(),
            "topic": visit_data.get("topic"),
            "key_points": {
                "interest": visit_data.get("customer_interest", []),
                "concerns": visit_data.get("key_concerns", []),
                "next_steps": visit_data.get("next_steps", []),
                "risk_level": visit_data.get("risk_level"),
                "risk_reason": visit_data.get("risk_reason"),
            },
            "transcript_snippet": transcript[:200]  # 保存前200字符
        }
        self.visit_history.append(visit_summary)
        
        # Update key facts
        if visit_data.get("key_concerns"):
            for concern in visit_data["key_concerns"]:
                if concern not in self.key_facts["pain_points"]:
                    self.key_facts["pain_points"].append(concern)
        
        if visit_data.get("competitors_mentioned"):
            for comp in visit_data["competitors_mentioned"]:
                if comp not in self.key_facts["competitors"]:
                    self.key_facts["competitors"].append(comp)
        
        # Update risk trend
        risk = visit_data.get("risk_level")
        if risk:
            self.risk_trend.append((datetime.now().isoformat(), risk))
        
        self.last_updated = datetime.now().isoformat()
    
    def get_context_for_prompt(self) -> str:
        """Generate context string to inject into LLM prompt."""
        if not self.visit_history:
            return "（该客户无历史记录）"
        
        lines = [f"【客户：{self.customer_name} 的历史拜访记录】"]
        
        # Most recent 3 visits
        recent_visits = self.visit_history[-3:]
        for i, visit in enumerate(recent_visits):
            lines.append(f"\n第 {len(self.visit_history) - 3 + i + 1} 次拜访：")
            lines.append(f"  议题：{visit['topic']}")
            lines.append(f"  客户兴趣：{', '.join(visit['key_points']['interest']) or '无'}")
            lines.append(f"  关键疑虑：{', '.join(visit['key_points']['concerns']) or '无'}")
            lines.append(f"  风险等级：{visit['key_points']['risk_level']}")
            if visit['key_points']['next_steps']:
                steps = [f"{s['action']}（{s.get('owner', '未知')}）" for s in visit['key_points']['next_steps']]
                lines.append(f"  下一步：{'；'.join(steps)}")
        
        # Key facts
        lines.append("\n【关键背景信息】")
        if self.key_facts["decision_maker"]:
            lines.append(f"- 决策者：{self.key_facts['decision_maker']}")
        if self.key_facts["pain_points"]:
            lines.append(f"- 已知痛点：{', '.join(self.key_facts['pain_points'])}")
        if self.key_facts["competitors"]:
            lines.append(f"- 提及竞对：{', '.join(self.key_facts['competitors'])}")
        
        # Risk trend
        if len(self.risk_trend) >= 2:
            lines.append("\n【风险趋势】")
            recent_risks = [r[1] for r in self.risk_trend[-3:]]
            lines.append(f"近期风险等级：{' → '.join(recent_risks)}")
        
        return "\n".join(lines)
    
    def to_dict(self) -> dict:
        return {
            "customer_name": self.customer_name,
            "visit_history": self.visit_history,
            "key_facts": self.key_facts,
            "risk_trend": self.risk_trend,
            "last_updated": self.last_updated
        }
    
    @classmethod
    def from_dict(cls, data: dict) -> "CustomerMemory":
        mem = cls(data["customer_name"])
        mem.visit_history = data.get("visit_history", [])
        mem.key_facts = data.get("key_facts", mem.key_facts)
        mem.risk_trend = data.get("risk_trend", [])
        mem.last_updated = data.get("last_updated")
        return mem


class MemoryStore:
    """Manages customer memories across all customers."""
    
    def __init__(self, storage_path: Optional[Path] = None):
        if storage_path is None:
            storage_path = Path(__file__).parent / "customer_memories.json"
        self.storage_path = storage_path
        self.memories = {}  # customer_name -> CustomerMemory
        self._load()
    
    def _load(self):
        """Load memories from disk."""
        if not self.storage_path.exists():
            return
        try:
            data = json.loads(self.storage_path.read_text(encoding="utf-8"))
            for name, mem_data in data.items():
                self.memories[name] = CustomerMemory.from_dict(mem_data)
        except Exception as e:
            print(f"[MemoryStore] Failed to load: {e}")
    
    def _save(self):
        """Save memories to disk."""
        try:
            data = {name: mem.to_dict() for name, mem in self.memories.items()}
            self.storage_path.write_text(
                json.dumps(data, ensure_ascii=False, indent=2),
                encoding="utf-8"
            )
        except Exception as e:
            print(f"[MemoryStore] Failed to save: {e}")
    
    def get_or_create(self, customer_name: str) -> CustomerMemory:
        """Get existing memory or create new one."""
        if customer_name not in self.memories:
            self.memories[customer_name] = CustomerMemory(customer_name)
        return self.memories[customer_name]
    
    def update_with_extraction(self, customer_name: str, extracted_data: dict, transcript: str):
        """Update memory with new extraction result."""
        memory = self.get_or_create(customer_name)
        memory.add_visit(extracted_data, transcript)
        self._save()
    
    def get_context(self, customer_name: str) -> str:
        """Get context string for a customer."""
        memory = self.memories.get(customer_name)
        if memory is None:
            return "（该客户无历史记录）"
        return memory.get_context_for_prompt()
    
    def get_customer_summary(self, customer_name: str) -> Optional[dict]:
        """Get a summary of customer status."""
        memory = self.memories.get(customer_name)
        if memory is None:
            return None
        return {
            "customer_name": customer_name,
            "total_visits": len(memory.visit_history),
            "last_visit": memory.visit_history[-1]["timestamp"] if memory.visit_history else None,
            "current_risk": memory.risk_trend[-1][1] if memory.risk_trend else "未知",
            "key_pain_points": memory.key_facts["pain_points"],
            "competitors": memory.key_facts["competitors"],
        }
    
    def list_all_customers(self) -> list[str]:
        """List all customers in memory."""
        return list(self.memories.keys())


if __name__ == "__main__":
    # Quick test
    store = MemoryStore()
    
    # Simulate two visits for same customer
    store.update_with_extraction(
        "滴滴",
        {
            "topic": "Mpilot Highway产品演示",
            "customer_interest": ["高速NOA功能"],
            "key_concerns": ["工程车识别精度需提升"],
            "competitors_mentioned": [],
            "next_steps": [{"action": "发送产品对比资料", "owner": "赵岩", "deadline": "2026-06-26"}],
            "risk_level": "低",
            "risk_reason": "客户兴趣积极，无明确阻力"
        },
        "客户对高速NOA功能很感兴趣，提到工程车识别精度需提升..."
    )
    
    store.update_with_extraction(
        "滴滴",
        {
            "topic": "工程版软件问题反馈",
            "customer_interest": [],
            "key_concerns": ["幽灵刹车问题", "工程版稳定性"],
            "competitors_mentioned": ["Mobileye"],
            "next_steps": [{"action": "提供排查报告", "owner": "赵岩", "deadline": "2026-06-29"}],
            "risk_level": "中",
            "risk_reason": "客户提到Mobileye作为对比，对稳定性有顾虑"
        },
        "客户反馈工程版出现幽灵刹车，提到Mobileye的方案更稳定..."
    )
    
    # Get context for next visit
    context = store.get_context("滴滴")
    print(context)
    print("\n\n=== Customer Summary ===")
    print(store.get_customer_summary("滴滴"))
