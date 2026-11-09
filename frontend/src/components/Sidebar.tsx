"use client";

import { useEffect, useRef, useState } from "react";
import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { api } from "@/lib/api";
import { clearAccessToken } from "@/lib/oauth";
import type { CurrentUser } from "@/lib/types";
import {
  ChatIcon,
  DocsIcon,
  PlusIcon,
  SlidersIcon,
} from "./icons";

const NAV = [
  { href: "/chat", label: "Chat", Icon: ChatIcon },
  { href: "/documents", label: "Documents", Icon: DocsIcon },
  { href: "/settings", label: "Settings", Icon: SlidersIcon },
];

function initials(email: string): string {
  const local = email.split("@")[0] || "?";
  return local.slice(0, 1).toUpperCase();
}

export default function Sidebar() {
  const pathname = usePathname();
  const router = useRouter();
  const [user, setUser] = useState<CurrentUser | null>(null);
  const [menuOpen, setMenuOpen] = useState(false);
  const menuRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    api
      .me()
      .then(setUser)
      .catch(() => setUser(null));
  }, []);

  useEffect(() => {
    function close(event: MouseEvent) {
      if (!menuRef.current?.contains(event.target as Node)) {
        setMenuOpen(false);
      }
    }
    document.addEventListener("mousedown", close);
    return () => document.removeEventListener("mousedown", close);
  }, []);

  function logout() {
    clearAccessToken();
    setMenuOpen(false);
    router.replace("/login");
  }

  return (
    <aside className="rail" aria-label="Primary">
      <Link href="/chat" className="rail-add" aria-label="New chat">
        <PlusIcon size={22} />
      </Link>

      <nav className="rail-nav">
        {NAV.map(({ href, label, Icon }) => {
          const active =
            pathname === href || (pathname?.startsWith(`${href}/`) ?? false);
          return (
            <Link
              key={href}
              href={href}
              className={`rail-link ${active ? "active" : ""}`}
              title={label}
              aria-label={label}
              aria-current={active ? "page" : undefined}
            >
              <Icon size={21} />
            </Link>
          );
        })}
      </nav>

      <div className="rail-user" ref={menuRef}>
        {menuOpen && (
          <div className="rail-menu">
            {user && <span className="rail-menu-email">{user.email}</span>}
            <Link href="/settings" onClick={() => setMenuOpen(false)}>
              Cài đặt
            </Link>
            <button type="button" onClick={logout}>
              Đăng xuất
            </button>
          </div>
        )}
        <button
          type="button"
          className="rail-mark"
          onClick={() => setMenuOpen((open) => !open)}
          aria-label="Tài khoản"
          aria-expanded={menuOpen}
          title={user?.email ?? "Tài khoản"}
        >
          {user?.avatar_url ? (
            // Google profile picture; falls back to the initial below.
            // eslint-disable-next-line @next/next/no-img-element
            <img src={user.avatar_url} alt="" referrerPolicy="no-referrer" />
          ) : (
            initials(user?.email ?? "R")
          )}
        </button>
      </div>
    </aside>
  );
}
