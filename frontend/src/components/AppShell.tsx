"use client";

import { useEffect, useState } from "react";
import { usePathname, useRouter } from "next/navigation";
import Sidebar from "./Sidebar";
import { DEV_ACCESS_TOKEN, OAUTH_ENABLED } from "@/lib/config";
import { getAccessToken } from "@/lib/oauth";

const BARE_ROUTES = new Set(["/login", "/auth/callback"]);

function AuthGate({ children }: { children: React.ReactNode }) {
  const router = useRouter();
  const [ready, setReady] = useState(!OAUTH_ENABLED || Boolean(DEV_ACCESS_TOKEN));

  useEffect(() => {
    if (!OAUTH_ENABLED || DEV_ACCESS_TOKEN) return;
    if (getAccessToken()) {
      setReady(true);
      return;
    }
    router.replace("/login");
  }, [router]);

  if (!ready) {
    return <div className="auth-loading">Đang kiểm tra phiên đăng nhập…</div>;
  }
  return <>{children}</>;
}

export default function AppShell({ children }: { children: React.ReactNode }) {
  const pathname = usePathname();
  const bare = BARE_ROUTES.has(pathname ?? "");

  if (bare) {
    return (
      <div className="app-backdrop">
        <div className="app-frame">
          <main className="app-main">{children}</main>
        </div>
      </div>
    );
  }

  return (
    <div className="app-backdrop">
      <div className="app-frame">
        <Sidebar />
        <main className="app-main">
          <AuthGate>{children}</AuthGate>
        </main>
      </div>
    </div>
  );
}
