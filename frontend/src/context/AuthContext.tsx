import { useCallback, useEffect, useMemo, useState, type ReactNode } from "react";
import { getCurrentUser, login as loginRequest, loginLocalDemo as loginLocalDemoRequest, logout as logoutRequest, register as registerRequest } from "../api/auth";
import { clearSession, dispatchWorkspaceClear, saveSession } from "../api/client";
import type { AuthSession, Role } from "../api/types";
import { AuthContext, type AuthContextValue } from "./AuthContextStore";

interface AuthProviderProps {
  readonly children: ReactNode;
}

export function AuthProvider({ children }: Readonly<AuthProviderProps>) {
  const [session, setSession] = useState<AuthSession | null>(null);
  const [isLoading, setIsLoading] = useState(true);
  const [idleWarning, setIdleWarning] = useState(false);

  useEffect(() => {
    let active = true;
    void getCurrentUser()
      .then((user) => {
        if (!active) return;
        const nextSession = { user };
        saveSession(nextSession);
        setSession(nextSession);
      })
      .catch(() => {
        clearSession();
        if (active) setSession(null);
      })
      .finally(() => { if (active) setIsLoading(false); });
    return () => { active = false; };
  }, []);

  const login = useCallback(async (email: string, password: string, remember: boolean) => {
    const nextSession = await loginRequest({ email, password, remember });
    const validRoles: readonly Role[] = ["Developer", "Reviewer", "Technical Lead", "Administrator"];
    if (!validRoles.includes(nextSession.user.role)) throw new Error("The account role is not recognized.");
    saveSession(nextSession);
    setSession(nextSession);
  }, []);

  const register = useCallback(async (fullName: string, email: string, password: string) => {
    const nextSession = await registerRequest({ full_name: fullName, email, password });
    saveSession(nextSession);
    setSession(nextSession);
  }, []);

  const loginAsLocalDemo = useCallback(async () => {
    const nextSession = await loginLocalDemoRequest();
    saveSession(nextSession);
    setSession(nextSession);
  }, []);

  const logout = useCallback(() => {
    void logoutRequest().catch(() => undefined);
    clearSession();
    setSession(null);
    setIdleWarning(false);
    dispatchWorkspaceClear();
  }, []);

  useEffect(() => {
    const expire = () => logout();
    window.addEventListener("coding-validation:session-expired", expire);
    return () => window.removeEventListener("coding-validation:session-expired", expire);
  }, [logout]);

  useEffect(() => {
    if (!session) return undefined;
    let warningTimer: number;
    let logoutTimer: number;
    const reset = () => {
      window.clearTimeout(warningTimer);
      window.clearTimeout(logoutTimer);
      setIdleWarning(false);
      warningTimer = window.setTimeout(() => setIdleWarning(true), 29 * 60 * 1000);
      logoutTimer = window.setTimeout(logout, 30 * 60 * 1000);
    };
    const activityEvents: Array<keyof WindowEventMap> = ["pointerdown", "keydown", "scroll", "touchstart"];
    activityEvents.forEach((name) => window.addEventListener(name, reset, { passive: true }));
    reset();
    return () => {
      window.clearTimeout(warningTimer);
      window.clearTimeout(logoutTimer);
      activityEvents.forEach((name) => window.removeEventListener(name, reset));
    };
  }, [session, logout]);

  const value = useMemo<AuthContextValue>(() => ({ session, user: session?.user ?? null, isAuthenticated: Boolean(session), isLoading, login, register, loginAsLocalDemo, logout }), [session, isLoading, login, register, loginAsLocalDemo, logout]);
  return <AuthContext.Provider value={value}>{children}{idleWarning && <dialog open className="idle-warning" aria-labelledby="idle-warning-title"><h2 id="idle-warning-title">Session ending soon</h2><p>You will be signed out in one minute because of inactivity.</p><button className="btn btn--primary" onClick={() => setIdleWarning(false)}>Stay signed in</button><button className="btn" onClick={logout}>Log out</button></dialog>}</AuthContext.Provider>;
}
