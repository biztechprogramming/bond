/**
 * Media router — image upload and serving endpoints.
 *
 * Design Doc 104: Agent Image Delivery
 * Eliminates shared-volume dependency between agent containers and gateway.
 */

import { Router } from "express";
import { randomBytes } from "node:crypto";
import { join, resolve, extname } from "node:path";
import { existsSync, mkdirSync, createReadStream, statSync, writeFileSync } from "node:fs";
import multer from "multer";

const MAX_UPLOAD_SIZE = 20 * 1024 * 1024; // 20MB

function getImagesDir(): string {
  const bondHome = process.env.BOND_HOME || join(process.env.HOME || "/root", ".bond");
  return join(bondHome, "images");
}

const MIME_TYPES: Record<string, string> = {
  ".png": "image/png",
  ".jpg": "image/jpeg",
  ".jpeg": "image/jpeg",
  ".gif": "image/gif",
  ".webp": "image/webp",
  ".svg": "image/svg+xml",
  ".bmp": "image/bmp",
};

function generateImageId(): string {
  return "img_" + randomBytes(6).toString("hex");
}

function ensureImagesDir(): string {
  const dir = getImagesDir();
  if (!existsSync(dir)) {
    mkdirSync(dir, { recursive: true });
  }
  return dir;
}

export function createMediaRouter(): Router {
  const router = Router();

  // Configure multer for in-memory storage (we write to disk ourselves with our ID scheme)
  const upload = multer({
    storage: multer.memoryStorage(),
    limits: { fileSize: MAX_UPLOAD_SIZE },
  });

  /**
   * POST /api/v1/images/upload
   * Accepts multipart/form-data with file, agent_id, conversation_id, filename, prompt
   */
  router.post("/api/v1/images/upload", upload.single("file"), (req: any, res: any) => {
    try {
      if (!req.file) {
        return res.status(400).json({ error: "file is required" });
      }

      const agentId = req.body?.agent_id;
      const conversationId = req.body?.conversation_id;
      if (!agentId) {
        return res.status(400).json({ error: "agent_id is required" });
      }
      if (!conversationId) {
        return res.status(400).json({ error: "conversation_id is required" });
      }

      const imagesDir = ensureImagesDir();

      const id = generateImageId();
      const originalName = req.body?.filename || req.file.originalname || "image.png";
      const ext = extname(originalName).toLowerCase() || ".png";
      const imageFile = `${id}${ext}`;

      // Write image file
      writeFileSync(join(imagesDir, imageFile), req.file.buffer);

      // Write sidecar metadata JSON
      const mime = req.file.mimetype || MIME_TYPES[ext] || "application/octet-stream";
      const metadata = {
        id,
        agent_id: agentId,
        conversation_id: conversationId,
        filename: originalName,
        prompt: req.body?.prompt || "",
        mime_type: mime,
        size_bytes: req.file.size,
        created_at: new Date().toISOString(),
      };
      writeFileSync(join(imagesDir, `${id}.json`), JSON.stringify(metadata, null, 2));

      res.status(201).json({
        id,
        url: `/api/v1/images/${imageFile}`,
        filename: originalName,
        size: req.file.size,
        mime,
        agent_id: agentId,
        conversation_id: conversationId,
        created_at: metadata.created_at,
      });
    } catch (err: any) {
      console.error("[media] Upload error:", err);
      res.status(500).json({ error: "Upload failed" });
    }
  });

  /**
   * GET /api/v1/images/:id.:ext
   * Serves uploaded images with aggressive caching.
   */
  router.get("/api/v1/images/:filename", (req: any, res: any) => {
    const filename = req.params.filename;
    const filePath = resolve(join(getImagesDir(), filename));

    // Security: ensure resolved path is within getImagesDir()
    if (!filePath.startsWith(resolve(getImagesDir()))) {
      return res.status(403).json({ error: "Access denied" });
    }

    if (!existsSync(filePath) || !statSync(filePath).isFile()) {
      return res.status(404).json({ error: "Image not found" });
    }

    const ext = extname(filename).toLowerCase();
    const contentType = MIME_TYPES[ext] || "application/octet-stream";
    const stat = statSync(filePath);

    res.setHeader("Content-Type", contentType);
    res.setHeader("Content-Length", stat.size);
    res.setHeader("Cache-Control", "public, max-age=31536000, immutable");
    createReadStream(filePath).pipe(res);
  });

  return router;
}
