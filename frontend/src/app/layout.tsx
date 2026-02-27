import type { Metadata } from "next";

export const metadata: Metadata = {
  title: "Bond",
  description: "Your local AI assistant",
};

export default function RootLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <html lang="en">
      <body style={{
        margin: 0,
        fontFamily: "-apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif",
        backgroundColor: "#0a0a0f",
        color: "#e0e0e8",
      }}>
        {children}
        <style>{`
          @keyframes pulse { 0%, 100% { opacity: 1; } 50% { opacity: 0.3; } }
          @media (max-width: 768px) {
            .board-main-content { flex-direction: column !important; }
            .board-kanban-area { border-right: none !important; border-bottom: 1px solid #1e1e2e !important; max-height: 60vh !important; }
            .board-chat-panel { max-width: none !important; min-width: 0 !important; }
            .board-columns-container { flex-direction: column !important; }
            .board-column { max-width: none !important; min-width: 0 !important; }
          }
        `}</style>
      </body>
    </html>
  );
}
