import { expect, test } from "@playwright/test";
import { readFileSync, writeFileSync } from "node:fs";
import { createHash, randomBytes } from "node:crypto";

test("application login uses its configured OIDC provider", async ({page, request}) => {
  test.skip(process.env.E2E_OIDC_UI !== "1", "isolated application OIDC test");
  const identity = JSON.parse(readFileSync("../../_qa/identity-e2e-private.json", "utf8"));
  const denied = await request.get("http://127.0.0.1:8002/v1/me", {headers: {"X-Debug-Identity": "local|attacker"}});
  expect(denied.status()).toBe(401);
  await page.goto("/");
  await page.getByRole("button", {name: "登录", exact: true}).click();
  await page.locator("#username").fill(identity.username);
  await page.locator("#password").fill(identity.password);
  await page.locator("#kc-login").click();
  await expect(page.getByText("已连接", {exact: true})).toBeVisible({timeout: 30000});
  await expect(page.getByRole("button", {name: "退出登录"})).toBeVisible();
  const authorization = await page.evaluate(() => {
    const key = Object.keys(sessionStorage).find(key => key.startsWith("oidc.user:"));
    return key ? JSON.parse(sessionStorage.getItem(key)!).access_token : "";
  });
  expect(Boolean(authorization)).toBeTruthy();
  const me = await request.get("http://127.0.0.1:8002/v1/me", {headers: {Authorization: `Bearer ${authorization}`}});
  expect(me.status()).toBe(200);
  expect((await me.json()).subject).toBe(identity.id);
});

test("real OIDC code flow uses PKCE and validates its audience", async ({page, request}) => {
  test.skip(process.env.E2E_OIDC !== "1", "explicit local Keycloak test");
  const identity = JSON.parse(readFileSync("../../_qa/identity-e2e-private.json", "utf8"));
  const verifier = randomBytes(48).toString("base64url");
  const challenge = createHash("sha256").update(verifier).digest("base64url");
  const state = randomBytes(24).toString("hex");
  const redirect = "http://127.0.0.1:8080/auth/callback";
  await page.route("**/auth/callback?**", route => route.fulfill({body: "Login completed"}));
  const parameters = new URLSearchParams({client_id: "deepvocal-web", response_type: "code",
    scope: "openid profile", redirect_uri: redirect, code_challenge: challenge, code_challenge_method: "S256", state});
  await page.goto(`${identity.issuer}/protocol/openid-connect/auth?${parameters}`);
  await page.locator("#username").fill(identity.username);
  await page.locator("#password").fill(identity.password);
  await page.locator("#kc-login").click();
  await page.waitForURL("**/auth/callback?**");
  const callback = new URL(page.url());
  expect(callback.searchParams.get("state")).toBe(state);
  const code = callback.searchParams.get("code")!;
  const token = await request.post(`${identity.issuer}/protocol/openid-connect/token`, {form: {
    client_id: "deepvocal-web", grant_type: "authorization_code", code, redirect_uri: redirect, code_verifier: verifier}});
  expect(token.status()).toBe(200);
  const tokens = await token.json();
  const payload = JSON.parse(Buffer.from(tokens.access_token.split(".")[1], "base64url").toString());
  expect([payload.aud].flat()).toContain("deepvocal-api");
  writeFileSync("../../_qa/identity-access-private.json", JSON.stringify({access_token: tokens.access_token, sub: payload.sub}));
  const replay = await request.post(`${identity.issuer}/protocol/openid-connect/token`, {form: {
    client_id: "deepvocal-web", grant_type: "authorization_code", code, redirect_uri: redirect, code_verifier: verifier}});
  expect(replay.status()).toBe(400);
});
