"use client";

import { useRef, useState } from "react";

import { useAuth } from "@/components/auth-provider";
import {
  apiFetch,
  BillingCheckoutOrder,
  BillingConfirmation,
} from "@/lib/api";

interface RazorpayResult {
  razorpay_order_id: string;
  razorpay_payment_id: string;
  razorpay_signature: string;
}

interface RazorpayInstance {
  open(): void;
  on(name: "payment.failed", callback: (event: { error?: { description?: string } }) => void): void;
}

interface RazorpayConstructor {
  new (options: Record<string, unknown>): RazorpayInstance;
}

declare global {
  interface Window {
    Razorpay?: RazorpayConstructor;
  }
}

let checkoutLoader: Promise<void> | null = null;

function loadCheckout(): Promise<void> {
  if (window.Razorpay) return Promise.resolve();
  if (checkoutLoader) return checkoutLoader;
  checkoutLoader = new Promise((resolve, reject) => {
    const script = document.createElement("script");
    script.src = "https://checkout.razorpay.com/v1/checkout.js";
    script.async = true;
    script.onload = () => resolve();
    script.onerror = () => reject(new Error("Secure checkout could not be loaded."));
    document.head.appendChild(script);
  });
  return checkoutLoader;
}

export function RazorpayCheckout({
  onConfirmed,
  label = "Pay ₹299 securely",
}: {
  onConfirmed?: (confirmation: BillingConfirmation) => void;
  label?: string;
}) {
  const { user } = useAuth();
  const idempotencyKey = useRef<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  async function startCheckout() {
    if (!user) return;
    setBusy(true);
    setError(null);
    setMessage("Preparing secure checkout…");
    try {
      await loadCheckout();
      idempotencyKey.current ??= crypto.randomUUID();
      const checkout = await apiFetch<BillingCheckoutOrder>(user, "/api/billing/orders", {
        method: "POST",
        body: JSON.stringify({ idempotency_key: idempotencyKey.current }),
      });
      if (!checkout.order.provider_order_id || !window.Razorpay) {
        throw new Error("The payment order could not be opened.");
      }
      const razorpay = new window.Razorpay({
        key: checkout.public_key_id,
        amount: checkout.order.amount_minor,
        currency: checkout.order.currency,
        name: checkout.business_name,
        description: "Founder Recovery Plan · 10 confirmed invoices",
        order_id: checkout.order.provider_order_id,
        prefill: { email: checkout.customer_email ?? undefined },
        retry: { enabled: true },
        theme: { color: "#0f766e" },
        handler: async (result: RazorpayResult) => {
          setMessage("Verifying captured payment…");
          try {
            const confirmation = await apiFetch<BillingConfirmation>(user, "/api/billing/confirm", {
              method: "POST",
              body: JSON.stringify({
                provider_order_id: result.razorpay_order_id,
                provider_payment_id: result.razorpay_payment_id,
                signature: result.razorpay_signature,
              }),
            });
            setMessage(
              confirmation.status === "CAPTURED"
                ? "Payment captured. Your Founder Plan is active."
                : "Payment is processing. This page will reflect it after capture.",
            );
            onConfirmed?.(confirmation);
          } catch (reason) {
            setError(reason instanceof Error ? reason.message : "Payment verification failed.");
          } finally {
            setBusy(false);
          }
        },
        modal: {
          ondismiss: () => {
            setMessage(null);
            setBusy(false);
          },
        },
      });
      razorpay.on("payment.failed", (event) => {
        setError(event.error?.description || "The payment was not completed. You can retry safely.");
        setMessage(null);
        setBusy(false);
      });
      setMessage(null);
      razorpay.open();
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Secure checkout is unavailable.");
      setMessage(null);
      setBusy(false);
    }
  }

  return (
    <div className="checkout-actions">
      <button className="button button-primary" type="button" disabled={busy} onClick={() => void startCheckout()}>
        {busy ? "Opening checkout…" : label}
      </button>
      {message && <span className="checkout-message" role="status">{message}</span>}
      {error && <span className="checkout-error" role="alert">{error}</span>}
    </div>
  );
}
