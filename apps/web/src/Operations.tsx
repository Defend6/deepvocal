import { useEffect, useState } from "react";
import { labels, request } from "./api";

type Diagnostics = {
  counts: Record<string, number>;
  active_retries: number;
  oldest_queued_seconds: number | null;
  budget_needs_review: number;
  worker_signal: string;
  alerts: string[];
  findings: {
    id: string;
    status: string;
    attempt: number;
    max_attempts: number;
    code: string;
  }[];
};
const explanations: Record<string, string> = {
  WORKER_HEARTBEAT_MISSING:
    "暂未收到正常 worker 的近期心跳，请检查本地执行服务。",
  EXPIRED_JOB_LEASE: "有任务执行租约过期，worker 将按剩余次数恢复或结束任务。",
  QUEUE_WAIT_OVER_60S: "有任务排队超过 60 秒，请检查 worker 与队列连接。",
  RECENT_JOB_FAILURES: "过去 24 小时存在失败任务，可根据下方原因定位。",
  MODEL_ACCOUNTING_REVIEW: "有模型费用异常或长期未结占用，需要核对供应商账单。",
  LEASE_EXPIRED: "租约已过期，等待恢复",
  RETRY_BACKOFF: "重试退避中，请等待",
  QUEUE_WAIT: "排队等待执行",
  JOB_FAILED: "执行失败，请检查本地诊断工具",
  TIMEOUT: "执行超时",
  LEASE_EXHAUSTED: "租约过期且重试次数已用完",
  INTERNAL_ERROR: "执行遇到内部错误，可用任务编号定位日志",
  PROVIDER_UNAVAILABLE: "外部服务暂不可用",
  UNSUPPORTED_EVALUATION: "该诊断任务类型不受支持",
};

export function Operations({
  identity,
  jobRevision,
}: {
  identity: string;
  jobRevision: string;
}) {
  const [data, setData] = useState<Diagnostics | null>(null);
  const [error, setError] = useState("");
  const [revision, setRevision] = useState(0);
  useEffect(() => {
    const controller = new AbortController();
    let timer: ReturnType<typeof setTimeout>;
    async function refresh() {
      try {
        const next = await request<Diagnostics>(
          identity,
          "/v1/operations/diagnostics",
          {
            signal: controller.signal,
          },
        );
        if (!controller.signal.aborted) {
          setData(next);
          setError("");
        }
      } catch (error) {
        if (!controller.signal.aborted) setError((error as Error).message);
      } finally {
        if (!controller.signal.aborted) timer = setTimeout(refresh, 15000);
      }
    }
    void refresh();
    return () => {
      controller.abort();
      clearTimeout(timer);
    };
  }, [identity, jobRevision, revision]);
  return (
    <section
      className="history panel operations"
      aria-labelledby="operations-heading"
    >
      <div className="panel-head">
        <h2 id="operations-heading">任务运行诊断</h2>
        <button
          className="text-button"
          onClick={() => setRevision((value) => value + 1)}
        >
          刷新诊断
        </button>
      </div>
      <div className="panel-content" aria-live="polite">
        <p className="muted">
          仅统计当前身份的任务与费用异常，每 15
          秒刷新。执行服务状态仅表示近期心跳。
        </p>
        {error && (
          <p className="error" role="alert">
            诊断刷新失败，已有数据可能过时：{error}
          </p>
        )}
        {!data && !error && <p>正在读取诊断…</p>}
        {data && (
          <>
            <p className="badge">
              {data.worker_signal === "recent_heartbeat"
                ? "执行服务近期有心跳"
                : "执行服务需要检查"}
            </p>
            <div className="metrics">
              {Object.entries(data.counts).map(([status, count]) => (
                <div key={status}>
                  <small>{labels[status] ?? status}</small>
                  <strong>{count}</strong>
                </div>
              ))}
            </div>
            <p>
              重试中 {data.active_retries} 项 · 最久排队{" "}
              {data.oldest_queued_seconds ?? 0} 秒 · 待核账{" "}
              {data.budget_needs_review} 项
            </p>
            {data.alerts.length === 0 && <p>当前未发现诊断规则命中的异常。</p>}
            {data.alerts.map((code) => (
              <p className="notice" key={code}>
                {explanations[code] ?? code}
              </p>
            ))}
            {data.findings.length > 0 && (
              <details open>
                <summary>需关注任务 · 最多 10 项</summary>
                {data.findings.map((item) => (
                  <div className="diagnostic-item" key={item.id}>
                    <strong>
                      {labels[item.status] ?? item.status} ·{" "}
                      {item.id.slice(0, 8)}
                    </strong>
                    <p>
                      {explanations[item.code] ??
                        labels[item.code] ??
                        item.code}{" "}
                      · 已尝试 {item.attempt}/{item.max_attempts}
                    </p>
                    <small className="muted">完整任务编号：{item.id}</small>
                  </div>
                ))}
              </details>
            )}
          </>
        )}
      </div>
    </section>
  );
}
