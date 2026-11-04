import re
from dataclasses import dataclass


@dataclass(frozen=True)
class RouteDecision:
    agent: str
    reason: str
    confidence: float
    tool_allowlist: tuple[str, ...]
    requires_confirmation: bool = False


class RuleRouter:
    """Deterministic first-pass router; model classification is an optional later stage."""

    def route(self, text: str, explicit_action: str | None = None) -> RouteDecision:
        normalized = text.strip().lower()
        explicit = {
            "knowledge.ask": ("knowledge", ("knowledge.retrieve",)),
            "assessment.submit": ("assessment", ("audio.inspect", "assessment.submit")),
            "planning.create": ("planning", ("reports.read", "plans.create")),
            "singing.generate": ("singing", ("singing.capabilities",)),
        }
        if explicit_action:
            if explicit_action not in explicit:
                return RouteDecision("clarify", "unknown explicit action", 0.0, ())
            agent, tools = explicit[explicit_action]
            return RouteDecision(agent, "explicit action", 1.0, tools, agent == "singing")
        singing = bool(re.search(r"生成.{0,12}(?:歌声|演唱|清唱)", normalized)) or any(
            k in normalized
            for k in (
                "生成歌声",
                "生成个性化歌声",
                "个性化歌声",
                "歌声生成",
                "唱出来",
            )
        )
        planning = bool(
            re.search(r"(?:制定|安排|规划|设计).{0,12}(?:练习|训练)", normalized)
        ) or any(k in normalized for k in ("计划", "练习安排", "安排练习", "训练安排"))
        assessment = any(
            k in normalized for k in ("分析", "诊断", "测评", "评估", "检测", "听听")
        ) and any(k in normalized for k in ("录音", "音频", "清唱", "全曲", "跑调", "音准", "音高"))
        assessment = assessment or any(
            k in normalized for k in ("我唱得怎么样", "帮我听", "看看有没有跑调", "评价我的演唱")
        )
        # A bare audio attachment description can request analysis; a theory question cannot.
        theory = any(k in normalized for k in ("什么是", "为什么", "原理", "区别", "含义"))
        if theory and not any(k in normalized for k in ("帮我", "请分析", "请制定", "并", "然后")):
            return RouteDecision("knowledge", "vocal theory question", 0.9, ("knowledge.retrieve",))
        if sum((singing, planning, assessment)) > 1:
            return RouteDecision("clarify", "multiple actionable intents", 0.4, ())
        if singing:
            return RouteDecision(
                "singing", "singing generation intent", 0.88, ("singing.capabilities",), True
            )
        if planning:
            return RouteDecision(
                "planning", "practice planning intent", 0.90, ("reports.read", "plans.create")
            )
        if assessment or (
            not theory and any(k in normalized for k in ("这段录音", "我的录音", "这段音频"))
        ):
            return RouteDecision(
                "assessment",
                "audio or assessment intent",
                0.92,
                ("audio.inspect", "assessment.submit"),
            )
        if any(
            k in normalized
            for k in (
                "什么是",
                "为什么",
                "怎么发声",
                "如何",
                "区别",
                "共鸣",
                "气息",
                "声区",
                "音准",
                "音高",
                "跑调",
                "发声",
                "练习方法",
            )
        ):
            return RouteDecision("knowledge", "vocal theory intent", 0.90, ("knowledge.retrieve",))
        return RouteDecision("clarify", "intent confidence is insufficient", 0.35, ())
