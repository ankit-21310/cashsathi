import { render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { AuthProvider, useAuth } from "@/components/auth-provider";
import { PREVIEW_DISABLED_MESSAGE } from "@/lib/env";
import { getFirebaseAuth } from "@/lib/firebase";

vi.mock("@/lib/firebase", () => ({ getFirebaseAuth: vi.fn() }));

const original = { ...process.env };

afterEach(() => {
  process.env = { ...original };
  vi.clearAllMocks();
});

function Probe() {
  const auth = useAuth();
  return <div>{auth.loading ? "loading" : auth.configurationError}</div>;
}

describe("AuthProvider preview mode", () => {
  it("renders a disabled state without initializing Firebase", async () => {
    process.env.NEXT_PUBLIC_APP_MODE = "preview-disabled";

    render(
      <AuthProvider>
        <Probe />
      </AuthProvider>,
    );

    await waitFor(() => expect(screen.getByText(PREVIEW_DISABLED_MESSAGE)).toBeInTheDocument());
    expect(getFirebaseAuth).not.toHaveBeenCalled();
  });
});
