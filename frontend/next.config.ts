import type { NextConfig } from "next";

// Where /api/* is proxied. Read when the app is built (rewrites are compiled into the build),
// so set it as a build argument in Docker and on Render.
const API_TARGET = (process.env.API_URL || process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000")
  .trim()
  .replace(/\/+$/, "");

const isDevelopment = process.env.NODE_ENV !== "production";

// Next.js inlines small bootstrap scripts and styles, so 'unsafe-inline' is required without
// per-request nonces; everything else is limited to this origin. All API traffic is same-origin
// through /api, so connect-src needs nothing more. Development adds eval and the HMR socket.
const contentSecurityPolicy = [
  "default-src 'self'",
  `script-src 'self' 'unsafe-inline'${isDevelopment ? " 'unsafe-eval'" : ""}`,
  "style-src 'self' 'unsafe-inline'",
  "img-src 'self' data: blob:",
  "font-src 'self' data:",
  `connect-src 'self'${isDevelopment ? " ws: wss:" : ""}`,
  "object-src 'none'",
  "base-uri 'self'",
  "form-action 'self'",
  "frame-ancestors 'none'",
].join("; ");

const securityHeaders = [
  { key: "Content-Security-Policy", value: contentSecurityPolicy },
  { key: "X-Frame-Options", value: "DENY" },
  { key: "X-Content-Type-Options", value: "nosniff" },
  { key: "Referrer-Policy", value: "strict-origin-when-cross-origin" },
  { key: "Permissions-Policy", value: "camera=(), microphone=(), geolocation=()" },
];

const nextConfig: NextConfig = {
  output: "standalone",
  poweredByHeader: false,
  async headers() {
    return [
      { source: "/:path*", headers: securityHeaders },
      // Customer payment URLs carry a bearer token: never leak them in a Referer header.
      { source: "/pay/:token", headers: [{ key: "Referrer-Policy", value: "no-referrer" }] },
    ];
  },
  async rewrites() {
    return [{ source: "/api/:path*", destination: `${API_TARGET}/:path*` }];
  },
  async redirects() {
    // The simulator used to be reachable at /simulation as well.
    return [{ source: "/simulation", destination: "/simulator", permanent: true }];
  },
};

export default nextConfig;
