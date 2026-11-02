import { apiBaseUrl, apiClient, mocksEnabled } from "./client";
import type { AnalysisRun, Report, StartAnalysisRequest, StartAnalysisResponse, WorkflowProgressEvent } from "./types";

export async function startAnalysis(request: StartAnalysisRequest, signal?: AbortSignal): Promise<StartAnalysisResponse> {
  if (mocksEnabled()) return { project_id: request.project_id, run_id: `mock-run-${crypto.randomUUID()}`, status: "started" };
  return apiClient.post<StartAnalysisResponse>("/analysis/run", request, signal);
}

export async function startAnalysisForProject(projectId: string, projectContext?: Record<string, unknown>, tools?: string[], signal?: AbortSignal): Promise<StartAnalysisResponse> {
  if (mocksEnabled()) return { project_id: projectId, run_id: `mock-run-${crypto.randomUUID()}`, status: "started" };
  return apiClient.post<StartAnalysisResponse>("/api/analysis", { project_id: projectId, project_context: projectContext, tools }, signal);
}

export function getAnalysisStatus(runId: string, signal?: AbortSignal): Promise<AnalysisRun> {
  if (mocksEnabled()) return Promise.resolve({ run_id: runId, project_id: runId, status: "running", progress: 45, current_agent: "Code Quality Agent" });
  return apiClient.get<AnalysisRun>(`/api/analysis/${encodeURIComponent(runId)}/status`, signal);
}

export function getAnalysisResults(runId: string, signal?: AbortSignal): Promise<Report> {
  if (mocksEnabled()) return Promise.resolve({ project_id: runId, status: "completed", findings: [], report: { validation_status: "unknown" } });
  return apiClient.get<Report>(`/api/analysis/${encodeURIComponent(runId)}/results`, signal);
}

export function openAnalysisStream(projectId: string, onEvent: (event: WorkflowProgressEvent) => void, onError?: () => void): EventSource {
  if (mocksEnabled()) {
    const controller = new AbortController();
    const mockEventSource = { close: () => controller.abort(), readyState: EventSource.CLOSED } as EventSource;
    queueMicrotask(() => {
      if (!controller.signal.aborted) onEvent({ step: "mock", message: "Mock workflow stream enabled", status: "running" });
    });
    return mockEventSource;
  }
  const source = new EventSource(`${apiBaseUrl()}/analysis/stream/${encodeURIComponent(projectId)}`);
  source.addEventListener("progress", (event) => {
    try { onEvent(JSON.parse((event as MessageEvent<string>).data) as WorkflowProgressEvent); } catch { onEvent({ level: "warning", message: "Received an unreadable progress event." }); }
  });
  source.addEventListener("complete", (event) => {
    try { onEvent({ ...JSON.parse((event as MessageEvent<string>).data) as WorkflowProgressEvent, status: "done" }); } catch { onEvent({ status: "done", message: "Workflow completed." }); }
  });
  source.addEventListener("failed", (event) => {
    try { onEvent({ ...JSON.parse((event as MessageEvent<string>).data) as WorkflowProgressEvent, status: "failed", level: "error" }); } catch { onEvent({ status: "failed", level: "error", message: "Workflow failed." }); }
  });
  source.onerror = () => onError?.();
  return source;
}

export function cancelAnalysis(runId: string): Promise<void> {
  if (mocksEnabled()) return Promise.resolve();
  return apiClient.post<void>(`/api/analysis/${encodeURIComponent(runId)}/cancel`, {});
}
