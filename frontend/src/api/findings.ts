import { apiClient, mocksEnabled } from "./client";
import type { FindingsResponse } from "./types";
import type { FindingStatus } from "../types/finding";

export interface FindingStatusHistoryEntry {
  readonly status: FindingStatus;
  readonly changed_by: string;
  readonly changed_at: string;
  readonly comment?: string;
}

const mockStatusHistory = new Map<string, FindingStatusHistoryEntry[]>();

export function getFindings(projectId: string, signal?: AbortSignal): Promise<FindingsResponse> {
  if (mocksEnabled()) return Promise.resolve({ project_id: projectId, findings: [], findings_count: 0 });
  return apiClient.get<FindingsResponse>(`/analysis/${encodeURIComponent(projectId)}/findings`, signal);
}

export async function updateFindingStatus(findingId: string, status: FindingStatus, comment?: string, signal?: AbortSignal): Promise<FindingStatusHistoryEntry> {
  if (status === "Fixed (validated)" || status === "repaired") throw new Error("Fixed status is set only by a passing validation.");
  if (mocksEnabled()) {
    const entry: FindingStatusHistoryEntry = { status, changed_by: "Mock developer", changed_at: new Date().toISOString(), comment };
    mockStatusHistory.set(findingId, [...(mockStatusHistory.get(findingId) ?? []), entry]);
    return entry;
  }
  return apiClient.patch<FindingStatusHistoryEntry>(`/api/findings/${encodeURIComponent(findingId)}/status`, { status, comment }, signal);
}

export function getFindingStatusHistory(findingId: string, signal?: AbortSignal): Promise<FindingStatusHistoryEntry[]> {
  if (mocksEnabled()) return Promise.resolve(mockStatusHistory.get(findingId) ?? []);
  return apiClient.get<{ readonly items: FindingStatusHistoryEntry[] }>(`/api/findings/${encodeURIComponent(findingId)}/status-history`, signal).then((response) => response.items);
}
