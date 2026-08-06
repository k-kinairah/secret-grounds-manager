import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  // The cashier installer bundles a private Node.js runtime and runs the
  // standalone server.js directly (see scripts\start-dashboard.ps1) so a
  // cashier PC never needs Node.js or npm installed. Standalone output
  // traces and copies only the dependencies server.js actually needs
  // instead of shipping the full dashboard\node_modules tree.
  output: "standalone",
  turbopack: {
    root: __dirname
  }
};

export default nextConfig;
