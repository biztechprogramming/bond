import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  async rewrites() {
    return [
      // Proxy SpacetimeDB HTTP endpoints through Next.js to avoid CORS
      {
        source: "/stdb/:path*",
        destination: "http://localhost:18787/:path*",
      },
    ];
  },
};

export default nextConfig;
