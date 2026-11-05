import { useEffect, useState } from "react";
import { request } from "./api";

type Memory = {
  enabled: boolean;
  revision: number;
  l1_events: { id: string; event_kind: string; created_at: string }[];
  l2_facts: {
    semantic_key: string;
    value: string | number;
    source_event_id: string;
  }[];
  l3_profile: { preferences?: { goal?: string; minutes?: number } };
};
type Version = {
  id: string;
  skill_id: string;
  version: number;
  status: string;
  critic: { passed: boolean };
};
type Skills = {
  discovery: { skill_id: string; name: string; description: string }[];
  versions: Version[];
};
type ModelUsage = {
  configured: boolean;
  currency: string;
  periods: {
    period: string;
    calls: number;
    accounted_minor: number;
    held_minor: number;
  }[];
  notice: string;
};

export function Governance({
  identity,
  completedJobs,
}: {
  identity: string;
  completedJobs: { id: string; kind: string; status: string }[];
}) {
  const [memory, setMemory] = useState<Memory | null>(null);
  const [skills, setSkills] = useState<Skills | null>(null);
  const [usage, setUsage] = useState<ModelUsage | null>(null);
  const [goal, setGoal] = useState("foundation");
  const [minutes, setMinutes] = useState(10);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [erasureText, setErasureText] = useState("");
  async function reload() {
    const [m, s, u] = await Promise.all([
      request<Memory>(identity, "/v1/memory"),
      request<Skills>(identity, "/v1/skills"),
      request<ModelUsage>(identity, "/v1/model/usage"),
    ]);
    setMemory(m);
    setSkills(s);
    setUsage(u);
    setGoal(m.l3_profile.preferences?.goal ?? "foundation");
    setMinutes(m.l3_profile.preferences?.minutes ?? 10);
  }
  useEffect(() => {
    const controller = new AbortController();
    void Promise.all([
      request<Memory>(identity, "/v1/memory", { signal: controller.signal }),
      request<Skills>(identity, "/v1/skills", { signal: controller.signal }),
      request<ModelUsage>(identity, "/v1/model/usage", {
        signal: controller.signal,
      }),
    ])
      .then(([m, s, u]) => {
        if (!controller.signal.aborted) {
          setMemory(m);
          setSkills(s);
          setUsage(u);
          setGoal(m.l3_profile.preferences?.goal ?? "foundation");
          setMinutes(m.l3_profile.preferences?.minutes ?? 10);
        }
      })
      .catch((error) => {
        if (!controller.signal.aborted) setError(error.message);
      });
    return () => controller.abort();
  }, [identity, completedJobs.length]);
  async function act(action: () => Promise<unknown>, refresh = true) {
    setBusy(true);
    setError("");
    try {
      await action();
      if (refresh) await reload();
    } catch (error) {
      setError((error as Error).message);
    } finally {
      setBusy(false);
    }
  }
  return (
    <section className="history panel">
      <div className="panel-head">
        <h2>记忆与任务模板</h2>
        <button
          className="text-button"
          disabled={busy}
          onClick={() => void act(reload)}
        >
          刷新
        </button>
      </div>
      <div className="panel-content">
        {error && (
          <p className="error" role="alert">
            {error}
          </p>
        )}
        <details>
          <summary>
            账户用量 ·{" "}
            {usage?.configured ? "查看用量" : "暂无用量"}
          </summary>
          {usage?.periods.length === 0 && <p>暂无使用记录。</p>}
          {usage?.periods.map((period) => (
            <p key={period.period}>
              {period.period} · {period.calls} 次调用 · 保守记账 ¥
              {(period.accounted_minor / 100).toFixed(2)} · 未结占用 ¥
              {(period.held_minor / 100).toFixed(2)}
            </p>
          ))}
        </details>
        <details>
          <summary>
            跨会话记忆 · {memory?.enabled ? "已开启" : "默认关闭"}
          </summary>
          <p>
            开启后保存明确偏好和任务类型记录。系统不会从录音猜测疾病或自动生成能力事实。关闭后不读取或新增长期记忆。
          </p>
          {memory && (
            <>
              <div className="actions">
                <button
                  disabled={busy}
                  onClick={() =>
                    void act(() =>
                      request(identity, "/v1/memory/consent", {
                        method: "PUT",
                        body: JSON.stringify({
                          enabled: !memory.enabled,
                          revision: memory.revision,
                        }),
                      }),
                    )
                  }
                >
                  {memory.enabled ? "关闭跨会话记忆" : "开启跨会话记忆"}
                </button>
                <button
                  className="secondary"
                  disabled={busy}
                  onClick={() =>
                    void act(() =>
                      request(identity, "/v1/memory", { method: "DELETE" }),
                    )
                  }
                >
                  清除全部记忆并关闭
                </button>
              </div>
              <p className="muted">
                清除范围为记忆数据；来源报告、计划与任务检查点各自保留。
              </p>
              {memory.enabled && (
                <>
                  <div className="form-grid">
                    <label>
                      默认目标
                      <select
                        value={goal}
                        onChange={(event) => setGoal(event.target.value)}
                      >
                        <option value="foundation">基础练习</option>
                        <option value="pitch">参考音模唱</option>
                        <option value="rhythm">节奏短句</option>
                        <option value="stability">长音观察</option>
                      </select>
                    </label>
                    <label>
                      默认每天分钟
                      <input
                        type="number"
                        min="5"
                        max="30"
                        value={minutes}
                        onChange={(event) =>
                          setMinutes(Number(event.target.value))
                        }
                      />
                    </label>
                  </div>
                  <button
                    disabled={busy}
                    onClick={() =>
                      void act(() =>
                        request(identity, "/v1/memory/preferences", {
                          method: "PUT",
                          body: JSON.stringify({
                            revision: memory.revision,
                            goal,
                            minutes,
                          }),
                        }),
                      )
                    }
                  >
                    确认保存偏好
                  </button>
                </>
              )}
              <h4>L2 已确认事实</h4>
              {memory.l2_facts.map((fact) => (
                <p key={fact.semantic_key}>
                  {fact.semantic_key}：{String(fact.value)}{" "}
                  <button
                    className="text-button"
                    disabled={busy}
                    onClick={() =>
                      void act(() =>
                        request(
                          identity,
                          `/v1/memory/facts/${fact.semantic_key}`,
                          { method: "DELETE" },
                        ),
                      )
                    }
                  >
                    删除此事实
                  </button>
                  <br />
                  <small className="muted">
                    来源事件 {fact.source_event_id}
                  </small>
                </p>
              ))}
              <h4>L3 偏好摘要</h4>
              <pre>{JSON.stringify(memory.l3_profile, null, 2)}</pre>
              <h4>L1 最近事件</h4>
              {memory.l1_events.map((event) => (
                <p className="muted" key={event.id}>
                  {event.event_kind} ·{" "}
                  {new Date(event.created_at).toLocaleString()}
                </p>
              ))}
            </>
          )}
        </details>
        <details>
          <summary>Skill 模板库 · 发现、审核与发布</summary>
          <p>
            只允许已有功能的受控步骤；自动 Critic
            是工程静态检查，不代表声乐专家审核。候选须依次审核、发布才能用于当前身份的新任务。
          </p>
          <div className="metrics">
            {skills?.discovery.map((item) => (
              <div key={item.skill_id}>
                <b>{item.name}</b>
                <small>{item.description}</small>
              </div>
            ))}
          </div>
          <h4>从已完成任务生成私有候选</h4>
          <div className="plan-list">
            {completedJobs
              .filter(
                (job) =>
                  job.status === "SUCCEEDED" &&
                  ["knowledge", "planning", "assessment", "singing"].includes(
                    job.kind,
                  ),
              )
              .slice(0, 5)
              .map((job) => (
                <button
                  className="secondary"
                  disabled={busy}
                  key={job.id}
                  onClick={() =>
                    void act(() =>
                      request(identity, `/v1/skills/from-job/${job.id}`, {
                        method: "POST",
                      }),
                    )
                  }
                >
                  {job.kind} · {job.id.slice(0, 8)}
                </button>
              ))}
          </div>
          <p className="muted">
            仅提取固定步骤类型，私有录音、问题内容与身份不会写入模板正文。
          </p>
          {skills?.versions.map((version) => (
            <div className="exercise" key={version.id}>
              <div>
                <strong>
                  {version.skill_id} · v{version.version} · {version.status}
                </strong>
                <p>静态 Critic：{version.critic.passed ? "通过" : "不通过"}</p>
                <div className="actions">
                  {(version.status === "candidate"
                    ? ["review", "reject"]
                    : version.status === "reviewed"
                      ? ["publish", "reject"]
                      : version.status === "published"
                        ? ["retire"]
                        : []
                  ).map((action) => (
                    <button
                      disabled={busy}
                      className="secondary"
                      key={action}
                      onClick={() =>
                        void act(() =>
                          request(
                            identity,
                            `/v1/skills/${version.id}/transition`,
                            {
                              method: "POST",
                              body: JSON.stringify({ action }),
                            },
                          ),
                        )
                      }
                    >
                      {
                        {
                          review: "审核",
                          publish: "发布",
                          reject: "拒绝",
                          retire: "退役",
                        }[action]
                      }
                    </button>
                  ))}
                </div>
              </div>
            </div>
          ))}
        </details>
        <details className="privacy-settings"><summary>数据与隐私</summary>
          <p>删除申请将停止正在进行的任务，清理录音、生成音频、对话、报告、计划、记忆及任务模板。
            备份中的历史副本按备份保留周期处理。申请期间账户暂时不可用。</p>
          <label>输入“删除我的全部项目数据”确认<input value={erasureText}
            onChange={event => setErasureText(event.target.value)} /></label>
          <button className="secondary" disabled={busy || erasureText !== "删除我的全部项目数据"}
            onClick={() => void act(async () => {
              await request(identity, "/v1/privacy/erase", {method: "POST", body: JSON.stringify({confirmation: erasureText})});
              setErasureText(""); setError("已申请删除，清理完成前账户暂停使用。请稍后重新登录。");
            }, false)}>申请删除全部项目数据</button>
        </details>
      </div>
    </section>
  );
}
