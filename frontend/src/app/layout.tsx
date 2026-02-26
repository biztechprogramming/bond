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
        <style>{`nextjs-portal [data-nextjs-toast] { bottom: 5rem !important; }`}</style>
      </body>
    </html>
  );
}
