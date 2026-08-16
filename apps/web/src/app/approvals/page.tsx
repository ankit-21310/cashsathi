"use client";

import Link from "next/link";
import { useCallback, useEffect, useState } from "react";

import { AuthenticatedShell } from "@/components/authenticated-shell";
import { useAuth } from "@/components/auth-provider";
import { ActionPage, apiFetch, ProposedAction } from "@/lib/api";
import { humanize } from "@/lib/format";

export default function ApprovalsPage() {
  const { user } = useAuth();
  const [actions, setActions] = useState<ProposedAction[]>([]);
  const [loaded, setLoaded] = useState(false);
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    if (!user) return;
    try {
      const page = await apiFetch<ActionPage>(user, "/api/actions?limit=50");
      setActions(page.items.filter((item) => item.state !== "CANCELLED"));
      setError(null);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Actions could not be loaded.");
    } finally {
      setLoaded(true);
    }
  }, [user]);

  useEffect(() => {
    const initial = window.setTimeout(() => void load(), 0);
    const interval = window.setInterval(() => {
      if (document.visibilityState === "visible") void load();
    }, 30_000);
    return () => { window.clearTimeout(initial); window.clearInterval(interval); };
  }, [load]);

  async function post(action: ProposedAction, suffix: string, body?: object) {
    if (!user) return;
    setBusy(action.id);
    setError(null);
    try {
      await apiFetch(user, `/api/actions/${action.id}/${suffix}`, {
        method: "POST",
        body: body ? JSON.stringify(body) : undefined,
      });
      await load();
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "The action could not be updated.");
    } finally {
      setBusy(null);
    }
  }

  return (
    <AuthenticatedShell>
      <main className="dashboard-main">
        <div className="dashboard-heading"><div><div className="eyebrow">Human control</div><h1>Action queue</h1></div><button className="button button-secondary" onClick={() => void load()}>Refresh</button></div>
        {error && <div className="alert alert-error">{error}</div>}
        <div className="action-grid">{actions.map((action) => (
          <article className="action-card" key={action.id}>
            <div className="action-card-heading"><span className={`state-badge state-${action.state.toLowerCase()}`}>{humanize(action.state)}</span><Link href={`/invoices/${action.invoice_id}`}>View invoice</Link></div>
            <h2>{action.subject ?? "Payment reminder"}</h2>
            <p className="message-preview">{action.body ?? "Message preview unavailable."}</p>
            {action.failure_message && <div className="alert alert-error">{action.failure_message}</div>}
            <div className="button-row">
              {action.state === "AWAITING_APPROVAL" && <button className="button button-primary" disabled={busy === action.id} onClick={() => void post(action, "approve")}>Approve & send</button>}
              {action.state === "FAILED" && action.delivery_possible === false && <button className="button button-primary" disabled={busy === action.id} onClick={() => void post(action, "retry")}>Retry confirmed failure</button>}
              {action.state === "UNKNOWN" && <><button className="button button-secondary" disabled={busy === action.id} onClick={() => void post(action, "resolve", { resolution: "CONFIRMED_DELIVERED", confirmed: true })}>Found in Sent</button><button className="button button-ghost" disabled={busy === action.id} onClick={() => void post(action, "resolve", { resolution: "CONFIRMED_NOT_DELIVERED", confirmed: true })}>Not delivered</button></>}
              {action.state === "FAILED" && <button className="button button-secondary" disabled={busy === action.id} onClick={() => void post(action, "resolve", { resolution: "MANUALLY_SENT", confirmed: true })}>Mark manually sent</button>}
              {(["AWAITING_APPROVAL", "PROPOSED", "FAILED"] as string[]).includes(action.state) && <button className="button button-ghost" disabled={busy === action.id} onClick={() => void post(action, "cancel", { reason: "Cancelled by owner from the action queue." })}>Cancel</button>}
              {action.body && <button className="button button-ghost" onClick={() => void navigator.clipboard.writeText(action.body ?? "")}>Copy message</button>}
            </div>
          </article>
        ))}</div>
        {!loaded ? <div className="empty-panel standalone"><strong>Loading action queue…</strong></div> : !actions.length && <div className="empty-panel standalone"><strong>No active actions</strong><p>Approval-gated and failed reminders will appear here.</p></div>}
      </main>
    </AuthenticatedShell>
  );
}
