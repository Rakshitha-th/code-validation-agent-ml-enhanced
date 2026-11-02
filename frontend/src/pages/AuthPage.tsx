import { useEffect, useState } from "react";
import { apiClient } from "../api/client";
import { SUPPORTED_LANGUAGE_OPTIONS } from "../api/files";

export type AuthMode = "signin" | "signup" | "forgot" | "reset";

export type AuthFormValues = {
  fullName?: string;
  email: string;
  password: string;
  confirmPassword?: string;
  resetToken?: string;
  remember: boolean;
};

type AgentServiceStatuses = Record<string, boolean>;

const LANDING_SNIPPETS: Record<string, string[]> = {
  python: ["def validate(source):", "    issues = analyze(source)", "    return issues"],
  java: ["class Validator {", "  List<Issue> analyze(String code) {", "    return checker.run(code);"],
  c: ["int validate(const char *code) {", "  return analyze(code);", "}"],
  cpp: ["class Validator {", "  auto analyze(std::string_view code) {", "    return checker.run(code);"],
  csharp: ["class Validator {", "  IReadOnlyList<Issue> Analyze(string code) {", "    return checker.Run(code);"],
  javascript: ["function validate(source) {", "  const issues = analyze(source);", "  return issues;"],
  typescript: ["function validate(source: string) {", "  const issues = analyze(source);", "  return issues;"],
  go: ["func validate(source string) []Issue {", "  issues := analyze(source)", "  return issues"],
};

function serviceBadgeStatus(statuses: AgentServiceStatuses | null, service: string): string {
  if (!statuses) return "...";
  return statuses[service] ? "ON" : "OFF";
}

interface AuthPageProps {
  readonly mode: AuthMode;
  readonly localDemoEnabled: boolean;
  readonly busy: boolean;
  readonly message?: string;
  readonly error?: string;
  readonly resetToken?: string;
  readonly onModeChange: (mode: AuthMode) => void;
  readonly onSubmit: (values: AuthFormValues, mode: AuthMode) => void;
  readonly onLocalDemo: () => void;
}

function headingForMode(mode: AuthMode): string {
  const headings: Record<AuthMode, string> = {
    signin: "Welcome Back",
    signup: "Create your workspace",
    forgot: "Forgot password",
    reset: "Reset password",
  };
  return headings[mode];
}

function introForMode(mode: AuthMode): string {
  if (mode === "forgot") return "If your email is registered, we will send a secure password reset link.";
  if (mode === "reset") return "Choose a new password for your account.";
  if (mode === "signup") return "Create an account to save projects and analysis reports to your workspace.";
  return "Sign in to your Code Validation Agent account";
}

export function AuthPage({ mode, localDemoEnabled, busy, message, error, resetToken, onModeChange, onSubmit, onLocalDemo }: Readonly<AuthPageProps>) {
  const isSignup = mode === "signup";
  const isRecovery = mode === "forgot" || mode === "reset";
  const [showPassword, setShowPassword] = useState(false);
  const [form, setForm] = useState<AuthFormValues>({
    fullName: "",
    email: "",
    password: "",
    confirmPassword: "",
    resetToken: resetToken ?? "",
    remember: true,
  });
  const [formError, setFormError] = useState("");
  const [agentServices, setAgentServices] = useState<AgentServiceStatuses | null>(null);
  const [illustrationLanguage, setIllustrationLanguage] = useState<string>("python");

  useEffect(() => {
    setForm((current) => ({ ...current, password: "", confirmPassword: "", resetToken: resetToken ?? "" }));
  }, [mode, resetToken]);

  useEffect(() => {
    let active = true;
    void apiClient.get<{ services?: AgentServiceStatuses }>("/api/system/public-agents")
      .then((payload) => { if (active) setAgentServices(payload.services ?? {}); })
      .catch(() => { if (active) setAgentServices({}); });
    return () => { active = false; };
  }, []);

  function handleFieldChange(key: keyof AuthFormValues, value: string | boolean) {
    setForm((current) => ({ ...current, [key]: value }));
  }

  function handleSubmit(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setFormError("");

    if (mode !== "reset" && !form.email.trim()) {
      setFormError("Enter a valid email address.");
      return;
    }

    if (isSignup && !form.fullName?.trim()) {
      setFormError("Enter your full name.");
      return;
    }

    if ((isSignup || mode === "reset") && form.password.length < 12) {
      setFormError("Use a password with at least 12 characters.");
      return;
    }

    if (mode === "reset" && !(resetToken ?? form.resetToken)?.trim()) {
      setFormError("This reset link is missing its security token. Request a new link.");
      return;
    }

    if ((isSignup || mode === "reset") && form.password !== form.confirmPassword) {
      setFormError("The passwords do not match.");
      return;
    }

    if (!isRecovery && !form.password.trim()) {
      setFormError("Enter your password.");
      return;
    }

    onSubmit({
      ...form,
      email: form.email.trim(),
      fullName: form.fullName?.trim(),
      resetToken: resetToken ?? form.resetToken,
    }, mode);
  }

  return (
    <div className="auth-shell auth-shell--reference">
      <header className="auth-nav">
        <div className="auth-nav__brand"><span className="auth-nav__logo">&lt;/&gt;</span><strong>Code Validation Agent</strong></div>
      </header>

      <div className="auth-reference-layout" id="home">
        <section className="auth-reference-hero">
          <p className="reference-eyebrow">AI-POWERED ENGINEERING WORKSPACE</p>
          <h1>AI-Powered Code<br /><em>Validation &amp; Repair</em><br />Across Supported Languages</h1>
          <p className="auth-intro">Analyze code across multiple programming languages, detect bugs and security vulnerabilities, generate intelligent fixes, and validate code for safer, cleaner, and more reliable software.</p>

          <ul className="feature-list">
            <li><span className="feature-list__icon feature-list__icon--violet">✦</span><span><strong>Multi-Language Analysis</strong><small>Analyze code across supported programming languages.</small></span></li>
            <li><span className="feature-list__icon feature-list__icon--blue">ϟ</span><span><strong>AI-Powered Repair</strong><small>Generate fixes and revalidate repaired code.</small></span></li>
            <li><span className="feature-list__icon feature-list__icon--green">▥</span><span><strong>Clear Insights</strong><small>Show issue severity, explanations, and suggested fixes.</small></span></li>
            <li><span className="feature-list__icon feature-list__icon--orange">▣</span><span><strong>Secure Validation</strong><small>Provide static analysis, compilation, and sandboxed execution where supported.</small></span></li>
          </ul>

          <div className="auth-illustration" aria-label="Code analysis workflow illustration">
            <svg className="auth-illustration__connections" viewBox="0 0 560 500" aria-hidden="true">
              <path className="connection-line" d="M280 44 C280 83 280 120 280 160" />
              <path className="connection-line" d="M160 87 C181 104 193 131 205 160" />
              <path className="connection-line" d="M400 87 C379 104 367 131 355 160" />
              <path className="connection-line" d="M160 232 C164 232 167 232 170 232" />
              <path className="connection-line" d="M400 232 C396 232 393 232 390 232" />
              <path className="connection-line" d="M160 423 C181 406 193 369 205 340" />
              <path className="connection-line" d="M400 423 C379 406 367 369 355 340" />
              <path className="connection-line" d="M280 456 C280 416 280 378 280 340" />
              <circle className="connection-node" cx="280" cy="160" r="3" />
              <circle className="connection-node" cx="205" cy="160" r="3" />
              <circle className="connection-node" cx="355" cy="160" r="3" />
              <circle className="connection-node" cx="170" cy="232" r="3" />
              <circle className="connection-node" cx="390" cy="232" r="3" />
              <circle className="connection-node" cx="205" cy="340" r="3" />
              <circle className="connection-node" cx="355" cy="340" r="3" />
              <circle className="connection-node" cx="280" cy="340" r="3" />
            </svg>
            <div className="code-window">
              <div className="code-window__bar"><i /><i /><i /><b>Multi-Language Editor</b></div>
              <label className="code-window__language">Language
                <select value={illustrationLanguage} onChange={(event) => setIllustrationLanguage(event.target.value)} aria-label="Select illustration language">
                  {SUPPORTED_LANGUAGE_OPTIONS.map((language) => <option key={language.value} value={language.value}>{language.label}</option>)}
                </select>
              </label>
              <pre>{(LANDING_SNIPPETS[illustrationLanguage] ?? LANDING_SNIPPETS.python).map((line, index) => (
                <span className="code-window__line" key={`${illustrationLanguage}-${index}`}><span>{index + 1}</span>{line}</span>
              ))}</pre>
            </div>
            <div className="agent-badge agent-badge--orchestrator"><span className="agent-badge__index">ORCH</span><span>Workflow</span></div>
            <div className="agent-badge agent-badge--code-analysis"><span className="agent-badge__index" title="Code analysis runs inside the orchestrator">ORCH</span><span>Code Analysis</span></div>
            <div className="agent-badge agent-badge--security"><span className="agent-badge__index" title="Security service status">{serviceBadgeStatus(agentServices, "security")}</span><span>Security</span></div>
            <div className="agent-badge agent-badge--quality"><span className="agent-badge__index" title="Code Quality service status">{serviceBadgeStatus(agentServices, "code_quality")}</span><span>Code Quality</span></div>
            <div className="agent-badge agent-badge--bug"><span className="agent-badge__index" title="Bug & Resource service status">{serviceBadgeStatus(agentServices, "bug_resource")}</span><span>Bug &amp; Resource</span></div>
            <div className="agent-badge agent-badge--repair"><span className="agent-badge__index" title="Repair service status">{serviceBadgeStatus(agentServices, "repair")}</span><span>Repair</span></div>
            <div className="agent-badge agent-badge--validation"><span className="agent-badge__index" title="Validation service status">{serviceBadgeStatus(agentServices, "validation")}</span><span>Validation</span></div>
            <div className="agent-badge agent-badge--report"><span className="agent-badge__index" title="Report & Recommendation service status">{serviceBadgeStatus(agentServices, "report_recommendation")}</span><span>Reporting</span></div>
          </div>

        </section>

        <section className="auth-card auth-card--reference auth-card__panel--form">
          <div className="auth-form__logo">&lt;/&gt;</div>
          <h2>{headingForMode(mode)}</h2>
          <p className="auth-form__intro">{introForMode(mode)}</p>
          {isSignup && <p className="auth-local-notice">New accounts receive Developer access.</p>}
          {error && <p className="auth-feedback-message" role="alert">{error}</p>}
          {formError && <p className="auth-feedback-message" role="alert">{formError}</p>}
          {message && <output className="auth-feedback-message">{message}</output>}
          <form className="auth-form" onSubmit={handleSubmit}>
            {isSignup && (
              <label className="field">
                <span>Full name</span>
                <input
                  type="text"
                  required
                  placeholder="Alex Morgan"
                  value={form.fullName ?? ""}
                  onChange={(event) => handleFieldChange("fullName", event.target.value)}
                />
              </label>
            )}

            {mode !== "reset" && <label className="field">
              <span>Email address</span>
              <input
                type="email"
                required
                placeholder="name@company.com"
                value={form.email}
                onChange={(event) => handleFieldChange("email", event.target.value)}
              />
            </label>}

            {mode !== "forgot" && <label className="field">
              <span>Password</span>
              <span className="field__input-wrap">
                <input
                  type={showPassword ? "text" : "password"}
                  placeholder={isSignup ? "Create a strong password" : mode === "reset" ? "Choose a new password" : "Enter your password"}
                  required
                  minLength={isSignup || mode === "reset" ? 12 : undefined}
                  autoComplete={mode === "signin" ? "current-password" : "new-password"}
                  value={form.password}
                  onChange={(event) => handleFieldChange("password", event.target.value)}
                />
                <button type="button" className="field__visibility" onClick={() => setShowPassword((visible) => !visible)} aria-label={showPassword ? "Hide password" : "Show password"}>
                  {showPassword ? "Hide" : "Show"}
                </button>
              </span>
            </label>}

            {(isSignup || mode === "reset") && <label className="field"><span>Confirm password</span><input type="password" required minLength={12} autoComplete="new-password" value={form.confirmPassword ?? ""} onChange={(event) => handleFieldChange("confirmPassword", event.target.value)} /></label>}

            {mode === "signin" && <div className="auth-row">
              <label className="remember-me">
                <input
                  type="checkbox"
                  checked={form.remember}
                  onChange={(event) => handleFieldChange("remember", event.target.checked)}
                />
                <span>Keep me signed in</span>
              </label>
              <button type="button" className="inline-link" onClick={() => onModeChange("forgot")}>Forgot password?</button>
            </div>}

            <button type="submit" className="btn btn--primary auth-submit" disabled={busy}>
              {busy ? "Please wait..." : submitLabel(mode)}{!busy && <span>→</span>}
            </button>

            {mode === "signin" && localDemoEnabled && <><div className="auth-divider"><span>or</span></div><button type="button" className="btn btn--secondary auth-local-demo" disabled={busy} onClick={onLocalDemo}>Continue in local demo</button><p className="auth-local-notice">Testing only. Local Demo uses a separate development-only server session.</p></>}
            {mode === "signin" && <p className="auth-switch">Don't have an account? <button type="button" className="inline-link" onClick={() => onModeChange("signup")}>Sign Up</button></p>}
            {mode === "signup" && <p className="auth-switch">Already have an account? <button type="button" className="inline-link" onClick={() => onModeChange("signin")}>Sign In</button></p>}
            {isRecovery && <p className="auth-switch"><button type="button" className="inline-link" onClick={() => onModeChange("signin")}>Back to Sign In</button></p>}
          </form>
        </section>
      </div>
      <div className="auth-shell__wave" />
    </div>
  );
}

function submitLabel(mode: AuthMode): string {
  const labels: Record<AuthMode, string> = {
    signin: "Sign In",
    signup: "Create account",
    forgot: "Request password reset",
    reset: "Reset password",
  };
  return labels[mode];
}
