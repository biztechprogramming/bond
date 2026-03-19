"use client";

import React from "react";

interface LoadingSkeletonProps {
  lines?: number;
}

export default function LoadingSkeleton({ lines = 3 }: LoadingSkeletonProps) {
  return (
    <div>
      <style>{`
        @keyframes opt-shimmer {
          0% { background-position: -400px 0; }
          100% { background-position: 400px 0; }
        }
      `}</style>
      {Array.from({ length: lines }).map((_, i) => (
        <div key={i} style={{
          height: "16px",
          marginBottom: "12px",
          borderRadius: "4px",
          width: i === lines - 1 ? "60%" : "100%",
          background: "linear-gradient(90deg, #1e1e2e 25%, #2a2a3e 50%, #1e1e2e 75%)",
          backgroundSize: "800px 100%",
          animation: "opt-shimmer 1.5s infinite linear",
        }} />
      ))}
    </div>
  );
}
