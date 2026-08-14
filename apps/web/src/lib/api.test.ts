import type { User } from "firebase/auth";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { apiFetch } from "@/lib/api";

function configureEnvironment() {
  process.env.NEXT_PUBLIC_PRODUCT_NAME = "Receivables Operator Preview";
  process.env.NEXT_PUBLIC_API_BASE_URL = "http://localhost:8000";
  process.env.NEXT_PUBLIC_FIREBASE_API_KEY = "test";
  process.env.NEXT_PUBLIC_FIREBASE_AUTH_DOMAIN = "local.test";
  process.env.NEXT_PUBLIC_FIREBASE_PROJECT_ID = "local-project";
  process.env.NEXT_PUBLIC_FIREBASE_STORAGE_BUCKET = "local-bucket";
  process.env.NEXT_PUBLIC_FIREBASE_MESSAGING_SENDER_ID = "123";
  process.env.NEXT_PUBLIC_FIREBASE_APP_ID = "app-id";
  process.env.NEXT_PUBLIC_USE_FIREBASE_EMULATOR = "false";
}

describe("API client", () => {
  beforeEach(() => {
    configureEnvironment();
    vi.restoreAllMocks();
  });

  it("forwards a Firebase bearer token and request ID", async () => {
    const getIdToken = vi.fn().mockResolvedValue("firebase-token");
    const fetchMock = vi.spyOn(globalThis, "fetch").mockResolvedValue(
      new Response(JSON.stringify({ uid: "alice" }), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      }),
    );

    await apiFetch({ getIdToken } as unknown as User, "/api/me");
    const headers = new Headers(fetchMock.mock.calls[0][1]?.headers);
    expect(headers.get("Authorization")).toBe("Bearer firebase-token");
    expect(headers.get("X-Request-ID")).toBeTruthy();
  });

  it("refreshes the token once after an unauthorized response", async () => {
    const getIdToken = vi.fn().mockResolvedValueOnce("old").mockResolvedValueOnce("fresh");
    vi.spyOn(globalThis, "fetch")
      .mockResolvedValueOnce(new Response(null, { status: 401 }))
      .mockResolvedValueOnce(
        new Response(JSON.stringify({ uid: "alice" }), {
          status: 200,
          headers: { "Content-Type": "application/json" },
        }),
      );

    await apiFetch({ getIdToken } as unknown as User, "/api/me");
    expect(getIdToken).toHaveBeenNthCalledWith(1, false);
    expect(getIdToken).toHaveBeenNthCalledWith(2, true);
  });

  it("surfaces the safe API error envelope", async () => {
    const getIdToken = vi.fn().mockResolvedValue("token");
    vi.spyOn(globalThis, "fetch").mockResolvedValue(
      new Response(
        JSON.stringify({
          error: { code: "business_not_found", message: "Complete onboarding.", request_id: "req-1" },
        }),
        { status: 404, headers: { "Content-Type": "application/json" } },
      ),
    );

    await expect(
      apiFetch({ getIdToken } as unknown as User, "/api/businesses/current"),
    ).rejects.toMatchObject({
      code: "business_not_found",
      requestId: "req-1",
    });
  });
});
