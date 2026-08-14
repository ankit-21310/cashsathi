import type { User } from "firebase/auth";

import { getPublicEnvironment } from "@/lib/env";

export interface Business {
  id: string;
  name: string;
  owner_user_id: string;
  created_at: string;
}

export interface Membership {
  business_id: string;
  user_id: string;
  role: "OWNER";
  status: "ACTIVE";
  created_at: string;
}

export interface MeResponse {
  uid: string;
  email: string | null;
  display_name: string | null;
  business: Business | null;
  membership: Membership | null;
}

interface ErrorEnvelope {
  error: { code: string; message: string; request_id: string; details?: unknown };
}

export class ApiClientError extends Error {
  constructor(
    message: string,
    public readonly status: number,
    public readonly code: string,
    public readonly requestId: string | null,
  ) {
    super(message);
    this.name = "ApiClientError";
  }
}

async function request(
  user: User,
  path: string,
  init: RequestInit,
  forceRefresh = false,
): Promise<Response> {
  const environment = getPublicEnvironment();
  const token = await user.getIdToken(forceRefresh);
  const headers = new Headers(init.headers);
  headers.set("Authorization", `Bearer ${token}`);
  headers.set("Content-Type", "application/json");
  headers.set("X-Request-ID", crypto.randomUUID());
  return fetch(`${environment.apiBaseUrl}${path}`, { ...init, headers, cache: "no-store" });
}

export async function apiFetch<T>(user: User, path: string, init: RequestInit = {}): Promise<T> {
  let response = await request(user, path, init);
  if (response.status === 401) response = await request(user, path, init, true);

  if (!response.ok) {
    let envelope: ErrorEnvelope | null = null;
    try {
      envelope = (await response.json()) as ErrorEnvelope;
    } catch {
      // A platform/proxy error may not use the API error envelope.
    }
    throw new ApiClientError(
      envelope?.error.message ?? "The service could not complete the request.",
      response.status,
      envelope?.error.code ?? "request_failed",
      envelope?.error.request_id ?? response.headers.get("X-Request-ID"),
    );
  }
  return (await response.json()) as T;
}

export async function getApiReadiness(): Promise<boolean> {
  const { apiBaseUrl } = getPublicEnvironment();
  try {
    return (await fetch(`${apiBaseUrl}/readyz`, { cache: "no-store" })).ok;
  } catch {
    return false;
  }
}
