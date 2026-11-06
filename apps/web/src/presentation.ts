import { labels } from "./api";
import type { Job } from "./types";

export const done = (job: Job) => ["SUCCEEDED", "FAILED", "CANCELLED"].includes(job.status);
export const human = (text?: string | null) => text
  ? labels[text] ?? (/^[A-Z][A-Z_]+$/.test(text) ? "暂时无法提供结果，请稍后重试。" : text)
  : "";

// Execution success does not imply that an answer or artifact was produced.
export function jobLabel(job: Job): string {
  if (job.status !== "SUCCEEDED") return human(job.status);
  if (!job.result) return "结果暂不可用";
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
  if (["refused", "disabled", "unavailable"].includes(job.result.status))
    return job.result.reason ? human(job.result.reason) : "这次还无法给出可靠结果，请补充信息后重试。";
  if (job.kind === "assessment") return "录音分析已完成，下面是测量结果和练习参考。";
  if (job.kind === "planning") return "练习计划已经准备好了，我们从适合你的节奏开始。";
  if (job.kind === "singing") return "音频已生成，可以在下面试听和下载。";
  return "";
}
