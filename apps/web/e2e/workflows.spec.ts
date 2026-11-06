import { expect, test } from "@playwright/test";

const corpusAvailable = process.env.E2E_CORPUS_AVAILABLE !== "0";
const knowledgeLive = process.env.E2E_KNOWLEDGE_LIVE === "1";
const knowledgeOutcome = knowledgeLive ? /已回答|资料依据不足/
  : corpusAvailable ? /问答暂不可用/ : /尚未准备可用的本地知识资料/;

function wav(): Buffer {
  const output = Buffer.alloc(44 + 32000);
  output.write("RIFF", 0);
  output.writeUInt32LE(output.length - 8, 4);
  output.write("WAVEfmt ", 8);
  output.writeUInt32LE(16, 16);
  output.writeUInt16LE(1, 20);
  output.writeUInt16LE(1, 22);
  output.writeUInt32LE(16000, 24);
  output.writeUInt32LE(32000, 28);
  output.writeUInt16LE(2, 32);
  output.writeUInt16LE(16, 34);
  output.write("data", 36);
  output.writeUInt32LE(32000, 40);
  for (let i = 0; i < 16000; i++)
    output.writeInt16LE(
      Math.round(5000 * Math.sin((2 * Math.PI * 220 * i) / 16000)),
      44 + i * 2,
    );
  return output;
}

test("real local workflow: knowledge, plan editing, feedback, skills, memory, pending upload, owner switch", async ({
  page,
}, testInfo) => {
  const errors: string[] = [];
  page.on("pageerror", (error) => errors.push(error.message));
  const identity = `e2e|${crypto.randomUUID()}`;
  await page.addInitScript(
    (value) => sessionStorage.setItem("dv.identity", value),
    identity,
  );
  await page.goto("/");
  await page.getByRole("button", { name: "专业工作台", exact: true }).click();
  await expect(
    page.getByText("已连接", { exact: false }),
  ).toBeVisible({timeout: 30000});
  const diagnostic = page.locator(".operations");
  await expect(
    diagnostic.getByRole("heading", { name: "任务运行诊断" }),
  ).toBeVisible();
  const failedResponse = await page.request.post("/api/v1/jobs", {
    headers: { "X-Debug-Identity": identity },
    data: {
      kind: "evaluation",
      input: { check: "e2e-injected-failure" },
      idempotency_key: crypto.randomUUID(),
    },
  });
  expect(failedResponse.status()).toBe(202);
  const failedJob = await failedResponse.json();
  await expect(
    diagnostic.getByText("过去 24 小时存在失败任务", { exact: false }),
  ).toBeVisible({ timeout: 30000 });
  await expect(
    diagnostic.getByText(`完整任务编号：${failedJob.id}`, { exact: true }),
  ).toBeVisible();
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= window.innerWidth,
    ),
  ).toBeTruthy();
  await page.screenshot({
    path: `../../_qa/frontend-${testInfo.project.name}.png`,
    fullPage: true,
  });
  await page.getByLabel("你的问题").fill("什么是音准？");
  await page.getByRole("button", { name: "提交问题", exact: true }).click();
  await expect(
    page
      .locator(".current-job")
      .getByText(knowledgeOutcome).first(),
  ).toBeVisible({ timeout: knowledgeLive ? 120000 : 30000 });
  if (corpusAvailable && !knowledgeLive) await expect(page.getByRole("heading", {name: "相关资料", exact: true})).toBeVisible();
  await page
    .getByText("账户用量 · 暂无用量", { exact: true })
    .click();
  await expect(
    page.getByText("暂无使用记录。", { exact: true }),
  ).toBeVisible();
  await page
    .getByRole("button", { name: "练习规划 目标约束 · 练习反馈" })
    .click();
  await page.getByLabel("练习目标", { exact: true }).selectOption("pitch");
  await page.getByRole("button", { name: "生成练习计划", exact: true }).click();
  await expect(
    page.locator(".plan-list").getByRole("button", { name: /参考音模唱/ }),
  ).toBeVisible({ timeout: 30000 });
  await page
    .locator(".plan-list")
    .getByRole("button", { name: /参考音模唱/ })
    .click();
  await page.getByLabel("编辑计划目标").selectOption("rhythm");
  await page.getByRole("button", { name: "保存新版本", exact: true }).click();
  await expect(page.getByText("编辑计划 · v2", { exact: true })).toBeVisible();
  await page.getByLabel("实际秒数").fill("120");
  await page.getByLabel("练习备注").fill("E2E测试反馈（非真实练习记录）");
  await page.getByRole("button", { name: "保存练习反馈", exact: true }).click();
  await expect(
    page.getByText("实际练习反馈已记录。", { exact: true }),
  ).toBeVisible();
  await page.getByText("跨会话记忆 · 默认关闭", { exact: true }).click();
  await page
    .getByRole("button", { name: "开启跨会话记忆", exact: true })
    .click();
  await page.getByRole("button", { name: "确认保存偏好", exact: true }).click();
  await expect(
    page.getByText("goal：foundation", { exact: false }),
  ).toBeVisible();
  await page
    .getByRole("button", { name: "清除全部记忆并关闭", exact: true })
    .click();
  await expect(
    page.getByText("跨会话记忆 · 默认关闭", { exact: true }),
  ).toBeVisible();
  await page
    .getByText("Skill 模板库 · 发现、审核与发布", { exact: true })
    .click();
  await page
    .locator(".history")
    .last()
    .getByRole("button", { name: /planning ·/ })
    .click();
  await page.getByRole("button", { name: "审核", exact: true }).click();
  await page.getByRole("button", { name: "发布", exact: true }).click();
  await expect(
    page.getByText("planning-trace-template · v1 · published", { exact: true }),
  ).toBeVisible();
  await page.getByRole("button", { name: "退役", exact: true }).click();
  await page
    .getByRole("button", { name: "录音诊断与全曲测评 音高节奏 · 分段报告" })
    .click();
  await page.getByLabel("选择清唱录音").setInputFiles({
    name: "synthetic-test.wav",
    mimeType: "audio/wav",
    buffer: wav(),
  });
  await expect(
    page.getByText("synthetic-test.wav · 可以开始分析", { exact: false }),
  ).toBeVisible({ timeout: 30000 });
  await expect(
    page.getByRole("button", { name: "开始测评", exact: true }),
  ).toBeEnabled();
  await page.screenshot({
    path: `../../_qa/frontend-${testInfo.project.name}-upload.png`,
    fullPage: true,
  });
  await page.getByText("开发设置", {exact: true}).click();
  await page.getByLabel("本地测试身份").fill(`e2e|${crypto.randomUUID()}`);
  await page.getByRole("button", { name: "切换身份", exact: true }).click();
  await expect(
    page.getByText("本身份还没有任务。任务状态与结果会保存在这里。", {
      exact: true,
    }),
  ).toBeVisible();
  await expect(page.locator(".job-list .history-item")).toHaveCount(0);
  await expect(
    diagnostic.getByText("当前未发现诊断规则命中的异常。", { exact: true }),
  ).toBeVisible();
  await expect(
    diagnostic.getByText(`完整任务编号：${failedJob.id}`, { exact: true }),
  ).toHaveCount(0);
  expect(errors).toEqual([]);
});

test("beginner mode delegates routing and keeps clarification, plans and pending audio usable", async ({ page }, testInfo) => {
  const errors: string[] = [];
  const requests: Record<string, unknown>[] = [];
  page.on("pageerror", error => errors.push(error.message));
  page.on("request", req => {
    if (req.url().includes("/messages") && req.method() === "POST") requests.push(req.postDataJSON());
  });
  await page.addInitScript((identity) => sessionStorage.setItem("dv.identity", identity), `e2e|${crypto.randomUUID()}`);
  await page.goto("/");
  await expect(page.getByRole("button", { name: "智能助手", exact: true })).toHaveAttribute("aria-pressed", "true");
  await expect(page.locator(".sidebar")).toHaveCount(0);
  await expect(page.locator(".operations")).toHaveCount(0);
  await expect(page.getByRole("button", { name: "自动识别意图", exact: true })).toHaveCount(0);
  const question = page.getByLabel("说说你的问题或目标");
  const send = page.getByRole("button", { name: "发送", exact: true });
  await question.fill("你好");
  await send.click();
  const feed = page.getByRole("log", {name: "对话消息"});
  await expect(feed).toContainText("请补充希望解决的具体问题");
  await expect(page.locator(".job-list .history-item")).toHaveCount(0);
  await question.fill("什么是音准？");
  await send.click();
  await expect(feed.getByText(knowledgeOutcome).first()).toBeVisible({ timeout: knowledgeLive ? 120000 : 30000 });
  await expect(page.locator(".sidebar")).toHaveCount(0);
  await question.fill("帮我制定音准练习计划，每天15分钟，共5天");
  await send.click();
  const plan = page.locator(".plan-list").getByRole("button", { name: /参考音模唱 · 15分钟 × 5天/ });
  await expect(plan).toBeVisible({ timeout: 30000 });
  await plan.click();
  await expect(page.getByText("编辑计划 · v1", { exact: true })).toBeVisible();
  await question.fill("帮我制定每天60分钟的练习计划");
  await send.click();
  await expect(feed).toContainText("5–30 分钟");
  await question.fill("请分析我的录音");
  await send.click();
  await expect(feed).toContainText("请附上");
  await page.getByRole("button", {name: "添加录音", exact: true}).click();
  await page.getByLabel("附上清唱录音", { exact: true }).setInputFiles({ name: "beginner-synthetic.wav", mimeType: "audio/wav", buffer: wav() });
  await expect(page.getByText("beginner-synthetic.wav · 准备完成", { exact: false })).toBeVisible({ timeout: 30000 });
  await question.fill("请分析我的录音并制定练习计划");
  await send.click();
  await expect(feed.locator(".inline-plan")).toHaveCount(2, {timeout: 30000});
  await expect(feed.locator(".inline-plan").last()).toContainText("分钟");
  await page.getByRole("button", { name: "移除附件", exact: true }).click();
  await question.fill("帮我生成个性化歌声");
  await send.click();
  await expect(feed).toContainText("在歌声生成工作台上传本人声音");
  expect(requests.length).toBeGreaterThan(5);
  for (const body of requests) {
    expect(body).not.toHaveProperty("action");
    expect(body).not.toHaveProperty("kind");
    expect(body).not.toHaveProperty("input");
  }
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBeTruthy();
  await page.screenshot({ path: `../../_qa/assistant-${testInfo.project.name}.png`, fullPage: true });
  const count = await page.locator(".job-list .history-item").count();
  await page.getByRole("button", { name: "专业工作台", exact: true }).click();
  await expect(page.locator(".sidebar .agent")).toHaveCount(4);
  await page.reload();
  await expect(page.getByRole("button", { name: "专业工作台", exact: true })).toHaveAttribute("aria-pressed", "true");
  await expect(page.locator(".job-list .history-item")).toHaveCount(count);
  await page.getByRole("button", { name: "智能助手", exact: true }).click();
  await expect(page.locator(".sidebar")).toHaveCount(0);
  await page.getByText("开发设置", {exact: true}).click();
  await page.getByLabel("本地测试身份").fill(`e2e|${crypto.randomUUID()}`);
  await page.getByRole("button", { name: "切换身份", exact: true }).click();
  await expect(page.locator(".job-list .history-item")).toHaveCount(0);
  await expect(page.locator(".plan-list button")).toHaveCount(0);
  expect(errors).toEqual([]);
});
