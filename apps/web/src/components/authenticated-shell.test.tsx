import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { AuthenticatedShell } from "@/components/authenticated-shell";

const mocks = vi.hoisted(() => ({
  apiFetch: vi.fn(),
  replace: vi.fn(),
  signOut: vi.fn(),
}));

vi.mock("next/navigation", () => ({
  useRouter: () => ({ replace: mocks.replace }),
  usePathname: () => "/dashboard",
}));

vi.mock("next/link", () => ({
  default: ({ children, href, onClick, ...props }: React.AnchorHTMLAttributes<HTMLAnchorElement>) => (
    <a
      href={String(href)}
      onClick={(event) => {
        event.preventDefault();
        onClick?.(event);
      }}
      {...props}
    >
      {children}
    </a>
  ),
}));

vi.mock("@/components/auth-provider", () => ({
  useAuth: () => ({
    user: { email: "owner@example.test" },
    loading: false,
    signOut: mocks.signOut,
  }),
}));

vi.mock("@/lib/api", () => ({
  apiFetch: mocks.apiFetch,
}));

describe("authenticated navigation", () => {
  afterEach(cleanup);

  beforeEach(() => {
    mocks.apiFetch.mockReset();
    mocks.replace.mockReset();
    mocks.signOut.mockReset();
    mocks.apiFetch.mockResolvedValue({
      uid: "owner-1",
      email: "owner@example.test",
      display_name: null,
      business: {
        id: "business-1",
        name: "Aster Studio",
        owner_user_id: "owner-1",
        created_at: "2026-08-16T00:00:00Z",
        data_classification: "REAL",
        relationship: "ARMS_LENGTH",
        evidence_pseudonym: "aster",
      },
      membership: {
        business_id: "business-1",
        user_id: "owner-1",
        role: "OWNER",
        status: "ACTIVE",
        created_at: "2026-08-16T00:00:00Z",
      },
      is_platform_admin: true,
    });
  });

  it("opens and closes the responsive menu accessibly", async () => {
    render(<AuthenticatedShell><div>Page content</div></AuthenticatedShell>);

    await screen.findByRole("link", { name: "Admin" });
    const menuButton = screen.getByRole("button", { name: "Menu" });

    expect(menuButton).toHaveAttribute("aria-expanded", "false");
    fireEvent.click(menuButton);
    expect(screen.getByRole("button", { name: "Close" })).toHaveAttribute("aria-expanded", "true");

    fireEvent.keyDown(window, { key: "Escape" });
    await waitFor(() => expect(screen.getByRole("button", { name: "Menu" })).toHaveFocus());
    expect(screen.getByRole("button", { name: "Menu" })).toHaveAttribute("aria-expanded", "false");
  });

  it("closes the menu after a navigation link is selected", async () => {
    render(<AuthenticatedShell><div>Page content</div></AuthenticatedShell>);

    await screen.findByRole("link", { name: "Admin" });
    fireEvent.click(screen.getByRole("button", { name: "Menu" }));
    fireEvent.click(screen.getByRole("button", { name: "More" }));
    fireEvent.click(screen.getByRole("link", { name: "Privacy" }));

    expect(screen.getByRole("button", { name: "Menu" })).toHaveAttribute("aria-expanded", "false");
  });

  it("groups secondary destinations and account actions into menus", async () => {
    render(<AuthenticatedShell><div>Page content</div></AuthenticatedShell>);

    await screen.findByRole("link", { name: "Admin" });
    const moreButton = screen.getByRole("button", { name: "More" });
    expect(moreButton).toHaveAttribute("aria-expanded", "false");
    fireEvent.click(moreButton);
    expect(moreButton).toHaveAttribute("aria-expanded", "true");
    expect(screen.getByText("Workspace")).toBeInTheDocument();

    const accountButton = screen.getByRole("button", { name: "Account menu for owner@example.test" });
    fireEvent.click(accountButton);
    expect(accountButton).toHaveAttribute("aria-expanded", "true");
    expect(moreButton).toHaveAttribute("aria-expanded", "false");
    expect(screen.getByRole("button", { name: "Sign out" })).toBeInTheDocument();
  });
});
