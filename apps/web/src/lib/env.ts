import { z } from "zod";

const appModeSchema = z.enum(["local", "production", "preview-disabled"]);
export type AppMode = z.infer<typeof appModeSchema>;

export const PREVIEW_DISABLED_MESSAGE = "Preview build — live services are disabled.";

const publicEnvironmentSchema = z.object({
  productName: z.string().min(2).max(80),
  apiBaseUrl: z.url().transform((value) => value.replace(/\/$/, "")),
  firebase: z.object({
    apiKey: z.string().min(1),
    authDomain: z.string().min(1),
    projectId: z.string().min(1),
    storageBucket: z.string().min(1),
    messagingSenderId: z.string().min(1),
    appId: z.string().min(1),
  }),
  useFirebaseEmulator: z.enum(["true", "false"]).transform((value) => value === "true"),
});

export type PublicEnvironment = z.infer<typeof publicEnvironmentSchema>;

export function publicAppMode(): AppMode {
  const fallback = process.env.NODE_ENV === "production" ? "production" : "local";
  return appModeSchema.parse(process.env.NEXT_PUBLIC_APP_MODE ?? fallback);
}

export function getPublicEnvironment(): PublicEnvironment {
  if (publicAppMode() === "preview-disabled") {
    throw new Error(PREVIEW_DISABLED_MESSAGE);
  }
  return publicEnvironmentSchema.parse({
    productName: process.env.NEXT_PUBLIC_PRODUCT_NAME,
    apiBaseUrl: process.env.NEXT_PUBLIC_API_BASE_URL,
    firebase: {
      apiKey: process.env.NEXT_PUBLIC_FIREBASE_API_KEY,
      authDomain: process.env.NEXT_PUBLIC_FIREBASE_AUTH_DOMAIN,
      projectId: process.env.NEXT_PUBLIC_FIREBASE_PROJECT_ID,
      storageBucket: process.env.NEXT_PUBLIC_FIREBASE_STORAGE_BUCKET,
      messagingSenderId: process.env.NEXT_PUBLIC_FIREBASE_MESSAGING_SENDER_ID,
      appId: process.env.NEXT_PUBLIC_FIREBASE_APP_ID,
    },
    useFirebaseEmulator: process.env.NEXT_PUBLIC_USE_FIREBASE_EMULATOR ?? "false",
  });
}

export function publicProductName(): string {
  return process.env.NEXT_PUBLIC_PRODUCT_NAME || "Receivables Operator Preview";
}
