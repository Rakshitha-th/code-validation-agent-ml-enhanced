import { apiClient } from "./client";

export interface OverviewMetrics {
  readonly project_count: number;
  readonly run_count: number;
  readonly runs_today: number;
  readonly latest_security_findings: number;
  readonly latest_high_security_findings: number;
  readonly repair_successes: number;
  readonly repair_attempted: number;
  readonly validation_passed: number;
  readonly validation_counted: number;
}

export interface OverviewRun {
  readonly run_id: string;
  readonly project_id: string;
  readonly project_name: string;
  readonly status: string;
  readonly validation_status: string;
  readonly finding_count: number;
  readonly retry_count: number;
  readonly started_at: string | null;
  readonly language: string | null;
}

export interface OverviewLanguage {
  readonly key: string;
  readonly label: string;
  readonly support: "full" | "partial" | "unsupported";
  readonly project_count: number;
}

export interface OverviewResponse {
  readonly metrics: OverviewMetrics;
  readonly latest_run_id: string | null;
  readonly recent_runs: OverviewRun[];
  readonly latest_findings_by_severity: Record<string, number>;
  readonly latest_findings_by_language: Record<string, number>;
  readonly languages: OverviewLanguage[];
}

export interface OverviewServiceHealth {
  readonly status: string;
  readonly healthy: boolean;
}

export interface OverviewAgentHealth {
  readonly services: Record<string, OverviewServiceHealth>;
  readonly ready: number;
  readonly configured: number;
}

export function getOverview(signal?: AbortSignal): Promise<OverviewResponse> {
  return apiClient.get<OverviewResponse>("/api/overview", signal);
}

export function getOverviewAgentHealth(signal?: AbortSignal): Promise<OverviewAgentHealth> {
  return apiClient.get<OverviewAgentHealth>("/api/system/agents", signal);
}
