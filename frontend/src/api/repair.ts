import { apiClient, mocksEnabled } from "./client";
import type { ProjectFile, RepairAttempt } from "./types";

export interface StartRepairRequest {
  readonly project_id: string;
  readonly finding_ids?: string[];
  readonly mode: "targeted" | "regeneration";
  readonly max_iterations: number;
}

export interface StartRepairResponse {
  readonly attempt: RepairAttempt;
  readonly files: ProjectFile[];
}

export interface ApplyRepairResponse {
  readonly success: boolean;
  readonly project_id: string;
  readonly attempt_id: string;
}

export async function startRepair(request: StartRepairRequest, signal?: AbortSignal): Promise<StartRepairResponse> {
  if (mocksEnabled()) return { attempt: { attempt_id: `mock-repair-${crypto.randomUUID()}`, iteration: 1, mode: request.mode, status: "generated", patch_summary: "Mock repair candidate; it is not accepted until validation passes." }, files: [] };
  return apiClient.post<StartRepairResponse>("/api/repair", request, signal);
}

export async function applyRepairToProject(projectId: string, attemptId: string, files: ProjectFile[], signal?: AbortSignal): Promise<ApplyRepairResponse> {
  if (mocksEnabled()) return { success: true, project_id: projectId, attempt_id: attemptId };
  return apiClient.post<ApplyRepairResponse>(`/api/projects/${encodeURIComponent(projectId)}/apply-repair`, { attempt_id: attemptId, files }, signal);
}
