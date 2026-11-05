import { useEffect, useState } from "react";
import { request } from "./api";

type Entry = {id: string; created_at: string; profile: string; algorithm: string;
  ruleset: string; reference_fingerprint: string | null; quality: string;
  total: {available: boolean; value: number | null} | null};
type Summary = {practice_count: number; practice_seconds: number; reports: Entry[]};

export function Progress({identity, revision}: {identity: string; revision: number}) {
  const [summary, setSummary] = useState<Summary | null>(null);
  const [group, setGroup] = useState("");
  useEffect(() => {
    const controller = new AbortController();
    void request<Summary>(identity, "/v1/progress", {signal: controller.signal})
      .then(setSummary).catch(() => {});
    return () => controller.abort();
  }, [identity, revision]);
  if (!summary) return null;
  const groups = new Map<string, Entry[]>();
  for (const report of summary.reports) {
    if (report.quality !== "usable" || !report.total?.available || report.total.value === null || !report.reference_fingerprint) continue;
    const key = JSON.stringify([report.profile, report.algorithm, report.ruleset, report.reference_fingerprint]);
    groups.set(key, [...(groups.get(key) || []), report]);
  }
  const selected = groups.get(group) || [...groups.values()][0] || [];
  const first = selected[0], last = selected[selected.length - 1];
  const delta = first && last ? last.total!.value! - first.total!.value! : 0;
  return <section className="history panel"><h2>练习进展</h2>
    <p>最近 100 次记录：完成 {summary.practice_count} 次练习，累计 {Math.round(summary.practice_seconds / 60)} 分钟。</p>
    {groups.size > 1 && <label>比较记录<select value={groups.has(group) ? group : [...groups.keys()][0]}
      onChange={e => setGroup(e.target.value)}>{[...groups.entries()].map(([key, items], index) =>
        <option key={key} value={key}>参考旋律 {index + 1} · {items.length} 次测评</option>)}</select></label>}
    {selected.length >= 2 ? <><p>同一参考旋律、移调及评分版本下，最近一次较首次{delta >= 0 ? "增加" : "减少"} {Math.abs(delta).toFixed(1)} 分。</p>
      <svg viewBox="0 0 800 140" role="img" aria-label="同条件测评得分变化">
        <polyline fill="none" stroke="#6854d8" strokeWidth="3" points={selected.map((r, i) => `${20 + i / (selected.length - 1) * 760},${120 - r.total!.value!}`).join(" ")} />
        {selected.map((r, i) => <circle key={r.id} cx={20 + i / (selected.length - 1) * 760} cy={120 - r.total!.value!} r="4" fill="#6854d8"><title>{new Date(r.created_at).toLocaleString()} · {r.total!.value!.toFixed(1)} 分</title></circle>)}
      </svg></> : <p className="muted">完成至少两次相同参考旋律与移调的清晰录音测评后，可查看得分变化。</p>}
  </section>;
}
