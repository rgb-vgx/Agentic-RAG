import type { Metadata } from "next";
import Link from "next/link";
import "./globals.css";

export const metadata: Metadata = {
  title: "Agentic RAG — Document Q&A",
  description: "Adaptive Document Intelligence & Question Answering System",
};

export default function RootLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <html lang="en">
      <body>
        <div className="app-shell">
          <nav className="sidebar">
            <div className="brand">Agentic RAG</div>
            <Link href="/chat" className="nav-link">
              Chat
            </Link>
            <Link href="/documents" className="nav-link">
              Documents
            </Link>
          </nav>
          <main className="main">{children}</main>
        </div>
      </body>
    </html>
  );
}
