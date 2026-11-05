from __future__ import annotations

from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator


class PlanningInput(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)
    text: str = Field(default="", max_length=4000)
    goal: Literal["foundation", "pitch", "rhythm", "stability"] = "foundation"
    minutes: int = Field(default=10, ge=5, le=30)
    days: int = Field(default=3, ge=1, le=7)
    report_id: UUID | None = None
    discomfort: bool = False
    input_asset_id: str | None = None


class Exercise(BaseModel):
    model_config = ConfigDict(extra="forbid")
    title: str = Field(min_length=1, max_length=100)
    seconds: int = Field(ge=30, le=600)
    instruction: str = Field(min_length=1, max_length=1000)


class PlanEdit(BaseModel):
    model_config = ConfigDict(extra="forbid")
    version: int = Field(ge=1)
    goal: Literal["foundation", "pitch", "rhythm", "stability"]
    days: int = Field(ge=1, le=7)
    exercises: list[Exercise] = Field(min_length=1, max_length=10)

    @model_validator(mode="after")
    def budget(self):
        if not 300 <= sum(item.seconds for item in self.exercises) <= 1800:
            raise ValueError("plan must contain 5 to 30 minutes including rest")
        if not any(item.title == "休息" for item in self.exercises):
            raise ValueError("plan must retain a rest block")
        return self


class PracticeCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    idempotency_key: UUID
    plan_version: int = Field(ge=1)
    duration_seconds: int = Field(ge=0, le=1800)
    difficulty: int = Field(ge=1, le=5)
    discomfort: bool = False
    note: str = Field(default="", max_length=1000)


def create_plan(
    config: PlanningInput, report: dict | None = None, history: list | None = None
) -> dict:
    if config.discomfort:
        return {
            "status": "refused",
            "reason": "DISCOMFORT_STOP_PRACTICE",
            "message": "先停止练声；持续疼痛或不适时寻求专业帮助。",
        }
    history = history or []
    adaptation = []
    config = config.model_copy(deep=True)
    if report and report.get("quality") == "usable" and config.goal == "foundation":
        candidates = {
            key: metric["value"]
            for key, metric in report.get("dimensions", {}).items()
            if key in {"pitch", "rhythm", "stability"}
            and metric.get("available")
            and isinstance(metric.get("value"), (float, int))
        }
        if candidates:
            config.goal = min(candidates, key=candidates.get)
            adaptation.append("依据本次报告中较弱的可用维度安排重点练习。")
    recent = [item.get("payload", item) for item in history[:3]]
    if any(item.get("discomfort") for item in recent):
        return {
            "status": "refused",
            "reason": "DISCOMFORT_STOP_PRACTICE",
            "message": "最近练习记录有不适，请先确认已恢复，再重新安排练习。",
        }
    if recent and sum(item.get("difficulty", 3) for item in recent) / len(recent) >= 4:
        config.minutes = max(5, int(config.minutes * 0.8))
        adaptation.append("近期反馈难度较高，已减少本次练习时长。")
    instructions = {
        "foundation": ("舒适音区短音", "用轻松音量做短音，每次之间自然休息，不追求极限音高。"),
        "pitch": ("参考音模唱", "选舒适参考音，先听后短句模唱；使用相同参考与移调比较。"),
        "rhythm": ("节拍与短句", "先轻拍节奏，再以舒适速度唱短句；不要用局部拉伸掩盖节奏差异。"),
        "stability": (
            "短长音观察",
            "舒适音区轻松持续3至5秒，观察跟踪覆盖；不憋气，不以颤音判定错误。",
        ),
    }
    title, instruction = instructions[config.goal]
    total = config.minutes * 60
    exercises = [
        {
            "title": "准备与状态检查",
            "seconds": 60,
            "instruction": "确认无疼痛、明显疲劳；舒适站坐，自然呼吸。",
        },
        {
            "title": title,
            "seconds": total - 180,
            "instruction": instruction + " 每小组之间充分停顿，可随时提前结束。",
        },
        {"title": "休息", "seconds": 60, "instruction": "停止发声，自然休息；出现不适不再继续。"},
        {
            "title": "记录与回顾",
            "seconds": 60,
            "instruction": "记录实际练习时长、主观难度和不适；无需完成额定时长。",
        },
    ]
    core = exercises.pop(1)
    remaining = core["seconds"]
    group = 1
    while remaining > 0:
        seconds = min(600, remaining)
        exercises.insert(
            group, {**core, "seconds": seconds, "title": f"{core['title']} · 第{group}组"}
        )
        group += 1
        remaining -= seconds
    observations = []
    if report:
        observations = [f"报告任务：{report.get('profile')}；质量：{report.get('quality')}。"]
        if not report.get("total", {}).get("available"):
            observations.append("报告综合分不可用，不据此判断唱功或自动增加强度。")
    return {
        "status": "planned",
        "plan": {
            "goal": config.goal,
            "days": config.days,
            "minutes": config.minutes,
            "exercises": exercises,
            "observations": observations,
            "adaptation": adaptation,
            "basis": "report_and_confirmed_feedback_v2",
            "daily_schedule": [
                {
                    "day": day,
                    "focus": config.goal,
                    "minutes": config.minutes,
                    "instruction": "完成后记录难度与状态，再决定下一次安排。",
                }
                for day in range(1, config.days + 1)
            ],
            "report_id": str(config.report_id) if config.report_id else None,
            "ruleset_version": "practice-template-v1",
            "notice": "按舒适状态练习，可随时调整或提前结束。",
            "stop_rule": "疼痛、持续沙哑或明显不适时停止；持续问题请寻求专业帮助。",
        },
    }
