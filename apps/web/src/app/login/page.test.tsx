import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import LoginPage from "@/app/login/page";

const mocks = vi.hoisted(() => ({
  replace: vi.fn(),
  sendPasswordReset: vi.fn(),
}));

const originalUseFirebaseEmulator = process.env.NEXT_PUBLIC_USE_FIREBASE_EMULATOR;

vi.mock("next/navigation", () => ({
  useRouter: () => ({ replace: mocks.replace }),
}));

vi.mock("@/components/auth-provider", () => ({
  useAuth: () => ({
    user: null,
    loading: false,
    configurationError: null,
    signInWithGoogle: vi.fn(),
    signInWithEmail: vi.fn(),
    createEmailAccount: vi.fn(),
    sendPasswordReset: mocks.sendPasswordReset,
    signOut: vi.fn(),
  }),
}));

describe("login page password reset", () => {
  afterEach(() => {
    cleanup();
    if (originalUseFirebaseEmulator === undefined) {
      delete process.env.NEXT_PUBLIC_USE_FIREBASE_EMULATOR;
    } else {
      process.env.NEXT_PUBLIC_USE_FIREBASE_EMULATOR = originalUseFirebaseEmulator;
    }
  });

  beforeEach(() => {
    mocks.replace.mockReset();
    mocks.sendPasswordReset.mockReset();
    mocks.sendPasswordReset.mockResolvedValue(undefined);
  });

  it("sends a reset link from the forgot-password form", async () => {
    render(<LoginPage />);

    fireEvent.click(screen.getByRole("button", { name: "Forgot password?" }));

    expect(screen.getByRole("heading", { name: "Reset your password" })).toBeInTheDocument();
    expect(screen.queryByLabelText("Password")).not.toBeInTheDocument();

    fireEvent.change(screen.getByLabelText("Email"), {
      target: { value: "  owner@example.com  " },
    });
    fireEvent.click(screen.getByRole("button", { name: "Send reset link" }));

    await waitFor(() => {
      expect(mocks.sendPasswordReset).toHaveBeenCalledWith("owner@example.com");
    });
    expect(screen.getByRole("status")).toHaveTextContent(
      "If an account exists for that email, a password reset link is on its way.",
    );
    expect(mocks.replace).not.toHaveBeenCalled();
  });

  it("returns to the sign-in form", () => {
    render(<LoginPage />);

    fireEvent.click(screen.getByRole("button", { name: "Forgot password?" }));
    fireEvent.click(screen.getByRole("button", { name: "Back to sign in" }));

    expect(screen.getByRole("heading", { name: "Welcome back" })).toBeInTheDocument();
    expect(screen.getByLabelText("Password")).toBeInTheDocument();
  });

  it("explains that local reset links are not delivered by email", async () => {
    process.env.NEXT_PUBLIC_USE_FIREBASE_EMULATOR = "true";
    render(<LoginPage />);

    fireEvent.click(screen.getByRole("button", { name: "Forgot password?" }));
    expect(screen.getByText(/captures reset links instead of sending email/)).toBeInTheDocument();

    fireEvent.change(screen.getByLabelText("Email"), {
      target: { value: "owner-a@example.test" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Send reset link" }));

    expect(await screen.findByRole("status")).toHaveTextContent(
      "The Auth emulator does not send email",
    );
  });
});
