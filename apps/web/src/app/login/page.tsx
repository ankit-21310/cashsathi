"use client";

import { FormEvent, useEffect, useState } from "react";
import { useRouter } from "next/navigation";

import { useAuth } from "@/components/auth-provider";
import { Brand } from "@/components/brand";

type AuthMode = "signin" | "create" | "reset";

export default function LoginPage() {
  const auth = useAuth();
  const router = useRouter();
  const usingFirebaseEmulator = process.env.NEXT_PUBLIC_USE_FIREBASE_EMULATOR === "true";
  const [mode, setMode] = useState<AuthMode>("signin");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [success, setSuccess] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);

  useEffect(() => {
    if (!auth.loading && auth.user) router.replace("/onboarding");
  }, [auth.loading, auth.user, router]);

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setError(null);
    setSuccess(null);
    setSubmitting(true);
    try {
      if (mode === "reset") {
        await auth.sendPasswordReset(email.trim());
        setSuccess(
          usingFirebaseEmulator
            ? "Reset link created locally. The Auth emulator does not send email; open the reset URL shown in the emulator terminal."
            : "If an account exists for that email, a password reset link is on its way.",
        );
        return;
      }
      if (mode === "signin") await auth.signInWithEmail(email, password);
      else await auth.createEmailAccount(email, password);
      router.replace("/onboarding");
    } catch {
      setError(
        mode === "reset"
          ? usingFirebaseEmulator
            ? "No local reset link was created. Make sure this email is registered in the Auth emulator."
            : "We couldn't send a reset link. Please try again in a moment."
          : "Sign-in failed. Check your details and try again.",
      );
    } finally {
      setSubmitting(false);
    }
  }

  function changeMode(nextMode: AuthMode) {
    setMode(nextMode);
    setError(null);
    setSuccess(null);
    setPassword("");
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
            <button className={mode === "signin" ? "active" : ""} onClick={() => changeMode("signin")}>Sign in</button>
            <button className={mode === "create" ? "active" : ""} onClick={() => changeMode("create")}>Create account</button>
          </div>
          <h2>
            {mode === "signin"
              ? "Welcome back"
              : mode === "create"
                ? "Create your owner account"
                : "Reset your password"}
          </h2>
          <p>
            {mode === "signin"
              ? "Continue to your business workspace."
              : mode === "create"
                ? "One owner and one isolated business for this preview."
                : usingFirebaseEmulator
                  ? "Local Auth emulator is enabled. It captures reset links instead of sending email."
                  : "Enter your email and we'll send you a secure reset link."}
          </p>
          {auth.configurationError && <div className="alert alert-error">{auth.configurationError}</div>}
          {error && <div className="alert alert-error" role="alert">{error}</div>}
          {success && <div className="alert alert-success" role="status">{success}</div>}
          {mode !== "reset" && (
            <>
              <button
                className="button button-google"
                disabled={submitting || Boolean(auth.configurationError)}
                onClick={async () => {
                  setError(null);
                  setSuccess(null);
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
            </>
          )}
          <form onSubmit={submit}>
            <label>Email<input type="email" autoComplete="email" required value={email} onChange={(event) => setEmail(event.target.value)} /></label>
            {mode !== "reset" && (
              <label>Password<input type="password" minLength={8} autoComplete={mode === "signin" ? "current-password" : "new-password"} required value={password} onChange={(event) => setPassword(event.target.value)} /></label>
            )}
            {mode === "signin" && (
              <div className="auth-form-actions">
                <button className="link-button" type="button" onClick={() => changeMode("reset")}>Forgot password?</button>
              </div>
            )}
            <button className="button button-primary full-width" disabled={submitting || Boolean(auth.configurationError)}>
              {submitting
                ? "Please wait…"
                : mode === "signin"
                  ? "Sign in"
                  : mode === "create"
                    ? "Create account"
                    : "Send reset link"}
            </button>
            {mode === "reset" && (
              <button className="link-button auth-back-link" type="button" onClick={() => changeMode("signin")}>Back to sign in</button>
            )}
          </form>
        </div>
      </section>
    </main>
  );
}
