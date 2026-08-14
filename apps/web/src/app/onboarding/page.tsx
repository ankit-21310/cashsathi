"use client";

import { FormEvent, useEffect, useState } from "react";
import { useRouter } from "next/navigation";

import { AuthenticatedShell } from "@/components/authenticated-shell";
import { useAuth } from "@/components/auth-provider";
import { apiFetch, Business, MeResponse } from "@/lib/api";

export default function OnboardingPage() {
  const { user, loading } = useAuth();
  const router = useRouter();
  const [businessName, setBusinessName] = useState("");
  const [checking, setChecking] = useState(true);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (loading || !user) return;
    apiFetch<MeResponse>(user, "/api/me")
      .then((me) => {
        if (me.business) router.replace("/dashboard");
        else setChecking(false);
      })
      .catch((reason: unknown) => {
        setError(reason instanceof Error ? reason.message : "The account could not be loaded.");
        setChecking(false);
      });
  }, [loading, router, user]);

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!user) return;
    setSubmitting(true);
    setError(null);
    try {
      await apiFetch<Business>(user, "/api/businesses", {
        method: "POST",
        body: JSON.stringify({ name: businessName }),
      });
      router.replace("/dashboard");
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "The business could not be created.");
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <AuthenticatedShell>
      <main className="onboarding-main">
        <div className="progress-label">Foundation setup · 1 of 1</div>
        <section className="onboarding-card">
          <div className="eyebrow">Create your isolated workspace</div>
          <h1>What should we call your business?</h1>
          <p>This name identifies your tenant. Customer and invoice setup begins in the next product phase.</p>
          {error && <div className="alert alert-error">{error}</div>}
          {checking ? <div className="centered-state compact">Checking existing membership…</div> : (
            <form onSubmit={submit}>
              <label>Business name<input autoFocus minLength={2} maxLength={100} required placeholder="Aster Creative Studio" value={businessName} onChange={(event) => setBusinessName(event.target.value)} /></label>
              <div className="policy-preview"><strong>Safe defaults included</strong><span>72-hour cooldown · ₹50,000 approval threshold · disputes escalated</span></div>
              <button className="button button-primary full-width" disabled={submitting}>{submitting ? "Creating secure workspace…" : "Create business workspace"}</button>
            </form>
          )}
        </section>
      </main>
    </AuthenticatedShell>
  );
}
