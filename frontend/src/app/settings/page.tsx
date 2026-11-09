"use client";

/** Per-user integration settings. */

import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { api } from "@/lib/api";
import { getAccessToken, clearAccessToken } from "@/lib/oauth";
import type { IntegrationStatus } from "@/lib/types";

export default function SettingsPage() {
  const router = useRouter();
  const [status, setStatus] = useState<IntegrationStatus | null>(null);
  const [apiKey, setApiKey] = useState("");
  const [enabled, setEnabled] = useState(true);
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [hasToken, setHasToken] = useState(false);

  useEffect(() => {
    setHasToken(Boolean(getAccessToken()));
  }, []);

  useEffect(() => {
    api
      .getTavily()
      .then((value) => {
        setStatus(value);
        setEnabled(value.enabled);
      })
      .catch((err) =>
        setError(err instanceof Error ? err.message : String(err))
      );
  }, []);

  async function save() {
    setBusy(true);
    setError(null);
    setMessage(null);
    try {
      const value = await api.saveTavily(apiKey.trim() || null, enabled);
      setStatus(value);
      setApiKey("");
      setMessage("Đã lưu cấu hình Tavily.");
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  }

  async function test() {
    setBusy(true);
    setError(null);
    setMessage(null);
    try {
      await api.testTavily(apiKey.trim() || undefined);
      setMessage("Kết nối Tavily thành công.");
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  }

  function logout() {
    clearAccessToken();
    router.replace("/login");
  }

  async function remove() {
    if (!window.confirm("Xoá Tavily API key của bạn?")) return;
    setBusy(true);
    setError(null);
    try {
      await api.deleteTavily();
      setStatus({ configured: false, enabled: false });
      setApiKey("");
      setMessage("Đã xoá cấu hình Tavily.");
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="settings-page">
      <header className="settings-head">
        <div>
          <span className="settings-eyebrow">Cá nhân</span>
          <h1>Cài đặt tích hợp</h1>
          <p>
            API key được lưu riêng cho tài khoản của bạn và mã hoá phía server.
          </p>
        </div>
      </header>

      <section className="settings-card">
        <div className="settings-card-head">
          <div>
            <h2>Tavily — tìm kiếm web</h2>
            <p>
              Bật/tắt trong chat bằng nút <strong>Web</strong>. Khi bật, câu
              hỏi được gửi tới Tavily để lấy nguồn bên ngoài.
            </p>
          </div>
          <span
            className={`settings-status ${
              status?.configured ? "configured" : ""
            }`}
          >
            {status?.configured
              ? status.enabled
                ? "Đã cấu hình"
                : "Đang tắt"
              : "Chưa cấu hình"}
          </span>
        </div>

        {status?.configured && (
          <p className="settings-masked">
            Key hiện tại: <code>{status.masked_key ?? "••••"}</code>
          </p>
        )}

        <label className="settings-field">
          <span>API key</span>
          <input
            type="password"
            value={apiKey}
            onChange={(event) => setApiKey(event.target.value)}
            placeholder={
              status?.configured
                ? "Nhập key mới để thay thế"
                : "tvly-..."
            }
            autoComplete="off"
          />
        </label>

        <label className="settings-check">
          <input
            type="checkbox"
            checked={enabled}
            onChange={(event) => setEnabled(event.target.checked)}
          />
          <span>Cho phép dùng Tavily khi bật nút Web</span>
        </label>

        <div className="settings-actions">
          <button type="button" onClick={save} disabled={busy}>
            Lưu
          </button>
          <button
            type="button"
            className="secondary"
            onClick={test}
            disabled={busy || (!apiKey.trim() && !status?.configured)}
          >
            Kiểm tra kết nối
          </button>
          {status?.configured && (
            <button
              type="button"
              className="danger"
              onClick={remove}
              disabled={busy}
            >
              Xoá key
            </button>
          )}
        </div>

        {message && <p className="settings-ok">{message}</p>}
        {error && <p className="settings-error">{error}</p>}
      </section>

      {hasToken && (
        <div className="settings-actions">
          <button type="button" className="secondary" onClick={logout}>
            Đăng xuất
          </button>
        </div>
      )}
    </div>
  );
}
