import { useState } from "react";
import { request } from "./api";
import { human } from "./presentation";
import type { Citation, Result, Plan } from "./types";

function PitchChart({points, reference = []}: {points: {time: number; hz: number | null}[];
  reference?: {start: number; end: number; hz: number}[]}) {
  const voiced = points.filter(p => p.hz && p.hz > 0);
  if (!voiced.length) return <p>未检测到稳定音高，请尝试更清晰的录音。</p>;
  const maxTime = Math.max(1, points[points.length - 1].time, ...reference.map(p => p.end));
  const midi = (hz: number) => 69 + 12 * Math.log2(hz / 440);
  const values = [...voiced.map(p => midi(p.hz!)), ...reference.map(p => midi(p.hz))];
  const low = Math.min(...values) - 1, high = Math.max(...values) + 1;
  return <figure className="pitch-chart"><figcaption>演唱音高走势{reference.length > 0 && " · 紫色演唱 / 绿色参考"}</figcaption>
    <svg viewBox="0 0 800 200" role="img" aria-label="录音音高随时间变化曲线">
      <line x1="20" x2="790" y1="180" y2="180" stroke="#ccd2d8" />
      {reference.map((p, i) => <line key={`r${i}`} x1={20 + Math.max(0, p.start) / maxTime * 760}
        x2={20 + Math.max(0, p.end) / maxTime * 760}
        y1={175 - (midi(p.hz) - low) / (high - low) * 155}
        y2={175 - (midi(p.hz) - low) / (high - low) * 155} stroke="#24866b" strokeWidth="3" />)}
      {voiced.map((p, i) => <circle key={i} cx={20 + p.time / maxTime * 760}
        cy={175 - (midi(p.hz!) - low) / (high - low) * 155} r="1.8" fill="#6854d8" />)}
      <text x="20" y="198">0 秒</text><text x="720" y="198">{maxTime.toFixed(1)} 秒</text>
    </svg></figure>;
}


function Evidence({
  items,
  preview,
}: {
  items: Citation[];
  preview?: boolean;
}) {
  return (
    <section className="evidence">
      <h4>{preview ? "相关资料" : "回答依据"}</h4>
      {items.map((item) => (
        <details key={item.number}>
          <summary>
            [{item.number}] {item.title}{" "}
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

export function ResultView({ result, identity, plan, onOpenPlan }: { result: Result; identity: string; plan?: Plan; onOpenPlan?: (plan: Plan) => void }) {
  return (
    <div className="result-view">
      {result.reason && <p className="notice">{human(result.reason)}</p>}
      {result.message && <p>{result.message}</p>}
      {result.answer && <p className="answer">{result.answer}</p>}
      {result.asset_id && <GeneratedAudio key={`${identity}:${result.asset_id}`} assetId={result.asset_id} identity={identity} />}
      {result.total && (
        <>
          <div className="score">
            {result.total.available ? result.total.value?.toFixed(1) : "—"}
            <small>练习评分 · {result.quality === "usable" ? "录音清晰" : "录音质量有限"}</small>
          </div>
          {!result.total.available && (
            <p className="muted">{human(result.total.reason)}</p>
          )}
          <div className="metrics">
            {Object.entries(result.dimensions ?? {}).map(([key, metric]) => (
              <div key={key}>
                <small>{({pitch: "音准", rhythm: "节奏", stability: "稳定性", coverage: "音高覆盖"} as Record<string, string>)[key] || "测量维度"}</small>
                <strong>
                  {metric.available ? metric.value?.toFixed(1) : "—"}
                </strong>
                <small>{metric.unit ?? human(metric.reason)}</small>
              </div>
            ))}
          </div>
          <p className="muted">
            {result.duration_ms
              ? `${(result.duration_ms / 1000).toFixed(1)} 秒 · `
              : ""}
            跟踪覆盖 {((result.tracking_coverage ?? 0) * 100).toFixed(1)}% ·{" "}
          </p>
          {!!result.pitch_contour?.length && <PitchChart points={result.pitch_contour} reference={result.reference_contour} />}
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
        <div className="inline-plan">
          <h4>你的练习安排</h4>
          {plan ? <>
            <p>每天 {plan.payload.minutes} 分钟，共 {plan.payload.days} 天。从舒适的练习强度开始。</p>
            <ol>{plan.payload.exercises.map((exercise, i) => <li key={i}>
              <strong>{exercise.title}</strong><span>{Math.round(exercise.seconds / 60 * 10) / 10} 分钟</span>
              <p>{exercise.instruction}</p>
            </li>)}</ol>
            {onOpenPlan && <button className="secondary" onClick={() => onOpenPlan(plan)}>调整计划与记录反馈</button>}
          </> : <p>计划已保存，可在下方「我的计划」中查看、编辑并记录反馈。</p>}
        </div>
      )}
    </div>
  );
}
function GeneratedAudio({assetId, identity}: {assetId: string; identity: string}) {
  const [url, setUrl] = useState("");
  const [error, setError] = useState("");
  return <div><button className="secondary" onClick={() => {
    void request<{url: string}>(identity,
      `/v1/uploads/${assetId}/playback`).then(r => {setUrl(r.url); setError("");})
      .catch(e => setError(e.message));
  }}>播放生成歌声</button>{url && <><audio controls src={url} /><a href={url} download>下载音频</a></>}
    {error && <p role="alert">{error}</p>}</div>;
}
