import type { NextConfig } from "next";

const stdbUrl = process.env.BOND_SPACETIMEDB_URL;

const nextConfig: NextConfig = {
  async rewrites() {
    return [
      // Proxy SpacetimeDB HTTP endpoints through Next.js to avoid CORS
      {
        source: "/stdb/:path*",
        destination: `${stdbUrl}/:path*`,
      },
    ];
  },
};

export default nextConfig;
