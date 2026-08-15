"use client";

import Link from "next/link";
import { useParams } from "next/navigation";
import { useState } from "react";

import { AuthenticatedShell } from "@/components/authenticated-shell";
import { useAuth } from "@/components/auth-provider";
import { apiFetch, Membership } from "@/lib/api";
import { humanize } from "@/lib/format";

export default function AcceptInvitationPage() {
  const { invitation_id: invitationId } = useParams<{ invitation_id: string }>();
  const { user } = useAuth();
  const [membership, setMembership] = useState<Membership | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function accept() {
    if (!user || !invitationId) return;
    setBusy(true); setError(null);
    try {
      const accepted = await apiFetch<Membership>(
        user,
        `/api/team/invitations/${invitationId}/accept`,
        { method: "POST" },
      );
      setMembership(accepted);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Invitation could not be accepted.");
    } finally {
      setBusy(false);
    }
  }

  return <AuthenticatedShell><main className="onboarding-main"><section className="onboarding-card">
    <div className="eyebrow">Team invitation</div>
    <h1>{membership ? "Access granted" : "Join this business"}</h1>
    {error && <div className="alert alert-error">{error}</div>}
    {membership ? <><p>You joined with the {humanize(membership.role)} role. Permissions refresh on every API request.</p><Link className="button button-primary" href="/dashboard">Open dashboard</Link></> : <><p>Accept with the exact email address that received the invitation. An account can belong to only one business.</p><button className="button button-primary" disabled={busy} onClick={() => void accept()}>{busy ? "Accepting…" : "Accept invitation"}</button></>}
  </section></main></AuthenticatedShell>;
}
