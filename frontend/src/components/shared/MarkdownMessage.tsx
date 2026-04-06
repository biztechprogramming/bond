"use client";
import React, { useState, useCallback } from "react";
import ReactMarkdown, { Components } from "react-markdown";
import remarkGfm from "remark-gfm";
import rehypeRaw from "rehype-raw";


function CopyButton({ text }: { text: string }) {
  const [copied, setCopied] = useState(false);
  const handleCopy = useCallback(() => {
    navigator.clipboard.writeText(text).catch(() => {
      const ta = document.createElement("textarea");
      ta.value = text;
      ta.style.position = "fixed";
      ta.style.opacity = "0";
      document.body.appendChild(ta);
      ta.select();
      document.execCommand("copy");
      document.body.removeChild(ta);
    });
    setCopied(true);
    setTimeout(() => setCopied(false), 2000);
  }, [text]);

  return (
    <button
      onClick={handleCopy}
      style={{
        position: "absolute",
        top: "6px",
        right: "6px",
        background: "rgba(255,255,255,0.1)",
        border: "1px solid rgba(255,255,255,0.15)",
        borderRadius: "4px",
        color: "#b0b0c0",
        fontSize: "0.72rem",
        padding: "2px 8px",
        cursor: "pointer",
        opacity: 0.7,
      }}
    >
      {copied ? "Copied!" : "Copy"}
    </button>
  );
}

const components: Components = {
  a: ({ href, children, ...props }) => (
    <a
      href={href}
      target="_blank"
      rel="noreferrer noopener"
      style={{ color: "#6c8aff", textDecoration: "underline", textUnderlineOffset: "2px" }}
      {...props}
    >
      {children}
    </a>
  ),
  pre: ({ children, ...props }) => {
    // Extract text content from the code child
    let text = "";
    React.Children.forEach(children, (child) => {
      if (React.isValidElement(child) && child.props) {
        const p = child.props as { children?: React.ReactNode };
        if (typeof p.children === "string") text = p.children;
      }
    });
    return (
      <pre
        style={{
          background: "rgba(0,0,0,0.15)",
          padding: "10px 12px",
          borderRadius: "6px",
          overflowX: "auto",
          position: "relative",
          margin: "8px 0",
          fontSize: "0.82rem",
          lineHeight: 1.5,
        }}
        {...props}
      >
        <CopyButton text={text} />
        {children}
      </pre>
    );
  },
  code: ({ className, children, ...props }) => {
    const isBlock = className?.startsWith("language-");
    if (isBlock) {
      const lang = className?.replace("language-", "") || "";
      return (
        <>
          {lang && (
            <div style={{ fontSize: "0.7rem", color: "#666", marginBottom: "4px", textTransform: "uppercase", letterSpacing: "0.5px" }}>
              {lang}
            </div>
          )}
          <code className={className} style={{ fontFamily: "monospace" }} {...props}>{children}</code>
        </>
      );
    }
    return (
      <code
        style={{
          background: "rgba(0,0,0,0.15)",
          padding: "0.15em 0.4em",
          borderRadius: "3px",
          fontSize: "0.88em",
          fontFamily: "monospace",
        }}
        {...props}
      >
        {children}
      </code>
    );
  },
  blockquote: ({ children, ...props }) => (
    <blockquote
      style={{
        borderLeft: "3px solid rgba(108,138,255,0.4)",
        padding: "8px 12px",
        margin: "8px 0",
        background: "rgba(255,255,255,0.02)",
        borderRadius: "0 4px 4px 0",
      }}
      {...props}
    >
      {children}
    </blockquote>
  ),
  table: ({ children, ...props }) => (
    <div style={{ overflowX: "auto", margin: "8px 0" }}>
      <table
        style={{ borderCollapse: "collapse", width: "100%", fontSize: "0.85rem" }}
        {...props}
      >
        {children}
      </table>
    </div>
  ),
  th: ({ children, ...props }) => (
    <th
      style={{
        border: "1px solid rgba(255,255,255,0.1)",
        padding: "6px 10px",
        textAlign: "left",
        background: "rgba(255,255,255,0.05)",
        fontWeight: 600,
      }}
      {...props}
    >
      {children}
    </th>
  ),
  td: ({ children, ...props }) => (
    <td
      style={{
        border: "1px solid rgba(255,255,255,0.1)",
        padding: "6px 10px",
      }}
      {...props}
    >
      {children}
    </td>
  ),
  img: ({ src, alt, ...props }) => {
    // Rewrite workspace image paths to gateway file-serving endpoint
    let resolvedSrc = src || "";
    if (resolvedSrc.startsWith("/workspace/") || resolvedSrc.startsWith(".bond/images/") || resolvedSrc.startsWith("/data/images/")) {
      const encodedPath = encodeURIComponent(resolvedSrc);
      resolvedSrc = `/api/v1/workspace-files/${encodedPath}`;
    }
    return (
      <img
        src={resolvedSrc}
        alt={alt || ""}
        loading="lazy"
        style={{
          maxWidth: "min(100%, 512px)",
          maxHeight: "420px",
          borderRadius: "8px",
          border: "1px solid rgba(255,255,255,0.1)",
          boxShadow: "0 2px 8px rgba(0,0,0,0.3)",
          cursor: "pointer",
        }}
        onClick={() => {
          if (resolvedSrc) window.open(resolvedSrc, "_blank");
        }}
        {...props}
      />
    );
  },
  hr: () => (
    <hr style={{ border: "none", borderTop: "1px solid rgba(255,255,255,0.1)", margin: "12px 0" }} />
  ),
  h1: ({ children, ...props }) => <h1 style={{ fontSize: "1.4em", marginBottom: "8px", marginTop: "12px" }} {...props}>{children}</h1>,
  h2: ({ children, ...props }) => <h2 style={{ fontSize: "1.25em", marginBottom: "6px", marginTop: "10px" }} {...props}>{children}</h2>,
  h3: ({ children, ...props }) => <h3 style={{ fontSize: "1.1em", marginBottom: "4px", marginTop: "8px" }} {...props}>{children}</h3>,
  ul: ({ children, ...props }) => <ul style={{ paddingLeft: "20px", margin: "6px 0" }} {...props}>{children}</ul>,
  ol: ({ children, ...props }) => <ol style={{ paddingLeft: "20px", margin: "6px 0" }} {...props}>{children}</ol>,
  li: ({ children, ...props }) => <li style={{ marginBottom: "2px" }} {...props}>{children}</li>,
  p: ({ children, ...props }) => <p style={{ margin: "6px 0" }} {...props}>{children}</p>,
};

export default function MarkdownMessage({ content }: { content: string }) {
  if (!content) return null;
  return (
    <ReactMarkdown
      remarkPlugins={[remarkGfm]}
      rehypePlugins={[rehypeRaw]}
      components={components}
    >
      {content}
    </ReactMarkdown>
  );
}
