import type React from "react";

export const STATUS_EMOJI: Record<string, string> = {
  active: "\uD83D\uDD04",
  paused: "\u23F8",
  completed: "\u2705",
  failed: "\u274C",
  cancelled: "\uD83D\uDEAB",
};

export const ITEM_STATUS_COLORS: Record<string, string> = {
  new: "#8888a0",
  in_progress: "#6c8aff",
  done: "#6cffa0",
  in_review: "#ffcc44",
  approved: "#44ddff",
  in_test: "#ff9944",
  tested: "#44ffbb",
  complete: "#6cffa0",
  blocked: "#ff6c8a",
  failed: "#ff4444",
};

export const KANBAN_COLUMNS: { key: string; label: string }[] = [
  { key: "new", label: "New" },
  { key: "in_progress", label: "In Progress" },
  { key: "done", label: "Done" },
  { key: "in_review", label: "In Review" },
  { key: "complete", label: "Complete" },
];

export function toolIcon(name: string): string {
  const icons: Record<string, string> = {
    file_read: "\uD83D\uDCC4", file_write: "\uD83D\uDCDD", file_edit: "\u270F\uFE0F",
    code_execute: "\u26A1", web_search: "\uD83D\uDD0D",
    web_read: "\uD83C\uDF10", memory_save: "\uD83D\uDCBE", search_memory: "\uD83E\uDDE0",
    memory_update: "\uD83D\uDCBE", memory_delete: "\uD83D\uDDD1\uFE0F", respond: "\uD83D\uDCAC",
    browser: "\uD83D\uDDA5\uFE0F", email: "\u2709\uFE0F", cron: "\u23F0", notify: "\uD83D\uDD14",
    call_subordinate: "\uD83E\uDD16", skills: "\uD83E\uDDE9", work_plan: "\uD83D\uDCCB",
  };
  return icons[name] || "\uD83D\uDD27";
}

export function statusEmoji(status: string): string {
  if (status === "complete" || status === "done") return "\u2705";
  if (status === "in_progress") return "\uD83D\uDD04";
  if (status === "failed") return "\u274C";
  return "\u2B1C";
}

/** Shared base styles used across pages */
export const sharedStyles: Record<string, React.CSSProperties> = {
  chatMsg: {
    padding: "8px 12px",
    borderRadius: "8px",
    backgroundColor: "#12121a",
    maxWidth: "95%",
  },
  chatMsgUser: {
    alignSelf: "flex-end",
    backgroundColor: "#1a2a3a",
  },
  chatMsgRole: {
    fontSize: "0.68rem",
    color: "#6c8aff",
    marginBottom: "3px",
    fontWeight: 600,
  },
  chatMsgContent: {
    whiteSpace: "pre-wrap" as const,
    lineHeight: 1.5,
    fontSize: "0.85rem",
  },
};
