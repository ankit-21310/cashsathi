"use client";

import { FormEvent, useCallback, useEffect, useState } from "react";
import { useRouter } from "next/navigation";

import { AuthenticatedShell } from "@/components/authenticated-shell";
import { useAuth } from "@/components/auth-provider";
import {
  apiDownload,
  apiFetch,
  FounderPlan,
  MeResponse,
  OptionalConsentDefinition,
  OptionalConsentResponse,
  OptionalConsentType,
} from "@/lib/api";
import { formatMinorAmount, humanize } from "@/lib/format";

export default function PrivacyPage() {
  const { user, signOut } = useAuth();
  const router = useRouter();
  const [account, setAccount] = useState<MeResponse | null>(null);
  const [consents, setConsents] = useState<OptionalConsentDefinition[]>([]);
  const [plan, setPlan] = useState<FounderPlan | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);

  const load = useCallback(async () => {
    if (!user) return;
    try {
      const [me, consentResult, currentPlan] = await Promise.all([
        apiFetch<MeResponse>(user, "/api/me"),
        apiFetch<OptionalConsentResponse>(user, "/api/privacy/consents"),
        apiFetch<FounderPlan | null>(user, "/api/plans/current"),
      ]);
      setAccount(me); setConsents(consentResult.items); setPlan(currentPlan); setError(null);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Privacy controls are unavailable.");
    }
  }, [user]);

  useEffect(() => {
    const initial = window.setTimeout(() => void load(), 0);
    return () => window.clearTimeout(initial);
  }, [load]);

  async function grant(type: OptionalConsentType, text?: string) {
    if (!user) return;
    const definition = consents.find((item) => item.consent_type === type);
    if (!definition) return;
    try {
      const result = await apiFetch<OptionalConsentResponse>(
        user,
        `/api/privacy/consents/${type}/grant`,
        {
          method: "POST",
          body: JSON.stringify({
            version: definition.version,
            accepted: true,
            approved_text: type === "ANONYMIZED_METRICS" ? null : text,
            channels: type === "ANONYMIZED_METRICS" ? [] : ["XPRIZE_SUBMISSION"],
          }),
        },
      );
      setConsents(result.items); setNotice("Consent recorded."); setError(null);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Consent could not be recorded.");
    }
  }

  async function withdraw(grantId: string) {
    if (!user) return;
    try {
      const result = await apiFetch<OptionalConsentResponse>(
        user,
        `/api/privacy/consents/${grantId}/withdraw`,
        { method: "POST", body: JSON.stringify({ confirmed: true }) },
      );
      setConsents(result.items); setNotice("Consent withdrawn from future exports.");
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Consent could not be withdrawn.");
    }
  }

  async function downloadAccount() {
    if (!user) return;
    try {
      const { blob, filename } = await apiDownload(user, "/api/account/export");
      const url = URL.createObjectURL(blob);
      const link = document.createElement("a");
      link.href = url; link.download = filename; link.click(); URL.revokeObjectURL(url);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Account export failed.");
    }
  }

  async function deleteAccount(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!user || !account?.business) return;
    const form = event.currentTarget;
    const name = String(new FormData(form).get("business_name") ?? "");
    try {
      const result = await apiFetch<{
        google_revocation_instructions_required: boolean;
      }>(user, "/api/account/delete", {
        method: "POST", body: JSON.stringify({ business_name: name, confirmed: true }),
      });
      if (result.google_revocation_instructions_required) {
        window.alert("Local data was deleted. Revoke CashSathi from your Google Account connections.");
      }
      await signOut();
      router.replace("/");
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Account deletion failed.");
    }
  }

  return (
    <AuthenticatedShell><main className="dashboard-main">
      <div className="dashboard-heading"><div><div className="eyebrow">Account control</div><h1>Privacy and plan</h1></div><button className="button button-secondary" onClick={() => void downloadAccount()}>Download my data</button></div>
      {error && <div className="alert alert-error">{error}</div>}
      {notice && <div className="alert alert-success">{notice}</div>}
      <section className="foundation-panel"><div className="panel-heading"><div><h2>Founder Recovery Plan</h2><p>Manual payment; no automatic billing.</p></div></div>{plan ? <div className="policy-preview"><strong>{formatMinorAmount(plan.price_minor, plan.currency)} one-time · {humanize(plan.status)}</strong><span>{plan.invoices_used} of {plan.invoice_limit} confirmed invoices used</span></div> : <div className="centered-state compact">No active plan. Trials and judge demos remain available.</div>}</section>
      <section className="consent-grid">{consents.map((definition) => <ConsentCard key={definition.consent_type} definition={definition} businessName={account?.business?.name ?? ""} onGrant={grant} onWithdraw={withdraw} />)}</section>
      <section className="workflow-card danger-card"><div className="step-number">!</div><div><div className="eyebrow">Irreversible</div><h2>Delete account and tenant data</h2><p>Download your data first. Automation is disabled, Gmail access is revoked when possible, and operational records are purged.</p><form onSubmit={deleteAccount}><label>Type the exact business name<input name="business_name" required placeholder={account?.business?.name ?? "Business name"} /></label><label className="checkbox-label"><input name="confirmed" type="checkbox" required />I understand this permanently deletes the account and tenant data.</label><button className="button button-danger">Permanently delete account</button></form></div></section>
    </main></AuthenticatedShell>
  );
}

function ConsentCard({ definition, businessName, onGrant, onWithdraw }: {
  definition: OptionalConsentDefinition;
  businessName: string;
  onGrant: (type: OptionalConsentType, text?: string) => Promise<void>;
  onWithdraw: (grantId: string) => Promise<void>;
}) {
  const [text, setText] = useState(
    definition.consent_type === "IDENTITY_DISCLOSURE" ? businessName : "",
  );
  return <article className="foundation-panel"><div className="eyebrow">Optional permission</div><h2>{humanize(definition.consent_type)}</h2><p>{definition.statement}</p>{definition.active_grant ? <><div className="policy-preview"><strong>Granted</strong><span>{definition.active_grant.channels.join(", ") || "Aggregated metrics only"}</span></div><button className="button button-ghost" onClick={() => void onWithdraw(definition.active_grant!.id)}>Withdraw</button></> : <>{definition.consent_type !== "ANONYMIZED_METRICS" && <label>{definition.consent_type === "TESTIMONIAL" ? "Exact approved quote" : "Approved identity details"}<textarea required value={text} onChange={(event) => setText(event.target.value)} /></label>}<button className="button button-secondary" disabled={definition.consent_type !== "ANONYMIZED_METRICS" && !text.trim()} onClick={() => void onGrant(definition.consent_type, text)}>Grant for XPRIZE submission</button></>}</article>;
}
