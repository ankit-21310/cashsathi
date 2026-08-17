import { afterEach, describe, expect, it } from "vitest";

import {
  getPublicEnvironment,
  PREVIEW_DISABLED_MESSAGE,
  publicAppMode,
} from "@/lib/env";

const original = { ...process.env };

afterEach(() => {
  process.env = { ...original };
});

describe("public environment", () => {
  it("validates and normalizes browser configuration", () => {
    process.env.NEXT_PUBLIC_APP_MODE = "local";
    process.env.NEXT_PUBLIC_PRODUCT_NAME = "Receivables Operator Preview";
    process.env.NEXT_PUBLIC_API_BASE_URL = "http://localhost:8000/";
    process.env.NEXT_PUBLIC_FIREBASE_API_KEY = "test";
    process.env.NEXT_PUBLIC_FIREBASE_AUTH_DOMAIN = "local.test";
    process.env.NEXT_PUBLIC_FIREBASE_PROJECT_ID = "local-project";
    process.env.NEXT_PUBLIC_FIREBASE_STORAGE_BUCKET = "local-bucket";
    process.env.NEXT_PUBLIC_FIREBASE_MESSAGING_SENDER_ID = "123";
    process.env.NEXT_PUBLIC_FIREBASE_APP_ID = "app-id";
    process.env.NEXT_PUBLIC_USE_FIREBASE_EMULATOR = "true";

    const environment = getPublicEnvironment();
    expect(environment.apiBaseUrl).toBe("http://localhost:8000");
    expect(environment.useFirebaseEmulator).toBe(true);
  });

  it("disables live configuration for preview deployments", () => {
    process.env.NEXT_PUBLIC_APP_MODE = "preview-disabled";

    expect(publicAppMode()).toBe("preview-disabled");
    expect(() => getPublicEnvironment()).toThrow(PREVIEW_DISABLED_MESSAGE);
  });
});
