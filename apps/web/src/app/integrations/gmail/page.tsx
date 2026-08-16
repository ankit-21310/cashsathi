"use client";

import { useEffect, useState } from "react";

import { AuthenticatedShell } from "@/components/authenticated-shell";
import { useAuth } from "@/components/auth-provider";
import { apiFetch, GmailStatus, PolicySettings } from "@/lib/api";

export default function GmailIntegrationPage() {
  const { user } = useAuth();
  const [oauthResult, setOauthResult] = useState<string | null>(null);
  const [status, setStatus] = useState<GmailStatus | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!user) return;
    const result = new URLSearchParams(window.location.search).get("status");
    apiFetch<GmailStatus>(user, "/api/integrations/gmail/status").then((current) => {
      setOauthResult(result);
      setStatus(current);
    }).catch((reason: unknown) => setError(reason instanceof Error ? reason.message : "Gmail status unavailable."));
  }, [user]);

  async function connect() {
    if (!user) return;
    setBusy(true);
    try {
      const result = await apiFetch<{ authorization_url: string }>(user, "/api/integrations/gmail/connect", { method: "POST" });
      window.location.assign(result.authorization_url);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Gmail could not be connected.");
      setBusy(false);
    }
  }

  async function setAutomation(enabled: boolean) {
    if (!user) return;
    setBusy(true);
    try {
      const settings = await apiFetch<PolicySettings>(user, "/api/settings/automation", { method: "POST", body: JSON.stringify({ enabled, confirmed: true }) });
      setStatus((current) => current ? { ...current, automation_enabled: settings.automation_enabled } : current);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Automation setting could not be saved.");
    } finally {
      setBusy(false);
    }
  }

  async function disconnect() {
    if (!user) return;
    setBusy(true);
    try {
      const result = await apiFetch<GmailStatus>(user, "/api/integrations/gmail/disconnect", { method: "POST" });
      setStatus(result);
      setError(null);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Gmail could not be disconnected.");
    } finally {
      setBusy(false);
    }
  }

  return (
    <AuthenticatedShell>
      <main className="workflow-main">
        <div className="workflow-heading"><div><div className="eyebrow">Controlled action tool</div><h1>Gmail connection</h1></div></div>
        {oauthResult && <div className="alert">OAuth result: {oauthResult}</div>}
        {error && <div className="alert alert-error">{error}</div>}
        <section className="integration-card">
          <div className="integration-status"><span className={`status-dot ${status?.connected ? "connected" : ""}`} /><div><strong>{status?.connected ? "Gmail connected" : "Gmail not connected"}</strong><p>Only the Gmail send scope is requested. CashSathi cannot read your inbox.</p>{status?.connected_at && <p>Connected {new Date(status.connected_at).toLocaleString("en-IN")}</p>}{status?.last_error_code && <p className="alert alert-error">Last error: {status.last_error_code}</p>}</div></div>
          {!status?.connected ? <button className="button button-primary" disabled={busy} onClick={() => void connect()}>Connect Gmail</button> : <div className="button-row"><button className="button button-primary" disabled={busy} onClick={() => void setAutomation(!status.automation_enabled)}>{status.automation_enabled ? "Disable automatic sending" : "Enable automatic sending"}</button><button className="button button-ghost" disabled={busy} onClick={() => void disconnect()}>Disconnect</button></div>}
          <aside className="tenant-note"><strong>{status?.automation_enabled ? "Live automation enabled" : "Approval-only mode"}</strong><span>High-value, non-INR, manual-only, disputed, demo, and unclassified work remains approval-gated.</span></aside>
        </section>
      </main>
    </AuthenticatedShell>
  );
}
