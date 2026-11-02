import type { ReactNode } from "react";
import { usePermission, type Capability } from "../auth/permissions";

interface RequirePermissionProps {
  readonly capability: Capability;
  readonly children: ReactNode;
  readonly fallback?: ReactNode;
}

export function RequirePermission({ capability, children, fallback = null }: Readonly<RequirePermissionProps>) {
  return usePermission(capability) ? <>{children}</> : <>{fallback}</>;
}