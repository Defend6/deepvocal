export async function request<T>(
  identity: string,
  path: string,
  options: RequestInit = {},
): Promise<T> {
  const response = await fetch(`/api${path}`, {
    ...options,
    headers: {
      "Content-Type": "application/json",
      "X-Debug-Identity": identity,
      ...options.headers,
    },
  });
  const body = await response.json().catch(() => null);
  if (!response.ok) {
    const detail =
      typeof body?.detail === "string"
        ? body.detail
        : "请求失败，请检查输入后重试";
    throw new Error(`${response.status} · ${detail}`);
  }
  return body as T;
}

export const labels: Record<string, string> = {
  QUEUED: "排队中",
  RUNNING: "处理中",
  SUCCEEDED: "已完成",
  FAILED: "失败",
  CANCELLED: "已取消",
  disabled: "尚未启用",
  refused: "未生成",
  answered: "已回答",
  measured: "已测量",
  planned: "计划已保存",
  unavailable: "暂不可用",
  MODEL_API_KEY_REQUIRED: "模型 API Key 尚未配置，当前只展示检索资料预览。",
  EXTERNAL_SINGING_SERVICE_NOT_CONFIGURED:
    "歌声生成接口已预留，外部服务尚未配置。",
  INSUFFICIENT_EVIDENCE: "当前资料不足以支持回答，请补充或调整问题。",
  INVALID_GENERATED_CITATIONS: "模型引用校验未通过，本次回答未发布。",
  HEALTH_BOUNDARY: "健康问题请寻求专业帮助。",
  DISCOMFORT_STOP_PRACTICE: "出现不适时先停止练声。",
};
