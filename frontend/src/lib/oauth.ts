/** Google OAuth PKCE helpers (authorization-code flow). */

import { API_PREFIX } from "./config";

export const ACCESS_TOKEN_KEY = "rag.access_token";
const STATE_KEY = "rag.oauth_state";
const VERIFIER_KEY = "rag.oauth_verifier";

function base64Url(bytes: Uint8Array): string {
  let value = "";
  bytes.forEach((byte) => {
    value += String.fromCharCode(byte);
  });
  return btoa(value).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
}

function randomToken(bytes = 32): string {
  const buffer = new Uint8Array(bytes);
  crypto.getRandomValues(buffer);
  return base64Url(buffer);
}

async function challengeFor(verifier: string): Promise<string> {
  const digest = await crypto.subtle.digest(
    "SHA-256",
    new TextEncoder().encode(verifier)
  );
  return base64Url(new Uint8Array(digest));
}

export interface GoogleConfig {
  enabled: boolean;
  client_id: string;
  redirect_uri: string;
}

export async function fetchGoogleConfig(): Promise<GoogleConfig> {
  const response = await fetch(`${API_PREFIX}/auth/google/config`);
  if (!response.ok) throw new Error("Không đọc được cấu hình Google OAuth");
  return (await response.json()) as GoogleConfig;
}

export async function beginGoogleLogin(): Promise<void> {
  const config = await fetchGoogleConfig();
  if (!config.enabled || !config.client_id) {
    throw new Error("Google OAuth chưa được cấu hình trên server");
  }
  const state = randomToken(24);
  const verifier = randomToken(64);
  sessionStorage.setItem(STATE_KEY, state);
  sessionStorage.setItem(VERIFIER_KEY, verifier);
  const params = new URLSearchParams({
    client_id: config.client_id,
    redirect_uri: config.redirect_uri,
    response_type: "code",
    scope: "openid email profile",
    state,
    code_challenge: await challengeFor(verifier),
    code_challenge_method: "S256",
    prompt: "select_account",
  });
  window.location.href = `https://accounts.google.com/o/oauth2/v2/auth?${params}`;
}

export async function completeGoogleLogin(
  code: string,
  state: string
): Promise<void> {
  const expected = sessionStorage.getItem(STATE_KEY);
  const verifier = sessionStorage.getItem(VERIFIER_KEY);
  sessionStorage.removeItem(STATE_KEY);
  sessionStorage.removeItem(VERIFIER_KEY);
  if (!expected || state !== expected || !verifier) {
    throw new Error("Phiên đăng nhập không hợp lệ, thử lại.");
  }
  const config = await fetchGoogleConfig();
  const response = await fetch(`${API_PREFIX}/auth/google/code`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      code,
      redirect_uri: config.redirect_uri,
      code_verifier: verifier,
    }),
  });
  if (!response.ok) {
    const body = await response.text().catch(() => "");
    throw new Error(
      response.status === 403
        ? "Tài khoản chưa được cấp quyền truy cập."
        : `Đăng nhập thất bại (${response.status}) ${body}`
    );
  }
  const data = (await response.json()) as { access_token: string };
  localStorage.setItem(ACCESS_TOKEN_KEY, data.access_token);
}

export function getAccessToken(): string | null {
  if (typeof window === "undefined") return null;
  return localStorage.getItem(ACCESS_TOKEN_KEY);
}

export function clearAccessToken(): void {
  localStorage.removeItem(ACCESS_TOKEN_KEY);
}
