"use client";

import { useState } from "react";
import { beginGoogleLogin } from "@/lib/oauth";

export default function LoginPage() {
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function signIn() {
    setBusy(true);
    setError(null);
    try {
      await beginGoogleLogin();
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
      setBusy(false);
    }
  }

  return (
    <div className="login-page">
      <div className="login-card">
        <div className="login-mark">R</div>
        <span className="settings-eyebrow">Agentic RAG</span>
        <h1>Đăng nhập để tiếp tục</h1>
        <p>
          Dùng tài khoản Google đã được cấp quyền. Hệ thống không tự tạo tài
          khoản mới.
        </p>
        <button type="button" onClick={signIn} disabled={busy}>
          {busy ? "Đang chuyển hướng…" : "Đăng nhập bằng Google"}
        </button>
        {error && <p className="settings-error">{error}</p>}
        <small>
          Bằng việc đăng nhập, bạn đồng ý nội dung tài liệu và câu hỏi được xử
          lý theo chính sách dữ liệu của hệ thống.
        </small>
      </div>
    </div>
  );
}
