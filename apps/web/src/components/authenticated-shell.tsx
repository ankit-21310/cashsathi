"use client";

import { ReactNode, useEffect, useRef, useState } from "react";
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
  const [menuOpen, setMenuOpen] = useState(false);
  const menuButtonRef = useRef<HTMLButtonElement>(null);
  const navRef = useRef<HTMLElement>(null);

  useEffect(() => {
    if (!loading && !user) router.replace("/login");
  }, [loading, router, user]);

  useEffect(() => {
    if (!user) return;
    apiFetch<MeResponse>(user, "/api/me").then(setAccount).catch(() => setAccount(null));
  }, [user]);

  useEffect(() => {
    if (!menuOpen) return;

    function closeOnEscape(event: KeyboardEvent) {
      if (event.key === "Escape") {
        setMenuOpen(false);
        menuButtonRef.current?.focus();
      }
    }

    function closeOnOutsideClick(event: MouseEvent) {
      const target = event.target as Node;
      if (navRef.current?.contains(target) || menuButtonRef.current?.contains(target)) return;
      setMenuOpen(false);
    }

    window.addEventListener("keydown", closeOnEscape);
    document.addEventListener("mousedown", closeOnOutsideClick);
    return () => {
      window.removeEventListener("keydown", closeOnEscape);
      document.removeEventListener("mousedown", closeOnOutsideClick);
    };
  }, [menuOpen]);

  if (loading || !user) return <main className="centered-state">Checking your secure session…</main>;

  return (
    <div className="app-shell">
      <header className="app-header">
        <div className="app-header-inner">
          <Brand href="/dashboard" />
          <button
            type="button"
            ref={menuButtonRef}
            className="nav-toggle button button-ghost"
            aria-expanded={menuOpen}
            aria-controls="app-navigation"
            onClick={() => setMenuOpen((open) => !open)}
          >
            {menuOpen ? "Close" : "Menu"}
          </button>
          <nav
            id="app-navigation"
            ref={navRef}
            className={`app-navigation${menuOpen ? " open" : ""}`}
            aria-label="Primary navigation"
          >
            <Link href="/dashboard" onClick={() => setMenuOpen(false)}>Invoices</Link>
            <Link href="/invoices/new" onClick={() => setMenuOpen(false)}>Upload</Link>
            <Link href="/activity" onClick={() => setMenuOpen(false)}>Activity</Link>
            <Link href="/approvals" onClick={() => setMenuOpen(false)}>Approvals</Link>
            <Link href="/impact" onClick={() => setMenuOpen(false)}>Impact</Link>
            <Link href="/integrations/gmail" onClick={() => setMenuOpen(false)}>Gmail</Link>
            {(account?.membership?.role === "OWNER" || account?.membership?.role === "ADMIN") && <Link href="/settings" onClick={() => setMenuOpen(false)}>Settings</Link>}
            {(account?.membership?.role === "OWNER" || account?.membership?.role === "ADMIN") && <Link href="/team" onClick={() => setMenuOpen(false)}>Team</Link>}
            {(account?.membership?.role === "OWNER" || account?.membership?.role === "ADMIN" || account?.membership?.role === "ADVISOR") && <Link href="/finance" onClick={() => setMenuOpen(false)}>Finance</Link>}
            <Link href="/privacy" onClick={() => setMenuOpen(false)}>Privacy</Link>
            {account?.is_platform_admin && <Link href="/admin/impact" onClick={() => setMenuOpen(false)}>Admin</Link>}
          </nav>
          <div className="user-actions">
            {account?.business && <span className={`data-pill data-${account.business.data_classification.toLowerCase()}`}>{humanize(account.business.data_classification)}</span>}
            <span className="user-email">{user.email}</span>
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
        </div>
      </header>
      {children}
    </div>
  );
}
