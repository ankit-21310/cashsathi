"use client";

import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";

import { AuthenticatedShell } from "@/components/authenticated-shell";
import { useAuth } from "@/components/auth-provider";
import { apiFetch, getApiReadiness, MeResponse } from "@/lib/api";

const foundationItems = [
  ["Identity", "Firebase owner authentication", "ready"],
  ["Tenant boundary", "Business derived by the API", "ready"],
  ["Data access", "Server-only Firestore repositories", "ready"],
  ["Invoice intelligence", "PDF extraction and confirmation", "next"],
];

export default function DashboardPage() {
  const { user, loading } = useAuth();
  const router = useRouter();
  const [me, setMe] = useState<MeResponse | null>(null);
  const [apiReady, setApiReady] = useState<boolean | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (loading || !user) return;
    Promise.all([apiFetch<MeResponse>(user, "/api/me"), getApiReadiness()])
      .then(([account, ready]) => {
        if (!account.business) return router.replace("/onboarding");
        setMe(account);
        setApiReady(ready);
      })
      .catch((reason: unknown) => setError(reason instanceof Error ? reason.message : "The workspace could not be loaded."));
  }, [loading, router, user]);

  return (
    <AuthenticatedShell>
      <main className="dashboard-main">
        <div className="dashboard-heading">
          <div><div className="eyebrow">Platform foundation</div><h1>{me?.business?.name ?? "Loading workspace…"}</h1></div>
          <div className={`service-pill ${apiReady === false ? "offline" : ""}`}><span />{apiReady === null ? "Checking API" : apiReady ? "Services ready" : "API unavailable"}</div>
        </div>
        {error && <div className="alert alert-error">{error}</div>}
        <section className="metric-grid" aria-label="Foundation status">
          <article><span>Monitored</span><strong>₹0</strong><small>Invoice phase not started</small></article>
          <article><span>AI decisions</span><strong>0</strong><small>Gemini begins in Phase 2</small></article>
          <article><span>Tenant role</span><strong>{me?.membership?.role ?? "—"}</strong><small>One owner in this preview</small></article>
        </section>
        <section className="foundation-panel">
          <div className="panel-heading"><div><div className="eyebrow">Phase 1 exit gate</div><h2>Secure workspace foundation</h2></div><span>{me ? "3 / 4 ready" : "Loading"}</span></div>
          <div className="foundation-list">
            {foundationItems.map(([title, detail, status]) => (
              <div className="foundation-row" key={title}>
                <span className={`status-icon ${status}`}>{status === "ready" ? "✓" : "→"}</span>
                <div><strong>{title}</strong><p>{detail}</p></div>
                <span className="row-status">{status === "ready" ? "Ready" : "Next phase"}</span>
              </div>
            ))}
          </div>
        </section>
        <aside className="tenant-note"><strong>Tenant isolation is active.</strong><span>Your browser never chooses a business ID. The API resolves business <code>{me?.business?.id ?? "…"}</code> from your verified membership.</span></aside>
      </main>
    </AuthenticatedShell>
  );
}
