"use client";

import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { completeGoogleLogin } from "@/lib/oauth";

export default function AuthCallbackPage() {
  const router = useRouter();
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    const params = new URLSearchParams(window.location.search);
    const code = params.get("code");
    const state = params.get("state");
    const oauthError = params.get("error");
    if (oauthError) {
      setError(`Google từ chối đăng nhập: ${oauthError}`);
      return;
    }
    if (!code || !state) {
      setError("Thiếu mã đăng nhập từ Google.");
      return;
    }
    completeGoogleLogin(code, state)
      .then(() => router.replace("/chat"))
      .catch((err) =>
        setError(err instanceof Error ? err.message : String(err))
      );
  }, [router]);

  return (
    <div className="login-page">
      <div className="login-card">
        <div className="login-mark">R</div>
        <h1>{error ? "Không đăng nhập được" : "Đang đăng nhập…"}</h1>
        {error ? (
          <>
            <p className="settings-error">{error}</p>
            <button type="button" onClick={() => router.replace("/login")}>
              Thử lại
            </button>
          </>
        ) : (
          <p>Đang xác thực với Google và tạo phiên làm việc.</p>
        )}
      </div>
    </div>
  );
}
