import { authHeaders } from "./auth";

export class RequestError extends Error {
  constructor(public status: number, message: string) { super(message); }
}

export async function request<T>(
  identity: string,
  path: string,
  options: RequestInit = {},
): Promise<T> {
  const response = await fetch(`/api${path}`, {
    ...options,
    headers: {
      "Content-Type": "application/json",
      ...await authHeaders(identity),
      ...options.headers,
    },
  });
  const body = await response.json().catch(() => null);
  if (!response.ok) {
    const detail =
      typeof body?.detail === "string"
        ? body.detail
        : "请求失败，请检查输入后重试";
    throw new RequestError(response.status, userMessage(response.status, detail));
  }
  return body as T;
}

export function userMessage(status: number, detail: string) {
  const messages: Record<number, string> = {
    401: "请登录后继续。", 403: "你没有执行此操作的权限。",
    404: "内容不存在或已被删除。", 410: "上传已过期，请重新选择文件。",
    413: "文件或内容过大，请缩小后重试。", 422: "请检查填写的内容和文件格式。",
    429: "操作较频繁或任务额度已用完，请稍后重试。",
    503: "服务暂时繁忙，请稍后重试。",
  };
  if (status === 409) return "内容状态已更新，请刷新后重试。";
  if (messages[status]) return messages[status];
  return /[\u4e00-\u9fff]/.test(detail) ? detail : "操作未完成，请稍后重试。";
}

export const labels: Record<string, string> = {
  QUEUED: "排队中",
  RUNNING: "处理中",
  SUCCEEDED: "已完成",
  FAILED: "失败",
  CANCELLED: "已取消",
  disabled: "尚未启用",
  refused: "本次未提供结果",
  answered: "已回答",
  measured: "已测量",
  generated: "歌声已生成",
  verified: "录音检查完成",
  clarify: "请补充信息",
  blocked: "等待准备完成",
  planned: "计划已保存",
  unavailable: "暂不可用",
  MODEL_API_KEY_REQUIRED: "问答暂不可用，你可以先查看相关资料。",
  PREPARE_REVIEWED_CORPUS_FIRST: "尚未准备可用的本地知识资料，请导入并审核资料后重新准备知识库。",
  MODEL_BUDGET_NOT_CONFIGURED:
    "问答暂不可用，请稍后再试。",
  MODEL_PRICING_NOT_CONFIGURED: "问答暂不可用，请稍后再试。",
  MODEL_BUDGET_EXHAUSTED: "本期问答额度已用完，请稍后再试。",
  MODEL_JOB_NO_LONGER_ACTIVE: "任务已取消或执行租约失效，已阻止后续模型调用。",
  EXTERNAL_SINGING_SERVICE_NOT_CONFIGURED:
    "歌声生成暂不可用，请稍后再试。",
  INSUFFICIENT_EVIDENCE: "当前资料不足以支持回答，请补充或调整问题。",
  LOW_RELEVANCE: "我找到了部分资料，但与这个问题的关联还不够，暂时无法据此给出可靠回答。可以把问题拆成一个具体概念，或补充你想了解的方面。",
  MODEL_PROVIDER_FAILED: "回答服务暂时没有响应，请稍后重试。",
  KNOWLEDGE_INDEX_UNAVAILABLE: "知识检索服务暂不可用，请稍后重试。",
  INVALID_GENERATED_CITATIONS: "模型引用校验未通过，本次回答未发布。",
  HEALTH_BOUNDARY: "健康问题请寻求专业帮助。",
  DISCOMFORT_STOP_PRACTICE: "出现不适时先停止练声。",
  AUTOMATIC_ALIGNMENT_UNRELIABLE: "参考旋律与录音未能可靠对齐，请确认曲目、声道与录音是否正确。",
  VOICE_CONSENT_REVOKED: "声音使用授权已撤回，本次结果未发布。",
  VOCAL_SEPARATION_UNAVAILABLE: "人声提取暂不可用，请上传清唱录音后重试。",
  READY_ASSET_REQUIRED: "请先上传录音并等待检查完成。",
  ASSET_CONTENT_MISMATCH: "录音内容检查未通过，请重新上传。",
};
