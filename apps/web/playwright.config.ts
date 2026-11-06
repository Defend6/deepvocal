import { defineConfig } from "@playwright/test";

export default defineConfig({
  testDir: "./e2e",
  outputDir: "../../_qa/playwright-results",
  timeout: 60000,
  fullyParallel: false,
  use: {
    baseURL: process.env.E2E_BASE_URL || "http://127.0.0.1:5173",
    ...(process.env.E2E_BROWSER_CHANNEL ? {channel: process.env.E2E_BROWSER_CHANNEL} : {}),
    trace: "retain-on-failure",
    screenshot: "only-on-failure",
  },
  projects: [
    { name: "desktop", use: { viewport: { width: 1440, height: 1000 } } },
    {
      name: "mobile",
      use: {
        viewport: { width: 390, height: 844 },
        isMobile: true,
        hasTouch: true,
      },
    },
  ],
});
