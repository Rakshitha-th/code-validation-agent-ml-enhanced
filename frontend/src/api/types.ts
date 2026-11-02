import type { Finding as ExistingFinding, ProjectFile as ExistingProjectFile, Severity, ValidationStatus } from "../types/finding";

export type Role = "Developer" | "Reviewer" | "Technical Lead" | "Administrator";
export type RunStatus = "queued" | "running" | "completed" | "failed" | "cancelled" | "unknown";
export type AgentRunStatus = "pending" | "running" | "done" | "failed" | "skipped";

export interface ProjectFile extends ExistingProjectFile {
  readonly sizeBytes?: number;
}

export interface Project {
  readonly project_id: string;
  readonly name: string;
  readonly description?: string;
  readonly file_count: number;
  readonly created_at: string;
  readonly files_persisted?: boolean;
}

export interface ProjectSummary extends Project {
  readonly owner?: string;
  readonly status?: string;
  readonly last_run?: string | null;
}

export interface ProjectDetail extends ProjectSummary {
  readonly files: Array<{
    readonly path: string;
    readonly size: number;
    readonly last_modified?: string;
  }>;
  readonly run_history: HistoryItem[];
}

export interface ProjectsResponse {
  readonly items: ProjectSummary[];
  readonly total: number;
  readonly page: number;
  readonly page_size: number;
}

export interface AnalysisRun {
  readonly run_id?: string;
  readonly project_id: string;
  readonly status: RunStatus;
  readonly current_agent?: string;
  readonly progress?: number;
  readonly started_at?: string;
  readonly completed_at?: string;
  readonly error?: string;
}

export interface AgentStatus {
  readonly agent: string;
  readonly status: AgentRunStatus;
  readonly duration_ms?: number;
  readonly summary?: string;
}

export type Finding = ExistingFinding & {
  readonly cwe?: string[];
  readonly owasp?: string[];
  readonly root_cause?: string;
  readonly requires_human_review?: boolean;
};

export interface RepairAttempt {
  readonly attempt_id: string;
  readonly iteration: number;
  readonly mode: "targeted" | "regeneration";
  readonly status: "pending" | "generated" | "accepted" | "rejected" | "failed";
  readonly patch_summary?: string;
  readonly reason?: string;
  readonly before_files?: ProjectFile[];
  readonly after_files?: ProjectFile[];
  readonly validation?: ValidationResult;
  readonly created_at?: string;
}

export interface ValidationResult {
  readonly status: "PASS" | "FAIL" | "pending" | "unknown";
  readonly passed_tests?: number;
  readonly failed_tests?: number;
  readonly skipped_tests?: number;
  readonly coverage_percent?: number;
  readonly failing_output?: string;
  readonly static_analysis_before?: number;
  readonly static_analysis_after?: number;
  readonly security_findings_before?: number;
  readonly security_findings_after?: number;
  readonly sandbox?: SandboxExecution;
}

export interface SandboxExecution {
  readonly stdout?: string;
  readonly stderr?: string;
  readonly exit_code?: number | null;
  readonly timed_out?: boolean;
  readonly cpu_limit?: string;
  readonly memory_limit?: string;
  readonly duration_ms?: number;
}

export interface Report {
  readonly project_id: string;
  readonly run_id?: string;
  readonly project_name?: string;
  readonly status?: RunStatus;
  readonly validation_status?: ValidationStatus;
  readonly started_at?: string;
  readonly completed_at?: string;
  readonly findings?: Finding[];
  readonly result?: Record<string, unknown>;
  readonly repairs?: RepairAttempt[];
  readonly validation?: ValidationResult;
  readonly recommendations?: string[];
  readonly report?: Record<string, unknown> | null;
  readonly [key: string]: unknown;
}

export interface PersistedHistoryItem {
  readonly run_id: string;
  readonly project_id: string;
  readonly project_name: string;
  readonly status: RunStatus;
  readonly validation_status: ValidationStatus;
  readonly started_at?: string;
  readonly completed_at?: string;
  readonly finding_count: number;
  readonly retry_count: number;
}

export interface HistoryItem {
  readonly run_id?: string;
  readonly project_id: string;
  readonly project_name?: string;
  readonly status: RunStatus;
  readonly severity?: Severity;
  readonly started_at?: string;
  readonly completed_at?: string;
  readonly repair_attempts?: RepairAttempt[];
}

export interface User {
  readonly id: string;
  readonly email: string;
  readonly full_name: string;
  readonly role: Role;
  readonly active?: boolean;
}

export interface AuthSession {
  readonly user: User;
}

export interface WorkflowProgressEvent {
  readonly step?: string;
  readonly message?: string;
  readonly agent?: string;
  readonly status?: AgentRunStatus;
  readonly timestamp?: string;
  readonly level?: "info" | "warning" | "error";
  readonly [key: string]: unknown;
}

export interface StartAnalysisRequest {
  readonly project_id: string;
  readonly files: ProjectFile[];
  readonly language?: string;
  readonly project_type?: "dotnet" | "single_file";
  readonly project_context?: Record<string, unknown>;
  readonly tools?: string[];
}

export interface StartAnalysisResponse {
  readonly project_id: string;
  readonly run_id?: string;
  readonly status?: string;
  readonly [key: string]: unknown;
}

export interface FindingsResponse {
  readonly project_id: string;
  readonly findings: Finding[];
  readonly findings_count?: number;
}

export type FrontendValidationStatus = ValidationStatus;
