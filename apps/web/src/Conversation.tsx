import { useEffect, useRef, useState } from "react";
import { ArrowDown, Check, Copy, MessageCircle, Plus, RotateCcw, Sparkles, X } from "lucide-react";
import type { ConversationSummary, ConversationTurn, Job, Plan } from "./types";
import { done, jobIntroduction, jobLabel } from "./presentation";
import { ResultView } from "./ResultView";

type Props = {
  identity: string;
  turns: ConversationTurn[];
  jobs: Job[];
  plans: Plan[];
  busy: boolean;
  sending: string;
  id?: string;
  history: ConversationSummary[];
  onNew: () => void;
  onHistory: () => void;
  onSelect: (id: string) => void;
  onDelete: () => void;
  onRetry: (text: string) => void;
  onCancel: (job: Job) => void;
  onOpenPlan: (plan: Plan) => void;
};

export function Conversation(props: Props) {
  const { identity, turns, jobs, plans, busy, sending } = props;
  const [showHistory, setShowHistory] = useState(false);
  const [copied, setCopied] = useState<number | null>(null);
  const [copyError, setCopyError] = useState("");
  const viewport = useRef<HTMLDivElement>(null);
  const follow = useRef(true);
  const [away, setAway] = useState(false);
  const signature = turns.map(t => {
    const job = jobs.find(j => j.id === t.response.job?.id) ?? t.response.job;
    return `${job?.id}:${job?.status}:${job?.result?.status}`;
  }).join("|");
  useEffect(() => {
    follow.current = true;
    setAway(false);
    setShowHistory(false);
  }, [identity, props.id]);
  useEffect(() => {
    if (follow.current && viewport.current) viewport.current.scrollTop = viewport.current.scrollHeight;
  }, [turns.length, signature, sending, identity, props.id, plans]);

  return <section className="conversation-panel" aria-label="与 DeepVocal 对话">
    <div className="conversation-toolbar">
      <div className="conversation-title"><span className="assistant-avatar"><Sparkles size={18} /></span>
        <div><strong>DeepVocal 助手</strong><small>提问、上传录音，一起找到下一步</small></div>
      </div>
      <div className="conversation-tools">
        <button className="text-button" disabled={busy} onClick={() => {
          setShowHistory(value => !value); props.onHistory();
        }} aria-expanded={showHistory}><MessageCircle size={15} /> 历史对话</button>
        <button className="secondary" disabled={busy} onClick={props.onNew}><Plus size={15} /> 新对话</button>
      </div>
    </div>
    {showHistory && <div className="conversation-history">
      <div className="job-heading"><strong>历史对话</strong><button className="text-button" aria-label="收起历史对话" onClick={() => setShowHistory(false)}><X size={16} /></button></div>
      {!props.history.length && <p className="muted">暂无历史对话。</p>}
      {props.history.map(thread => <button key={thread.id} className="thread-item" disabled={busy}
        aria-current={thread.id === props.id ? "true" : undefined}
        onClick={() => { follow.current = true; props.onSelect(thread.id); setShowHistory(false); }}>
        <MessageCircle size={15} /><span>{thread.title}</span><span>→</span>
      </button>)}
      {props.id && <details className="delete-conversation"><summary>管理当前对话</summary>
        <p className="muted">删除后将无法恢复此对话。</p><button className="text-button" disabled={busy} onClick={props.onDelete}>确认删除当前对话</button>
      </details>}
    </div>}
    <div ref={viewport} className="conversation-feed" role="log" aria-label="对话消息" aria-live="polite"
      onScroll={() => {const el = viewport.current!; follow.current = el.scrollHeight - el.scrollTop - el.clientHeight < 90; setAway(!follow.current);}}>
      {!turns.length && !sending && <div className="conversation-welcome">
        <span className="welcome-symbol"><Sparkles size={28} /></span><h3>今天，从你的声音开始。</h3>
        <p>聊聊声乐问题，安排练习，或让我听一段录音。<br />不用选择处理方式，直接告诉我就好。</p>
      </div>}
      {turns.map((turn, i) => {
        const job = jobs.find(j => j.id === turn.response.job?.id) ?? turn.response.job;
        const introduction = job ? jobIntroduction(job) : turn.response.message;
        const negative = job && (job.status === "FAILED" || ["refused", "disabled", "unavailable"].includes(job.result?.status ?? ""));
        return <div className="conversation-turn" key={turn.id ?? `${i}:${turn.user_text}`} data-job-id={job?.id}>
          <div className="user-message"><span className="message-label">你</span><p>{turn.user_text}</p></div>
          <article className="assistant-message" aria-label="助手回复">
            <span className="assistant-avatar"><Sparkles size={16} /></span><div className="assistant-content">
              <div className="message-meta"><strong>DeepVocal</strong>{job && <span className={`message-state ${!done(job) ? "working" : ""}`}>{!done(job) && <i />}{jobLabel(job)}</span>}</div>
              {introduction && <p className={negative ? "answer-explanation" : "assistant-introduction"}>{introduction}</p>}
              {job?.result && <ResultView identity={identity} result={{...job.result, reason: undefined}}
                plan={plans.find(p => p.id === job.result?.plan_id)} onOpenPlan={props.onOpenPlan} />}
              <div className="message-actions">
                {job && !done(job) && <button className="text-button" disabled={busy} onClick={() => props.onCancel(job)}>停止处理</button>}
                {negative && <button className="text-button" disabled={busy} onClick={() => props.onRetry(turn.user_text)}><RotateCcw size={13} /> 编辑后重试</button>}
                {job?.result?.answer && <button className="text-button" onClick={() => {
                  void navigator.clipboard.writeText(job.result!.answer!).then(() => {setCopied(i); setCopyError("");})
                    .catch(() => setCopyError("无法自动复制，请选择回答文字后复制。"));
                }}>{copied === i ? <Check size={13} /> : <Copy size={13} />}{copied === i ? "已复制" : "复制回答"}</button>}
              </div>
            </div>
          </article>
        </div>;
      })}
      {sending && <div className="conversation-turn pending-turn"><div className="user-message"><span className="message-label">你</span><p>{sending}</p></div>
        <article className="assistant-message"><span className="assistant-avatar"><Sparkles size={16} /></span><p className="muted">正在接收你的消息…</p></article></div>}
    </div>
    {away && <button className="jump-latest secondary" onClick={() => {
      follow.current = true; setAway(false); if (viewport.current) viewport.current.scrollTop = viewport.current.scrollHeight;
    }}><ArrowDown size={14} /> 回到最新消息</button>}
    {copyError && <p role="status" className="muted">{copyError}</p>}
  </section>;
}
