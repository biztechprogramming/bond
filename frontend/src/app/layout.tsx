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
            .board-kanban-area { border-right: none !important; border-bottom: 1px solid #1e1e2e !important; flex: none !important; max-height: none !important; overflow: visible !important; }
            .board-chat-panel { max-width: none !important; min-width: 0 !important; flex: 1 !important; min-height: 300px !important; }
            .board-columns-container { flex-direction: column !important; gap: 16px !important; min-width: 0 !important; }
            .board-column { max-width: none !important; min-width: 0 !important; }
            .board-header { flex-wrap: wrap !important; gap: 8px !important; }
            .board-plan-selector { max-width: none !important; flex: 1 0 100% !important; margin: 0 !important; order: 10 !important; }
            .board-header-controls .board-pause-btn-inline { display: none !important; }
            .board-fab { display: flex !important; }
          }
        `}</style>
      </body>
    </html>
  );
}
