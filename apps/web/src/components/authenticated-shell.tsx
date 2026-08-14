"use client";

import { ReactNode, useEffect } from "react";
import { useRouter } from "next/navigation";
import Link from "next/link";

import { useAuth } from "@/components/auth-provider";
import { Brand } from "@/components/brand";

export function AuthenticatedShell({ children }: { children: ReactNode }) {
  const { user, loading, signOut } = useAuth();
  const router = useRouter();

  useEffect(() => {
    if (!loading && !user) router.replace("/login");
  }, [loading, router, user]);

  if (loading || !user) return <main className="centered-state">Checking your secure session…</main>;

  return (
    <div className="app-shell">
      <header className="app-header">
        <div className="app-navigation">
          <Link href="/dashboard"><Brand /></Link>
          <Link href="/dashboard">Invoices</Link>
          <Link href="/invoices/new">Upload</Link>
        </div>
        <div className="user-actions">
          <span>{user.email}</span>
          <button
            className="button button-ghost"
            onClick={async () => {
              await signOut();
              router.replace("/login");
            }}
          >
            Sign out
          </button>
        </div>
      </header>
      {children}
    </div>
  );
}
