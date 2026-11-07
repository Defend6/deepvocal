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
  Send,
  Paperclip,
  X,
  ChevronDown,
} from "lucide-react";
import { request, RequestError } from "./api";
import { human, done, jobLabel } from "./presentation";
import { ResultView } from "./ResultView";
import { Conversation } from "./Conversation";
import type { Agent, Job, Plan, Report, Practice, ConversationTurn, ConversationSummary } from "./types";
import { Governance } from "./Governance";
import { Operations } from "./Operations";
import "./styles.css";
import { authConfig, initializeAuth, login, logout } from "./auth";
import { Studio } from "./Studio";
import { Progress } from "./Progress";

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
    detail: "音高节奏 · 分段报告",
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
    detail: "你的旋律 · 个性演唱",
  },
];
const goals: Record<string, string> = {
  foundation: "基础练习",
  pitch: "参考音模唱",
  rhythm: "节奏短句",
  stability: "长音观察",
};
function App() {
  const [mode, setMode] = useState<"assistant" | "expert">(() =>
    localStorage.getItem("dv.mode") === "expert" ? "expert" : "assistant",
  );
  const intelligent = mode === "assistant";
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
  const [conversation, setConversation] = useState<{id: string; revision: number} | null>(null);
  const [turns, setTurns] = useState<ConversationTurn[]>([]);
  const [history, setHistory] = useState<ConversationSummary[]>([]);
  const [sending, setSending] = useState("");
  const [restoring, setRestoring] = useState(false);
  const [expandedJob, setExpandedJob] = useState<string | null>(null);
  const [attachmentsOpen, setAttachmentsOpen] = useState(false);
  const composerRef = useRef<HTMLTextAreaElement>(null);
  const submissionLock = useRef(false);
  const conversationStorage = `dv.conversation:${identity}`;
  const [devSettings, setDevSettings] = useState(false);
  const epoch = useRef(0);
  const submitKey = useRef<{ signature: string; key: string } | null>(null);
  const feedbackKey = useRef<{ signature: string; key: string } | null>(null);
  const active = agents.find((agent) => agent.id === selected)!;

  useEffect(() => {
    if (!upload || upload.status !== "verifying") return;
    const controller = new AbortController();
    let timer: ReturnType<typeof setTimeout>;
    const check = async () => {
      try {
        const asset = await request<{status: string}>(identity, `/v1/uploads/${upload.id}`, {signal: controller.signal});
        if (controller.signal.aborted) return;
        if (asset.status === "verifying") timer = setTimeout(check, 1500);
        else {
          setUpload({...upload, status: asset.status});
          setNotice(asset.status === "ready" ? "录音准备完成，可以开始分析。" : "录音检查未通过，请重新上传清晰的音频。");
        }
      } catch {
        if (!controller.signal.aborted) timer = setTimeout(check, 4000);
      }
    };
    void check();
    return () => {controller.abort(); clearTimeout(timer);};
  }, [upload?.id, upload?.status, identity]);

  const refresh = useCallback(
    async (signal?: AbortSignal) => {
      const scope = epoch.current;
      const values = await Promise.all([
        request<Job[]>(identity, "/v1/jobs", { signal }),
        request<Plan[]>(identity, "/v1/plans", { signal }),
        request<Report[]>(identity, "/v1/reports", { signal }),
        request<Practice[]>(identity, "/v1/practice", { signal }),
      ]);
      if (signal?.aborted || scope !== epoch.current) return;
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
    const restoreScope = epoch.current;
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
    setConversation(null);
    setTurns([]);
    setHistory([]);
    setExpandedJob(null);
    setSending("");
    setCapabilities({});
    const savedThread = sessionStorage.getItem(`dv.conversation:${identity}`);
    setRestoring(!!savedThread);
    if (savedThread) void request<{id: string; revision: number; turns: ConversationTurn[]}>(identity,
      `/v1/conversations/${savedThread}`, {signal: controller.signal}).then(thread => {
        if (controller.signal.aborted || restoreScope !== epoch.current) return;
        setConversation({id: thread.id, revision: thread.revision});
        setTurns(thread.turns);
      }).catch(error => {
        if (controller.signal.aborted || restoreScope !== epoch.current) return;
        if (error instanceof RequestError && error.status === 404) sessionStorage.removeItem(`dv.conversation:${identity}`);
        else setError("历史对话暂时加载失败，可通过「历史对话」重新打开。");
      }).finally(() => {
        if (!controller.signal.aborted && restoreScope === epoch.current) setRestoring(false);
      });
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

  const jobMap = new Map(jobs.map(job => [job.id, job]));
  const observed = [...turns.flatMap(turn => turn.response.job ? [turn.response.job] : []), ...(current ? [current] : [])];
  const pendingIds = [...new Set([...observed, ...jobs].filter(job => !done(jobMap.get(job.id) ?? job)).map(job => job.id))].sort().join(",");
  useEffect(() => {
    if (!pendingIds) return;
    const controller = new AbortController();
    let timeout: ReturnType<typeof setTimeout>;
    const scope = epoch.current;
    const poll = async () => {
      const ids = pendingIds.split(",");
      const results = await Promise.allSettled(ids.map(id =>
        request<Job>(identity, `/v1/jobs/${id}`, {signal: controller.signal})));
      if (controller.signal.aborted || scope !== epoch.current) return;
      const updates = new Map<string, Job | null>();
      results.forEach((result, i) => {
        if (result.status === "fulfilled") updates.set(result.value.id, result.value);
        else if (result.reason instanceof RequestError && result.reason.status === 404) updates.set(ids[i], null);
      });
      setJobs(previous => [...updates.values()].filter((job): job is Job => !!job)
        .concat(previous.filter(job => !updates.has(job.id))).sort((a, b) => b.created_at.localeCompare(a.created_at)));
      setCurrent(previous => previous && updates.has(previous.id) ? updates.get(previous.id) ?? null : previous);
      setTurns(previous => previous.map(turn => {
        const id = turn.response.job?.id;
        if (!id || !updates.has(id)) return turn;
        const job = updates.get(id);
        return {...turn, response: job ? {...turn.response, job} : {status: "unavailable", message: "此条消息的处理记录已不可用，你可以重新发送。"}};
      }));
      // A terminal update changes pendingIds and aborts this effect. Refresh related
      // plans/reports independently so that final artifacts are still loaded.
      if ([...updates.values()].some(job => job && done(job))) void refresh().catch(() => {});
      setConnected(results.some(result => result.status === "fulfilled"));
      timeout = setTimeout(poll, 1500);
    };
    timeout = setTimeout(poll, 400);
    return () => {controller.abort(); clearTimeout(timeout);};
  }, [pendingIds, identity, refresh, conversation?.id]);

  function newConversation() {
    epoch.current += 1;
    sessionStorage.removeItem(conversationStorage);
    setConversation(null); setTurns([]); setCurrent(null); setUpload(null); setQuestion("");
    setNotice(""); setError(""); submitKey.current = null;
    setRestoring(false);
  }

  async function openConversation(id: string) {
    const scope = ++epoch.current;
    const thread = await request<{id: string; revision: number; turns: ConversationTurn[]}>(identity, `/v1/conversations/${id}`);
    if (scope !== epoch.current) return;
    setConversation({id: thread.id, revision: thread.revision}); setTurns(thread.turns); setCurrent(null);
    setUpload(null); setQuestion(""); setError(""); setNotice("");
    sessionStorage.setItem(conversationStorage, thread.id);
    await refresh();
  }

  function openPlan(plan: Plan) {
    setEdit(structuredClone(plan)); feedbackKey.current = null;
    setTimeout(() => document.querySelector(".plan-editor")?.scrollIntoView({behavior: "smooth", block: "center"}), 50);
  }

  async function cancelJob(job: Job) {
    const updated = await request<Job>(identity, `/v1/jobs/${job.id}/cancel`, {method: "POST"});
    setCurrent(previous => previous?.id === updated.id ? updated : previous);
    setTurns(previous => previous.map(turn => turn.response.job?.id === updated.id
      ? {...turn, response: {...turn.response, job: updated}} : turn));
    await refresh();
  }

  function retryQuestion(text: string) {
    setQuestion(text); submitKey.current = null;
    composerRef.current?.focus();
    composerRef.current?.scrollIntoView({behavior: "smooth", block: "center"});
  }

  async function perform(action: () => Promise<void>) {
    if (submissionLock.current) return;
    submissionLock.current = true;
    setBusy(true);
    setError("");
    setNotice("");
    try {
      await action();
    } catch (error) {
      setError((error as Error).message);
    } finally {
      submissionLock.current = false;
      setBusy(false);
      setSending("");
    }
  }

  async function submit(automatic = false) {
    if (restoring) return;
    const scope = epoch.current;
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
      ...(automatic && upload ? { input_asset_id: upload.id } : {}),
    };
    const signature = JSON.stringify(body);
    if (submitKey.current?.signature !== signature)
      submitKey.current = { signature, key: crypto.randomUUID() };
    let thread = conversation;
    if (automatic && intelligent && !thread) {
      thread = await request<{id: string; revision: number}>(identity, "/v1/conversations", {
        method: "POST", body: JSON.stringify({title: text.slice(0, 60)})});
      if (scope !== epoch.current) return;
      setConversation(thread);
      sessionStorage.setItem(conversationStorage, thread.id);
    }
    if (automatic && intelligent) setSending(text);
    let response;
    try { response = await request<{
      status: string;
      message?: string;
      job?: Job;
      agent?: Agent;
      revision?: number;
      show_evidence?: boolean;
    }>(identity, automatic && intelligent && thread ? `/v1/conversations/${thread.id}/messages` : "/v1/assistant", {
      method: "POST",
      body: JSON.stringify(automatic && intelligent && thread
        ? {text, revision: thread.revision, input_asset_id: upload?.id, idempotency_key: submitKey.current.key}
        : { ...body, idempotency_key: submitKey.current.key }),
    }); } catch (error) {
      if (scope === epoch.current && thread && error instanceof RequestError && error.status === 409) {
        const latest = await request<{id: string; revision: number; turns: ConversationTurn[]}>(identity, `/v1/conversations/${thread.id}`);
        if (scope === epoch.current) {
          setConversation({id: latest.id, revision: latest.revision}); setTurns(latest.turns);
          submitKey.current = null;
        }
        throw new Error("对话已同步到最新内容，你输入的消息已保留，请再次发送。");
      }
      throw error;
    }
    if (scope !== epoch.current) return;
    setSending("");
    submitKey.current = null;
    if (thread && response.revision !== undefined) {
      setConversation({...thread, revision: response.revision});
      setTurns(previous => [...previous, {user_text: text, response}]);
      setQuestion("");
    }
    if (response.job) {
      setCurrent(response.job);
      if (!intelligent) setSelected(response.agent!);
      await refresh();
    } else {
      setCurrent(null);
      if (!intelligent) setNotice(response.message ?? response.status);
    }
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
    await request(identity, `/v1/uploads/${result.asset_id}/complete`, {method: "POST"});
    setUpload({ id: result.asset_id, filename: file.name, status: "verifying" });
    setNotice("录音已上传，正在检查，请稍候。 ");
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
    <main className={`shell ${intelligent ? "chat-mode" : "expert-mode"}`}>
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
            <a className="pill-secondary" href={intelligent ? "#workbench" : "#capability-note"}>
              认识你的 AI 搭档
            </a>
          </div>
          <p className="hero-caption">
            <span className={connected ? "dot" : "dot offline"} />
            {connected ? "已连接" : "正在连接服务"}
          </p>
        </div>
      </section>
      {authConfig.mode === "oidc" ? <div className="account-actions">
        <button className="secondary" onClick={() => void login()}>登录</button>
        <button className="text-button" onClick={() => void logout()}>退出登录</button>
      </div> : <details className="developer-settings" open={devSettings} onToggle={e => setDevSettings(e.currentTarget.open)}>
      <summary>开发设置</summary><div className="identity-bar">
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
      </div></details>}
      <section id="workbench" className={`workspace ${intelligent ? "assistant-workspace" : ""}`}>
        <div className="workspace-chrome">
          <div className="mode-switch" role="group" aria-label="工作模式">
            {([['assistant', '智能助手'], ['expert', '专业工作台']] as const).map(([value, label]) => (
              <button key={value} aria-pressed={mode === value} disabled={busy}
                onClick={() => {
                  setMode(value);
                  localStorage.setItem("dv.mode", value);
                  setNotice("");
                  setError("");
                }}>{label}</button>
            ))}
          </div>
          <span>DeepVocal Workspace</span>
          {!intelligent && <span>
            <Brain size={13} /> Supervisor
          </span>}
        </div>
        {!intelligent && <aside className="sidebar">
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
            <span className="dot" /> 多个需求，一起交给我
            <br />
            <small>
              明确意图，再交给对应 Agent。
              <br />
              当前内容不替代专业声乐教学。
            </small>
          </div>
        </aside>}
        <section className="panel">
          <div className="panel-head">
            <div>
              <p className="eyebrow">{intelligent ? "YOUR VOICE, YOUR PACE" : `${selected.toUpperCase()} AGENT`}</p>
              <h2>{intelligent ? "今天，想怎样练好声音？" : active.label}</h2>
            </div>
            {intelligent ? <Mic2 size={25} /> : <active.icon size={25} />}
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
            {intelligent && <>
              {restoring && <p role="status" className="muted">正在恢复上次对话…</p>}
              <Conversation identity={identity} turns={turns} jobs={jobs} plans={plans} busy={busy || restoring}
                sending={sending} id={conversation?.id} history={history}
                onNew={newConversation}
                onHistory={() => void perform(async () => {setHistory(await request<ConversationSummary[]>(identity, "/v1/conversations"));})}
                onSelect={id => void perform(() => openConversation(id))}
                onDelete={() => void perform(async () => {
                  if (conversation) await request(identity, `/v1/conversations/${conversation.id}`, {method: "DELETE"});
                  newConversation(); setHistory(await request<ConversationSummary[]>(identity, "/v1/conversations"));
                })}
                onRetry={retryQuestion} onCancel={job => void perform(() => cancelJob(job))} onOpenPlan={openPlan} />
              {!turns.length && <div className="assistant-examples">
                {['什么是胸声和头声？', '帮我制定音准练习计划，每天10分钟，共3天', '请分析我的录音有没有跑调'].map(text => (
                  <button className="secondary" key={text} disabled={busy} onClick={() => {setQuestion(text); composerRef.current?.focus();}}>{text}<span>↗</span></button>
                ))}
              </div>}
              <div className="composer chat-composer">
                <label className="sr-only" htmlFor="assistant-question">说说你的问题或目标</label>
                <textarea ref={composerRef} id="assistant-question" rows={3} maxLength={4000} value={question}
                  placeholder="问一个声乐问题，或说说你的练习目标…" disabled={busy || restoring}
                  onKeyDown={event => {
                    if (event.key === "Enter" && !event.shiftKey && !event.nativeEvent.isComposing) {
                      event.preventDefault(); if (question.trim() && !busy && !restoring) void perform(() => submit(true));
                    }
                  }}
                  onChange={event => setQuestion(event.target.value)} />
                {upload && <div className="attachment-chip"><Mic2 size={15} /><span>{upload.filename} · {upload.status === "ready" ? "准备完成" : upload.status === "verifying" ? "正在检查" : "请重新上传"}</span>
                  <button className="text-button" aria-label="移除附件" disabled={busy} onClick={() => setUpload(null)}><X size={14} /></button></div>}
                <div className="composer-footer">
                  <button className="text-button attachment-toggle" aria-expanded={attachmentsOpen} onClick={() => setAttachmentsOpen(value => !value)}><Paperclip size={17} /> 添加录音</button>
                  <span className="composer-hint">Enter 发送 · Shift + Enter 换行</span>
                  <button className="send-button" aria-label="发送" disabled={busy || restoring || !question.trim()} onClick={() => void perform(() => submit(true))}>{busy ? "发送中" : "发送"}<Send size={16} /></button>
                </div>
                <div className={`assistant-attachment ${attachmentsOpen ? "expanded" : "collapsed"}`}>
                  <label>附上清唱录音（可选）
                    <input aria-label="附上清唱录音" type="file" accept="audio/*,.wav,.flac,.mp3,.m4a" disabled={busy}
                      onChange={event => {
                        const file = event.target.files?.[0];
                        if (file) void perform(() => uploadFile(file));
                        event.target.value = "";
                      }} />
                  </label>
                  <small className="muted">支持 WAV、FLAC、MP3、M4A，最大 100 MiB。上传后会自动检查。</small>
                </div>
              </div>
              <p className="composer-note">练声建议仅作学习参考。出现疼痛或明显不适，请停止练习。</p>
            </>}
            {!intelligent && selected === "knowledge" && (
              <p className="intro">
                提出声乐概念或练习问题，结合相关资料理解发声与练习方法。
              </p>
            )}
            {!intelligent && selected === "assessment" && (
              <>
                <p className="intro">
                  上传本人单人清唱。无参考自由演唱只提供技术测量，有参考曲目与明确长音任务可按规则计算练习参考分。
                </p>
                <div className="upload-box">
                  <UploadCloud size={32} />
                  <strong>选择一段清唱录音</strong>
                  <small>音频最大 100 MiB · 上传后自动检查</small>
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
                    {upload.filename} · {upload.status === "ready" ? "可以开始分析" : upload.status === "verifying" ? "正在检查" : "请重新上传"}
                  </p>
                )}
                <Studio identity={identity} assetId={upload?.status === "ready" ? upload.id : null}
                  kind="assessment" onJob={async job => {setCurrent(job as Job); await refresh();}} />
                {!!reports.length && (
                  <>
                    <h3>我的测评报告</h3>
                    {reports.map((report) => (
                      <details key={report.id}>
                        <summary>
                          {new Date(report.created_at).toLocaleString()} ·{" "}
                          {report.payload.quality}
                        </summary>
                        <ResultView identity={identity} result={report.payload} />
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
            {!intelligent && selected === "planning" && (
              <>
                <p className="intro">
                选择练习目标与可用时间，结合测评报告和练习反馈，调整适合自己的练习安排。
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
            {!intelligent && selected === "singing" && (
              <div className="feature-note">
                <Music2 size={38} />
                <h3>个性化歌声</h3>
                <p>使用自己的声音参考和原创歌词，创作一段演唱。</p>
                <Studio identity={identity} assetId={upload?.status === "ready" ? upload.id : null}
                  kind="singing" onUpload={uploadFile} onJob={async job => {setCurrent(job as Job); await refresh();}} />
              </div>
            )}
            {!intelligent && selected !== "assessment" && (
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
            {current && !intelligent && (
              <section aria-live="polite" className="current-job">
                <div className="job-heading">
                  <h3>
                    {intelligent ? "本次处理" : agents.find((agent) => agent.id === current.kind)?.label} ·{" "}
                    {jobLabel(current)}
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
                {!intelligent && <small className="muted">
                  任务 {current.id.slice(0, 8)} · 尝试 {current.attempt} · 追踪{" "}
                  {current.trace_id.slice(0, 8)}
                </small>}
                {current.status === "QUEUED" && (
                  <p className="muted">
                    {intelligent ? "已收到，正在等待处理。你可以在任务记录中再次查看。" : "等待后台任务处理。如长时间未开始，请检查 worker 是否运行。"}
                  </p>
                )}
                {current.error_code && (
                  <p className="error">{current.error_code}</p>
                )}
                {current.result && <ResultView identity={identity} result={current.result} plans={plans} jobStatus={current.status} showEvidence={current.show_evidence} plan={plans.find(p => p.id === current.result?.plan_id)} onOpenPlan={openPlan} />}
              </section>
            )}
            {(intelligent || selected === "planning") && (
              <section className="plans-section">
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
                    <button className="secondary" disabled={busy} onClick={() => void perform(async () => {
                      const result = await request<Plan & {status?: string; message?: string}>(identity,
                        `/v1/plans/${edit.id}/adapt`, {method: "POST"});
                      if (result.status === "refused") {setNotice(result.message || "请先休息，恢复后再练习。"); return;}
                      setEdit(result); await refresh(); setNotice("已根据本版练习反馈调整计划。");
                    })}>根据反馈调整计划</button>
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
                <div className={`history-entry ${expandedJob === job.id ? "expanded" : ""}`} key={job.id}>
                  <button className="history-item" aria-expanded={expandedJob === job.id}
                    aria-controls={`history-result-${job.id}`}
                    onClick={() => {setExpandedJob(previous => previous === job.id ? null : job.id);}}>
                    <span>{job.result?.steps ? "综合处理" : agents.find(agent => agent.id === job.kind)?.label ?? "处理记录"}
                      <small>{new Date(job.created_at).toLocaleString()}</small></span>
                    <span className="history-state"><span className={`badge ${job.status === "FAILED" ? "bad" : ""}`}>{jobLabel(job)}</span><ChevronDown size={16} /></span>
                  </button>
                  {expandedJob === job.id && <div id={`history-result-${job.id}`} className="history-result" role="region" aria-label="任务结果">
                    {!job.result && <p>{job.status === "FAILED" ? "这次处理未完成，请稍后重试。" : job.status === "CANCELLED" ? "本次处理已取消。" : job.status === "SUCCEEDED" ? "本次处理已结束，但结果暂时无法读取，请刷新后重试。" : "正在处理，结果会自动更新。"}</p>}
                    {job.result && <ResultView identity={identity} result={job.result} plans={plans} jobStatus={job.status} showEvidence={job.show_evidence} plan={plans.find(p => p.id === job.result?.plan_id)} onOpenPlan={openPlan} />}
                    {!done(job) && <button className="secondary" disabled={busy} onClick={() => void perform(() => cancelJob(job))}>停止处理</button>}
                  </div>}
                </div>
              ))}
            </div>
          )}
        </div>
      </section>
      {!intelligent && <><Operations
        key={`operations-${identity}`}
        identity={identity}
        jobRevision={jobs
          .map((job) => `${job.id}:${job.status}:${job.attempt}`)
          .join(",")}
      />
      <Progress identity={identity} revision={practice.length + reports.length} />
      <Governance key={identity} identity={identity} completedJobs={jobs} /></>}
      <footer>DeepVocal · 让每一次练习都有方向</footer>
    </main>
  );
}

initializeAuth().then(() => createRoot(document.getElementById("root")!).render(<App />))
  .catch(() => { document.getElementById("root")!.textContent = "暂时无法连接服务，请稍后刷新。"; });
