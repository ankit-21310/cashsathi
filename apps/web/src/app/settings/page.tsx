"use client";

import { FormEvent, useEffect, useState } from "react";

import { AuthenticatedShell } from "@/components/authenticated-shell";
import { useAuth } from "@/components/auth-provider";
import { apiFetch, PolicySettings, PolicyTemplate, PolicyTemplatePage } from "@/lib/api";
import { formatMinorAmount } from "@/lib/format";

export default function SettingsPage() {
  const { user } = useAuth();
  const [settings, setSettings] = useState<PolicySettings | null>(null);
  const [templates, setTemplates] = useState<PolicyTemplate[]>([]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [success, setSuccess] = useState<string | null>(null);

  useEffect(() => {
    if (!user) return;
    Promise.all([
      apiFetch<PolicySettings>(user, "/api/settings/automation"),
      apiFetch<PolicyTemplatePage>(user, "/api/settings/policy/templates"),
    ])
      .then(([current, templatePage]) => {
        setSettings(current);
        setTemplates(templatePage.items);
      })
      .catch((reason: unknown) =>
        setError(reason instanceof Error ? reason.message : "Policy settings are unavailable."),
      );
  }, [user]);

  async function savePolicy(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!user || !settings) return;
    const data = new FormData(event.currentTarget);
    setBusy(true);
    setError(null);
    setSuccess(null);
    try {
      const updated = await apiFetch<PolicySettings>(user, "/api/settings/policy", {
        method: "PATCH",
        body: JSON.stringify({
          reminder_cooldown_hours: Number(data.get("cooldown")),
          high_value_threshold_minor: Math.round(Number(data.get("threshold_rupees")) * 100),
          confirmed: true,
        }),
      });
      setSettings(updated);
      setSuccess("Restrictive policy saved and added to the audit history.");
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Policy could not be updated.");
    } finally {
      setBusy(false);
    }
  }

  async function applyTemplate(template: PolicyTemplate) {
    if (!user) return;
    setBusy(true);
    setError(null);
    setSuccess(null);
    try {
      const updated = await apiFetch<PolicySettings>(
        user,
        `/api/settings/policy/templates/${template.template}/apply`,
        { method: "POST", body: JSON.stringify({ confirmed: true }) },
      );
      setSettings(updated);
      const templatePage = await apiFetch<PolicyTemplatePage>(
        user,
        "/api/settings/policy/templates",
      );
      setTemplates(templatePage.items);
      setSuccess(`${template.label} safeguards applied and added to the audit history.`);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Policy template could not be applied.");
    } finally {
      setBusy(false);
    }
  }

  return (
    <AuthenticatedShell>
      <main className="workflow-main">
        <div className="workflow-heading">
          <div>
            <div className="eyebrow">Owner-controlled safeguards</div>
            <h1>Collection policy</h1>
          </div>
        </div>
        {error && <div className="alert alert-error">{error}</div>}
        {success && <div className="alert alert-success">{success}</div>}
        {!settings ? (
          <div className="centered-state compact">Loading restrictive policy…</div>
        ) : (
          <><section className="foundation-panel">
            <div className="panel-heading"><div><h2>Safe starting templates</h2><p>Templates only tighten your current policy; immutable hard stops remain active.</p></div></div>
            <div className="template-grid">
              {templates.map((template) => (
                <article className="template-card" key={template.template}>
                  <strong>{template.label}</strong>
                  <span>{template.reminder_cooldown_hours}-hour cooldown</span>
                  <span>{formatMinorAmount(template.high_value_threshold_minor, "INR")} approval threshold</span>
                  <button className="button button-secondary" disabled={busy} onClick={() => void applyTemplate(template)}>Apply safely</button>
                </article>
              ))}
            </div>
          </section><form className="integration-card" onSubmit={savePolicy}>
            <div>
              <h2>Restriction-only controls</h2>
              <p className="currency-note">
                Changes can make automation more cautious, never less cautious. Saved values
                cannot later be loosened.
              </p>
            </div>
            <div className="form-grid">
              <label>
                Reminder cooldown (hours)
                <input
                  name="cooldown"
                  type="number"
                  min={settings.reminder_cooldown_hours}
                  max={720}
                  defaultValue={settings.reminder_cooldown_hours}
                  required
                />
                <small>May only increase from the current {settings.reminder_cooldown_hours} hours.</small>
              </label>
              <label>
                High-value approval threshold (₹)
                <input
                  name="threshold_rupees"
                  type="number"
                  min={0}
                  max={settings.high_value_threshold_minor / 100}
                  step="0.01"
                  defaultValue={settings.high_value_threshold_minor / 100}
                  required
                />
                <small>
                  May only decrease from {formatMinorAmount(settings.high_value_threshold_minor, "INR")}.
                </small>
              </label>
            </div>
            <div className="policy-preview">
              <strong>Immutable hard stops</strong>
              <span>Active disputes always require human review.</span>
              <span>Legal or threatening language is never permitted.</span>
              <span>Only owner-confirmed payments can close an invoice.</span>
            </div>
            <label className="checkbox-label">
              <input type="checkbox" required />
              I understand this update is append-only and cannot weaken the current safeguards.
            </label>
            <button className="button button-primary" disabled={busy}>
              {busy ? "Saving…" : "Save restrictive policy"}
            </button>
          </form></>
        )}
      </main>
    </AuthenticatedShell>
  );
}
