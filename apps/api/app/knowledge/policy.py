import re

from pydantic import BaseModel, ConfigDict, Field


class KnowledgeInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    text: str = Field(min_length=1, max_length=4000)


def health_boundary(question: str) -> bool:
    return bool(
        re.search(
            r"用药|吃.{0,8}药|诊断.{0,8}病|声带结节|嗓子疼|喉咙[痛疼]|咳血|失声|(?:持续|突然|明显|声音)嘶哑",
            question,
        )
    )
