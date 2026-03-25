import { describe, it, expect, vi } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import React from "react";
import InlineQuestion from "@/components/discovery/InlineQuestion";

describe("InlineQuestion", () => {
  const baseQuestion = {
    question: "What port does the app listen on?",
    context: "Could not detect automatically.",
    field: "app_port",
  };

  it("renders question text", () => {
    render(<InlineQuestion question={baseQuestion} onAnswer={vi.fn()} />);
    expect(screen.getByText("What port does the app listen on?")).toBeTruthy();
    expect(screen.getByText("Could not detect automatically.")).toBeTruthy();
  });

  it("renders text input when no options", () => {
    render(<InlineQuestion question={baseQuestion} onAnswer={vi.fn()} />);
    expect(screen.getByPlaceholderText("Type your answer...")).toBeTruthy();
  });

  it("renders radio buttons when options provided", () => {
    const q = { ...baseQuestion, options: ["3000", "8080", "8000"] };
    render(<InlineQuestion question={q} onAnswer={vi.fn()} />);
    const radios = screen.getAllByRole("radio");
    expect(radios.length).toBe(3);
  });

  it("pre-selects default value for radio", () => {
    const q = { ...baseQuestion, options: ["3000", "8080"], default: "8080" };
    render(<InlineQuestion question={q} onAnswer={vi.fn()} />);
    const radio = screen.getByDisplayValue("8080") as HTMLInputElement;
    expect(radio.checked).toBe(true);
  });

  it("calls onAnswer with selected radio value", () => {
    const onAnswer = vi.fn();
    const q = { ...baseQuestion, options: ["3000", "8080"], default: "3000" };
    render(<InlineQuestion question={q} onAnswer={onAnswer} />);

    fireEvent.click(screen.getByDisplayValue("8080"));
    fireEvent.click(screen.getByText("Continue"));

    expect(onAnswer).toHaveBeenCalledWith("app_port", "8080");
  });

  it("calls onAnswer with text input value", () => {
    const onAnswer = vi.fn();
    render(<InlineQuestion question={baseQuestion} onAnswer={onAnswer} />);

    fireEvent.change(screen.getByPlaceholderText("Type your answer..."), { target: { value: "4000" } });
    fireEvent.click(screen.getByText("Continue"));

    expect(onAnswer).toHaveBeenCalledWith("app_port", "4000");
  });

  it("shows questions remaining", () => {
    const q = { ...baseQuestion, questions_remaining: 2 };
    render(<InlineQuestion question={q} onAnswer={vi.fn()} />);
    expect(screen.getByText(/2 remaining/)).toBeTruthy();
  });
});
