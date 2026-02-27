import React from "react";
import { STATUS_EMOJI, statusEmoji } from "@/lib/theme";

interface StatusBadgeProps {
  status: string;
  /** Use "plan" for plan-level status, "item" for work item status */
  variant?: "plan" | "item";
}

export default function StatusBadge({ status, variant = "item" }: StatusBadgeProps) {
  const emoji = variant === "plan"
    ? (STATUS_EMOJI[status] || "\uD83D\uDCCB")
    : statusEmoji(status);

  return <span>{emoji}</span>;
}
