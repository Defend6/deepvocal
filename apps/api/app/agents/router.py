"""Deterministic intent decomposition and dependency-aware routing, without tool execution."""

import re
from dataclasses import dataclass

TOOLS = {
    "knowledge": ("knowledge.retrieve",),
    "assessment": ("audio.inspect", "assessment.submit"),
    "planning": ("reports.read", "plans.create"),
    "singing": ("singing.capabilities",),
}
ACTIONS = {
    "knowledge.ask": "knowledge",
    "assessment.submit": "assessment",
    "planning.create": "planning",
    "singing.generate": "singing",
}
TITLES = {
    "knowledge": "声乐解答",
    "assessment": "录音分析",
    "planning": "练习安排",
    "singing": "歌声生成",
}


@dataclass(frozen=True)
class IntentStep:
    agent: str
    text: str
    depends_on: tuple[str, ...] = ()

    def as_dict(self):
        return {
            "id": self.agent,
            "agent": self.agent,
            "text": self.text,
            "title": TITLES[self.agent],
            "depends_on": list(self.depends_on),
        }


@dataclass(frozen=True)
class RouteDecision:
    agent: str
    reason: str
    confidence: float
    tool_allowlist: tuple[str, ...]
    requires_confirmation: bool = False
    steps: tuple[IntentStep, ...] = ()


def wants_evidence(message: str) -> bool:
    """Source display is opt-in for this request, independent from evidence validation."""
    return bool(
        re.search(
            r"来源|出处|参考文献|回答依据|参考资料|引用|证据|依据(?:是什么|呢|吗)|有.{0,3}依据|(?:提供|给出|附上|展示|说明|列出).{0,6}依据",
            message,
        )
    ) and not bool(
        re.search(
            r"(?:不需要|不要|不用|无需|不必|别).{0,8}(?:来源|出处|文献|依据|资料|引用|证据)",
            message,
        )
    )


class RuleRouter:
    @staticmethod
    def _intent(clause: str) -> str | None:
        value = clause.strip().lower()
        if not value:
            return None
        if re.search(
            r"(?:不要|不用|无需|不必|别)(?:再|给我|帮我|进行|做)?(?:分析|诊断|测评|制定|安排|生成)",
            value,
        ):
            return None
        # A question about a tool or workflow is not permission to run that tool.
        concept = bool(re.search(r"什么是|原理|区别|含义|是什么意思", value))
        reference_question = bool(
            re.search(r"(?:需要|要什么|哪些).{0,8}(?:参考|信息|材料|资料)", value)
        )
        if concept or reference_question:
            return "knowledge"
        if re.search(r"生成.{0,12}(?:歌声|演唱|清唱)|歌声生成|个性化歌声|唱出来", value):
            return "singing"
        if re.search(
            r"(?:制定|安排|规划|设计).{0,12}(?:练习|训练|计划)|练习安排|训练安排|计划", value
        ):
            return "planning"
        audio = re.search(r"录音|音频|清唱|全曲|跑调|音准|音高|演唱", value)
        if (audio and re.search(r"分析|诊断|测评|评估|检测|听听", value)) or re.search(
            r"我唱得怎么样|帮我听|看看有没有跑调|评价我的演唱", value
        ):
            return "assessment"
        if re.search(
            r"为什么|怎么|如何|解释|介绍|说明|区别|共鸣|气息|声区|音准|音高|高音|低音|胸声|头声|混声|换声|跑调|发声|练习方法",
            value,
        ):
            return "knowledge"
        if re.search(r"这段录音|我的录音|这段音频", value):
            return "assessment"
        return None

    def route(self, text: str, explicit_action: str | None = None) -> RouteDecision:
        if explicit_action:
            agent = ACTIONS.get(explicit_action)
            if agent is None:
                return RouteDecision("clarify", "unknown explicit action", 0.0, ())
            return RouteDecision(
                agent,
                "explicit action",
                1.0,
                TOOLS[agent],
                agent == "singing",
                (IntentStep(agent, text),),
            )
        # Preserve plan constraints and goal lists; group related subquestions so each
        # agent runs once per request, with bounded and auditable tool calls.
        clauses = re.split(
            r"[，,。；;！？?!\n]+|(?:然后|接着|之后|同时|另外|以及|并且|并|再|和|、|还想|也想|还要|也要|顺便|后)"
            r"(?=(?:请|帮我|给我|为我|解释|介绍|说明|分析|诊断|测评|评估|制定|安排|规划|设计|生成|唱|提供|给出|练习|如何|怎么|讲解))",
            text,
        )
        grouped: dict[str, list[str]] = {}
        previous = None
        pending = []
        for clause in clauses:
            clause = clause.strip()
            if not clause:
                continue
            if re.search(
                r"(?:不要|不用|无需|不必|别)(?:再|给我|帮我|进行|做)?(?:分析|诊断|测评|制定|安排|生成)",
                clause,
            ):
                continue
            agent = self._intent(clause)
            if agent:
                grouped.setdefault(agent, []).extend([*pending, clause])
                pending = []
                previous = agent
            elif previous:
                grouped[previous].append(clause)
            else:
                pending.append(clause)
        if not grouped:
            return RouteDecision("clarify", "intent confidence is insufficient", 0.35, ())
        order = list(grouped)
        if (
            "assessment" in order
            and "planning" in order
            and order.index("planning") < order.index("assessment")
        ):
            order.remove("assessment")
            order.insert(order.index("planning"), "assessment")
        steps = tuple(
            IntentStep(
                agent,
                "，".join(grouped[agent]),
                ("assessment",) if agent == "planning" and "assessment" in grouped else (),
            )
            for agent in order
        )
        first = steps[0].agent
        reasons = {
            "knowledge": "vocal theory intent",
            "assessment": "audio or assessment intent",
            "planning": "practice planning intent",
            "singing": "singing generation intent",
        }
        return RouteDecision(
            first,
            "structured multi-intent workflow" if len(steps) > 1 else reasons[first],
            0.9,
            TOOLS[first],
            "singing" in grouped,
            steps,
        )
