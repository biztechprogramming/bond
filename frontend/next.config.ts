import type { NextConfig } from "next";

const stdbUrl = process.env.BOND_SPACETIMEDB_URL;
const gatewayUrl = process.env.BOND_GATEWAY_URL || "http://localhost:18789";

const nextConfig: NextConfig = {
  async rewrites() {
    return [
      // Proxy SpacetimeDB HTTP endpoints through Next.js to avoid CORS
      {
        source: "/stdb/:path*",
        destination: `${stdbUrl}/:path*`,
      },
      // Proxy gateway API endpoints (Design Doc 104: image delivery + general API)
      {
        source: "/api/v1/:path*",
        destination: `${gatewayUrl}/api/v1/:path*`,
      },
    ];
  },
};

export default nextConfig;
