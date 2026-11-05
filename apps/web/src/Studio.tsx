import { useEffect, useState } from "react";
import { request } from "./api";

type Song = {id: string; title: string};
export function Studio({identity, assetId, kind, onJob, onUpload}: {
  identity: string; assetId: string | null; kind: "assessment" | "singing";
  onJob: (job: unknown) => Promise<void>;
  onUpload?: (file: File) => Promise<void>;
}) {
  const [profile, setProfile] = useState("free_singing");
  const [songs, setSongs] = useState<Song[]>([]);
  const [song, setSong] = useState("");
  const [duration, setDuration] = useState(5);
  const [lyrics, setLyrics] = useState("");
  const [style, setStyle] = useState("Chinese solo vocal, clean singing");
  const [authorized, setAuthorized] = useState(false);
  const [message, setMessage] = useState("");
  const [busy, setBusy] = useState(false);
  const [songTitle, setSongTitle] = useState("");
  const [attribution, setAttribution] = useState("");
  const [track, setTrack] = useState(0);
  const [channel, setChannel] = useState("loudest");
  const [separate, setSeparate] = useState(false);
  const [semitones, setSemitones] = useState(0);
  const [key, setKey] = useState(() => crypto.randomUUID());
  const [media, setMedia] = useState("");
  const [consents, setConsents] = useState<{id: string; created_at: string; revoked_at: string | null}[]>([]);
  async function loadConsents() {
    setConsents(await request(identity, "/v1/singing/consents"));
  }
  useEffect(() => {if (kind === "singing") void loadConsents().catch(() => {});}, [kind, identity]);
  useEffect(() => {void request<Song[]>(identity, "/v1/songs").then(setSongs).catch(() => {});}, [identity]);
  useEffect(() => {setAuthorized(false); setMedia(""); setKey(crypto.randomUUID());}, [assetId]);
  async function run(action: () => Promise<void>) {
    setBusy(true); setMessage("");
    try {await action();} catch (error) {setMessage((error as Error).message);} finally {setBusy(false);}
  }
  async function submit() {
    if (!assetId) {setMessage("请先上传录音，等待检查完成。"); return;}
    let input: Record<string, unknown>;
    if (kind === "singing") {
      if (!authorized || !lyrics.trim()) {setMessage("请填写歌词并确认声音与歌词的使用授权。"); return;}
      const consent = await request<{id: string}>(identity, "/v1/singing/consents", {
        method: "POST", body: JSON.stringify({asset_id: assetId, owns_voice: true,
          permits_generation: true, statement_version: "voice-consent-v1"})});
      input = {lyrics, style, duration_seconds: Math.max(5, Math.min(60, duration)), seed: 42, consent_id: consent.id};
    } else {
      input = {profile, channel_mode: channel, semitones, separate_vocals: separate,
        ...(profile === "long_tone" ? {goal_duration_ms: duration * 1000} : {}),
        ...(profile === "reference_song" ? {song_id: song, auto_align: true} : {})};
    }
    const job = await request(identity, "/v1/jobs", {method: "POST", body: JSON.stringify({
      kind, input, input_asset_id: assetId, idempotency_key: key})});
    await onJob(job); if (kind === "singing") await loadConsents();
    setKey(crypto.randomUUID()); setMessage("已开始处理，可在任务记录中查看进度。");
  }
  return <section className="studio">
    {kind === "assessment" ? <>
      <div className="form-grid"><label>测评方式<select aria-label="测评方式" value={profile} onChange={e => setProfile(e.target.value)}>
        <option value="free_singing">自由演唱分析</option><option value="long_tone">长音练习</option>
        <option value="reference_song">参考曲目测评</option></select></label>
        <label>音频声道<select value={channel} onChange={e => setChannel(e.target.value)}>
          <option value="loudest">自动选择</option><option value="left">左声道</option>
          <option value="right">右声道</option><option value="center">中央声道（人声居中录音）</option></select></label>
        {profile === "long_tone" && <label>目标时长 / 秒<input type="number" min="3" max="120" value={duration} onChange={e => setDuration(+e.target.value)} /></label>}
        {profile === "reference_song" && <><label>参考曲目<select value={song} onChange={e => setSong(e.target.value)}><option value="">选择参考旋律</option>{songs.map(s => <option key={s.id} value={s.id}>{s.title}</option>)}</select></label>
          <label>移调 / 半音<input type="number" min="-12" max="12" value={semitones} onChange={e => setSemitones(+e.target.value)} /></label></>}
      </div>
      <label className="check"><input type="checkbox" checked={separate} onChange={e => setSeparate(e.target.checked)} />录音含伴奏，先提取人声（处理时间较长）</label>
      <details><summary>管理参考曲目</summary>
        <p>导入自己的原创或已获授权的单声部 MIDI。参考旋律仅对当前账户可见。</p>
        <div className="form-grid"><label>曲目名称<input value={songTitle} onChange={e => setSongTitle(e.target.value)} /></label>
          <label>来源与授权说明<input value={attribution} onChange={e => setAttribution(e.target.value)} /></label>
          <label>旋律轨道（从 0 开始）<input type="number" min="0" max="255" value={track} onChange={e => setTrack(+e.target.value)} /></label></div>
        <label>选择已获授权的 MIDI<input type="file" accept=".mid,.midi" disabled={busy || !songTitle || !attribution} onChange={e => {
          const file = e.target.files?.[0]; e.target.value = ""; if (!file) return;
          void run(async () => {
            if (file.size > 2 * 1024 * 1024) throw new Error("MIDI 文件不能超过 2 MiB。");
            const bytes = new Uint8Array(await file.arrayBuffer()); let binary = "";
            for (let i = 0; i < bytes.length; i++) binary += String.fromCharCode(bytes[i]);
            const saved = await request<Song>(identity, "/v1/songs", {method: "POST", body: JSON.stringify({
              title: songTitle, midi_base64: btoa(binary), track, license: "permission", attribution, rights_confirmed: true})});
            setSongs(previous => [saved, ...previous]); setSong(saved.id); setMessage("参考曲目已保存。");
          });
        }} /></label>
        {songs.map(s => <p key={s.id}>{s.title} <button className="text-button" disabled={busy} onClick={() => void run(async () => {
          await request(identity, `/v1/songs/${s.id}`, {method: "DELETE"}); setSongs(songs.filter(item => item.id !== s.id));
          if (song === s.id) setSong("");
        })}>删除</button></p>)}
      </details>
    </> : <>
      <p>上传自己的声音并填写歌词。生成结果会参考音频特征，实际音色以试听为准。</p>
      {onUpload && <label>选择自己的声音<input type="file" accept="audio/*,.wav,.flac,.mp3,.m4a,.ogg" disabled={busy}
        onChange={e => {const file = e.target.files?.[0]; e.target.value = "";
          if (file) void run(() => onUpload(file));}} /></label>}
      <label>歌词<textarea value={lyrics} onChange={e => setLyrics(e.target.value)} maxLength={2000} /></label>
      <div className="form-grid"><label>演唱风格<input value={style} maxLength={500} onChange={e => setStyle(e.target.value)} /></label>
        <label>生成秒数<input type="number" min="5" max="60" value={duration} onChange={e => setDuration(+e.target.value)} /></label></div>
      <label className="check"><input type="checkbox" checked={authorized} onChange={e => setAuthorized(e.target.checked)} />
        我确认这是本人的声音，歌词为原创或已获授权，并同意用于本次歌声生成。</label>
      {!!consents.length && <details><summary>声音使用授权</summary>{consents.map(c => <p key={c.id}>
        {new Date(c.created_at).toLocaleString()} · {c.revoked_at ? "已撤回" : "有效"}
        {!c.revoked_at && <button className="text-button" disabled={busy} onClick={() => void run(async () => {
          await request(identity, `/v1/singing/consents/${c.id}`, {method: "DELETE"}); await loadConsents();
          setAuthorized(false); setMessage("授权已撤回，相关等待中或正在生成的任务已停止。");
        })}>撤回授权</button>}</p>)}</details>}
    </>}
    <div className="actions"><button disabled={busy || !assetId || (profile === "reference_song" && !song && kind === "assessment")}
      onClick={() => void run(submit)}>{busy ? "提交中…" : kind === "assessment" ? "开始测评" : "生成歌声"}</button>
      {assetId && <button className="secondary" disabled={busy} onClick={() => void run(async () => {
        const response = await request<{url: string}>(identity, `/v1/uploads/${assetId}/playback`); setMedia(response.url);
      })}>试听录音</button>}</div>
    {media && <audio controls src={media} />}
    {message && <p role="status" className="notice">{message}</p>}
  </section>;
}
