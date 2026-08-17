import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import Home from "@/app/page";

vi.mock("next/link", () => ({
  default: ({ children, href, ...props }: React.AnchorHTMLAttributes<HTMLAnchorElement>) => (
    <a href={String(href)} {...props}>{children}</a>
  ),
}));

describe("landing page", () => {
  it("states the controlled receivables promise", () => {
    render(<Home />);
    expect(screen.getByRole("heading", { level: 1 })).toHaveTextContent(
      "Turn invoice follow-up into a controlled operating loop.",
    );
    expect(screen.getByText("Disputes stop automation")).toBeInTheDocument();
    expect(screen.getByText("₹299 one-time")).toBeInTheDocument();
    expect(screen.getByText(/10 confirmed invoices/)).toBeInTheDocument();
    expect(screen.getByText("Secure one-time checkout")).toBeInTheDocument();
    expect(screen.getAllByRole("link", { name: "Open secure preview" })[0]).toHaveAttribute(
      "href",
      "/login",
    );
  });
});
