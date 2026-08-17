import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { RazorpayCheckout } from "@/components/razorpay-checkout";

const mocks = vi.hoisted(() => ({
  apiFetch: vi.fn(),
  open: vi.fn(),
  options: null as Record<string, unknown> | null,
  failed: null as ((event: { error?: { description?: string } }) => void) | null,
}));

vi.mock("@/components/auth-provider", () => ({
  useAuth: () => ({ user: { email: "owner@example.test" } }),
}));

vi.mock("@/lib/api", () => ({ apiFetch: mocks.apiFetch }));

describe("Razorpay checkout", () => {
  afterEach(cleanup);

  beforeEach(() => {
    mocks.apiFetch.mockReset();
    mocks.open.mockReset();
    mocks.options = null;
    mocks.failed = null;
    class MockRazorpay {
      constructor(options: Record<string, unknown>) {
        mocks.options = options;
      }

      open() {
        mocks.open();
      }

      on(name: string, callback: (event: { error?: { description?: string } }) => void) {
        if (name === "payment.failed") mocks.failed = callback;
      }
    }
    window.Razorpay = MockRazorpay;
  });

  it("activates only after the server confirms the captured payment", async () => {
    const onConfirmed = vi.fn();
    mocks.apiFetch
      .mockResolvedValueOnce({
        public_key_id: "rzp_test_public",
        business_name: "Aster Studio",
        customer_email: "owner@example.test",
        order: {
          provider_order_id: "order_123",
          amount_minor: 29900,
          currency: "INR",
        },
      })
      .mockResolvedValueOnce({ status: "CAPTURED", transaction: {}, plan: {} });

    render(<RazorpayCheckout onConfirmed={onConfirmed} />);
    fireEvent.click(screen.getByRole("button", { name: /pay/i }));

    await waitFor(() => expect(mocks.open).toHaveBeenCalledOnce());
    expect(mocks.apiFetch).toHaveBeenNthCalledWith(
      1,
      expect.anything(),
      "/api/billing/orders",
      expect.objectContaining({ method: "POST" }),
    );

    const handler = mocks.options?.handler as ((result: Record<string, string>) => Promise<void>);
    await handler({
      razorpay_order_id: "order_123",
      razorpay_payment_id: "pay_123",
      razorpay_signature: "signed",
    });

    expect(mocks.apiFetch).toHaveBeenNthCalledWith(
      2,
      expect.anything(),
      "/api/billing/confirm",
      expect.objectContaining({
        body: JSON.stringify({
          provider_order_id: "order_123",
          provider_payment_id: "pay_123",
          signature: "signed",
        }),
      }),
    );
    expect(onConfirmed).toHaveBeenCalledWith(expect.objectContaining({ status: "CAPTURED" }));
    expect(await screen.findByRole("status")).toHaveTextContent("Payment captured");
  });

  it("keeps processing distinct from captured and reports gateway failures", async () => {
    const onConfirmed = vi.fn();
    mocks.apiFetch
      .mockResolvedValueOnce({
        public_key_id: "rzp_test_public",
        business_name: "Aster Studio",
        order: { provider_order_id: "order_123", amount_minor: 29900, currency: "INR" },
      })
      .mockResolvedValueOnce({ status: "PROCESSING", transaction: {}, plan: null });

    render(<RazorpayCheckout onConfirmed={onConfirmed} />);
    fireEvent.click(screen.getByRole("button", { name: /pay/i }));
    await waitFor(() => expect(mocks.open).toHaveBeenCalledOnce());

    const handler = mocks.options?.handler as ((result: Record<string, string>) => Promise<void>);
    await handler({
      razorpay_order_id: "order_123",
      razorpay_payment_id: "pay_123",
      razorpay_signature: "signed",
    });
    expect(await screen.findByRole("status")).toHaveTextContent("processing");
    expect(onConfirmed).toHaveBeenCalledWith(expect.objectContaining({ status: "PROCESSING" }));

    mocks.failed?.({ error: { description: "Payment declined" } });
    expect(await screen.findByRole("alert")).toHaveTextContent("Payment declined");
  });
});
