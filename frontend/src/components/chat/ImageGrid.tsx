"use client";
import React, { useState } from "react";
import ImageCard, { type ImageCardProps } from "./ImageCard";
import ImageLightbox from "./ImageLightbox";

export interface ImageGridProps {
  images: ImageCardProps[];
}

export default function ImageGrid({ images }: ImageGridProps) {
  const [lightboxIndex, setLightboxIndex] = useState<number | null>(null);
  const count = images.length;

  const gridStyle: React.CSSProperties =
    count === 1
      ? { maxWidth: "512px" }
      : count === 2
      ? { display: "grid", gridTemplateColumns: "1fr 1fr", gap: "8px" }
      : count === 3
      ? { display: "grid", gridTemplateColumns: "1fr 1fr", gap: "8px" }
      : { display: "grid", gridTemplateColumns: "1fr 1fr", gap: "8px" };

  return (
    <>
      <div style={gridStyle}>
        {images.map((img, i) => (
          <div
            key={i}
            style={count === 3 && i === 2 ? { gridColumn: "1 / -1" } : undefined}
          >
            <ImageCard {...img} onExpand={() => setLightboxIndex(i)} />
          </div>
        ))}
      </div>
      {lightboxIndex !== null && (
        <ImageLightbox
          {...images[lightboxIndex]}
          onClose={() => setLightboxIndex(null)}
          onDownload={() => {
            const img = images[lightboxIndex];
            const a = document.createElement("a");
            a.href = img.src.startsWith("/") ? img.src : `/api/v1/workspace-files/${encodeURIComponent(img.src)}`;
            a.download = img.src.split("/").pop() || "image.png";
            a.click();
          }}
          onRegenerate={() => {
            navigator.clipboard.writeText(images[lightboxIndex].prompt);
          }}
        />
      )}
    </>
  );
}
