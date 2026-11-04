import { expect, test } from "@playwright/test";

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
  await expect(
    page.getByText("本地服务已连接", { exact: false }),
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
      .getByText("模型 API Key 尚未配置", { exact: false }),
  ).toBeVisible({ timeout: 30000 });
  await expect(page.getByText("资料预览（尚未生成模型回答）")).toBeVisible();
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
    .getByRole("button", { name: "录音诊断与全曲测评 工程测量 · 分段报告" })
    .click();
  await page
    .getByLabel("选择清唱录音")
    .setInputFiles({
      name: "synthetic-test.wav",
      mimeType: "audio/wav",
      buffer: wav(),
    });
  await expect(
    page.getByText("synthetic-test.wav · 待校验（pending）", { exact: false }),
  ).toBeVisible({ timeout: 30000 });
  await expect(
    page.getByRole("button", { name: "提交录音测评 · 待开放" }),
  ).toBeDisabled();
  await page.screenshot({
    path: `../../_qa/frontend-${testInfo.project.name}-upload.png`,
    fullPage: true,
  });
  await page.getByLabel("本地测试身份").fill(`e2e|${crypto.randomUUID()}`);
  await page.getByRole("button", { name: "切换身份", exact: true }).click();
  await expect(
    page.getByText("本身份还没有任务。任务状态与结果会保存在这里。", {
      exact: true,
    }),
  ).toBeVisible();
  await expect(page.locator(".job-list .history-item")).toHaveCount(0);
  expect(errors).toEqual([]);
});
