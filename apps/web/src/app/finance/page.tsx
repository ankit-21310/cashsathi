"use client";

import { useEffect, useState } from "react";

import { AuthenticatedShell } from "@/components/authenticated-shell";
import { useAuth } from "@/components/auth-provider";
import { apiDownload, apiFetch, CashForecast } from "@/lib/api";
import { formatMinorAmount } from "@/lib/format";

export default function FinancePage() {
  const { user } = useAuth();
  const [forecast, setForecast] = useState<CashForecast | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!user) return;
    apiFetch<CashForecast>(user, "/api/forecast")
      .then(setForecast)
      .catch((reason: unknown) =>
        setError(reason instanceof Error ? reason.message : "Forecast is unavailable."),
      );
  }, [user]);

  async function downloadPack() {
    if (!user) return;
    setBusy(true);
    setError(null);
    try {
      const { blob, filename } = await apiDownload(user, "/api/finance-pack");
      const url = URL.createObjectURL(blob);
      const anchor = document.createElement("a");
      anchor.href = url;
      anchor.download = filename;
      anchor.click();
      URL.revokeObjectURL(url);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Finance pack could not be generated.");
    } finally {
      setBusy(false);
    }
  }

  return (
    <AuthenticatedShell>
      <main className="dashboard-main">
        <div className="dashboard-heading">
          <div>
            <div className="eyebrow">Deterministic planning</div>
            <h1>Cash forecast</h1>
          </div>
          <button className="button button-primary" disabled={busy} onClick={() => void downloadPack()}>
            {busy ? "Generating…" : "Download finance pack"}
          </button>
        </div>
        {error && <div className="alert alert-error">{error}</div>}
        {!forecast ? <div className="centered-state compact">Calculating forecast…</div> : <>
          <div className="data-banner">
            <strong>{forecast.observed_payment_delay_days}-day observed payment delay</strong>
            <span>Numbers use due dates and verified payment history only. Gemini cannot alter them.</span>
          </div>
          <section className="metric-grid">
            {forecast.horizons.map((horizon) => <article key={horizon.weeks}>
              <span>{horizon.weeks}-week expected inflow</span>
              <strong>{Object.entries(horizon.expected_inflow_by_currency).map(([currency, amount]) => formatMinorAmount(amount, currency)).join(" · ") || "No dated inflow"}</strong>
              <small>{horizon.buckets.reduce((total, bucket) => total + bucket.invoice_count, 0)} invoice placements</small>
            </article>)}
          </section>
          <section className="foundation-panel">
            <div className="panel-heading"><div><div className="eyebrow">12-week detail</div><h2>Expected receipt windows</h2></div></div>
            {forecast.horizons[2].buckets.map((bucket) => <div className="foundation-row" key={bucket.starts_on}>
              <span className="status-icon">{bucket.invoice_count}</span>
              <div><strong>{new Date(`${bucket.starts_on}T00:00:00`).toLocaleDateString("en-IN")} – {new Date(`${bucket.ends_on}T00:00:00`).toLocaleDateString("en-IN")}</strong><p>{Object.entries(bucket.expected_inflow_by_currency).map(([currency, amount]) => formatMinorAmount(amount, currency)).join(" · ") || "No expected inflow"}</p></div>
              <span className="row-status">Due-date projection</span>
            </div>)}
          </section>
        </>}
      </main>
    </AuthenticatedShell>
  );
}
