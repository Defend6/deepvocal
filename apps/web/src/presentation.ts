import { labels } from "./api";
import type { Job, Result } from "./types";

export const done = (job: Job) => ["SUCCEEDED", "FAILED", "CANCELLED"].includes(job.status);
export const human = (text?: string | null) => text
  ? labels[text] ?? (/^[A-Z][A-Z_]+$/.test(text) ? "暂时无法提供结果，请稍后重试。" : text)
  : "";

// Execution success does not imply that an answer or artifact was produced.
export function jobLabel(job: Job): string {
  if (job.status !== "SUCCEEDED") return human(job.status);
  if (!job.result) return "结果暂不可用";
  if (job.result.steps) return ({completed: "处理完成", partial: "部分完成", needs_input: "待补充信息"} as Record<string, string>)[job.result.status] ?? "正在处理";
  if (job.result.reason === "LOW_RELEVANCE" || job.result.reason === "INSUFFICIENT_EVIDENCE")
    return "资料依据不足";
  if (job.result.status === "refused") return "本次未提供结果";
  if (job.result.status === "disabled" || job.result.status === "unavailable") return "暂不可用";
  return human(job.result.status) || "处理完成";
}

export function jobIntroduction(job: Job): string {
  if (job.status === "QUEUED") return "已收到，正在安排处理。结果会直接显示在这里。";
  if (job.status === "RUNNING") return "正在处理你的内容，请稍候。你可以继续提问。";
  if (job.status === "CANCELLED") return "本次处理已取消，你可以修改内容后重新发送。";
  if (job.status === "FAILED") return "这次处理没有完成。可以重试；若仍失败，请稍后再试。";
  if (!job.result) return "暂时无法读取本次结果，请重新打开记录。";
  if (job.result.steps) return job.result.status === "completed" ? "已按你的要求依次处理，结果如下。"
    : "已处理可以完成的内容。其余事项需要补充信息，请看下方提示。";
  if (["refused", "disabled", "unavailable"].includes(job.result.status))
    return job.result.reason ? human(job.result.reason) : "这次还无法给出可靠结果，请补充信息后重试。";
  if (job.kind === "assessment") return "录音分析已完成，下面是测量结果和练习参考。";
  if (job.kind === "planning") return "练习计划已经准备好了，我们从适合你的节奏开始。";
  if (job.kind === "singing") return "音频已生成，可以在下面试听和下载。";
  return "";
}

export function answerText(result: Result, showEvidence = false): string {
  if (showEvidence) return result.answer ?? "";
  return (result.answer ?? "").replace(/\[\d+(?:\s*[,，、–-]\s*\d+)*\]|【\d+】/g, "");
}

export function copyResult(result: Result, showEvidence = false): string {
  if (result.steps) return result.steps.map(step => `${step.title}\n${step.result ? copyResult(step.result, showEvidence) : step.message ?? "尚未完成"}`).join("\n\n");
  return answerText(result, showEvidence) || result.message || (result.plan
    ? `每天 ${result.plan.minutes} 分钟，共 ${result.plan.days} 天\n` + result.plan.exercises.map(e => `${e.title}（${e.seconds} 秒）：${e.instruction}`).join("\n")
    : "");
}
