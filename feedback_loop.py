"""Feedback loop for continuous learning from human corrections.

This module enables the agent to learn from human corrections:
- Records corrections made by humans
- Identifies patterns in corrections
- Automatically updates few-shot examples
- Improves prompts over time
"""

import json
from pathlib import Path
from typing import Optional, Callable
from datetime import datetime
from collections import defaultdict


class CorrectionRecord:
    """A single human correction on an extraction result."""
    
    def __init__(
        self,
        transcript: str,
        original_extraction: dict,
        corrected_extraction: dict,
        correction_reason: str,
        corrected_by: str = "human",
        timestamp: Optional[str] = None
    ):
        self.transcript = transcript
        self.original_extraction = original_extraction
        self.corrected_extraction = corrected_extraction
        self.correction_reason = correction_reason
        self.corrected_by = corrected_by
        self.timestamp = timestamp or datetime.now().isoformat()
    
    def get_field_diffs(self) -> dict[str, tuple]:
        """Get fields that were corrected."""
        diffs = {}
        orig = self.original_extraction
        corr = self.corrected_extraction
        
        for key in corr:
            if key == "original_transcript":
                continue
            if key not in orig or orig[key] != corr[key]:
                diffs[key] = (orig.get(key), corr[key])
        
        return diffs
    
    def to_dict(self) -> dict:
        return {
            "transcript": self.transcript,
            "original_extraction": self.original_extraction,
            "corrected_extraction": self.corrected_extraction,
            "correction_reason": self.correction_reason,
            "corrected_by": self.corrected_by,
            "timestamp": self.timestamp,
            "field_diffs": {k: [str(v[0]), str(v[1])] for k, v in self.get_field_diffs().items()}
        }
    
    @classmethod
    def from_dict(cls, data: dict) -> "CorrectionRecord":
        return cls(
            transcript=data["transcript"],
            original_extraction=data["original_extraction"],
            corrected_extraction=data["corrected_extraction"],
            correction_reason=data["correction_reason"],
            corrected_by=data.get("corrected_by", "human"),
            timestamp=data.get("timestamp")
        )


class FeedbackStore:
    """Stores and manages correction records."""
    
    def __init__(self, storage_path: Optional[Path] = None):
        if storage_path is None:
            storage_path = Path(__file__).parent / "feedback_corrections.json"
        self.storage_path = storage_path
        self.corrections: list[CorrectionRecord] = []
        self._load()
    
    def _load(self):
        """Load corrections from disk."""
        if not self.storage_path.exists():
            return
        try:
            data = json.loads(self.storage_path.read_text(encoding="utf-8"))
            for item in data:
                self.corrections.append(CorrectionRecord.from_dict(item))
        except Exception as e:
            print(f"[FeedbackStore] Failed to load: {e}")
    
    def _save(self):
        """Save corrections to disk."""
        try:
            data = [c.to_dict() for c in self.corrections]
            self.storage_path.write_text(
                json.dumps(data, ensure_ascii=False, indent=2),
                encoding="utf-8"
            )
        except Exception as e:
            print(f"[FeedbackStore] Failed to save: {e}")
    
    def add_correction(self, record: CorrectionRecord):
        """Add a new correction record."""
        self.corrections.append(record)
        self._save()
    
    def get_corrections_for_field(self, field_name: str) -> list[CorrectionRecord]:
        """Get all corrections that involve a specific field."""
        return [
            c for c in self.corrections
            if field_name in c.get_field_diffs()
        ]
    
    def get_correction_patterns(self) -> dict[str, int]:
        """Identify common correction patterns."""
        patterns = defaultdict(int)
        for record in self.corrections:
            diffs = record.get_field_diffs()
            for field in diffs:
                patterns[field] += 1
        return dict(patterns)
    
    def should_update_few_shots(self, min_corrections: int = 3) -> bool:
        """Check if we have enough corrections to update few-shot examples."""
        patterns = self.get_correction_patterns()
        return any(count >= min_corrections for count in patterns.values())
    
    def export_learned_examples(self, max_examples: int = 5) -> list[dict]:
        """Export best correction examples for few-shot injection."""
        # Sort by recency and completeness
        sorted_corrections = sorted(
            self.corrections,
            key=lambda c: c.timestamp,
            reverse=True
        )
        
        examples = []
        for correction in sorted_corrections[:max_examples]:
            diffs = correction.get_field_diffs()
            if diffs:  # Only include meaningful corrections
                examples.append({
                    "transcript": correction.transcript,
                    "corrected_fields": {k: v[1] for k, v in diffs.items()},
                    "reason": correction.correction_reason
                })
        
        return examples


class LearningEngine:
    """Engine that learns from corrections and improves the agent."""
    
    def __init__(self, feedback_store: FeedbackStore):
        self.feedback_store = feedback_store
        self.improvement_callbacks: list[Callable] = []
    
    def register_improvement_callback(self, callback: Callable[[list[dict]], None]):
        """Register a callback to be called when improvements are ready."""
        self.improvement_callbacks.append(callback)
    
    def process_new_correction(self, record: CorrectionRecord) -> dict[str, any]:
        """Process a new correction and determine if improvements are needed."""
        self.feedback_store.add_correction(record)
        
        # Check if we should update few-shot examples
        should_update = self.feedback_store.should_update_few_shots()
        
        result = {
            "correction_recorded": True,
            "total_corrections": len(self.feedback_store.corrections),
            "should_update_few_shots": should_update,
            "patterns": self.feedback_store.get_correction_patterns()
        }
        
        # If we have enough data, trigger improvement
        if should_update:
            learned_examples = self.feedback_store.export_learned_examples()
            result["learned_examples"] = learned_examples
            
            # Call improvement callbacks
            for callback in self.improvement_callbacks:
                try:
                    callback(learned_examples)
                except Exception as e:
                    print(f"[LearningEngine] Callback failed: {e}")
        
        return result
    
    def get_learning_summary(self) -> str:
        """Generate a human-readable learning summary."""
        if not self.feedback_store.corrections:
            return "暂无学习记录。"
        
        patterns = self.feedback_store.get_correction_patterns()
        total = len(self.feedback_store.corrections)
        
        lines = [
            f"📊 学习总结",
            f"=" * 40,
            f"总修正次数：{total}",
            f"\n常见修正字段："
        ]
        
        for field, count in sorted(patterns.items(), key=lambda x: x[1], reverse=True):
            lines.append(f"  - {field}: {count} 次")
        
        lines.append(f"\n💡 系统已从 {total} 次修正中学习，")
        lines.append(f"   并将在下次提取时自动应用改进。")
        
        return "\n".join(lines)


def create_correction_interface(
    transcript: str,
    original_result: dict,
    feedback_store: FeedbackStore
) -> CorrectionRecord:
    """
    Interactive interface for humans to correct extraction results.
    In production, this would be a UI; here it's a template for integration.
    """
    print("\n" + "=" * 60)
    print("🔧 人工修正界面")
    print("=" * 60)
    print(f"\n【原始记录】\n{transcript[:200]}...")
    print(f"\n【当前提取结果】")
    for key, value in original_result.items():
        if key != "original_transcript":
            print(f"  {key}: {value}")
    
    print("\n" + "-" * 40)
    print("请修正以上结果（输入字段名和新值，输入 'done' 结束）：")
    
    corrected = original_result.copy()
    while True:
        field = input("字段名（或 'done'）: ").strip()
        if field.lower() == 'done':
            break
        if field not in original_result:
            print(f"  字段 '{field}' 不存在，跳过。")
            continue
        new_value = input(f"新值 for '{field}': ").strip()
        corrected[field] = new_value
    
    reason = input("\n修正原因: ").strip()
    
    record = CorrectionRecord(
        transcript=transcript,
        original_extraction=original_result,
        corrected_extraction=corrected,
        correction_reason=reason
    )
    
    engine = LearningEngine(feedback_store)
    result = engine.process_new_correction(record)
    
    print(f"\n✅ 修正已记录！")
    print(f"   总修正次数：{result['total_corrections']}")
    if result['should_update_few_shots']:
        print(f"   💡 系统已学习新示例，将改进未来提取。")
    
    return record


if __name__ == "__main__":
    # Demo: simulate a correction and learning
    store = FeedbackStore()
    engine = LearningEngine(store)
    
    # Simulate a correction
    correction = CorrectionRecord(
        transcript="客户对产品很感兴趣，但提到价格比竞对高20%...",
        original_extraction={
            "risk_level": "低",
            "risk_reason": "客户兴趣积极"
        },
        corrected_extraction={
            "risk_level": "中",
            "risk_reason": "客户提到价格比竞对高，可能有预算顾虑"
        },
        correction_reason="价格顾虑应该提升风险等级到'中'",
        corrected_by="human"
    )
    
    result = engine.process_new_correction(correction)
    print("Learning result:", result)
    print("\n" + engine.get_learning_summary())
