import { expect, test, type Page } from "@playwright/test";
import type { Job, ConversationTurn } from "../src/types";

async function assistantFixture(page: Page) {
  const jobs: Job[] = [];
  const turns: ConversationTurn[] = [];
  const messages: Record<string, unknown>[] = [];
  let revision = 0;
  let exists = false;
  let conflict = false;
  const identity = "e2e|conversation-fixture";
  await page.addInitScript(value => sessionStorage.setItem("dv.identity", value), identity);
  await page.route("**/api/**", async route => {
    const req = route.request();
    const path = new URL(req.url()).pathname.replace("/api", "");
    const reply = (body: unknown, status = 200) => route.fulfill({ status, json: body });
    const owner = req.headers()["x-debug-identity"];
    if (path === "/v1/auth/config") return reply({mode: "local"});
    if (path === "/v1/jobs") return reply(owner === identity ? jobs : []);
    if (path.startsWith("/v1/jobs/")) {
      const job = jobs.find(j => j.id === path.split("/")[3]);
      if (job && path.endsWith("/cancel")) Object.assign(job, {status: "CANCELLED"});
      return job ? reply(job) : reply({detail: "not found"}, 404);
    }
    if (path === "/v1/conversations") {
      if (req.method() === "POST") { exists = true; return reply({id: "thread", revision}, 201); }
      return reply(exists && owner === identity ? [{id: "thread", title: "测试中的练声对话", revision}] : []);
    }
    if (path === "/v1/conversations/thread/messages") {
      const body = req.postDataJSON();
      messages.push(body);
      if (conflict) { conflict = false; revision++; return reply({detail: "conversation changed"}, 409); }
      expect(body.revision).toBe(revision);
      const job: Job = {id: `job-${messages.length}`, kind: "knowledge", status: "QUEUED", result: null,
        error_code: null, attempt: 0, trace_id: "test", created_at: new Date(Date.now() + messages.length).toISOString()};
      jobs.unshift(job);
      const response = {status: "queued", revision: ++revision, job: structuredClone(job), show_evidence: body.text.includes("来源")};
      turns.push({id: `turn-${revision}`, user_text: body.text, response});
      return reply(response);
    }
    if (path === "/v1/conversations/thread") return reply({id: "thread", revision, turns: turns.map(turn => ({
      ...turn, response: {...turn.response, job: jobs.find(j => j.id === turn.response.job?.id)}
    }))});
    if (path.endsWith("/capabilities")) return reply({status: "ready"});
    if (path === "/v1/progress") return reply({practice_count: 0, practice_seconds: 0, reports: []});
    if (path === "/v1/memory") return reply({enabled: false, revision: 0, l1_events: [], l2_facts: [], l3_profile: {}});
    if (path === "/v1/skills") return reply({discovery: [], versions: []});
    if (path === "/v1/model/usage") return reply({configured: false, currency: "CNY", periods: [], notice: ""});
    return reply([]);
  });
  await page.goto("/");
  return {jobs, messages, conflict: () => {conflict = true;}};
}

test("concurrent jobs answer in their own turns, history expands and reload restores results", async ({page}, info) => {
  const errors: string[] = [];
  page.on("pageerror", error => errors.push(error.message));
  const {jobs, messages} = await assistantFixture(page);
  const question = page.getByLabel("说说你的问题或目标");
  const feed = page.getByRole("log", {name: "对话消息"});
  await question.fill("什么是音准？");
  await question.press("Enter");
  await expect(feed.locator(".conversation-turn")).toHaveCount(1);
  await question.fill("什么是胸声？");
  await page.getByRole("button", {name: "发送", exact: true}).click();
  await expect(feed.locator(".conversation-turn")).toHaveCount(2);
  // The second turn finishes first. The original queued snapshots must not win.
  Object.assign(jobs[0], {status: "SUCCEEDED", result: {status: "refused", reason: "LOW_RELEVANCE"}});
  await expect(feed.locator("[data-job-id=job-2]")).toContainText("资料依据不足");
  Object.assign(jobs[1], {status: "SUCCEEDED", result: {status: "answered", answer: "音准是演唱音高与目标音高的一致程度。[1]",
    citations: [{number: 1, title: "测试音准资料", text: "仅用于界面回归的合成资料。", url: "https://example.com/test"}]}});
  await expect(feed.locator("[data-job-id=job-1]")).toContainText("音准是演唱音高");
  await expect(feed.locator(".evidence")).toHaveCount(0);
  await expect(feed.locator(".answer")).not.toContainText("[1]");
  await expect(feed.locator("[data-job-id=job-2]")).not.toContainText("音准是演唱音高");
  await expect(page.locator(".current-job")).toHaveCount(0);
  await expect(page.getByText("未生成", {exact: true})).toHaveCount(0);
  const row = page.locator(".job-list .history-item").filter({hasText: "已回答"});
  await row.click();
  await expect(row).toHaveAttribute("aria-expanded", "true");
  await expect(page.locator(".history-result")).toContainText("音准是演唱音高");
  await row.click();
  await expect(page.locator(".history-result")).toHaveCount(0);
  await page.reload();
  await expect(feed.locator(".conversation-turn")).toHaveCount(2);
  await expect(feed).toContainText("音准是演唱音高");
  await expect(feed).toContainText("资料依据不足");
  await feed.getByRole("button", {name: "编辑后重试"}).click();
  await expect(question).toHaveValue("什么是胸声？");
  await page.getByRole("button", {name: "新对话", exact: true}).click();
  await expect(feed.locator(".conversation-turn")).toHaveCount(0);
  await page.getByRole("button", {name: "历史对话", exact: true}).click();
  await page.getByRole("button", {name: /测试中的练声对话/}).click();
  await expect(feed).toContainText("音准是演唱音高");
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBeTruthy();
  await page.screenshot({path: `../../_qa/chat-${info.project.name}.png`, fullPage: true});
  await page.getByText("开发设置", {exact: true}).click();
  await page.getByLabel("本地测试身份").fill("e2e|another-owner");
  await page.getByRole("button", {name: "切换身份", exact: true}).click();
  await expect(feed.locator(".conversation-turn")).toHaveCount(0);
  await expect(page.locator(".job-list .history-item")).toHaveCount(0);
  expect(messages).toHaveLength(2);
  expect(errors).toEqual([]);
});

test("multiple needs show sequential progress, inline plans and prerequisites; sources are per turn", async ({page}, info) => {
  const errors: string[] = [];
  page.on("pageerror", error => errors.push(error.message));
  const {jobs} = await assistantFixture(page);
  await page.evaluate(() => {
    Object.defineProperty(navigator, "clipboard", {value: {writeText: async (text: string) => {
      (window as unknown as {copiedText: string}).copiedText = text;
    }}});
  });
  const question = page.getByLabel("说说你的问题或目标");
  const feed = page.getByRole("log", {name: "对话消息"});
  await question.fill("请解释胸声，分析我的录音，然后制定音准和节奏练习计划");
  await question.press("Enter");
  await expect(feed.locator(".conversation-turn")).toHaveCount(1);
  const citations = [{number: 1, title: "声乐测试资料", text: "合成资料", url: "https://example.com", license: "CC0", review_status: "approved"}];
  const answer = {status: "answered", answer: "## 胸声是什么\n**胸声**是一种声音体验。[1]\n\n- 轻松练习\n- <script>window.bad=true</script>", citations};
  Object.assign(jobs[0], {status: "RUNNING", result: {status: "processing", steps: [
    {id: "knowledge", agent: "knowledge", title: "声乐解答", status: "completed", result: answer},
    {id: "assessment", agent: "assessment", title: "录音分析", status: "running"},
    {id: "planning", agent: "planning", title: "练习安排", status: "waiting"},
  ]}});
  await expect(feed.getByRole("heading", {name: "胸声是什么"})).toBeVisible();
  await expect(feed.locator(".step-running")).toContainText("录音分析");
  await expect(feed.locator(".step-waiting")).toContainText("练习安排");
  await expect(feed.locator(".evidence")).toHaveCount(0);
  await expect(feed.locator(".answer")).not.toContainText("[1]");
  const plan = {goal: "pitch", goals: ["pitch", "rhythm"], days: 3, minutes: 10, notice: "", stop_rule: "不适时停止练习", observations: [],
    exercises: [{title: "参考音模唱", seconds: 210, instruction: "先听再唱"}, {title: "节拍与短句", seconds: 210, instruction: "先拍再唱"}]};
  Object.assign(jobs[0], {status: "SUCCEEDED", result: {status: "partial", steps: [
    {id: "knowledge", agent: "knowledge", title: "声乐解答", status: "completed", result: answer},
    {id: "assessment", agent: "assessment", title: "录音分析", status: "needs_input", message: "请上传本人的清唱录音。"},
    {id: "planning", agent: "planning", title: "练习安排", status: "blocked", message: "需先完成本次录音分析。"},
  ]}});
  await expect(feed).toContainText("部分完成");
  await expect(feed).toContainText("请上传本人的清唱录音");
  await feed.getByRole("button", {name: "复制回答", exact: true}).click();
  expect(await page.evaluate(() => (window as unknown as {copiedText: string}).copiedText)).not.toContain("[1]");
  await feed.getByRole("button", {name: "编辑后重试"}).click();
  await expect(question).toHaveValue("请解释胸声，分析我的录音，然后制定音准和节奏练习计划");
  await question.fill("解释胸声并制定计划，请提供来源");
  await question.press("Enter");
  await expect(feed.locator(".conversation-turn")).toHaveCount(2);
  Object.assign(jobs[0], {status: "SUCCEEDED", result: {status: "completed", steps: [
    {id: "knowledge", agent: "knowledge", title: "声乐解答", status: "completed", result: answer},
    {id: "planning", agent: "planning", title: "练习安排", status: "completed", result: {status: "planned", plan}},
  ]}});
  const last = feed.locator(".conversation-turn").last();
  await expect(last.locator(".evidence")).toHaveCount(1);
  await expect(last.locator(".answer")).toContainText("[1]");
  await expect(last.locator(".inline-plan")).toContainText("每天 10 分钟，共 3 天");
  await expect(last.locator(".inline-plan")).toContainText("参考音模唱");
  await expect(last.locator(".inline-plan")).toContainText("节拍与短句");
  await expect(feed.locator(".conversation-turn").first().locator(".evidence")).toHaveCount(0);
  await page.reload();
  await expect(feed.locator(".workflow-results")).toHaveCount(2);
  await expect(feed.locator(".evidence")).toHaveCount(1);
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBeTruthy();
  await last.scrollIntoViewIfNeeded();
  await page.screenshot({path: `../../_qa/multi-chat-${info.project.name}.png`, fullPage: true});
  expect(errors).toEqual([]);
});

test("cancelling a composite response remains cancelled after reload", async ({page}) => {
  const {jobs} = await assistantFixture(page);
  await page.getByLabel("说说你的问题或目标").fill("解释胸声并制定计划");
  await page.getByRole("button", {name: "发送", exact: true}).click();
  const feed = page.getByRole("log");
  await expect(feed.locator(".conversation-turn")).toHaveCount(1);
  Object.assign(jobs[0], {status: "RUNNING", result: {status: "processing", steps: [
    {id: "knowledge", agent: "knowledge", title: "声乐解答", status: "running"},
    {id: "planning", agent: "planning", title: "练习安排", status: "waiting"},
  ]}});
  await expect(feed.locator(".workflow-results")).toHaveCount(1);
  await feed.getByRole("button", {name: "停止处理"}).click();
  await expect(feed).toContainText("本次处理已取消");
  await expect(feed.locator(".step-running, .step-waiting")).toHaveCount(0);
  await expect(feed.locator(".step-cancelled")).toHaveCount(2);
  await page.reload();
  await expect(feed).toContainText("本次处理已取消");
  await expect(feed.getByRole("button", {name: "停止处理"})).toHaveCount(0);
});

test("revision conflicts retain input and synchronize before retry; IME and multiline do not submit", async ({page}) => {
  const fixture = await assistantFixture(page);
  const question = page.getByLabel("说说你的问题或目标");
  await question.fill("什么是音准");
  await question.dispatchEvent("keydown", {key: "Enter", isComposing: true});
  await question.press("Shift+Enter");
  expect(fixture.messages).toHaveLength(0);
  fixture.conflict();
  await question.fill("什么是音准？");
  await question.press("Enter");
  await expect(page.getByRole("alert")).toContainText("对话已同步");
  await expect(question).toHaveValue("什么是音准？");
  await question.press("Enter");
  await expect(page.getByRole("log").locator(".conversation-turn")).toHaveCount(1);
  expect(fixture.messages).toHaveLength(2);
  expect(fixture.messages[1].revision).toBe(1);
  Object.assign(fixture.jobs[0], {status: "FAILED", error_code: "TEST_ONLY"});
  await expect(page.getByRole("log")).toContainText("这次处理没有完成");
  await page.getByRole("log").getByRole("button", {name: "编辑后重试"}).click();
  await expect(question).toHaveValue("什么是音准？");
});
