"use client";

import { FormEvent, useEffect, useState } from "react";
import { useRouter } from "next/navigation";

import { useAuth } from "@/components/auth-provider";
import { Brand } from "@/components/brand";

export default function LoginPage() {
  const auth = useAuth();
  const router = useRouter();
  const [mode, setMode] = useState<"signin" | "create">("signin");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);

  useEffect(() => {
    if (!auth.loading && auth.user) router.replace("/onboarding");
  }, [auth.loading, auth.user, router]);

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setError(null);
    setSubmitting(true);
    try {
      if (mode === "signin") await auth.signInWithEmail(email, password);
      else await auth.createEmailAccount(email, password);
      router.replace("/onboarding");
    } catch {
      setError("Sign-in failed. Check your details and try again.");
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <main className="auth-page">
      <section className="auth-aside">
        <Brand />
        <div>
          <div className="eyebrow">Secure owner access</div>
          <h1>Your receivables queue belongs to your business alone.</h1>
          <p>Firebase authenticates you; the API derives your tenant and never trusts a browser-supplied business ID.</p>
        </div>
        <p className="aside-note">Private preview · Public product name pending clearance</p>
      </section>
      <section className="auth-card-wrap">
        <div className="auth-card">
          <div className="tab-list" role="tablist">
            <button className={mode === "signin" ? "active" : ""} onClick={() => setMode("signin")}>Sign in</button>
            <button className={mode === "create" ? "active" : ""} onClick={() => setMode("create")}>Create account</button>
          </div>
          <h2>{mode === "signin" ? "Welcome back" : "Create your owner account"}</h2>
          <p>{mode === "signin" ? "Continue to your business workspace." : "One owner and one isolated business for this preview."}</p>
          {auth.configurationError && <div className="alert alert-error">{auth.configurationError}</div>}
          {error && <div className="alert alert-error" role="alert">{error}</div>}
          <button
            className="button button-google"
            disabled={submitting || Boolean(auth.configurationError)}
            onClick={async () => {
              setError(null);
              try {
                await auth.signInWithGoogle();
                router.replace("/onboarding");
              } catch {
                setError("Google sign-in did not complete.");
              }
            }}
          >
            Continue with Google
          </button>
          <div className="divider"><span>or use email</span></div>
          <form onSubmit={submit}>
            <label>Email<input type="email" autoComplete="email" required value={email} onChange={(event) => setEmail(event.target.value)} /></label>
            <label>Password<input type="password" minLength={8} autoComplete={mode === "signin" ? "current-password" : "new-password"} required value={password} onChange={(event) => setPassword(event.target.value)} /></label>
            <button className="button button-primary full-width" disabled={submitting || Boolean(auth.configurationError)}>
              {submitting ? "Please wait…" : mode === "signin" ? "Sign in" : "Create account"}
            </button>
          </form>
        </div>
      </section>
    </main>
  );
}
