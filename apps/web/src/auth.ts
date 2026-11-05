import { UserManager, WebStorageStateStore } from "oidc-client-ts";

type AuthConfig = { mode: "local" | "oidc"; issuer: string; client_id: string };
let manager: UserManager | null = null;
export let authConfig: AuthConfig = { mode: "local", issuer: "", client_id: "" };

export async function initializeAuth() {
  const response = await fetch("/api/v1/auth/config", { cache: "no-store" });
  if (!response.ok) throw new Error("暂时无法连接服务，请稍后刷新。");
  authConfig = await response.json();
  if (authConfig.mode !== "oidc") return;
  manager = new UserManager({
    authority: authConfig.issuer,
    client_id: authConfig.client_id,
    redirect_uri: `${location.origin}/auth/callback`,
    post_logout_redirect_uri: location.origin,
    response_type: "code", scope: "openid profile",
    automaticSilentRenew: false,
    userStore: new WebStorageStateStore({ store: sessionStorage }),
  });
  if (location.pathname === "/auth/callback") {
    await manager.signinRedirectCallback();
    history.replaceState(null, "", "/");
  }
}

export async function authHeaders(identity: string): Promise<Record<string, string>> {
  if (authConfig.mode === "local") return { "X-Debug-Identity": identity };
  const user = await manager?.getUser();
  if (!user || user.expired) throw new Error("请先登录后继续。");
  return { Authorization: `Bearer ${user.access_token}` };
}

export async function login() { await manager?.signinRedirect(); }
export async function logout() {
  if (manager) await manager.signoutRedirect();
}

export async function accountName() {
  const user = await manager?.getUser();
  return user?.profile.preferred_username || user?.profile.name || "我的账户";
}
