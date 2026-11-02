import { useEffect, useState } from "react";
import { Navigate, useLocation, useNavigate } from "react-router-dom";
import { ApiError } from "../api/client";
import { useAuth } from "../context/AuthContextStore";
import { requestPasswordReset, resetPassword } from "../api/auth";
import { AuthPage, type AuthFormValues, type AuthMode } from "./AuthPage";

export function LoginRoute() {
  const { isAuthenticated, isLoading, login, register, loginAsLocalDemo } = useAuth();
  const navigate = useNavigate();
  const location = useLocation();
  const [error, setError] = useState<string | null>(() => ((location.state as { expired?: boolean } | null)?.expired ? "Session expired. Sign in again to continue." : null));
  const [message, setMessage] = useState("");
  const resetToken = new URLSearchParams(location.hash.replace(/^#/, "")).get("reset-token") ?? "";
  const [mode, setMode] = useState<AuthMode>(() => resetToken ? "reset" : "signin");
  const [busy, setBusy] = useState(false);
  const destination = (location.state as { from?: string } | null)?.from ?? "/";
  const localDemoEnabled = import.meta.env.VITE_LOCAL_DEMO_AUTH === "true";

  useEffect(() => {
    if (resetToken) setMode("reset");
  }, [resetToken]);

  if (isLoading) return <main className="auth-loading" aria-live="polite">Checking your session…</main>;
  if (isAuthenticated) return <Navigate to="/" replace />;

  function handleModeChange(nextMode: AuthMode): void {
    setError(null);
    setMessage("");
    setMode(nextMode);
  }

  function handleSubmit(values: AuthFormValues, formMode: AuthMode): void {
    setError(null);
    setMessage("");
    if (formMode === "forgot") {
      setBusy(true);
      void requestPasswordReset(values.email)
        .then((response) => setMessage(response.message))
        .catch((cause: unknown) => setError(cause instanceof ApiError ? cause.message : "Password reset could not be requested. Please try again."))
        .finally(() => setBusy(false));
      return;
    }
    if (formMode === "reset") {
      if (!values.resetToken) {
        setError("This reset link is missing its security token. Request a new link.");
        return;
      }
      setBusy(true);
      void resetPassword({ token: values.resetToken, new_password: values.password })
        .then((response) => {
          setMessage(response.message);
          setMode("signin");
          window.history.replaceState(null, "", `${location.pathname}${location.search}`);
        })
        .catch((cause: unknown) => setError(cause instanceof ApiError ? cause.message : "Password could not be reset. Request a new link and try again."))
        .finally(() => setBusy(false));
      return;
    }
    setBusy(true);
    const request = formMode === "signup"
      ? register(values.fullName ?? "", values.email, values.password)
      : login(values.email, values.password, values.remember);
    void request
      .then(() => {
        void navigate(destination, { replace: true });
      })
      .catch((cause: unknown) => {
        setError(cause instanceof ApiError ? cause.message : "Sign in failed. Please check your credentials and try again.");
      })
      .finally(() => setBusy(false));
  }

  function handleLocalDemo(): void {
    setBusy(true);
    setError(null);
    void loginAsLocalDemo()
      .then(() => navigate(destination, { replace: true }))
      .catch((cause: unknown) => setError(cause instanceof ApiError ? cause.message : "Local demo access is unavailable."))
      .finally(() => setBusy(false));
  }

  return (
    <>
      <AuthPage mode={mode} localDemoEnabled={localDemoEnabled} busy={busy} message={message} error={error ?? undefined} resetToken={resetToken} onModeChange={handleModeChange} onSubmit={handleSubmit} onLocalDemo={handleLocalDemo} />
      {(error || busy) && <div className="auth-feedback" aria-live="polite">
        {busy && <p>{mode === "forgot" ? "Requesting reset instructions…" : mode === "reset" ? "Updating password…" : mode === "signup" ? "Creating account…" : "Signing in…"}</p>}
      </div>}
    </>
  );
}
