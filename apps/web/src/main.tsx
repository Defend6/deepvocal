import { useCallback, useEffect, useRef, useState } from "react";
import { createRoot } from "react-dom/client";
import {
  Activity,
  BookOpen,
  Brain,
  Clock3,
  Mic2,
  Music2,
  Route,
  UploadCloud,
} from "lucide-react";
import { labels, request } from "./api";
import { Governance } from "./Governance";
import { Operations } from "./Operations";
import "./styles.css";

type Agent = "knowledge" | "assessment" | "planning" | "singing";
type Metric = {
  available: boolean;
  value: number | null;
  unit?: string;
  reason?: string;
};
type Citation = {
  number: number;
  title: string;
  url: string;
  text: string;
  license: string;
  review_status: string;
};
type Result = {
  status: string;
  reason?: string;
  message?: string;
  answer?: string | null;
  mode?: string;
  review_notice?: string;
  evidence_preview?: Citation[];
  citations?: Citation[];
  total?: Metric;
  dimensions?: Record<string, Metric>;
  quality?: string;
  duration_ms?: number;
  tracking_coverage?: number;
  algorithm_version?: string;
  ruleset_version?: string;
  limitations?: string[];
  report_id?: string;
  plan_id?: string;
  segments?: {
    start_ms: number;
    end_ms: number;
    coverage: number;
    mean_abs_cents: number | null;
  }[];
};
type Job = {
  id: string;
  kind: Agent;
  status: string;
  result: Result | null;
  error_code: string | null;
  created_at: string;
  attempt: number;
  trace_id: string;
};
type Exercise = { title: string; seconds: number; instruction: string };
type Plan = {
  id: string;
  version: number;
  payload: {
    goal: string;
    days: number;
    minutes: number;
    exercises: Exercise[];
    notice: string;
    stop_rule: string;
    observations: string[];
  };
};
type Report = { id: string; created_at: string; payload: Result };
type Practice = {
  id: string;
  plan_version: number;
  payload: {
    duration_seconds: number;
    difficulty: number;
    discomfort: boolean;
    note: string;
  };
  created_at: string;
};
const agents = [
  {
    id: "knowledge" as Agent,
    label: "声乐理论答疑",
    icon: BookOpen,
    detail: "中文证据 · 来源可追溯",
  },
  {
    id: "assessment" as Agent,
    label: "录音诊断与全曲测评",
    icon: Activity,
    detail: "工程测量 · 分段报告",
  },
  {
    id: "planning" as Agent,
    label: "练习规划",
    icon: Route,
    detail: "目标约束 · 练习反馈",
  },
  {
    id: "singing" as Agent,
    label: "个性化歌声生成",
    icon: Music2,
    detail: "外部服务待接入",
  },
];
const goals: Record<string, string> = {
  foundation: "基础练习",
  pitch: "参考音模唱",
  rhythm: "节奏短句",
  stability: "长音观察",
};
const done = (job: Job) =>
  ["SUCCEEDED", "FAILED", "CANCELLED"].includes(job.status);
const human = (text?: string | null) => (text ? (labels[text] ?? text) : "");

function Evidence({
  items,
  preview,
}: {
  items: Citation[];
  preview?: boolean;
}) {
  return (
    <section className="evidence">
      <h4>{preview ? "资料预览（尚未生成模型回答）" : "回答依据"}</h4>
      {items.map((item) => (
        <details key={item.number}>
          <summary>
            [{item.number}] {item.title}{" "}
            <span className="muted">
              {item.license} · {item.review_status}
            </span>
          </summary>
          <p>{item.text}</p>
          {/^https?:\/\//.test(item.url) ? (
            <a href={item.url} target="_blank" rel="noreferrer">
              查看来源
            </a>
          ) : (
            <small>{item.url}</small>
          )}
        </details>
      ))}
    </section>
  );
}

function ResultView({ result }: { result: Result }) {
  return (
    <div className="result-view">
      <span className="badge">{human(result.status)}</span>
      {result.reason && <p className="notice">{human(result.reason)}</p>}
      {result.message && <p>{result.message}</p>}
      {result.answer && <p className="answer">{result.answer}</p>}
      {result.review_notice && <p className="notice">{result.review_notice}</p>}
      {result.total && (
        <>
          <div className="score">
            {result.total.available ? result.total.value?.toFixed(1) : "—"}
            <small>练习参考分 · {result.quality}</small>
          </div>
          {!result.total.available && (
            <p className="muted">{result.total.reason}</p>
          )}
          <div className="metrics">
            {Object.entries(result.dimensions ?? {}).map(([key, metric]) => (
              <div key={key}>
                <small>{key}</small>
                <strong>
                  {metric.available ? metric.value?.toFixed(1) : "—"}
                </strong>
                <small>{metric.unit ?? metric.reason}</small>
              </div>
            ))}
          </div>
          <p className="muted">
            {result.duration_ms
              ? `${(result.duration_ms / 1000).toFixed(1)} 秒 · `
              : ""}
            跟踪覆盖 {((result.tracking_coverage ?? 0) * 100).toFixed(1)}% ·{" "}
            {result.algorithm_version} / {result.ruleset_version}
          </p>
          {result.limitations?.map((item) => (
            <p className="notice" key={item}>
              {item}
            </p>
          ))}
          {!!result.segments?.length && (
            <details>
              <summary>分段测量（前100段）</summary>
              <table>
                <thead>
                  <tr>
                    <th>区间 / 秒</th>
                    <th>覆盖</th>
                    <th>绝对偏差 / cents</th>
                  </tr>
                </thead>
                <tbody>
                  {result.segments.slice(0, 100).map((segment, index) => (
                    <tr key={index}>
                      <td>
                        {segment.start_ms / 1000}–{segment.end_ms / 1000}
                      </td>
                      <td>{(segment.coverage * 100).toFixed(1)}%</td>
                      <td>{segment.mean_abs_cents ?? "—"}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </details>
          )}
        </>
      )}
      {!!result.citations?.length && <Evidence items={result.citations} />}
      {!!result.evidence_preview?.length && (
        <Evidence items={result.evidence_preview} preview />
      )}
      {result.plan_id && (
        <p className="notice">计划已保存，可在「练习规划」中编辑并记录反馈。</p>
      )}
    </div>
  );
}

function App() {
  const [selected, setSelected] = useState<Agent>("knowledge");
  const [identity, setIdentity] = useState(
    sessionStorage.getItem("dv.identity") || "local|learner",
  );
  const [identityDraft, setIdentityDraft] = useState(identity);
  const [connected, setConnected] = useState(false);
  const [question, setQuestion] = useState("");
  const [jobs, setJobs] = useState<Job[]>([]);
  const [current, setCurrent] = useState<Job | null>(null);
  const [plans, setPlans] = useState<Plan[]>([]);
  const [reports, setReports] = useState<Report[]>([]);
  const [practice, setPractice] = useState<Practice[]>([]);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [busy, setBusy] = useState(false);
  const [goal, setGoal] = useState("foundation");
  const [minutes, setMinutes] = useState(10);
  const [days, setDays] = useState(3);
  const [reportId, setReportId] = useState("");
  const [discomfort, setDiscomfort] = useState(false);
  const [upload, setUpload] = useState<{
    id: string;
    filename: string;
    status: string;
  } | null>(null);
  const [edit, setEdit] = useState<Plan | null>(null);
  const [feedback, setFeedback] = useState({
    seconds: 300,
    difficulty: 2,
    discomfort: false,
    note: "",
  });
  const [capabilities, setCapabilities] = useState<
    Record<string, { status: string; reason?: string }>
  >({});
  const epoch = useRef(0);
  const submitKey = useRef<{ signature: string; key: string } | null>(null);
  const feedbackKey = useRef<{ signature: string; key: string } | null>(null);
  const active = agents.find((agent) => agent.id === selected)!;

  const refresh = useCallback(
    async (signal?: AbortSignal) => {
      const values = await Promise.all([
        request<Job[]>(identity, "/v1/jobs", { signal }),
        request<Plan[]>(identity, "/v1/plans", { signal }),
        request<Report[]>(identity, "/v1/reports", { signal }),
        request<Practice[]>(identity, "/v1/practice", { signal }),
      ]);
      if (signal?.aborted) return;
      setJobs(values[0]);
      setPlans(values[1]);
      setReports(values[2]);
      setPractice(values[3]);
      setConnected(true);
    },
    [identity],
  );

  useEffect(() => {
    const controller = new AbortController();
    epoch.current += 1;
    setCurrent(null);
    setJobs([]);
    setPlans([]);
    setReports([]);
    setPractice([]);
    setEdit(null);
    setUpload(null);
    setError("");
    setNotice("");
    setReportId("");
    setQuestion("");
    submitKey.current = null;
    feedbackKey.current = null;
    void refresh(controller.signal).catch((error) => {
      if (!controller.signal.aborted) {
        setConnected(false);
        setError(error.message);
      }
    });
    void Promise.all(
      agents.map(
        async (agent) =>
          [
            agent.id,
            await request(identity, `/v1/agents/${agent.id}/capabilities`, {
              signal: controller.signal,
            }),
          ] as const,
      ),
    )
      .then((values) => {
        if (!controller.signal.aborted)
          setCapabilities(Object.fromEntries(values) as typeof capabilities);
      })
      .catch(() => {});
    return () => controller.abort();
  }, [identity, refresh]);

  useEffect(() => {
    if (!current || done(current)) return;
    const controller = new AbortController();
    let timeout: ReturnType<typeof setTimeout>;
    const poll = async () => {
      try {
        const updated = await request<Job>(identity, `/v1/jobs/${current.id}`, {
          signal: controller.signal,
        });
        if (controller.signal.aborted) return;
        setConnected(true);
        if (done(updated)) await refresh(controller.signal);
        else timeout = setTimeout(poll, 1500);
        if (!controller.signal.aborted) setCurrent(updated);
      } catch (error) {
        if (!controller.signal.aborted) {
          setError((error as Error).message);
          timeout = setTimeout(poll, 4000);
        }
      }
    };
    timeout = setTimeout(poll, 500);
    return () => {
      controller.abort();
      clearTimeout(timeout);
    };
  }, [current?.id, current?.status, identity, refresh]);

  async function perform(action: () => Promise<void>) {
    setBusy(true);
    setError("");
    setNotice("");
    try {
      await action();
    } catch (error) {
      setError((error as Error).message);
    } finally {
      setBusy(false);
    }
  }

  async function submit(automatic = false) {
    const text =
      question.trim() ||
      (selected === "planning"
        ? "制定练习计划"
        : selected === "singing"
          ? "查看歌声生成能力"
          : "");
    if (!text) return;
    const body = {
      text,
      action: automatic
        ? undefined
        : {
            knowledge: "knowledge.ask",
            planning: "planning.create",
            assessment: "assessment.submit",
            singing: "singing.generate",
          }[selected],
      input:
        selected === "planning" && !automatic
          ? {
              goal,
              minutes,
              days,
              discomfort,
              ...(reportId ? { report_id: reportId } : {}),
            }
          : {},
    };
    const signature = JSON.stringify(body);
    if (submitKey.current?.signature !== signature)
      submitKey.current = { signature, key: crypto.randomUUID() };
    const response = await request<{
      status: string;
      message?: string;
      job?: Job;
      agent?: Agent;
    }>(identity, "/v1/assistant", {
      method: "POST",
      body: JSON.stringify({ ...body, idempotency_key: submitKey.current.key }),
    });
    submitKey.current = null;
    if (response.job) {
      setCurrent(response.job);
      setSelected(response.agent!);
      await refresh();
    } else setNotice(response.message ?? response.status);
  }

  async function uploadFile(file: File) {
    if (file.size === 0 || file.size > 104857600)
      throw new Error("请选择 1 字节至 100 MiB 的音频");
    const ext = file.name.split(".").pop()?.toLowerCase() ?? "";
    const mime = file.type.startsWith("audio/")
      ? file.type
      : (
          {
            wav: "audio/wav",
            flac: "audio/flac",
            mp3: "audio/mpeg",
            m4a: "audio/mp4",
            ogg: "audio/ogg",
          } as Record<string, string>
        )[ext];
    if (!mime) throw new Error("无法识别音频格式");
    const scope = epoch.current;
    const bytes = await file.arrayBuffer();
    const hash = [
      ...new Uint8Array(await crypto.subtle.digest("SHA-256", bytes)),
    ]
      .map((value) => value.toString(16).padStart(2, "0"))
      .join("");
    const result = await request<{
      asset_id: string;
      upload_url: string;
      required_headers: Record<string, string>;
    }>(identity, "/v1/uploads", {
      method: "POST",
      body: JSON.stringify({
        filename: file.name,
        content_type: mime,
        size_bytes: file.size,
        sha256: hash,
        purpose: "assessment",
      }),
    });
    const response = await fetch(result.upload_url, {
      method: "PUT",
      headers: result.required_headers,
      body: bytes,
    });
    if (!response.ok)
      throw new Error(`对象上传失败 ${response.status}，请重新申请上传`);
    if (scope !== epoch.current) return;
    setUpload({ id: result.asset_id, filename: file.name, status: "pending" });
    setNotice(
      "文件已传至私有对象存储。按当前上传范围，资产仍待校验，暂不能提交测评。",
    );
  }

  async function savePlan() {
    if (!edit) return;
    const updated = await request<Plan>(identity, `/v1/plans/${edit.id}`, {
      method: "PATCH",
      body: JSON.stringify({
        version: edit.version,
        goal: edit.payload.goal,
        days: edit.payload.days,
        exercises: edit.payload.exercises,
      }),
    });
    setEdit(updated);
    await refresh();
    setNotice("计划已保存为新版本。");
  }

  async function saveFeedback() {
    if (!edit) return;
    const body = {
      plan_version: edit.version,
      duration_seconds: feedback.seconds,
      difficulty: feedback.difficulty,
      discomfort: feedback.discomfort,
      note: feedback.note,
    };
    const signature = JSON.stringify({ id: edit.id, ...body });
    if (feedbackKey.current?.signature !== signature)
      feedbackKey.current = { signature, key: crypto.randomUUID() };
    await request(identity, `/v1/plans/${edit.id}/practice`, {
      method: "POST",
      body: JSON.stringify({
        ...body,
        idempotency_key: feedbackKey.current.key,
      }),
    });
    feedbackKey.current = null;
    await refresh();
    setNotice("实际练习反馈已记录。");
  }

  return (
    <main className="shell">
      <header className="topbar">
        <a className="brand" href="#">
          <Mic2 size={24} />
          <span>
            DeepVocal<span className="brand-period">.</span>
          </span>
        </a>
        <nav aria-label="主导航">
          <a href="#workbench">工作台</a>
          <a href="#task-history">任务记录</a>
          <button
            onClick={() =>
              document
                .getElementById("workbench")
                ?.scrollIntoView({ behavior: "smooth" })
            }
          >
            开始练声 <span>↗</span>
          </button>
        </nav>
      </header>
      <section className="hero">
        <div className="hero-art" aria-hidden="true">
          <div className="voice-orb orb-blue">
            <svg viewBox="0 0 80 70">
              <path d="M19 33q8-12 15 0m12 0q8-12 15 0" />
              <path d="M32 47q9 8 18-3" />
            </svg>
          </div>
          <div className="voice-orb orb-yellow">
            <Music2 />
          </div>
          <div className="voice-orb orb-purple">
            <svg viewBox="0 0 80 70">
              <circle cx="28" cy="30" r="3" />
              <circle cx="52" cy="30" r="3" />
              <path d="M27 44q14 14 27-1" />
            </svg>
          </div>
          <div className="voice-orb orb-green">
            <Activity />
          </div>
          <span className="spark spark-one">✦</span>
          <span className="spark spark-two">✦</span>
          <span className="spark spark-three">♪</span>
          <span className="spark spark-four">✦</span>
          <span className="tiny-dot dot-one" />
          <span className="tiny-dot dot-two" />
        </div>
        <div className="hero-copy">
          <p className="eyebrow">A LITTLE GUIDANCE. A LOT MORE YOU.</p>
          <h1>
            让每次练声，
            <br />
            都被认真听见。
          </h1>
          <p className="lead">
            一个问题，一段旋律，一点点进步。
            <br />
            你的四位 AI 练声搭档，把每次尝试连成自己的成长路径。
          </p>
          <div className="hero-actions">
            <button
              onClick={() => {
                setSelected("knowledge");
                document
                  .getElementById("workbench")
                  ?.scrollIntoView({ behavior: "smooth" });
              }}
            >
              进入练声工作台 <span>↗</span>
            </button>
            <a className="pill-secondary" href="#capability-note">
              认识你的 AI 搭档
            </a>
          </div>
          <p className="hero-caption">
            <span className={connected ? "dot" : "dot offline"} />
            {connected ? "本地服务已连接" : "等待本地服务连接"} ·
            研究资料与工程练习参考
          </p>
        </div>
      </section>
      <div className="identity-bar">
        <label>
          本地测试身份{" "}
          <input
            aria-label="本地测试身份"
            value={identityDraft}
            onChange={(event) => setIdentityDraft(event.target.value)}
            disabled={busy}
          />
        </label>
        <button
          className="secondary"
          disabled={busy || !/^[^|]+\|.+$/.test(identityDraft)}
          onClick={() => {
            sessionStorage.setItem("dv.identity", identityDraft.trim());
            setIdentity(identityDraft.trim());
          }}
        >
          切换身份
        </button>
        <small>仅供本机测试，切换后展示该身份的数据。</small>
      </div>
      <section id="workbench" className="workspace">
        <div className="workspace-chrome">
          <span className="window-dots">
            <i />
            <i />
            <i />
          </span>
          <span>DeepVocal Workspace</span>
          <span>
            <Brain size={13} /> Supervisor
          </span>
        </div>
        <aside className="sidebar">
          <p className="section-label">你的 AI 搭档</p>
          {agents.map(({ id, label, icon: Icon, detail }) => (
            <button
              key={id}
              className={selected === id ? "agent active" : "agent"}
              onClick={() => setSelected(id)}
            >
              <Icon size={19} />
              <span>
                <b>{label}</b>
                <small>{detail}</small>
              </span>
            </button>
          ))}
          <div id="capability-note" className="sidebar-note">
            <span className="dot" /> 一次只做一件对的事
            <br />
            <small>
              明确意图，再交给对应 Agent。
              <br />
              当前内容不替代专业声乐教学。
            </small>
          </div>
        </aside>
        <section className="panel">
          <div className="panel-head">
            <div>
              <p className="eyebrow">{selected.toUpperCase()} AGENT</p>
              <h2>{active.label}</h2>
            </div>
            <active.icon size={25} />
          </div>
          <div className="panel-content">
            {error && (
              <div role="alert" className="error">
                {error}
                <button
                  className="text-button"
                  onClick={() => void perform(() => refresh())}
                >
                  重新连接
                </button>
              </div>
            )}
            {notice && (
              <p role="status" className="notice">
                {notice}
              </p>
            )}
            {selected === "knowledge" && (
              <p className="intro">
                提出声乐概念或练习问题。回答须有中文资料支撑；未配置模型时，可查看检索资料预览。
              </p>
            )}
            {selected === "assessment" && (
              <>
                <p className="intro">
                  上传本人单人清唱。无参考自由演唱只提供技术测量，有参考曲目与明确长音任务可按规则计算练习参考分。
                </p>
                <div className="upload-box">
                  <UploadCloud size={32} />
                  <strong>选择一段清唱录音</strong>
                  <small>音频最大 100 MiB · 当前仅完成文件上传</small>
                  <input
                    aria-label="选择清唱录音"
                    type="file"
                    accept="audio/*,.wav,.flac,.mp3,.m4a"
                    disabled={busy}
                    onChange={(event) => {
                      const file = event.target.files?.[0];
                      if (file) void perform(() => uploadFile(file));
                      event.target.value = "";
                    }}
                  />
                </div>
                {upload && (
                  <p className="notice">
                    {upload.filename} · 待校验（{upload.status}）<br />
                    <small>资产 {upload.id}</small>
                  </p>
                )}
                <p className="notice">
                  当前采用仅预签名上传流程，尚未开放上传完成校验。录音不会自动变为可测评状态。
                </p>
                <button disabled title="需完成上传校验后开放">
                  提交录音测评 · 待开放
                </button>
                {!!reports.length && (
                  <>
                    <h3>我的测评报告</h3>
                    {reports.map((report) => (
                      <details key={report.id}>
                        <summary>
                          {new Date(report.created_at).toLocaleString()} ·{" "}
                          {report.payload.quality}
                        </summary>
                        <ResultView result={report.payload} />
                        <button
                          className="secondary"
                          onClick={() => {
                            setReportId(report.id);
                            setSelected("planning");
                          }}
                        >
                          以此报告制定计划
                        </button>
                      </details>
                    ))}
                  </>
                )}
              </>
            )}
            {selected === "planning" && (
              <>
                <p className="intro">
                  选择练习目标与可用时间，生成含休息的可编辑模板。提供真实反馈，逐步积累练习记录。
                </p>
                <div className="form-grid">
                  <label>
                    练习目标
                    <select
                      aria-label="练习目标"
                      value={goal}
                      onChange={(event) => setGoal(event.target.value)}
                    >
                      {Object.entries(goals).map(([id, label]) => (
                        <option key={id} value={id}>
                          {label}
                        </option>
                      ))}
                    </select>
                  </label>
                  <label>
                    每天分钟数
                    <input
                      aria-label="每天分钟数"
                      type="number"
                      min="5"
                      max="30"
                      value={minutes}
                      onChange={(event) =>
                        setMinutes(Number(event.target.value))
                      }
                    />
                  </label>
                  <label>
                    计划天数
                    <input
                      aria-label="计划天数"
                      type="number"
                      min="1"
                      max="7"
                      value={days}
                      onChange={(event) => setDays(Number(event.target.value))}
                    />
                  </label>
                  <label>
                    参考本人报告
                    <select
                      value={reportId}
                      onChange={(event) => setReportId(event.target.value)}
                    >
                      <option value="">无 · 基础模板</option>
                      {reports.map((report) => (
                        <option key={report.id} value={report.id}>
                          {new Date(report.created_at).toLocaleDateString()} ·{" "}
                          {report.id.slice(0, 8)}
                        </option>
                      ))}
                    </select>
                  </label>
                </div>
                <label className="check">
                  <input
                    type="checkbox"
                    checked={discomfort}
                    onChange={(event) => setDiscomfort(event.target.checked)}
                  />
                  目前有疼痛、沙哑或明显不适
                </label>
              </>
            )}
            {selected === "singing" && (
              <div className="feature-note">
                <Music2 size={38} />
                <h3>歌声生成 · 接口预留</h3>
                <p>
                  待接入已授权的外部服务后，支持个性化参考歌声。当前不会生成音频、提交付费生成任务或保存虚构结果。
                </p>
                <small>{capabilities.singing?.reason}</small>
              </div>
            )}
            {selected !== "assessment" && (
              <div className="composer">
                <label htmlFor="question">
                  {selected === "planning"
                    ? "补充练习目标（可选）"
                    : "你的问题"}
                </label>
                <textarea
                  id="question"
                  rows={3}
                  placeholder={
                    selected === "knowledge"
                      ? "例如：什么是音准？如何理解清唱测量中的音高偏差？"
                      : "描述你的目标或问题…"
                  }
                  value={question}
                  onChange={(event) => setQuestion(event.target.value)}
                />
                <div className="actions">
                  <button
                    disabled={
                      busy || (selected === "knowledge" && !question.trim())
                    }
                    onClick={() => void perform(() => submit())}
                  >
                    {busy
                      ? "提交中…"
                      : selected === "planning"
                        ? "生成练习计划"
                        : selected === "singing"
                          ? "查看接入状态"
                          : "提交问题"}
                  </button>
                  <button
                    className="secondary"
                    disabled={busy || !question.trim()}
                    onClick={() => void perform(() => submit(true))}
                  >
                    自动识别意图
                  </button>
                </div>
              </div>
            )}
            {current && (
              <section aria-live="polite" className="current-job">
                <div className="job-heading">
                  <h3>
                    {agents.find((agent) => agent.id === current.kind)?.label} ·{" "}
                    {human(current.status)}
                  </h3>
                  {!done(current) && (
                    <button
                      className="secondary"
                      disabled={busy}
                      onClick={() =>
                        void perform(async () => {
                          setCurrent(
                            await request<Job>(
                              identity,
                              `/v1/jobs/${current.id}/cancel`,
                              { method: "POST" },
                            ),
                          );
                          await refresh();
                        })
                      }
                    >
                      取消任务
                    </button>
                  )}
                </div>
                <small className="muted">
                  任务 {current.id.slice(0, 8)} · 尝试 {current.attempt} · 追踪{" "}
                  {current.trace_id.slice(0, 8)}
                </small>
                {current.status === "QUEUED" && (
                  <p className="muted">
                    等待后台任务处理。如长时间未开始，请检查 worker 是否运行。
                  </p>
                )}
                {current.error_code && (
                  <p className="error">{current.error_code}</p>
                )}
                {current.result && <ResultView result={current.result} />}
              </section>
            )}
            {selected === "planning" && (
              <section>
                <h3>
                  我的计划 <small>{plans.length}</small>
                </h3>
                {!plans.length && (
                  <p className="muted">
                    还没有练习计划，先从一个舒适的小目标开始。
                  </p>
                )}
                <div className="plan-list">
                  {plans.map((plan) => (
                    <button
                      className="secondary"
                      key={plan.id}
                      onClick={() => {
                        setEdit(structuredClone(plan));
                        feedbackKey.current = null;
                      }}
                    >
                      {goals[plan.payload.goal] ?? plan.payload.goal} ·{" "}
                      {plan.payload.minutes}分钟 × {plan.payload.days}天 · v
                      {plan.version}
                    </button>
                  ))}
                </div>
                {edit && (
                  <div className="plan-editor">
                    <div className="job-heading">
                      <h4>编辑计划 · v{edit.version}</h4>
                      <button
                        className="text-button"
                        onClick={() => setEdit(null)}
                      >
                        收起
                      </button>
                    </div>
                    <p className="notice">{edit.payload.notice}</p>
                    <label>
                      目标
                      <select
                        aria-label="编辑计划目标"
                        value={edit.payload.goal}
                        onChange={(event) =>
                          setEdit({
                            ...edit,
                            payload: {
                              ...edit.payload,
                              goal: event.target.value,
                            },
                          })
                        }
                      >
                        {Object.entries(goals).map(([id, label]) => (
                          <option key={id} value={id}>
                            {label}
                          </option>
                        ))}
                      </select>
                    </label>
                    {edit.payload.exercises.map((exercise, index) => (
                      <div className="exercise" key={index}>
                        <div>
                          <strong>
                            {index + 1}. {exercise.title}
                          </strong>
                          <p>{exercise.instruction}</p>
                        </div>
                        <label>
                          秒
                          <input
                            aria-label={`${exercise.title}秒数`}
                            type="number"
                            min="30"
                            max="600"
                            value={exercise.seconds}
                            onChange={(event) =>
                              setEdit({
                                ...edit,
                                payload: {
                                  ...edit.payload,
                                  exercises: edit.payload.exercises.map(
                                    (item, i) =>
                                      i === index
                                        ? {
                                            ...item,
                                            seconds: Number(event.target.value),
                                          }
                                        : item,
                                  ),
                                },
                              })
                            }
                          />
                        </label>
                      </div>
                    ))}
                    <p className="notice">{edit.payload.stop_rule}</p>
                    <button
                      disabled={busy}
                      onClick={() => void perform(savePlan)}
                    >
                      保存新版本
                    </button>
                    <h4>记录这次实际练习</h4>
                    <div className="form-grid">
                      <label>
                        实际秒数
                        <input
                          aria-label="实际秒数"
                          type="number"
                          min="0"
                          max="1800"
                          value={feedback.seconds}
                          onChange={(event) =>
                            setFeedback({
                              ...feedback,
                              seconds: Number(event.target.value),
                            })
                          }
                        />
                      </label>
                      <label>
                        主观难度
                        <select
                          value={feedback.difficulty}
                          onChange={(event) =>
                            setFeedback({
                              ...feedback,
                              difficulty: Number(event.target.value),
                            })
                          }
                        >
                          {[1, 2, 3, 4, 5].map((value) => (
                            <option key={value} value={value}>
                              {value} / 5
                            </option>
                          ))}
                        </select>
                      </label>
                    </div>
                    <label className="check">
                      <input
                        type="checkbox"
                        checked={feedback.discomfort}
                        onChange={(event) =>
                          setFeedback({
                            ...feedback,
                            discomfort: event.target.checked,
                          })
                        }
                      />
                      练习中出现不适
                    </label>
                    <label>
                      备注
                      <textarea
                        aria-label="练习备注"
                        value={feedback.note}
                        maxLength={1000}
                        onChange={(event) =>
                          setFeedback({ ...feedback, note: event.target.value })
                        }
                      />
                    </label>
                    <button
                      disabled={busy}
                      onClick={() => void perform(saveFeedback)}
                    >
                      保存练习反馈
                    </button>
                  </div>
                )}
                {!!practice.length && (
                  <details>
                    <summary>实际练习记录 · {practice.length}</summary>
                    {practice.map((item) => (
                      <p key={item.id}>
                        {new Date(item.created_at).toLocaleString()} ·{" "}
                        {item.payload.duration_seconds}秒 · 难度
                        {item.payload.difficulty}/5 ·{" "}
                        {item.payload.discomfort
                          ? "有不适，请停止练习"
                          : "未报告不适"}
                        <br />
                        {item.payload.note}
                      </p>
                    ))}
                  </details>
                )}
              </section>
            )}
          </div>
        </section>
      </section>
      <section id="task-history" className="history panel">
        <div className="panel-head">
          <h2>
            <Clock3 size={20} />
            任务记录
          </h2>
          <button
            className="text-button"
            disabled={busy}
            onClick={() => void perform(() => refresh())}
          >
            刷新
          </button>
        </div>
        <div className="panel-content">
          {!jobs.length ? (
            <p className="muted">
              本身份还没有任务。任务状态与结果会保存在这里。
            </p>
          ) : (
            <div className="job-list">
              {jobs.map((job) => (
                <button
                  className="history-item"
                  key={job.id}
                  onClick={() => {
                    setCurrent(job);
                    if (agents.some((agent) => agent.id === job.kind))
                      setSelected(job.kind);
                  }}
                >
                  <span>
                    {agents.find((agent) => agent.id === job.kind)?.label ??
                      job.kind}
                    <small>
                      {new Date(job.created_at).toLocaleString()} ·{" "}
                      {job.id.slice(0, 8)}
                    </small>
                  </span>
                  <span
                    className={`badge ${job.status === "FAILED" ? "bad" : ""}`}
                  >
                    {human(job.status)}
                    {job.result?.status ? ` · ${human(job.result.status)}` : ""}
                  </span>
                </button>
              ))}
            </div>
          )}
        </div>
      </section>
      <Operations
        key={`operations-${identity}`}
        identity={identity}
        jobRevision={jobs
          .map((job) => `${job.id}:${job.status}:${job.attempt}`)
          .join(",")}
      />
      <Governance key={identity} identity={identity} completedJobs={jobs} />
      <footer>
        DeepVocal · 本地研究工作台 · 资料与工程参考分均保留可用条件
      </footer>
    </main>
  );
}

createRoot(document.getElementById("root")!).render(<App />);
