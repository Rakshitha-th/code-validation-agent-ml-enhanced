import { createContext, useContext } from "react";
import type { AuthSession } from "../api/types";

export interface AuthContextValue {
  readonly session: AuthSession | null;
  readonly user: AuthSession["user"] | null;
  readonly isAuthenticated: boolean;
  readonly isLoading: boolean;
  readonly login: (email: string, password: string, remember: boolean) => Promise<void>;
  readonly register: (fullName: string, email: string, password: string) => Promise<void>;
  readonly loginAsLocalDemo: () => Promise<void>;
  readonly logout: () => void;
}

export const AuthContext = createContext<AuthContextValue | null>(null);

export function useAuth(): AuthContextValue {
  const context = useContext(AuthContext);
  if (!context) throw new Error("useAuth must be used within AuthProvider.");
  return context;
}