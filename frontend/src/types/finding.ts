export type Severity = "low" | "medium" | "high" | "critical" | "info";
export type FindingStatus = "Open" | "In repair" | "Fixed (validated)" | "Needs human review" | "False positive" | "Won't fix" | "open" | "repaired" | "ignored";

export interface Finding {
  finding_id: string;
  category: "security" | "code_quality" | "resource" | "bug" | "maintainability" | "style" | "convention" | "complexity";
  severity: Severity;
  file: string;
  line_number: number | null;
  issue: string;
  description: string;
  evidence?: string;
  recommendation?: string;
  status: FindingStatus;
  tool?: string;
  detected_by_tools?: string[];
  subcategory?: string;
  requires_human_review?: boolean;
}

export interface ProjectFile {
  path: string;
  content: string;
}

export interface SandboxTestCase {
  input: string;
  expected?: string | null;
}

export type ValidationStatus = "pending" | "not_run" | "passed" | "failed" | "blocked" | "inconclusive" | "needs_review" | "error" | "unknown";

export interface FinalReport {
  project_id: string;
  analysis_id?: string;
  workflow_status?: string;
  workflow_status_reason?: string;
  workflow_duration_seconds?: number;
  repair_status?: "success" | "partial" | "failed" | "needs_review" | "not_run" | "running"
    | "not_attempted" | "completed";
  analysis_checks?: {
    readonly code_quality?: boolean;
    readonly security?: boolean;
    readonly bug_resource?: boolean;
    readonly complexity?: boolean;
    readonly automated_repair?: boolean;
    readonly final_validation?: boolean;
    readonly ai_report_recommendations?: boolean;
  };
  sandbox_stdin?: string;
  test_cases?: SandboxTestCase[];
  validation_status: ValidationStatus;
  retry_count?: number;
  max_retries?: number;
  findings_summary: {
    total: number;
    by_severity: Record<Severity, number>;
  };
  validation_results: Record<string, unknown>;
  agent_errors?: Array<{ agent: string; error: string }>;
  findings?: Finding[];
  prioritized_findings?: Finding[];
  security_findings?: Finding[];
  resource_findings?: Finding[];
  recommendations?: string[];
  code_report?: {
    scanned_files: number;
    issues: Array<{
      file_path: string;
      line_number: number;
      issue_type: string;
      description: string;
    }>;
  };
  recommendation_report?: {
    fixes: Array<{
      file_path: string;
      line_number: number;
      original_code: string;
      suggested_code: string;
      explanation: string;
    }>;
    suggestions: Array<{
      category: string;
      suggestion: string;
      benefit: string;
    }>;
  };
  ai_recommendations_status?: {
    status: "disabled" | "skipped" | "completed" | "failed";
    message?: string;
    provider?: "ollama";
  };
  explanations?: Array<Record<string, unknown>>;
  repair_attempts?: Array<Record<string, unknown>>;
  stage_history?: Array<Record<string, unknown>>;
  sandbox_result?: Record<string, unknown>;
  final_code?: Record<string, string>;
  quality_summary?: QualitySummary;
  reanalysis_summary?: QualitySummary;
  reanalysis_findings?: Finding[];
  workflow_summary?: WorkflowSummary;
  workflow_trace?: string[];
  comparison?: {
    status?: "complete" | "partial" | "not_run";
    findings_before?: number;
    findings_after?: number | null;
    resolved_count?: number;
    unchanged_count?: number;
    new_count?: number;
    unverified_count?: number;
    skipped_checks?: string[];
    skipped_findings?: Finding[];
    remaining_original_count?: number;
    unverified_before_count?: number;
    unverified_after_count?: number;
    resolved_findings?: Finding[];
    unchanged_findings?: Finding[];
    new_findings?: Finding[];
    unverified_findings?: Finding[];
    remaining_findings?: Finding[];
    resolved_issues?: string[];
    remaining_issues?: string[];
    changed_files?: string[];
    improved?: boolean;
    quality_score_before?: number;
    quality_score_after?: number;
    findings_by_severity_before?: Record<string, number>;
    findings_by_severity_after?: Record<string, number>;
  };
}

export interface QualitySummary {
  files_analyzed: number;
  total_findings: number;
  errors: number;
  warnings: number;
  info: number;
  complex_functions: number;
  by_tool: Record<string, number>;
  by_severity: Record<string, number>;
  quality_score?: number;
  quality_gate_status?: string;
  quality_gate?: {
    status: string;
    overall_score: number;
    thresholds: Record<string, number>;
  };
  maintainability?: Record<string, unknown>;
  tool_runs?: Array<{ tool: string; agent?: string; status: string; finding_count: number; duration_ms?: number; exit_code?: number | null; error?: string; raw_output?: string; available?: boolean }>;
  tool_results?: Record<string, { status?: string; duration_ms?: number; exit_code?: number | null; error?: string; raw_output?: string; available?: boolean; agent?: string; finding_count?: number }>;
  hotspots?: Array<{ function_name: string; file: string | null; line: number | null; complexity: number; risk_level: string }>;
  per_file?: Record<string, { errors: number; warnings: number; complexity: string; issues: number; quality_score?: number; status?: string }>;
}

export interface WorkflowSummary {
  workflow_status: string;
  workflow_status_reason?: string;
  repair_status?: FinalReport["repair_status"];
  total_findings: number;
  findings_by_severity: Record<string, number>;
  quality_score?: number;
  quality_gate?: string;
  repair_attempts: Array<Record<string, unknown>>;
  validation_result: Record<string, unknown>;
  agent_failures: Array<{ agent: string; error: string }>;
  workflow_duration_seconds?: number;
}

export interface WorkflowRunResponse {
  run_id?: string;
  analysis_id?: string;
  project_name?: string;
  project_files?: ProjectFile[];
  findings_reported?: boolean;
  quality_summary?: QualitySummary;
  quality_findings?: Finding[];
  security_findings?: Finding[];
  security_tool_runs?: Array<Record<string, unknown>>;
  resource_findings?: Finding[];
  prioritized_findings?: Finding[];
  agent_errors?: Array<{ agent: string; error: string }>;
  stage_history?: Array<Record<string, unknown>>;
  repair_attempts?: Array<Record<string, unknown>>;
  validation_results?: Record<string, unknown>;
  sandbox_result?: Record<string, unknown>;
  reanalysis_findings?: Finding[];
  reanalysis_summary?: QualitySummary;
  comparison?: NonNullable<FinalReport["comparison"]>;
  analysis_configuration?: {
    readonly sandbox_timeout_seconds: number;
    readonly memory_limit_mb: number;
    readonly cpu_limit: number;
    readonly network_policy: "restricted" | "off";
    readonly llm_provider: string;
    readonly model_name: string;
  };
  project_id: string;
  validation_status: ValidationStatus;
  retry_count?: number;
  findings: Finding[];
  final_report: FinalReport;
}

export type QualityTool = "ruff" | "pylint" | "radon" | "mypy";
export type SecurityTool = "semgrep" | "bandit" | "gitleaks";

export interface RunHistoryEntry {
  project_id: string;
  timestamp: string;
  validation_status: ValidationStatus;
  result: WorkflowRunResponse;
  originalFiles: ProjectFile[];
}

export interface ProgressEvent {
  step: string;
  message: string;
  status?: string;
  retry_count?: number;
  error?: string;
}
