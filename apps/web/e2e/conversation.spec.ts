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
      const response = {status: "queued", revision: ++revision, job: structuredClone(job)};
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
