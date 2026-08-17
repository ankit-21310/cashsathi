import { cleanup, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import RevenueCommandPage from "@/app/admin/revenue/page";

const mocks = vi.hoisted(() => ({ apiFetch: vi.fn(), replace: vi.fn() }));

vi.mock("next/navigation", () => ({ useRouter: () => ({ replace: mocks.replace }) }));
vi.mock("@/components/auth-provider", () => ({
  useAuth: () => ({ user: { email: "tenant-admin@example.test" } }),
}));
vi.mock("@/components/authenticated-shell", () => ({
  AuthenticatedShell: ({ children }: { children: React.ReactNode }) => <>{children}</>,
}));
vi.mock("@/lib/api", () => ({ apiFetch: mocks.apiFetch, apiDownload: vi.fn() }));

describe("revenue command authorization", () => {
  afterEach(cleanup);

  beforeEach(() => {
    mocks.apiFetch.mockReset();
    mocks.replace.mockReset();
  });

  it("redirects a tenant administrator before rendering CRM data", async () => {
    mocks.apiFetch.mockResolvedValue({ is_platform_admin: false });
    render(<RevenueCommandPage />);

    expect(screen.queryByRole("heading", { name: "Revenue Command Center" })).toBeNull();
    await waitFor(() => expect(mocks.replace).toHaveBeenCalledWith("/dashboard"));
    expect(mocks.apiFetch).toHaveBeenCalledTimes(1);
    expect(mocks.apiFetch).toHaveBeenCalledWith(expect.anything(), "/api/me");
  });
});
