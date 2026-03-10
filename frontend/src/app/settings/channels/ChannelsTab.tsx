"use client";

import React, { useEffect, useState, useCallback, useRef } from "react";

const GATEWAY = "http://localhost:18789/api/v1";

interface ChannelInfo {
  type: string;
  status: "linked" | "not_linked" | "connecting";
  enabled: boolean;
  botInfo?: { id: number; username: string; firstName: string };
  user?: { id: string; name?: string };
}

export default function ChannelsTab() {
  const [channels, setChannels] = useState<ChannelInfo[]>([]);
  const [loading, setLoading] = useState(true);

  // Telegram wizard
  const [showTelegramSetup, setShowTelegramSetup] = useState(false);
  const [telegramToken, setTelegramToken] = useState("");
  const [telegramValidating, setTelegramValidating] = useState(false);
  const [telegramError, setTelegramError] = useState("");
  const [telegramBotName, setTelegramBotName] = useState("");

  // WhatsApp wizard
  const [showWhatsAppSetup, setShowWhatsAppSetup] = useState(false);
  const [whatsappQR, setWhatsappQR] = useState("");
  const [whatsappStatus, setWhatsappStatus] = useState("");
  const eventSourceRef = useRef<EventSource | null>(null);

  const fetchChannels = useCallback(async () => {
    try {
      const res = await fetch(`${GATEWAY}/channels`);
      if (res.ok) setChannels(await res.json());
    } catch { /* gateway not available */ }
    setLoading(false);
  }, []);

  useEffect(() => {
    fetchChannels();
    const interval = setInterval(fetchChannels, 5000);
    return () => clearInterval(interval);
  }, [fetchChannels]);

  const validateTelegram = async () => {
    setTelegramValidating(true);
    setTelegramError("");
    try {
      const res = await fetch(`${GATEWAY}/channels/telegram/setup`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ token: telegramToken }),
      });
      const data = await res.json();
      if (!res.ok) { setTelegramError(data.error); return; }
      setTelegramBotName(`@${data.bot.username}`);
      // Auto-start
      await fetch(`${GATEWAY}/channels/telegram/start`, { method: "POST" });
      await fetchChannels();
    } catch (err) {
      setTelegramError(err instanceof Error ? err.message : "Failed");
    } finally {
      setTelegramValidating(false);
    }
  };

  const startWhatsAppQR = () => {
    setShowWhatsAppSetup(true);
    setWhatsappQR("");
    setWhatsappStatus("connecting");

    // Close existing SSE
    eventSourceRef.current?.close();
    const es = new EventSource(`${GATEWAY}/channels/whatsapp/qr`);
    eventSourceRef.current = es;

    es.onmessage = (event) => {
      try {
        const data = JSON.parse(event.data);
        if (data.qr) setWhatsappQR(data.qr);
        if (data.status === "open") {
          setWhatsappStatus("connected");
          es.close();
          fetchChannels();
        }
        if (data.status) setWhatsappStatus(data.status);
      } catch { /* ignore */ }
    };

    es.onerror = () => {
      setWhatsappStatus("error");
      es.close();
    };
  };

  useEffect(() => {
    return () => { eventSourceRef.current?.close(); };
  }, []);

  const disconnectChannel = async (type: string) => {
    await fetch(`${GATEWAY}/channels/${type}`, { method: "DELETE" });
    setShowTelegramSetup(false);
    setShowWhatsAppSetup(false);
    setTelegramBotName("");
    setWhatsappQR("");
    await fetchChannels();
  };

  const stopChannel = async (type: string) => {
    await fetch(`${GATEWAY}/channels/${type}/stop`, { method: "POST" });
    await fetchChannels();
  };

  const startChannel = async (type: string) => {
    await fetch(`${GATEWAY}/channels/${type}/start`, { method: "POST" });
    await fetchChannels();
  };

  const getChannel = (type: string) => channels.find((c) => c.type === type);
  const telegram = getChannel("telegram");
  const whatsapp = getChannel("whatsapp");

  if (loading) return <div style={{ color: "#8888a0", padding: "24px" }}>Loading channels...</div>;

  return (
    <section style={s.section}>
      <h2 style={s.sectionTitle}>Channels</h2>

      {/* WebChat — always on */}
      <div style={s.card}>
        <div style={s.cardHeader}>
          <span style={s.channelName}>WebChat</span>
          <span style={{ ...s.badge, backgroundColor: "#1a3a1a", color: "#6cffa0" }}>Always on</span>
        </div>
        <p style={s.desc}>Built-in web interface chat</p>
      </div>

      {/* Telegram */}
      <div style={s.card}>
        <div style={s.cardHeader}>
          <span style={s.channelName}>Telegram</span>
          {telegram?.status === "linked" ? (
            <span style={{ ...s.badge, backgroundColor: "#1a3a1a", color: "#6cffa0" }}>Linked</span>
          ) : (
            <span style={{ ...s.badge, backgroundColor: "#2a2a3e", color: "#8888a0" }}>Not linked</span>
          )}
        </div>
        <p style={s.desc}>Paste bot token from @BotFather, then send /start to your bot</p>

        {telegram?.status === "linked" && telegram.botInfo && (
          <div style={s.linkedInfo}>
            <span>Bot: @{telegram.botInfo.username}</span>
            <div style={{ display: "flex", gap: "8px" }}>
              <button style={s.dangerBtn} onClick={() => disconnectChannel("telegram")}>Disconnect</button>
            </div>
          </div>
        )}

        {telegram?.status !== "linked" && !showTelegramSetup && (
          <button style={s.setupBtn} onClick={() => setShowTelegramSetup(true)}>Set Up</button>
        )}

        {showTelegramSetup && !telegramBotName && (
          <div style={s.wizard}>
            <ol style={s.steps}>
              <li>Open @BotFather on Telegram</li>
              <li>Create a bot with /newbot</li>
              <li>Paste the token below:</li>
            </ol>
            <input
              type="text"
              style={s.input}
              value={telegramToken}
              onChange={(e) => setTelegramToken(e.target.value)}
              placeholder="123456:ABC-DEF..."
            />
            {telegramError && <div style={s.error}>{telegramError}</div>}
            <button
              style={{ ...s.setupBtn, opacity: telegramValidating ? 0.5 : 1 }}
              onClick={validateTelegram}
              disabled={telegramValidating || !telegramToken.trim()}
            >
              {telegramValidating ? "Validating..." : "Validate & Connect"}
            </button>
          </div>
        )}

        {telegramBotName && telegram?.status !== "linked" && (
          <div style={s.wizard}>
            <div style={{ color: "#6cffa0" }}>Connected as {telegramBotName}</div>
            <p style={s.desc}>Now send /start to your bot to complete the connection.</p>
          </div>
        )}
      </div>

      {/* WhatsApp */}
      <div style={s.card}>
        <div style={s.cardHeader}>
          <span style={s.channelName}>WhatsApp</span>
          {whatsapp?.status === "linked" ? (
            <span style={{ ...s.badge, backgroundColor: "#1a3a1a", color: "#6cffa0" }}>Linked</span>
          ) : whatsapp?.status === "connecting" ? (
            <span style={{ ...s.badge, backgroundColor: "#2a2a1a", color: "#ffcc44" }}>Connecting</span>
          ) : (
            <span style={{ ...s.badge, backgroundColor: "#2a2a3e", color: "#8888a0" }}>Not linked</span>
          )}
        </div>
        <p style={s.desc}>Scan QR code with WhatsApp on your phone</p>

        {whatsapp?.status === "linked" && (
          <div style={s.linkedInfo}>
            {whatsapp.user && <span>Connected as {whatsapp.user.name || whatsapp.user.id}</span>}
            <button style={s.dangerBtn} onClick={() => disconnectChannel("whatsapp")}>Disconnect</button>
          </div>
        )}

        {whatsapp?.status !== "linked" && !showWhatsAppSetup && (
          <button style={s.setupBtn} onClick={startWhatsAppQR}>Set Up</button>
        )}

        {showWhatsAppSetup && whatsapp?.status !== "linked" && (
          <div style={s.wizard}>
            <p style={s.desc}>Scan this QR code with WhatsApp:</p>
            <p style={s.desc}>Open WhatsApp &rarr; Settings &rarr; Linked Devices &rarr; Link a Device</p>
            {whatsappQR ? (
              <div style={{ textAlign: "center" as const, margin: "16px 0" }}>
                <img src={whatsappQR} alt="WhatsApp QR Code" style={{ width: 256, height: 256, borderRadius: 8 }} />
              </div>
            ) : (
              <div style={{ color: "#8888a0", textAlign: "center" as const, padding: "32px" }}>
                {whatsappStatus === "error" ? "Connection error. Try again." : "Waiting for QR code..."}
              </div>
            )}
          </div>
        )}
      </div>

      {/* Discord — coming soon */}
      <div style={{ ...s.card, opacity: 0.5 }}>
        <div style={s.cardHeader}>
          <span style={s.channelName}>Discord</span>
          <span style={{ ...s.badge, backgroundColor: "#2a2a3e", color: "#8888a0" }}>Coming soon</span>
        </div>
        <p style={s.desc}>Discord bot integration</p>
      </div>

      {/* Slack — coming soon */}
      <div style={{ ...s.card, opacity: 0.5 }}>
        <div style={s.cardHeader}>
          <span style={s.channelName}>Slack</span>
          <span style={{ ...s.badge, backgroundColor: "#2a2a3e", color: "#8888a0" }}>Coming soon</span>
        </div>
        <p style={s.desc}>Slack workspace integration</p>
      </div>
    </section>
  );
}

const s: Record<string, React.CSSProperties> = {
  section: { display: "flex", flexDirection: "column", gap: "16px" },
  sectionTitle: { fontSize: "1.1rem", fontWeight: 600, color: "#6c8aff", margin: "0 0 8px 0" },
  card: { backgroundColor: "#12121a", borderRadius: "12px", padding: "20px", border: "1px solid #1e1e2e" },
  cardHeader: { display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: "8px" },
  channelName: { fontSize: "1rem", fontWeight: 600, color: "#e0e0e8" },
  badge: { fontSize: "0.75rem", padding: "4px 10px", borderRadius: "12px", fontWeight: 500 },
  desc: { color: "#8888a0", fontSize: "0.85rem", margin: "0 0 12px 0" },
  setupBtn: { backgroundColor: "#6c8aff", color: "#fff", border: "none", borderRadius: "8px", padding: "8px 16px", fontSize: "0.85rem", fontWeight: 600, cursor: "pointer" },
  dangerBtn: { backgroundColor: "#3a1a1a", color: "#ff6c8a", border: "1px solid #5a2a2a", borderRadius: "8px", padding: "6px 14px", fontSize: "0.8rem", fontWeight: 500, cursor: "pointer" },
  wizard: { marginTop: "12px", padding: "16px", backgroundColor: "#1e1e2e", borderRadius: "8px" },
  steps: { color: "#e0e0e8", fontSize: "0.85rem", margin: "0 0 12px 0", paddingLeft: "20px" },
  input: { width: "100%", backgroundColor: "#12121a", border: "1px solid #2a2a3e", borderRadius: "8px", padding: "10px 12px", color: "#e0e0e8", fontSize: "0.9rem", outline: "none", marginBottom: "12px", boxSizing: "border-box" as const },
  error: { color: "#ff6c8a", fontSize: "0.85rem", marginBottom: "8px" },
  linkedInfo: { display: "flex", justifyContent: "space-between", alignItems: "center", padding: "12px", backgroundColor: "#1e1e2e", borderRadius: "8px", color: "#e0e0e8", fontSize: "0.85rem" },
};
