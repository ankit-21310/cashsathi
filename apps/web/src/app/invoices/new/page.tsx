"use client";

import { FormEvent, useEffect, useState } from "react";
import { useRouter } from "next/navigation";

import { AuthenticatedShell } from "@/components/authenticated-shell";
import { useAuth } from "@/components/auth-provider";
import { RazorpayCheckout } from "@/components/razorpay-checkout";
import { ApiClientError, apiFetch, ConsentStatus, ExtractionResult, Invoice } from "@/lib/api";

const MAX_PDF_BYTES = 10 * 1024 * 1024;

interface ReviewFields {
  invoice_number: string;
  customer_name: string;
  customer_email: string;
  amount_decimal: string;
  currency: string;
  issue_date: string;
  due_date: string;
  payment_terms: string;
  customer_manual_only: boolean;
}

const emptyReview: ReviewFields = {
  invoice_number: "",
  customer_name: "",
  customer_email: "",
  amount_decimal: "",
  currency: "INR",
  issue_date: "",
  due_date: "",
  payment_terms: "",
  customer_manual_only: false,
};

export default function NewInvoicePage() {
  const { user, loading } = useAuth();
  const router = useRouter();
  const [consent, setConsent] = useState<ConsentStatus | null>(null);
  const [consentChecked, setConsentChecked] = useState(false);
  const [file, setFile] = useState<File | null>(null);
  const [extraction, setExtraction] = useState<ExtractionResult | null>(null);
  const [review, setReview] = useState<ReviewFields>(emptyReview);
  const [confirmed, setConfirmed] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [paymentRequired, setPaymentRequired] = useState(false);

  useEffect(() => {
    if (loading || !user) return;
    apiFetch<ConsentStatus>(user, "/api/consents/product-processing")
      .then(setConsent)
      .catch((reason: unknown) =>
        setError(reason instanceof Error ? reason.message : "Consent status could not be loaded."),
      );
  }, [loading, user]);

  async function acceptConsent() {
    if (!user || !consent || !consentChecked) return;
    setBusy(true);
    setError(null);
    try {
      setConsent(await apiFetch<ConsentStatus>(user, "/api/consents/product-processing", {
        method: "POST",
        body: JSON.stringify({ version: consent.version, accepted: true }),
      }));
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Consent could not be recorded.");
    } finally {
      setBusy(false);
    }
  }

  async function extract(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!user || !file) return;
    if (!file.name.toLowerCase().endsWith(".pdf") || file.type !== "application/pdf") {
      setError("Choose a PDF file with application/pdf type.");
      return;
    }
    if (file.size > MAX_PDF_BYTES) {
      setError("PDF files may not exceed 10 MiB.");
      return;
    }
    setBusy(true);
    setError(null);
    try {
      const formData = new FormData();
      formData.set("file", file);
      const result = await apiFetch<ExtractionResult>(user, "/api/invoices/extract", {
        method: "POST",
        body: formData,
      });
      setExtraction(result);
      setReview({
        invoice_number: result.draft.invoice_number ?? "",
        customer_name: result.draft.customer_name ?? "",
        customer_email: result.draft.customer_email ?? "",
        amount_decimal: result.draft.amount_decimal ?? "",
        currency: result.draft.currency ?? "INR",
        issue_date: result.draft.issue_date ?? "",
        due_date: result.draft.due_date ?? "",
        payment_terms: result.draft.payment_terms ?? "",
        customer_manual_only: false,
      });
      setFile(null);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "The invoice could not be extracted.");
    } finally {
      setBusy(false);
    }
  }

  function setField<K extends keyof ReviewFields>(field: K, value: ReviewFields[K]) {
    setReview((current) => ({ ...current, [field]: value }));
  }

  async function saveInvoice(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!user || !extraction || !confirmed) return;
    setBusy(true);
    setError(null);
    try {
      const invoice = await apiFetch<Invoice>(user, "/api/invoices", {
        method: "POST",
        body: JSON.stringify({
          extraction_id: extraction.extraction_id,
          ...review,
          issue_date: review.issue_date || null,
          due_date: review.due_date || null,
          payment_terms: review.payment_terms || null,
          customer_email: review.customer_email || null,
          confirmed: true,
        }),
      });
      router.push(`/invoices/${invoice.id}`);
    } catch (reason) {
      if (reason instanceof ApiClientError && reason.code === "payment_required") {
        setPaymentRequired(true);
      }
      setError(reason instanceof Error ? reason.message : "The invoice could not be confirmed.");
    } finally {
      setBusy(false);
    }
  }

  return (
    <AuthenticatedShell>
      <main className="workflow-main">
        <div className="workflow-heading">
          <div><div className="eyebrow">Phase 2 · Invoice intelligence</div><h1>Import an invoice safely</h1></div>
          <button className="button button-ghost" onClick={() => router.push("/dashboard")}>Back to dashboard</button>
        </div>
        {error && <div className="alert alert-error">{error}</div>}
        {!consent ? <div className="centered-state compact">Loading privacy boundary…</div> : !consent.granted ? (
          <section className="workflow-card consent-card">
            <div className="step-number">01</div>
            <div><div className="eyebrow">Required consent</div><h2>Authorize invoice processing</h2><p className="consent-copy">{consent.statement}</p>
              <label className="checkbox-label"><input type="checkbox" checked={consentChecked} onChange={(event) => setConsentChecked(event.target.checked)} /><span>I have read and accept version {consent.version}.</span></label>
              <button className="button button-primary" disabled={!consentChecked || busy} onClick={acceptConsent}>{busy ? "Recording consent…" : "Accept and continue"}</button>
            </div>
          </section>
        ) : !extraction ? (
          <section className="workflow-card upload-card">
            <div className="step-number">02</div>
            <div><div className="eyebrow">Transient processing</div><h2>Choose the invoice PDF</h2><p>The PDF is sent inline for extraction and discarded after this request. Only fields you confirm are saved.</p>
              <form onSubmit={extract}><label>Invoice PDF<input type="file" accept="application/pdf,.pdf" required onChange={(event) => setFile(event.target.files?.[0] ?? null)} /></label><div className="upload-limits">PDF · maximum 10 MiB · maximum 25 pages · encrypted files unsupported</div><button className="button button-primary" disabled={!file || busy}>{busy ? "Extracting with Gemini…" : "Extract invoice facts"}</button></form>
            </div>
          </section>
        ) : (
          <section className="workflow-card review-card">
            <div className="step-number">03</div>
            <div><div className="eyebrow">Review required</div><h2>Confirm every extracted fact</h2><p>Gemini used {extraction.model_id} in {extraction.latency_ms} ms. Correct any value before saving.</p>
              {extraction.draft.warnings.length > 0 && <div className="warning-list">{extraction.draft.warnings.map((warning, index) => <div key={`${warning.code}-${warning.field}-${index}`}><strong>{warning.field?.replaceAll("_", " ") ?? "Invoice"}</strong><span>{warning.message}</span></div>)}</div>}
              <form onSubmit={saveInvoice} className="review-form">
                <div className="form-grid">
                  <label>Invoice number<input required value={review.invoice_number} onChange={(e) => setField("invoice_number", e.target.value)} /></label>
                  <label>Customer name<input required value={review.customer_name} onChange={(e) => setField("customer_name", e.target.value)} /></label>
                  <label>Customer email<input type="email" value={review.customer_email} onChange={(e) => setField("customer_email", e.target.value)} /></label>
                  <label>Amount<input required inputMode="decimal" value={review.amount_decimal} onChange={(e) => setField("amount_decimal", e.target.value)} /></label>
                  <label>Currency<input required minLength={3} maxLength={3} value={review.currency} onChange={(e) => setField("currency", e.target.value.toUpperCase())} /></label>
                  <label>Issue date<input type="date" value={review.issue_date} onChange={(e) => setField("issue_date", e.target.value)} /></label>
                  <label>Due date <small>(optional)</small><input type="date" value={review.due_date} onChange={(e) => setField("due_date", e.target.value)} /></label>
                  <label>Payment terms<input value={review.payment_terms} onChange={(e) => setField("payment_terms", e.target.value)} /></label>
                </div>
                <label className="checkbox-label"><input type="checkbox" checked={review.customer_manual_only} onChange={(e) => setField("customer_manual_only", e.target.checked)} /><span>Always require approval for this customer</span></label>
                <label className="checkbox-label confirmation"><input type="checkbox" checked={confirmed} onChange={(e) => setConfirmed(e.target.checked)} /><span>I checked these fields against the invoice and confirm they are accurate.</span></label>
                {paymentRequired && <div className="invoice-paywall"><div><div className="eyebrow">Payment required</div><strong>Activate the ₹299 Founder Recovery Plan</strong><p>Your reviewed fields stay here. Complete checkout, then confirm this invoice again.</p></div><RazorpayCheckout label="Activate plan · ₹299" onConfirmed={(result) => { if (result.status === "CAPTURED") { setPaymentRequired(false); setError(null); } }} /></div>}
                <button className="button button-primary" disabled={!confirmed || busy}>{busy ? "Saving confirmed invoice…" : "Confirm invoice"}</button>
              </form>
            </div>
          </section>
        )}
      </main>
    </AuthenticatedShell>
  );
}
