import type { Metadata } from "next";
import { Geist, Geist_Mono } from "next/font/google";
import { AuthProvider } from "@/components/auth-provider";
import { PREVIEW_DISABLED_MESSAGE, publicAppMode, publicProductName } from "@/lib/env";
import "./globals.css";

const geistSans = Geist({
  variable: "--font-geist-sans",
  subsets: ["latin"],
});

const geistMono = Geist_Mono({
  variable: "--font-geist-mono",
  subsets: ["latin"],
});

export const metadata: Metadata = {
  title: { default: publicProductName(), template: `%s · ${publicProductName()}` },
  description: "A policy-controlled accounts-receivable operator for small businesses.",
};

export default function RootLayout({ children }: LayoutProps<"/">) {
  return (
    <html
      lang="en"
      className={`${geistSans.variable} ${geistMono.variable} h-full antialiased`}
    >
      <body className="min-h-full flex flex-col" suppressHydrationWarning>
        {publicAppMode() === "preview-disabled" && (
          <div className="alert alert-error" role="status">
            {PREVIEW_DISABLED_MESSAGE}
          </div>
        )}
        <AuthProvider>{children}</AuthProvider>
      </body>
    </html>
  );
}
