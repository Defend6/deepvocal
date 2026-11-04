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
        if any(
            k in normalized
            for k in ("生成歌声", "生成个性化歌声", "个性化歌声", "歌声生成", "唱出来")
        ):
            return RouteDecision(
                "singing", "singing generation intent", 0.88, ("singing.capabilities",), True
            )
        if any(k in normalized for k in ("什么是", "为什么", "怎么发声", "如何", "区别")):
            return RouteDecision("knowledge", "vocal theory question", 0.9, ("knowledge.retrieve",))
        if any(k in normalized for k in ("录音", "音频", "跑调", "音准", "音高", "噪声", "测评")):
            return RouteDecision(
                "assessment",
                "audio or assessment intent",
                0.92,
                ("audio.inspect", "assessment.submit"),
            )
        if any(k in normalized for k in ("计划", "练习安排", "每天", "训练")):
            return RouteDecision(
                "planning", "practice planning intent", 0.90, ("reports.read", "plans.create")
            )
        if any(k in normalized for k in ("什么是", "为什么", "怎么发声", "共鸣", "气息", "声区")):
            return RouteDecision("knowledge", "vocal theory intent", 0.90, ("knowledge.retrieve",))
        return RouteDecision("clarify", "intent confidence is insufficient", 0.35, ())
