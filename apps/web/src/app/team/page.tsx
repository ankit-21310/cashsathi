"use client";

import { FormEvent, useCallback, useEffect, useState } from "react";

import { AuthenticatedShell } from "@/components/authenticated-shell";
import { useAuth } from "@/components/auth-provider";
import {
  apiFetch,
  MeResponse,
  Membership,
  MembershipPage,
  TeamInvitation,
  TeamInvitationPage,
} from "@/lib/api";
import { humanize } from "@/lib/format";

export default function TeamPage() {
  const { user } = useAuth();
  const [account, setAccount] = useState<MeResponse | null>(null);
  const [members, setMembers] = useState<Membership[]>([]);
  const [invitations, setInvitations] = useState<TeamInvitation[]>([]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    if (!user) return;
    try {
      const [me, memberPage, invitationPage] = await Promise.all([
        apiFetch<MeResponse>(user, "/api/me"),
        apiFetch<MembershipPage>(user, "/api/team/members"),
        apiFetch<TeamInvitationPage>(user, "/api/team/invitations"),
      ]);
      setAccount(me);
      setMembers(memberPage.items);
      setInvitations(invitationPage.items);
      setError(null);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Team settings are unavailable.");
    }
  }, [user]);

  useEffect(() => {
    const timer = window.setTimeout(() => void load(), 0);
    return () => window.clearTimeout(timer);
  }, [load]);

  async function invite(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!user) return;
    const form = event.currentTarget;
    const data = new FormData(form);
    setBusy(true); setError(null);
    try {
      await apiFetch<TeamInvitation>(user, "/api/team/invitations", {
        method: "POST",
        body: JSON.stringify({ email: data.get("email"), role: data.get("role") }),
      });
      form.reset(); await load();
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Invitation could not be created.");
    } finally { setBusy(false); }
  }

  async function changeRole(event: FormEvent<HTMLFormElement>, member: Membership) {
    event.preventDefault();
    if (!user) return;
    const data = new FormData(event.currentTarget);
    setBusy(true); setError(null);
    try {
      await apiFetch<Membership>(user, `/api/team/members/${member.user_id}`, {
        method: "PATCH",
        body: JSON.stringify({ role: data.get("role"), confirmed: true }),
      });
      await load();
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Role could not be changed.");
    } finally { setBusy(false); }
  }

  async function revokeMember(member: Membership) {
    if (!user || !window.confirm(`Revoke access for ${member.user_id}?`)) return;
    setBusy(true); setError(null);
    try {
      await apiFetch<Membership>(user, `/api/team/members/${member.user_id}/revoke`, {
        method: "POST", body: JSON.stringify({ confirmed: true }),
      });
      await load();
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Member could not be revoked.");
    } finally { setBusy(false); }
  }

  async function revokeInvitation(invitation: TeamInvitation) {
    if (!user) return;
    setBusy(true); setError(null);
    try {
      await apiFetch<TeamInvitation>(user, `/api/team/invitations/${invitation.id}/revoke`, {
        method: "POST", body: JSON.stringify({ confirmed: true }),
      });
      await load();
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Invitation could not be revoked.");
    } finally { setBusy(false); }
  }

  return <AuthenticatedShell><main className="dashboard-main">
    <div className="dashboard-heading"><div><div className="eyebrow">Least-privilege access</div><h1>Team</h1></div></div>
    {error && <div className="alert alert-error">{error}</div>}
    <form className="integration-card" onSubmit={invite}>
      <div><h2>Invite a team member</h2><p className="currency-note">Invitations expire after seven days and must be accepted by the invited email.</p></div>
      <div className="form-grid"><label>Email<input name="email" type="email" required /></label><label>Role<select name="role" defaultValue="OPERATOR"><option value="ADMIN">Admin</option><option value="OPERATOR">Operator</option><option value="ADVISOR">Advisor</option></select></label></div>
      <button className="button button-primary" disabled={busy}>Create invitation</button>
    </form>
    <section className="foundation-panel team-panel">
      <div className="panel-heading"><div><div className="eyebrow">Active and revoked</div><h2>Members</h2></div></div>
      {members.map((member) => <div className="team-row" key={member.user_id}>
        <div><strong>{member.user_id}</strong><p>{humanize(member.status)} · joined {new Date(member.created_at).toLocaleDateString("en-IN")}</p></div>
        {member.role === "OWNER" ? <span className="state-badge">Owner</span> : <form className="team-role-form" onSubmit={(event) => void changeRole(event, member)}><select name="role" defaultValue={member.role} disabled={member.status === "REVOKED"}><option value="ADMIN">Admin</option><option value="OPERATOR">Operator</option><option value="ADVISOR">Advisor</option></select><button className="button button-secondary" disabled={busy || member.status === "REVOKED"}>Save</button></form>}
        {member.role !== "OWNER" && member.user_id !== account?.uid && member.status === "ACTIVE" && <button className="button button-ghost" disabled={busy} onClick={() => void revokeMember(member)}>Revoke</button>}
      </div>)}
    </section>
    <section className="foundation-panel team-panel">
      <div className="panel-heading"><div><div className="eyebrow">Seven-day links</div><h2>Invitations</h2></div></div>
      {invitations.length === 0 ? <div className="empty-panel">No invitations yet.</div> : invitations.map((invitation) => <div className="team-row" key={invitation.id}>
        <div><strong>{invitation.email}</strong><p>{humanize(invitation.role)} · expires {new Date(invitation.expires_at).toLocaleString("en-IN")}</p></div>
        <span className="state-badge">{humanize(invitation.status)}</span>
        <div className="button-row">{invitation.status === "PENDING" && <><button className="button button-secondary" onClick={() => void navigator.clipboard.writeText(`${window.location.origin}/team/invitations/${invitation.id}/accept`)}>Copy link</button><button className="button button-ghost" disabled={busy} onClick={() => void revokeInvitation(invitation)}>Revoke</button></>}</div>
      </div>)}
    </section>
  </main></AuthenticatedShell>;
}
