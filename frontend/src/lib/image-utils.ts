export interface ImageResult {
  paths: string[];
  prompt: string;
  revisedPrompt?: string;
  provider: string;
  model: string;
  size: string;
  cost?: number;
}

export const IMAGE_COSTS: Record<string, Record<string, number>> = {
  openai: {
    "gpt-image-1": 0.04,
    "gpt-image-1-hd": 0.08,
    "dall-e-3": 0.04,
    "dall-e-3-hd": 0.08,
    "dall-e-2": 0.02,
  },
  replicate: {
    "black-forest-labs/flux-1.1-pro": 0.05,
    "stability-ai/sdxl": 0.01,
  },
  comfyui: { default: 0 },
};

export function rewriteImageSrc(src: string): string {
  if (src.startsWith("/workspace/") || src.startsWith(".bond/images/") || src.startsWith("/data/images/")) {
    return `/api/v1/workspace-files/${encodeURIComponent(src)}`;
  }
  return src;
}

export function extractImageResults(content: string): ImageResult | null {
  try {
    // Try direct JSON parse
    let obj = tryParseJson(content);
    if (obj && isImageResult(obj)) return normalizeResult(obj);

    // Try extracting JSON from markdown code blocks
    const codeBlockMatch = content.match(/```(?:json)?\s*\n?([\s\S]*?)\n?```/);
    if (codeBlockMatch) {
      obj = tryParseJson(codeBlockMatch[1]);
      if (obj && isImageResult(obj)) return normalizeResult(obj);
    }

    return null;
  } catch {
    return null;
  }
}

function tryParseJson(s: string): Record<string, unknown> | null {
  try {
    const parsed = JSON.parse(s.trim());
    return typeof parsed === "object" && parsed !== null ? parsed : null;
  } catch {
    return null;
  }
}

function isImageResult(obj: Record<string, unknown>): boolean {
  if (!Array.isArray(obj.paths) || obj.paths.length === 0) return false;
  return obj.paths.some(
    (p: unknown) =>
      typeof p === "string" && /\.(png|jpg|jpeg|webp)$/i.test(p)
  );
}

function normalizeResult(obj: Record<string, unknown>): ImageResult {
  return {
    paths: obj.paths as string[],
    prompt: (obj.prompt as string) || "",
    revisedPrompt: obj.revised_prompt as string | undefined,
    provider: (obj.provider as string) || "openai",
    model: (obj.model as string) || "unknown",
    size: (obj.size as string) || "1024x1024",
    cost: typeof obj.cost === "number" ? obj.cost : undefined,
  };
}
