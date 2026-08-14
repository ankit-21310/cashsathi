"use client";

import { FormEvent, useCallback, useEffect, useState } from "react";

import { AuthenticatedShell } from "@/components/authenticated-shell";
import { useAuth } from "@/components/auth-provider";
import { apiFetch, Business, Page, Prospect, ProspectStatus } from "@/lib/api";
import { humanize } from "@/lib/format";

const statuses: ProspectStatus[] = [
  "NOT_CONTACTED", "CONTACTED", "INTERVIEW_SCHEDULED", "INTERVIEWED",
  "DESIGN_PARTNER", "CONVERTED", "DECLINED", "DO_NOT_CONTACT",
];

export default function ValidationPage() {
  const { user } = useAuth();
  const [prospects, setProspects] = useState<Prospect[]>([]);
  const [businesses, setBusinesses] = useState<Business[]>([]);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    if (!user) return;
    try {
      const [prospectPage, businessPage] = await Promise.all([
        apiFetch<Page<Prospect>>(user, "/api/admin/validation/prospects?limit=100"),
        apiFetch<Page<Business>>(user, "/api/admin/businesses?limit=100"),
      ]);
      setProspects(prospectPage.items); setBusinesses(businessPage.items); setError(null);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Validation records are unavailable.");
    }
  }, [user]);

  useEffect(() => {
    const initial = window.setTimeout(() => void load(), 0);
    return () => window.clearTimeout(initial);
  }, [load]);

  async function createProspect(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!user) return;
    const form = event.currentTarget;
    const data = new FormData(form);
    try {
      await apiFetch(user, "/api/admin/validation/prospects", {
        method: "POST",
        body: JSON.stringify({
          company: data.get("company"), city: data.get("city") || null,
          segment: data.get("segment"), public_website: data.get("website") || null,
          public_contact_channel: data.get("contact"), status: "NOT_CONTACTED",
          notes: data.get("notes") || null, next_follow_up_on: null, linked_business_id: null,
        }),
      });
      form.reset(); await load();
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Prospect could not be added.");
    }
  }

  async function updateProspect(prospect: Prospect, status: ProspectStatus, businessId?: string) {
    if (!user) return;
    try {
      await apiFetch(user, `/api/admin/validation/prospects/${prospect.id}`, {
        method: "PATCH",
        body: JSON.stringify({ status, ...(businessId !== undefined ? { linked_business_id: businessId || null } : {}) }),
      });
      await load();
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Prospect could not be updated.");
    }
  }

  async function recordInterview(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!user) return;
    const form = event.currentTarget;
    const data = new FormData(form);
    const prospectId = String(data.get("prospect_id"));
    try {
      await apiFetch(user, `/api/admin/validation/prospects/${prospectId}/interviews`, {
        method: "POST",
        body: JSON.stringify({
          occurred_on: data.get("occurred_on"), current_workflow: data.get("workflow"),
          top_pain: data.get("pain"), trust_boundary: data.get("trust"),
          weekly_receivables_minutes: Number(data.get("minutes")) || null,
          active_invoice_range: data.get("invoice_range") || null,
          automation_comfort: data.get("automation") === "yes",
          required_approval_cases: String(data.get("approval_cases") || "").split(",").map((item) => item.trim()).filter(Boolean),
          willingness_to_pay: data.get("willingness"), feedback: data.get("feedback"),
          follow_up_on: data.get("follow_up_on") || null,
        }),
      });
      form.reset(); await load();
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Interview could not be recorded.");
    }
  }

  return <AuthenticatedShell><main className="dashboard-main">
    <div className="dashboard-heading"><div><div className="eyebrow">Founder-led, manual outreach</div><h1>Customer validation</h1></div><a className="button button-secondary" href="/admin/impact">Evidence dashboard</a></div>
    {error && <div className="alert alert-error">{error}</div>}
    <section className="admin-grid">
      <form className="workflow-card" onSubmit={createProspect}><div className="step-number">1</div><div><h2>Add public-business prospect</h2><p>Do not add scraped personal details or private invoice information.</p><div className="form-grid"><label>Company<input name="company" required /></label><label>City<input name="city" /></label><label>Segment<input name="segment" required /></label><label>Public website<input name="website" type="url" /></label></div><label>Public contact channel<input name="contact" required placeholder="Website contact form" /></label><label>Notes<textarea name="notes" /></label><button className="button button-primary">Add prospect</button></div></form>
      <form className="workflow-card" onSubmit={recordInterview}><div className="step-number">2</div><div><h2>Record interview</h2><label>Prospect<select name="prospect_id" required><option value="">Select prospect</option>{prospects.map((prospect) => <option value={prospect.id} key={prospect.id}>{prospect.company}</option>)}</select></label><div className="form-grid"><label>Date<input name="occurred_on" type="date" required /></label><label>Weekly AR minutes<input name="minutes" type="number" min="0" /></label><label>Active invoice range<input name="invoice_range" placeholder="3–10" /></label><label>Willingness to pay<select name="willingness"><option value="NONE">None</option><option value="MAYBE">Maybe</option><option value="YES">Yes</option></select></label></div><label>Current workflow<textarea name="workflow" required /></label><label>Top pain<textarea name="pain" required /></label><label>Trust boundary<textarea name="trust" required /></label><label>Cases always needing approval<input name="approval_cases" placeholder="Dispute, high value" /></label><label>Feedback<textarea name="feedback" required /></label><label>Follow up<input name="follow_up_on" type="date" /></label><label className="checkbox-label"><input name="automation" type="checkbox" value="yes" />Comfortable with low-risk automation</label><button className="button button-primary">Record interview</button></div></form>
    </section>
    <section className="foundation-panel"><div className="panel-heading"><div><h2>Pipeline</h2><p>{prospects.length} public-business prospects</p></div></div><div className="activity-list">{prospects.map((prospect) => <div className="activity-row validation-row" key={prospect.id}><div><strong>{prospect.company}</strong><p>{prospect.segment}{prospect.city ? ` · ${prospect.city}` : ""} · {prospect.public_contact_channel}</p></div><select aria-label={`Status for ${prospect.company}`} value={prospect.status} onChange={(event) => void updateProspect(prospect, event.target.value as ProspectStatus)}>{statuses.map((status) => <option value={status} key={status}>{humanize(status)}</option>)}</select><select aria-label={`Linked business for ${prospect.company}`} value={prospect.linked_business_id ?? ""} onChange={(event) => void updateProspect(prospect, prospect.status, event.target.value)}><option value="">No linked business</option>{businesses.map((business) => <option value={business.id} key={business.id}>{business.name}</option>)}</select></div>)}</div></section>
  </main></AuthenticatedShell>;
}
