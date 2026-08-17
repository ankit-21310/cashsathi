import type { NextConfig } from "next";

const isDevelopment = process.env.NODE_ENV === "development";
const apiOrigin = process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://localhost:8000";
const firebaseAuthDomain = process.env.NEXT_PUBLIC_FIREBASE_AUTH_DOMAIN ?? "localhost";
const firebaseOrigins = (
  process.env.NEXT_PUBLIC_CSP_FIREBASE_ORIGINS ??
  "https://*.googleapis.com https://*.firebaseio.com wss://*.firebaseio.com"
).trim();
const firebaseEmulatorOrigin =
  process.env.NEXT_PUBLIC_USE_FIREBASE_EMULATOR === "true"
    ? " http://127.0.0.1:9099"
    : "";
const csp = [
  "default-src 'self'",
  `script-src 'self' 'unsafe-inline' https://apis.google.com https://checkout.razorpay.com${isDevelopment ? " 'unsafe-eval'" : ""}`,
  "style-src 'self' 'unsafe-inline'",
  "img-src 'self' data: blob: https:",
  "font-src 'self' data:",
  `connect-src 'self' ${apiOrigin} https://${firebaseAuthDomain} ${firebaseOrigins}${firebaseEmulatorOrigin} https://identitytoolkit.googleapis.com https://securetoken.googleapis.com https://www.googleapis.com https://api.razorpay.com https://checkout.razorpay.com`,
  `frame-src https://accounts.google.com https://apis.google.com https://${firebaseAuthDomain}${firebaseEmulatorOrigin} https://api.razorpay.com https://checkout.razorpay.com`,
  "object-src 'none'",
  "base-uri 'self'",
  "form-action 'self'",
  "frame-ancestors 'none'",
  ...(isDevelopment ? [] : ["upgrade-insecure-requests"]),
].join("; ");

const nextConfig: NextConfig = {
  distDir: process.env.NEXT_DIST_DIR || ".next",
  ...(process.env.NEXT_STANDALONE === "true" ? { output: "standalone" as const } : {}),
  poweredByHeader: false,
  async headers() {
    return [{
      source: "/(.*)",
      headers: [
        { key: "Content-Security-Policy", value: csp },
        { key: "Referrer-Policy", value: "no-referrer" },
        { key: "X-Content-Type-Options", value: "nosniff" },
        { key: "X-Frame-Options", value: "DENY" },
        { key: "Permissions-Policy", value: "camera=(), microphone=(), geolocation=()" },
        ...(isDevelopment ? [] : [{ key: "Strict-Transport-Security", value: "max-age=31536000; includeSubDomains" }]),
      ],
    }];
  },
};

export default nextConfig;
