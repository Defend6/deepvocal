import { useState } from "react";
import { Activity, BookOpen, Brain, Mic2, Music2, Route, Settings2 } from "lucide-react";
import "./styles.css";

const agents = [
  { id: "knowledge", label: "声乐理论答疑", icon: BookOpen, state: "待接入知识库" },
  { id: "assessment", label: "录音诊断与全曲测评", icon: Activity, state: "框架已就绪" },
  { id: "planning", label: "练习规划", icon: Route, state: "框架已就绪" },
  { id: "singing", label: "个性化歌声生成", icon: Music2, state: "接口预留" },
];

export default function App() {
  const [selected, setSelected] = useState("assessment");
  const active = agents.find((agent) => agent.id === selected) ?? agents[0];
  return (
    <main className="shell">
      <header className="topbar">
        <div className="brand"><Mic2 size={20} /><span>声乐导师 DeepVocal</span></div>
        <div className="mode"><span className="dot" /> MOCK 开发模式</div>
      </header>
      <section className="hero">
        <div>
          <p className="eyebrow">LANGGRAPH MULTI-AGENT WORKSPACE</p>
          <h1>把一次练声，变成一条可追踪的成长路径。</h1>
          <p className="lead">统一编排理论答疑、录音诊断、全曲测评、练习规划与歌声生成能力。当前已完成路由骨架，真实模型与知识库将在适配接口接入后启用。</p>
        </div>
        <div className="status-card"><Brain size={22} /><div><strong>SupervisorGraph</strong><span>规则优先 · 低置信度先澄清</span></div></div>
      </section>
      <section className="workspace">
        <aside className="sidebar">
          <div className="section-label">功能 Agent</div>
          {agents.map(({ id, label, icon: Icon, state }) => (
            <button className={selected === id ? "agent active" : "agent"} key={id} onClick={() => setSelected(id)}>
              <Icon size={18} /><span><b>{label}</b><small>{state}</small></span>
            </button>
          ))}
          <button className="settings"><Settings2 size={17} />系统配置</button>
        </aside>
        <section className="panel">
          <div className="panel-head"><div><p className="eyebrow">CURRENT AGENT</p><h2>{active.label}</h2></div><span className="badge">{active.state}</span></div>
          <div className="empty-state"><div className="icon-wrap"><active.icon size={28} /></div><h3>从一个明确动作开始</h3><p>输入问题、上传清唱录音或选择练习目标，系统会先进行结构化路由，再交给对应 Agent 处理。</p><div className="actions"><button>开始提问</button><button className="secondary">上传录音</button></div></div>
        </section>
      </section>
    </main>
  );
}
