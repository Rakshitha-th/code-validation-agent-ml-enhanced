import { apiClient, mocksEnabled } from "./client";
import type { ProjectFile, ValidationResult } from "./types";

export interface RunValidationRequest {
  readonly project_id: string;
  readonly files: ProjectFile[];
  readonly tests?: string[];
}

export async function runValidation(request: RunValidationRequest, signal?: AbortSignal): Promise<ValidationResult> {
  if (mocksEnabled()) return { status: "unknown", passed_tests: 0, failed_tests: 0, skipped_tests: 0 };
  return apiClient.post<ValidationResult>("/api/validation", request, signal);
}
