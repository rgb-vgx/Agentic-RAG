/**
 * Source-level acceptance check for the conversation history UI.
 *
 * The frontend has no unit-test harness; this script pins the required
 * integration points so the aidev task cannot "finish" without wiring the
 * history panel to the existing conversation API. `npm run build` still
 * catches type/compile errors, and the human review checks behaviour.
 */
import { existsSync, readFileSync } from "node:fs";
import { join } from "node:path";

const root = process.cwd();
const read = (path) => readFileSync(join(root, path), "utf8");
const failures = [];

function requireFile(path) {
  if (!existsSync(join(root, path))) {
    failures.push(`missing file: ${path}`);
    return "";
  }
  return read(path);
}

function requireMatch(path, label, pattern) {
  const source = requireFile(path);
  if (source && !pattern.test(source)) {
    failures.push(`${path}: missing ${label}`);
  }
}

const api = "frontend/src/lib/api.ts";
requireMatch(api, "renameConversation", /renameConversation\s*:/);
requireMatch(api, "deleteConversation", /deleteConversation\s*:/);

const panel = "frontend/src/components/HistoryPanel.tsx";
const panelSource = requireFile(panel);
if (panelSource) {
  requireMatch(panel, "listConversations", /listConversations/);
  requireMatch(panel, "listMessages", /listMessages/);
  requireMatch(panel, "rename action", /rename/);
  requireMatch(panel, "delete action", /delete/);
  requireMatch(panel, "active item class", /history-item/);
  requireMatch(panel, "onSelect callback", /onSelect/);
}

const chat = "frontend/src/app/chat/page.tsx";
requireMatch(chat, "HistoryPanel import", /HistoryPanel/);
requireMatch(chat, "conversation listing", /listConversations/);
requireMatch(chat, "message loading", /listMessages/);
requireMatch(chat, "rename wiring", /renameConversation/);
requireMatch(chat, "delete wiring", /deleteConversation/);

const css = "frontend/src/app/globals.css";
requireMatch(css, ".history-panel styles", /\.history-panel\b/);
requireMatch(css, ".history-item styles", /\.history-item\b/);

// The new panel must not replace the existing chat surface.
requireMatch(chat, "existing suggestion cards", /suggestion-card/);
requireMatch(chat, "existing composer", /composer-wrap/);
requireMatch("frontend/src/components/Robot.tsx", "existing robot", /robot/);

if (failures.length > 0) {
  console.error("conversation history acceptance check failed:");
  for (const failure of failures) console.error(`- ${failure}`);
  process.exit(1);
}
console.log("conversation history acceptance check passed");
