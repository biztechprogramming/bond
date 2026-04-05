"use client";
import React from "react";

export interface ImageGenerationLoaderProps {
  provider: string;
  model: string;
  size: string;
  prompt: string;
}

export default function ImageGenerationLoader({
  provider,
  model,
  size,
}: ImageGenerationLoaderProps) {
  const [w, h] = size.split("x").map(Number);
  const aspectRatio = w && h ? `${w} / ${h}` : "1 / 1";
  const providerLabel =
    provider === "openai" ? "OpenAI" : provider === "replicate" ? "Replicate" : provider === "comfyui" ? "ComfyUI" : provider;

  return (
    <div style={{ maxWidth: "512px" }}>
      <style>{`
        @keyframes image-gen-shimmer {
          0% { background-position: -200% 0; }
          100% { background-position: 200% 0; }
        }
        @media (prefers-reduced-motion: reduce) {
          .image-gen-skeleton { animation: none !important; }
        }
      `}</style>
      <div
        className="image-gen-skeleton"
        style={{
          aspectRatio,
          width: "100%",
          maxWidth: "512px",
          borderRadius: "10px",
          background: "linear-gradient(90deg, #1e1e2e 25%, #2a2a3e 50%, #1e1e2e 75%)",
          backgroundSize: "200% 100%",
          animation: "image-gen-shimmer 1.5s ease-in-out infinite",
        }}
      />
      <div style={{ marginTop: "8px", fontSize: "0.8rem", color: "#8888a0" }}>
        Generating with {providerLabel} ({model})...
      </div>
    </div>
  );
}
