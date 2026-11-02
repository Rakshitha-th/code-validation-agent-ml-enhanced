import { Navigate, Outlet, useLocation } from "react-router-dom";
import type { ReactNode } from "react";
import { useAuth } from "../context/AuthContextStore";
import type { Role } from "../api/types";
import { usePermission, type Capability } from "../auth/permissions";

interface RequireAuthProps {
  readonly children?: ReactNode;
  readonly roles?: readonly Role[];
  readonly capability?: Capability;
}

export function RequireAuth({ children, roles, capability }: Readonly<RequireAuthProps>) {
  const { isAuthenticated, isLoading, user } = useAuth();
  const location = useLocation();
  const permitted = usePermission(capability ?? "viewProjects");

  if (isLoading) return <main className="auth-loading" aria-live="polite">Checking your session…</main>;
  if (!isAuthenticated) return <Navigate to="/login" replace state={{ from: `${location.pathname}${location.search}${location.hash}` }} />;
  if ((capability && !permitted) || (roles && (!user || !roles.includes(user.role)))) return <Navigate to="/" replace />;
  return children ? <>{children}</> : <Outlet />;
}
