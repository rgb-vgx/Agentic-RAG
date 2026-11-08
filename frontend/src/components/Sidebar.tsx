"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { ChatIcon, DocsIcon, PlusIcon } from "./icons";

const NAV = [
  { href: "/chat", label: "Chat", Icon: ChatIcon },
  { href: "/documents", label: "Documents", Icon: DocsIcon },
];

export default function Sidebar() {
  const pathname = usePathname();

  return (
    <aside className="rail" aria-label="Primary">
      <Link href="/chat" className="rail-add" aria-label="New chat">
        <PlusIcon size={22} />
      </Link>

      <nav className="rail-nav">
        {NAV.map(({ href, label, Icon }) => {
          const active = pathname === href || pathname.startsWith(`${href}/`);
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

      <div className="rail-mark" aria-hidden="true">
        R
      </div>
    </aside>
  );
}
