"""Conservative, deterministic extraction. Never pretend to understand arbitrary prose."""

import re


def number(value: str) -> int:
    if value.isascii() and value.isdigit():
        return int(value)
    if not re.fullmatch(
        r"(?:[一二两三四五六七八九]?十[一二三四五六七八九]?|[零一二两三四五六七八九])", value
    ):
        return -1
    digits = {char: index for index, char in enumerate("零一二三四五六七八九")}
    digits["两"] = 2
    if "十" in value:
        left, right = value.split("十", 1)
        return (digits.get(left, 1) if left else 1) * 10 + digits.get(right, 0)
    return digits.get(value, -1)


def planning_input(text: str) -> tuple[dict, str | None]:
    """Extract supported explicit constraints; ask again on unsupported or conflicting units."""
    values = {"text": text, "goal": "foundation", "minutes": 10, "days": 3}
    if re.search(r"小时|半小时|星期|周|月|年|秒|隔天|每隔|工作日|周末|每次|百|千", text):
        return {}, "请用每天 5–30 分钟、共 1–7 天描述安排；当前暂不支持按小时、秒、周或间隔日排期。"
    token = r"[0-9零一二两三四五六七八九十]+"
    for field, unit, low, high in (("minutes", "分钟", 5, 30), ("days", "天", 1, 7)):
        found = re.findall(rf"({token})\s*{unit}", text)
        parsed = {number(value) for value in found}
        if len(parsed) > 1 or any(not low <= value <= high for value in parsed):
            return {}, "请明确一个每天 5–30 分钟、共 1–7 天的安排，避免多个冲突的时长或天数。"
        if parsed:
            values[field] = parsed.pop()
    if re.search(
        rf"{token}\s*[-~至到.～]\s*{token}|[-−负]\s*{token}\s*(?:分钟|天)"
        rf"|{token}\s*(?:分(?!钟)|min|hour|day)|半|几天|几分钟|若干",
        text,
        re.IGNORECASE,
    ):
        return {}, "请给出确定的每天分钟数和计划天数，例如每天 10 分钟、共 3 天。"
    targets = [
        goal
        for goal, words in (
            ("pitch", ("音准", "音高", "跑调", "模唱")),
            ("rhythm", ("节奏", "节拍")),
            ("stability", ("长音", "稳定")),
        )
        if any(word in text for word in words)
    ]
    if len(targets) > 1:
        return (
            {},
            "请先描述一个主要练习目标：基础、音准、节奏或长音稳定，完成后可以再安排下一个目标。",
        )
    if targets:
        values["goal"] = targets[0]
    elif any(word in text for word in ("高音", "低音", "换声", "混声", "颤音")):
        return (
            {},
            "当前练习模板支持基础、音准、节奏和长音稳定。请说明先从哪个目标开始，其他专项需要教师指导。",
        )
    if any(word in text for word in ("报告", "上次", "之前")):
        return (
            {},
            "请在专业工作台选择一份本人的测评报告后制定计划；直接输入目前只使用本次描述的目标和时间。",
        )
    symptoms = re.sub(r"(?:没有|无|不伴有)(?:疼痛|沙哑|不适)", "", text)
    values["discomfort"] = any(word in symptoms for word in ("疼", "痛", "沙哑", "不适"))
    return values, None
