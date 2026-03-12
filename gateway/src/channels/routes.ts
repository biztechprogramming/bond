/**
 * Channel management API routes.
 */
import { Router } from "express";
import type { Request, Response } from "express";
import type { ChannelManager } from "./manager.js";
import { TelegramChannel } from "./telegram.js";
import { DiscordChannel } from "./discord.js";
import { SlackChannel } from "./slack.js";
import QRCode from "qrcode";

export function createChannelRouter(channelManager: ChannelManager): Router {
  const router = Router();

  // GET /channels — list all channels with status
  router.get("/channels", (_req: Request, res: Response) => {
    res.json(channelManager.listChannels());
  });

  // POST /channels/telegram/setup — validate token, return bot info
  router.post("/channels/telegram/setup", async (req: Request, res: Response) => {
    try {
      const { token } = req.body;
      if (!token || typeof token !== "string") {
        res.status(400).json({ error: "Missing or invalid token" });
        return;
      }
      const botInfo = await TelegramChannel.validateToken(token);
      channelManager.configureTelegram(token, {
        id: botInfo.id,
        username: botInfo.username,
        firstName: botInfo.firstName,
      });
      res.json({ ok: true, bot: botInfo });
    } catch (err) {
      res.status(400).json({ error: err instanceof Error ? err.message : "Invalid token" });
    }
  });

  // POST /channels/telegram/start
  router.post("/channels/telegram/start", async (_req: Request, res: Response) => {
    try {
      await channelManager.startChannel("telegram");
      res.json({ ok: true });
    } catch (err) {
      res.status(500).json({ error: err instanceof Error ? err.message : "Failed to start" });
    }
  });

  // POST /channels/telegram/stop
  router.post("/channels/telegram/stop", async (_req: Request, res: Response) => {
    try {
      await channelManager.stopChannel("telegram");
      res.json({ ok: true });
    } catch (err) {
      res.status(500).json({ error: err instanceof Error ? err.message : "Failed to stop" });
    }
  });

  // GET /channels/whatsapp/qr — SSE endpoint streaming QR codes
  router.get("/channels/whatsapp/qr", async (req: Request, res: Response) => {
    res.setHeader("Content-Type", "text/event-stream");
    res.setHeader("Cache-Control", "no-cache");
    res.setHeader("Connection", "keep-alive");
    res.flushHeaders();

    const cleanup = channelManager.subscribeWhatsAppQR(async (qr: string) => {
      try {
        const dataUrl = await QRCode.toDataURL(qr, { width: 256 });
        res.write(`data: ${JSON.stringify({ qr: dataUrl })}\n\n`);
      } catch {
        res.write(`data: ${JSON.stringify({ qr: null, error: "QR generation failed" })}\n\n`);
      }
    });

    const statusCleanup = channelManager.subscribeWhatsAppStatus((status: string) => {
      res.write(`data: ${JSON.stringify({ status })}\n\n`);
    });

    req.on("close", () => {
      cleanup();
      statusCleanup();
    });

    // Start WhatsApp connection if not running so QR codes are generated
    if (!channelManager.isChannelRunning("whatsapp")) {
      try {
        await channelManager.startChannel("whatsapp");
      } catch (err) {
        res.write(`data: ${JSON.stringify({ error: err instanceof Error ? err.message : "Failed to start" })}\n\n`);
      }
    }
  });

  // POST /channels/whatsapp/start
  router.post("/channels/whatsapp/start", async (_req: Request, res: Response) => {
    try {
      await channelManager.startChannel("whatsapp");
      res.json({ ok: true });
    } catch (err) {
      res.status(500).json({ error: err instanceof Error ? err.message : "Failed to start" });
    }
  });

  // POST /channels/whatsapp/stop
  router.post("/channels/whatsapp/stop", async (_req: Request, res: Response) => {
    try {
      await channelManager.stopChannel("whatsapp");
      res.json({ ok: true });
    } catch (err) {
      res.status(500).json({ error: err instanceof Error ? err.message : "Failed to stop" });
    }
  });

  // POST /channels/discord/setup — validate token, save config
  router.post("/channels/discord/setup", async (req: Request, res: Response) => {
    try {
      const { token } = req.body;
      if (!token || typeof token !== "string") {
        res.status(400).json({ error: "Missing or invalid token" });
        return;
      }
      const botInfo = await DiscordChannel.validateToken(token);
      channelManager.configureDiscord(token, botInfo);
      res.json({ ok: true, bot: botInfo });
    } catch (err) {
      res.status(400).json({ error: err instanceof Error ? err.message : "Invalid token" });
    }
  });

  // POST /channels/discord/start
  router.post("/channels/discord/start", async (_req: Request, res: Response) => {
    try {
      await channelManager.startChannel("discord");
      res.json({ ok: true });
    } catch (err) {
      res.status(500).json({ error: err instanceof Error ? err.message : "Failed to start" });
    }
  });

  // POST /channels/discord/stop
  router.post("/channels/discord/stop", async (_req: Request, res: Response) => {
    try {
      await channelManager.stopChannel("discord");
      res.json({ ok: true });
    } catch (err) {
      res.status(500).json({ error: err instanceof Error ? err.message : "Failed to stop" });
    }
  });

  // POST /channels/slack/setup — validate tokens, save config
  router.post("/channels/slack/setup", async (req: Request, res: Response) => {
    try {
      const { botToken, appToken } = req.body;
      if (!botToken || typeof botToken !== "string") {
        res.status(400).json({ error: "Missing or invalid botToken" });
        return;
      }
      if (!appToken || typeof appToken !== "string") {
        res.status(400).json({ error: "Missing or invalid appToken" });
        return;
      }
      const workspaceInfo = await SlackChannel.validateToken(botToken);
      channelManager.configureSlack(botToken, appToken);
      res.json({ ok: true, workspace: workspaceInfo });
    } catch (err) {
      res.status(400).json({ error: err instanceof Error ? err.message : "Invalid token" });
    }
  });

  // POST /channels/slack/start
  router.post("/channels/slack/start", async (_req: Request, res: Response) => {
    try {
      await channelManager.startChannel("slack");
      res.json({ ok: true });
    } catch (err) {
      res.status(500).json({ error: err instanceof Error ? err.message : "Failed to start" });
    }
  });

  // POST /channels/slack/stop
  router.post("/channels/slack/stop", async (_req: Request, res: Response) => {
    try {
      await channelManager.stopChannel("slack");
      res.json({ ok: true });
    } catch (err) {
      res.status(500).json({ error: err instanceof Error ? err.message : "Failed to stop" });
    }
  });

  // DELETE /channels/:channel — disconnect and clean up
  router.delete("/channels/:channel", async (req: Request, res: Response) => {
    try {
      const channel = req.params.channel as string;
      await channelManager.removeChannel(channel);
      res.json({ ok: true });
    } catch (err) {
      res.status(500).json({ error: err instanceof Error ? err.message : "Failed to remove" });
    }
  });

  return router;
}
