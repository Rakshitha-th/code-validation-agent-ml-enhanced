import { useState } from "react";
import { Link } from "react-router-dom";
import { ApiError } from "../api/client";
import { changePassword } from "../api/auth";

export function AccountPasswordPage() {
  const [currentPassword, setCurrentPassword] = useState("");
  const [newPassword, setNewPassword] = useState("");
  const [confirmPassword, setConfirmPassword] = useState("");
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState("");
  const [error, setError] = useState("");

  async function submit(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setMessage("");
    setError("");
    if (newPassword.length < 12) {
      setError("Use a new password with at least 12 characters.");
      return;
    }
    if (newPassword !== confirmPassword) {
      setError("The new passwords do not match.");
      return;
    }

    setBusy(true);
    try {
      const response = await changePassword({
        current_password: currentPassword,
        new_password: newPassword,
      });
      setMessage(response.message);
      setCurrentPassword("");
      setNewPassword("");
      setConfirmPassword("");
    } catch (cause: unknown) {
      setError(cause instanceof ApiError ? cause.message : "Password could not be changed. Please try again.");
    } finally {
      setBusy(false);
    }
  }

  return (
    <section className="page">
      <div className="section-label"><div><Link to="/">Workspace</Link><h1>Change password</h1></div><span>Account security</span></div>
      <form className="auth-form account-password-form" onSubmit={(event) => void submit(event)}>
        {error && <p className="auth-feedback-message" role="alert">{error}</p>}
        {message && <p className="auth-feedback-message" role="status">{message}</p>}
        <label className="field"><span>Current password</span><input type="password" autoComplete="current-password" required value={currentPassword} onChange={(event) => setCurrentPassword(event.target.value)} /></label>
        <label className="field"><span>New password</span><input type="password" autoComplete="new-password" minLength={12} required value={newPassword} onChange={(event) => setNewPassword(event.target.value)} /></label>
        <label className="field"><span>Confirm new password</span><input type="password" autoComplete="new-password" minLength={12} required value={confirmPassword} onChange={(event) => setConfirmPassword(event.target.value)} /></label>
        <button className="btn btn--primary" type="submit" disabled={busy}>{busy ? "Updating password..." : "Update password"}</button>
      </form>
    </section>
  );
}
