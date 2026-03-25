"use client";

import React, { useState, useEffect, useRef } from "react";
import type { UserQuestion } from "@/lib/discovery-types";

interface Props {
  question: UserQuestion;
  onAnswer: (field: string, value: string) => void;
}

export default function InlineQuestion({ question, onAnswer }: Props) {
  const [selected, setSelected] = useState(question.default || "");
  const [textValue, setTextValue] = useState(question.default || "");
  const containerRef = useRef<HTMLDivElement>(null);

  const hasOptions = question.options && question.options.length > 0;

  useEffect(() => {
    containerRef.current?.focus();
  }, []);

  const handleSubmit = () => {
    const value = hasOptions ? selected : textValue;
    if (!value.trim()) return;
    onAnswer(question.field, value.trim());
  };

  const handleKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === "Enter" && !hasOptions) handleSubmit();
  };

  const questionId = `question-${question.field}`;

  return (
    <div ref={containerRef} tabIndex={-1} style={styles.container}>
      <div style={styles.header}>
        <span style={styles.badge}>
          Question{question.questions_remaining != null ? ` (${question.questions_remaining} remaining)` : ""}
        </span>
      </div>

      <p id={questionId} style={styles.questionText}>{question.question}</p>
      {question.context && <p style={styles.context}>{question.context}</p>}

      {hasOptions ? (
        <div role="radiogroup" aria-labelledby={questionId} style={styles.optionsGroup}>
          {question.options!.map((opt) => (
            <label key={opt} style={styles.radioLabel}>
              <input
                type="radio"
                name={question.field}
                value={opt}
                checked={selected === opt}
                onChange={() => setSelected(opt)}
                style={styles.radio}
              />
              <span>{opt}</span>
            </label>
          ))}
        </div>
      ) : (
        <input
          type="text"
          value={textValue}
          onChange={(e) => setTextValue(e.target.value)}
          onKeyDown={handleKeyDown}
          placeholder="Type your answer..."
          aria-labelledby={questionId}
          style={styles.textInput}
        />
      )}

      <button
        onClick={handleSubmit}
        disabled={!(hasOptions ? selected : textValue.trim())}
        style={{
          ...styles.submitBtn,
          opacity: (hasOptions ? selected : textValue.trim()) ? 1 : 0.5,
        }}
      >
        Continue
      </button>
    </div>
  );
}

const styles: Record<string, React.CSSProperties> = {
  container: {
    backgroundColor: "#12121a",
    borderWidth: 1,
    borderStyle: "solid",
    borderColor: "#6c8aff",
    borderRadius: 10,
    padding: 16,
    outline: "none",
  },
  header: { marginBottom: 8 },
  badge: {
    fontSize: "0.75rem",
    fontWeight: 600,
    color: "#6c8aff",
    textTransform: "uppercase" as const,
    letterSpacing: "0.05em",
  },
  questionText: {
    fontSize: "0.9rem",
    fontWeight: 600,
    color: "#e0e0e8",
    margin: "0 0 4px 0",
  },
  context: {
    fontSize: "0.8rem",
    color: "#8888a0",
    margin: "0 0 12px 0",
  },
  optionsGroup: {
    display: "flex",
    flexDirection: "column" as const,
    gap: 6,
    marginBottom: 12,
  },
  radioLabel: {
    display: "flex",
    alignItems: "center",
    gap: 8,
    fontSize: "0.85rem",
    color: "#e0e0e8",
    cursor: "pointer",
  },
  radio: { accentColor: "#6c8aff" },
  textInput: {
    width: "100%",
    padding: "8px 12px",
    backgroundColor: "#0a0a12",
    borderWidth: 1,
    borderStyle: "solid",
    borderColor: "#2a2a3e",
    borderRadius: 6,
    color: "#e0e0e8",
    fontSize: "0.85rem",
    outline: "none",
    marginBottom: 12,
    boxSizing: "border-box" as const,
  },
  submitBtn: {
    backgroundColor: "#6c8aff",
    color: "#fff",
    borderWidth: 0,
    borderStyle: "none",
    borderColor: "transparent",
    borderRadius: 6,
    padding: "8px 20px",
    fontSize: "0.85rem",
    fontWeight: 600,
    cursor: "pointer",
  },
};
