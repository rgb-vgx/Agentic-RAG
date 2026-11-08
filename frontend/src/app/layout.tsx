import type { Metadata } from "next";
import "@fontsource-variable/plus-jakarta-sans";
import "./globals.css";
import Sidebar from "@/components/Sidebar";

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
    <html lang="vi">
      <body>
        <div className="app-backdrop">
          <div className="app-frame">
            <Sidebar />
            <main className="app-main">{children}</main>
          </div>
        </div>
      </body>
    </html>
  );
}
