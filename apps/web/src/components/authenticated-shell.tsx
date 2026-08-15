"use client";

import { ReactNode, useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import Link from "next/link";

import { useAuth } from "@/components/auth-provider";
import { Brand } from "@/components/brand";
import { apiFetch, MeResponse } from "@/lib/api";
import { humanize } from "@/lib/format";

export function AuthenticatedShell({ children }: { children: ReactNode }) {
  const { user, loading, signOut } = useAuth();
  const router = useRouter();
  const [account, setAccount] = useState<MeResponse | null>(null);

  useEffect(() => {
    if (!loading && !user) router.replace("/login");
  }, [loading, router, user]);

  useEffect(() => {
    if (!user) return;
    apiFetch<MeResponse>(user, "/api/me").then(setAccount).catch(() => setAccount(null));
  }, [user]);

  if (loading || !user) return <main className="centered-state">Checking your secure session…</main>;

  return (
    <div className="app-shell">
      <header className="app-header">
        <div className="app-navigation">
          <Brand href="/dashboard" />
          <Link href="/dashboard">Invoices</Link>
          <Link href="/invoices/new">Upload</Link>
          <Link href="/activity">Activity</Link>
          <Link href="/approvals">Approvals</Link>
          <Link href="/impact">Impact</Link>
          <Link href="/integrations/gmail">Gmail</Link>
          {(account?.membership?.role === "OWNER" || account?.membership?.role === "ADMIN") && <Link href="/settings">Settings</Link>}
          {(account?.membership?.role === "OWNER" || account?.membership?.role === "ADMIN") && <Link href="/team">Team</Link>}
          {(account?.membership?.role === "OWNER" || account?.membership?.role === "ADMIN" || account?.membership?.role === "ADVISOR") && <Link href="/finance">Finance</Link>}
          <Link href="/privacy">Privacy</Link>
          {account?.is_platform_admin && <Link href="/admin/impact">Admin</Link>}
        </div>
        <div className="user-actions">
          {account?.business && <span className={`data-pill data-${account.business.data_classification.toLowerCase()}`}>{humanize(account.business.data_classification)}</span>}
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
