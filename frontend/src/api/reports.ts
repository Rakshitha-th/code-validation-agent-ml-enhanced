import { apiClient, mocksEnabled } from "./client";
import type { PersistedHistoryItem, Report } from "./types";

export async function getReport(projectId: string, signal?: AbortSignal): Promise<Report> {
  if (mocksEnabled()) return { project_id: projectId, status: "completed", report: { findings: [], recommendations: [] } };
  return apiClient.get<Report>(`/results/${encodeURIComponent(projectId)}`, signal);
}

export async function getAnalysisHistory(limit = 50, signal?: AbortSignal): Promise<PersistedHistoryItem[]> {
  if (mocksEnabled()) return [];
  const response = await apiClient.get<{ readonly items: PersistedHistoryItem[] }>(`/api/history?limit=${limit}`, signal);
  return response.items;
}
