import { useAuth } from "../context/AuthContextStore";
import type { Role } from "../api/types";

export type Capability = "viewProjects" | "editProject" | "createAnalysis" | "upload" | "runRepair" | "changeFindingStatus" | "deleteProject" | "manageUsers" | "adminAccess";

const DEVELOPER: Record<Capability, boolean> = {
  viewProjects: true, editProject: true, createAnalysis: true, upload: true,
  runRepair: true, changeFindingStatus: true, deleteProject: true, manageUsers: false, adminAccess: false,
};
const REVIEWER: Record<Capability, boolean> = {
  viewProjects: true, editProject: false, createAnalysis: false, upload: false,
  runRepair: false, changeFindingStatus: false, deleteProject: false, manageUsers: false, adminAccess: false,
};
const TECHNICAL_LEAD: Record<Capability, boolean> = { ...REVIEWER };
const ADMINISTRATOR: Record<Capability, boolean> = {
  viewProjects: true, editProject: true, createAnalysis: true, upload: true,
  runRepair: true, changeFindingStatus: true, deleteProject: true, manageUsers: true, adminAccess: true,
};

export const PERMISSIONS: Readonly<Record<Role, Readonly<Record<Capability, boolean>>>> = {
  Developer: DEVELOPER,
  Reviewer: REVIEWER,
  "Technical Lead": TECHNICAL_LEAD,
  Administrator: ADMINISTRATOR,
};

export function usePermission(capability: Capability): boolean {
  const { user } = useAuth();
  return user ? PERMISSIONS[user.role][capability] : false;
}