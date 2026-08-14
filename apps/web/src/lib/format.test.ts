import { describe, expect, it } from "vitest";

import { formatMinorAmount, humanize } from "@/lib/format";

describe("format helpers", () => {
  it("respects currency-specific minor units", () => {
    expect(formatMinorAmount(500_000, "INR")).toContain("5,000");
    expect(formatMinorAmount(500_000, "JPY")).toContain("5,00,000");
    expect(formatMinorAmount(500_000, "KWD")).toContain("500");
  });

  it("humanizes stable API enums", () => {
    expect(humanize("REQUEST_HUMAN_REVIEW")).toBe("Request human review");
  });
});
