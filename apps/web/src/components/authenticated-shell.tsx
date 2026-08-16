"use client";

import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { ReactNode, useEffect, useRef, useState } from "react";

import { useAuth } from "@/components/auth-provider";
import { Brand } from "@/components/brand";
import { apiFetch, MeResponse } from "@/lib/api";
import { humanize } from "@/lib/format";

export function AuthenticatedShell({ children }: { children: ReactNode }) {
  const { user, loading, signOut } = useAuth();
  const pathname = usePathname();
  const router = useRouter();
  const [account, setAccount] = useState<MeResponse | null>(null);
  const [menuOpen, setMenuOpen] = useState(false);
  const [moreOpen, setMoreOpen] = useState(false);
  const [accountOpen, setAccountOpen] = useState(false);
  const menuButtonRef = useRef<HTMLButtonElement>(null);
  const navRef = useRef<HTMLElement>(null);
  const moreRef = useRef<HTMLDivElement>(null);
  const moreButtonRef = useRef<HTMLButtonElement>(null);
  const accountRef = useRef<HTMLDivElement>(null);
  const accountButtonRef = useRef<HTMLButtonElement>(null);

  useEffect(() => {
    if (!loading && !user) router.replace("/login");
  }, [loading, router, user]);

  useEffect(() => {
    if (!user) return;
    apiFetch<MeResponse>(user, "/api/me").then(setAccount).catch(() => setAccount(null));
  }, [user]);

  useEffect(() => {
    if (!menuOpen && !moreOpen && !accountOpen) return;

    function closeOnEscape(event: KeyboardEvent) {
      if (event.key !== "Escape") return;

      if (accountOpen) {
        setAccountOpen(false);
        accountButtonRef.current?.focus();
      } else if (moreOpen) {
        setMoreOpen(false);
        moreButtonRef.current?.focus();
      } else {
        setMenuOpen(false);
        menuButtonRef.current?.focus();
      }
    }

    function closeOnOutsideClick(event: MouseEvent) {
      const target = event.target as Node;
      if (!moreRef.current?.contains(target)) setMoreOpen(false);
      if (!accountRef.current?.contains(target)) setAccountOpen(false);
      if (!navRef.current?.contains(target) && !menuButtonRef.current?.contains(target)) setMenuOpen(false);
    }

    window.addEventListener("keydown", closeOnEscape);
    document.addEventListener("mousedown", closeOnOutsideClick);
    return () => {
      window.removeEventListener("keydown", closeOnEscape);
      document.removeEventListener("mousedown", closeOnOutsideClick);
    };
  }, [accountOpen, menuOpen, moreOpen]);

  if (loading || !user) return <main className="centered-state">Checking your secure session…</main>;

  const canManage = account?.membership?.role === "OWNER" || account?.membership?.role === "ADMIN";
  const canViewFinance = canManage || account?.membership?.role === "ADVISOR";
  const moreIsActive = ["/integrations", "/settings", "/team", "/finance", "/privacy", "/admin"].some(
    (route) => pathname.startsWith(route),
  );
  const closeNavigation = () => {
    setMenuOpen(false);
    setMoreOpen(false);
    setAccountOpen(false);
  };
  const isActive = (route: string) => pathname === route || (route !== "/dashboard" && pathname.startsWith(`${route}/`));
  const accountInitial = (user.email?.trim().charAt(0) || "A").toUpperCase();

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
            onClick={() => {
              setMenuOpen((open) => !open);
              setAccountOpen(false);
              setMoreOpen(false);
            }}
          >
            {menuOpen ? "Close" : "Menu"}
          </button>
          <nav
            id="app-navigation"
            ref={navRef}
            className={`app-navigation${menuOpen ? " open" : ""}`}
            aria-label="Primary navigation"
          >
            <Link className={isActive("/dashboard") ? "active" : undefined} aria-current={isActive("/dashboard") ? "page" : undefined} href="/dashboard" onClick={closeNavigation}>Invoices</Link>
            <Link className={isActive("/invoices/new") ? "active" : undefined} aria-current={isActive("/invoices/new") ? "page" : undefined} href="/invoices/new" onClick={closeNavigation}>Upload</Link>
            <Link className={isActive("/activity") ? "active" : undefined} aria-current={isActive("/activity") ? "page" : undefined} href="/activity" onClick={closeNavigation}>Activity</Link>
            <Link className={isActive("/approvals") ? "active" : undefined} aria-current={isActive("/approvals") ? "page" : undefined} href="/approvals" onClick={closeNavigation}>Approvals</Link>
            <Link className={isActive("/impact") ? "active" : undefined} aria-current={isActive("/impact") ? "page" : undefined} href="/impact" onClick={closeNavigation}>Impact</Link>

            <div className="nav-menu nav-more" ref={moreRef}>
              <button
                type="button"
                ref={moreButtonRef}
                className={`nav-menu-button${moreIsActive ? " active" : ""}`}
                aria-expanded={moreOpen}
                aria-controls="more-navigation"
                onClick={() => {
                  setMoreOpen((open) => !open);
                  setAccountOpen(false);
                }}
              >
                More <span aria-hidden="true">⌄</span>
              </button>
              <div id="more-navigation" className={`nav-popover more-navigation${moreOpen ? " open" : ""}`}>
                <div className="nav-group">
                  <span>Connect</span>
                  <Link href="/integrations/gmail" onClick={closeNavigation}>Gmail integration</Link>
                </div>
                {(canManage || canViewFinance) && (
                  <div className="nav-group">
                    <span>Workspace</span>
                    {canManage && <Link href="/settings" onClick={closeNavigation}>Settings</Link>}
                    {canManage && <Link href="/team" onClick={closeNavigation}>Team</Link>}
                    {canViewFinance && <Link href="/finance" onClick={closeNavigation}>Finance</Link>}
                  </div>
                )}
                <div className="nav-group">
                  <span>Trust &amp; safety</span>
                  <Link href="/privacy" onClick={closeNavigation}>Privacy</Link>
                  {account?.is_platform_admin && <Link href="/admin/impact" onClick={closeNavigation}>Admin</Link>}
                </div>
              </div>
            </div>
          </nav>

          <div className="user-actions">
            {account?.business && <span className={`data-pill data-${account.business.data_classification.toLowerCase()}`}>{humanize(account.business.data_classification)}</span>}
            <div className="nav-menu account-menu" ref={accountRef}>
              <button
                type="button"
                ref={accountButtonRef}
                className="account-trigger"
                aria-label={`Account menu for ${user.email}`}
                aria-expanded={accountOpen}
                aria-controls="account-navigation"
                onClick={() => {
                  setAccountOpen((open) => !open);
                  setMoreOpen(false);
                }}
              >
                <span aria-hidden="true">{accountInitial}</span>
                <span className="account-trigger-label">Account</span>
                <span className="account-chevron" aria-hidden="true">⌄</span>
              </button>
              <div id="account-navigation" className={`nav-popover account-navigation${accountOpen ? " open" : ""}`}>
                <div className="account-summary">
                  <strong>{account?.business?.name || "Your workspace"}</strong>
                  <span>{user.email}</span>
                </div>
                <button
                  type="button"
                  className="account-sign-out"
                  onClick={async () => {
                    await signOut();
                    router.replace("/login");
                  }}
                >
                  Sign out
                </button>
              </div>
            </div>
          </div>
        </div>
      </header>
      {children}
    </div>
  );
}
