import type { ProgressEvent, ProjectFile, QualityTool, SandboxTestCase, WorkflowRunResponse } from "../types/finding";
import { apiBaseUrl, apiClient, mocksEnabled } from "../api/client";
import type { StartAnalysisResponse } from "../api/types";

const WORKFLOW_TIMEOUT_MS = 90 * 60 * 1000;

// In production this points at the API gateway, not individual agent services.
export interface StartAnalysisPayload {
  project_id: string;
  files: ProjectFile[];
  language?: string;
  project_type?: "dotnet" | "single_file";
  project_context?: Record<string, unknown>;
  tools?: QualityTool[];
  test_cases?: SandboxTestCase[];
}

interface StreamCompleteEvent {
  readonly result?: unknown;
}

interface WorkflowStreamSnapshot {
  readonly project_id?: unknown;
  readonly status?: unknown;
  readonly progress?: unknown;
  readonly error?: unknown;
  readonly retry_count?: unknown;
  readonly result?: unknown;
}

function workflowStatusMessage(status: string, step: string): string {
  if (status === "failed") return "The orchestrator reported a failed workflow.";
  if (status === "completed") return "The orchestrator completed the workflow.";
  return `Workflow ${status}: ${step}`;
}

function parseEvent<T>(event: MessageEvent<string>): T {
  return JSON.parse(event.data) as T;
}

export const api = {
  async startAnalysis(payload: StartAnalysisPayload, onProgress: (progress: ProgressEvent) => void, timeoutMs = WORKFLOW_TIMEOUT_MS): Promise<WorkflowRunResponse> {
    if (mocksEnabled()) return runMockAnalysis(payload, onProgress);
    const started = await apiClient.postWithProgress<StartAnalysisResponse>("/analysis/run", payload, (percent) => {
      onProgress({ step: "upload", status: "uploading", message: `Sending code to orchestrator: ${percent}%` });
    });
    const projectId = started.project_id || payload.project_id;

    return new Promise((resolve, reject) => {
      const workflowId = started.run_id || projectId;
      const source = new EventSource(`${apiBaseUrl()}/analysis/stream/${encodeURIComponent(workflowId)}`, { withCredentials: true });
      let settled = false;
      const timeout = window.setTimeout(() => finish(() => reject(new Error("Validation timed out while waiting for the orchestrator. You can retry the run."))), timeoutMs);
      const finish = (callback: () => void) => {
        if (settled) return;
        settled = true;
        window.clearTimeout(timeout);
        source.close();
        callback();
      };

      const emitSnapshot = (snapshot: WorkflowStreamSnapshot) => {
        const status = typeof snapshot.status === "string" ? snapshot.status : "working";
        const step = typeof snapshot.progress === "string" ? snapshot.progress : "unknown";
        onProgress({
          step,
          status,
          message: workflowStatusMessage(status, step),
          retry_count: typeof snapshot.retry_count === "number" ? snapshot.retry_count : undefined,
        });
        if (status === "cancelled") {
          finish(() => reject(new Error("The analysis workflow was cancelled before validation completed.")));
          return;
        }
        if (status === "failed") {
          finish(() => reject(new Error("The orchestrator could not complete this validation run.")));
          return;
        }
        if (status === "completed") {
          const result = toWorkflowRunResponse(snapshot.result, projectId);
          if (!result) {
            finish(() => reject(new Error("The orchestrator completed without a readable results report.")));
            return;
          }
          finish(() => resolve(result));
        }
      };

      source.onmessage = (event) => {
        try {
          emitSnapshot(JSON.parse(event.data) as WorkflowStreamSnapshot);
        } catch {
          onProgress({ step: "unknown", message: "Received an unreadable orchestrator status update." });
        }
      };

      source.addEventListener("progress", (event) => {
        try {
          const progress = parseEvent<ProgressEvent>(event as MessageEvent<string>);
          onProgress(progress);
        } catch {
          onProgress({ step: "unknown", message: "Received a progress update." });
        }
      });
      source.addEventListener("complete", (event) => {
        try {
          const data = parseEvent<StreamCompleteEvent>(event as MessageEvent<string>);
          const result = toWorkflowRunResponse(data.result, projectId);
          if (!result) throw new Error("Missing workflow result");
          finish(() => resolve(result));
        } catch {
          finish(() => reject(new Error("The workflow completed without a readable result.")));
        }
      });
      source.addEventListener("failed", () => finish(() => reject(new Error("The analysis workflow failed. Review the run status and retry if appropriate."))));
      source.onerror = () => {
        if (source.readyState === EventSource.CLOSED) finish(() => reject(new Error("The analysis event stream closed before a final result was received.")));
      };
    });
  },
  health(): Promise<{ status: string; service: string }> {
    return apiClient.get("/health");
  },
  status(projectId: string): Promise<Record<string, unknown>> {
    return apiClient.get(`/analysis/status/${encodeURIComponent(projectId)}`);
  },
  async agentCard(): Promise<Record<string, unknown>> {
    const url = `${import.meta.env.VITE_ORCHESTRATOR_URL ?? "http://localhost:8001"}/.well-known/agent-card.json`;
    try {
      const response = await fetch(url, { headers: { Accept: "application/json" } });
      if (!response.ok) throw new Error("Agent information is unavailable.");
      return await response.json() as Record<string, unknown>;
    } catch {
      throw new Error("Unable to load agent information.");
    }
  },
};

function isRecord(value: unknown): value is Record<string, unknown> {
  return value !== null && typeof value === "object" && !Array.isArray(value);
}

function firstRecord(...values: unknown[]): Record<string, unknown> | undefined {
  return values.find(isRecord);
}

function optionalArray(...values: unknown[]): unknown[] | undefined {
  return values.find(Array.isArray) as unknown[] | undefined;
}

function firstDefined(...values: unknown[]): unknown {
  return values.find((value) => value !== undefined && value !== null);
}

function normalizeValidationStatus(value: unknown): WorkflowRunResponse["validation_status"] {
  if (value === "passed" || value === "failed" || value === "pending" || value === "not_run" || value === "blocked" || value === "inconclusive" || value === "needs_review" || value === "error") return value;
  return "unknown";
}

function toWorkflowRunResponse(value: unknown, projectId: string): WorkflowRunResponse | null {
  if (!isRecord(value)) return null;
  const nested = firstRecord(value.result);
  const reportCandidate = firstRecord(value.final_report, nested?.final_report);
  if (!reportCandidate) return null;
  const reportedFindings = optionalArray(value.findings, nested?.findings, reportCandidate.findings);
  const findings = reportedFindings ?? [];
  const validationStatus = normalizeValidationStatus(firstDefined(value.validation_status, nested?.validation_status, reportCandidate.validation_status));
  const retryCount = firstDefined(value.retry_count, nested?.retry_count, reportCandidate.retry_count);
  const arrayValue = (key: string) => optionalArray(value[key], nested?.[key], reportCandidate[key]);
  const recordValue = (key: string) => firstRecord(value[key], nested?.[key], reportCandidate[key]);
  let analysisId: string | undefined;
  if (typeof value.analysis_id === "string") analysisId = value.analysis_id;
  else if (typeof reportCandidate.analysis_id === "string") analysisId = reportCandidate.analysis_id;
  return {
    run_id: typeof value.run_id === "string" ? value.run_id : undefined,
    analysis_id: analysisId,
    project_name: typeof value.project_name === "string" ? value.project_name : undefined,
    project_files: arrayValue("project_files") as ProjectFile[] | undefined,
    findings_reported: reportedFindings !== undefined,
    quality_summary: recordValue("quality_summary") as unknown as WorkflowRunResponse["quality_summary"],
    quality_findings: arrayValue("quality_findings") as WorkflowRunResponse["quality_findings"],
    security_findings: arrayValue("security_findings") as WorkflowRunResponse["security_findings"],
    security_tool_runs: arrayValue("security_tool_runs") as WorkflowRunResponse["security_tool_runs"],
    resource_findings: arrayValue("resource_findings") as WorkflowRunResponse["resource_findings"],
    prioritized_findings: arrayValue("prioritized_findings") as WorkflowRunResponse["prioritized_findings"],
    agent_errors: arrayValue("agent_errors") as WorkflowRunResponse["agent_errors"],
    stage_history: arrayValue("stage_history") as WorkflowRunResponse["stage_history"],
    repair_attempts: arrayValue("repair_attempts") as WorkflowRunResponse["repair_attempts"],
    validation_results: recordValue("validation_results"),
    sandbox_result: recordValue("sandbox_result"),
    reanalysis_findings: arrayValue("reanalysis_findings") as WorkflowRunResponse["reanalysis_findings"],
    reanalysis_summary: recordValue("reanalysis_summary") as unknown as WorkflowRunResponse["reanalysis_summary"],
    comparison: recordValue("comparison") as unknown as WorkflowRunResponse["comparison"],
    project_id: typeof value.project_id === "string" ? value.project_id : projectId,
    validation_status: validationStatus,
    retry_count: typeof retryCount === "number" ? retryCount : undefined,
    findings: findings as WorkflowRunResponse["findings"],
    final_report: reportCandidate as unknown as WorkflowRunResponse["final_report"],
  };
}

async function runMockAnalysis(payload: StartAnalysisPayload, onProgress: (progress: ProgressEvent) => void): Promise<WorkflowRunResponse> {
  const steps = ["understand", "analyze", "aggregate", "repair", "validate", "finalize"];
  steps.forEach((step, index) => {
    window.setTimeout(
      () => onProgress({ step, status: "working", retry_count: index >= 3 ? 1 : 0, message: `Mock workflow: ${step}` }),
      index * 80,
    );
  });
  await new Promise<void>((resolve) => window.setTimeout(resolve, steps.length * 80));
  const firstFile = payload.files[0];
  const finding = firstFile ? {
    finding_id: "mock-finding-001",
    category: "code_quality" as const,
    severity: "medium" as const,
    file: firstFile.path,
    line_number: 1,
    issue: "COMPLEXITY_B: example function has branching",
    description: "Mock finding used to exercise the evidence and status views.",
    evidence: "Mock evidence only; no analyzer was executed.",
    recommendation: "Review the branching structure.",
    status: "Open" as const,
    tool: "radon",
  } : undefined;
  const findings = finding ? [finding] : [];
  const repaired = firstFile ? { ...firstFile, content: `# Mock validated repair candidate\n${firstFile.content}` } : undefined;
  const configuredSandbox = payload.project_context ?? {};
  const toolNames = ["Semgrep", "Bandit", "Gitleaks", "Ruff", "Pylint", "Radon", "Pytest", "Coverage"];
  const toolResults = Object.fromEntries(toolNames.map((tool) => {
    const qualityTool = ["Ruff", "Pylint", "Radon"].includes(tool);
    return [tool.toLowerCase(), { agent: qualityTool ? "Code Quality Agent" : "Security/Validation Agent", status: qualityTool ? "completed" : "skipped", available: qualityTool, finding_count: qualityTool && tool === "Radon" ? findings.length : 0, duration_ms: qualityTool ? 18 : undefined, exit_code: qualityTool ? 0 : null, raw_output: qualityTool ? `Mock ${tool} output; no external tool executed.` : undefined }];
  }));
  const qualitySummary = {
    files_analyzed: payload.files.length, total_findings: findings.length, errors: 0, warnings: findings.length, info: 0,
    complex_functions: 1, by_tool: { ruff: 0, pylint: 0, radon: findings.length }, by_severity: { critical: 0, high: 0, medium: findings.length, low: 0, info: 0 },
    quality_score: 92, quality_gate_status: "PASS", quality_gate: { status: "PASS", overall_score: 92, thresholds: { minimum_score: 75, complexity_limit: 3 } }, maintainability: { maintainability_index: 84 },
    tool_runs: toolNames.map((tool) => ({ tool: tool.toLowerCase(), agent: toolResults[tool.toLowerCase()].agent, status: toolResults[tool.toLowerCase()].status, finding_count: toolResults[tool.toLowerCase()].finding_count, duration_ms: toolResults[tool.toLowerCase()].duration_ms, exit_code: toolResults[tool.toLowerCase()].exit_code, available: toolResults[tool.toLowerCase()].available, raw_output: toolResults[tool.toLowerCase()].raw_output })),
    tool_results: toolResults,
    hotspots: [{ function_name: "example", file: firstFile?.path ?? null, line: 1, complexity: 10, risk_level: "Low" }],
    per_file: Object.fromEntries(payload.files.map((file) => [file.path, { errors: 0, warnings: findings.length, complexity: "Low", issues: findings.length }])),
  };
  const runId = `mock-run-${crypto.randomUUID()}`;
  const report = {
    project_id: payload.project_id, validation_status: "passed" as const, retry_count: 1, max_retries: 3,
    findings_summary: { total: findings.length, by_severity: { critical: 0, high: 0, medium: findings.length, low: 0, info: 0 } },
    validation_results: { validation_id: `mock-validation-${crypto.randomUUID()}`, status: "PASS", sandbox_settings: configuredSandbox },
    repair_attempts: [{ attempt_id: `mock-repair-${crypto.randomUUID()}`, iteration: 1, status: "accepted", validation: { status: "PASS" } }],
    stage_history: steps.map((stage) => ({ stage, status: "completed" })), workflow_trace: steps,
    sandbox_result: { executed: true, duration_ms: 24, peak_memory_mb: 18, cpu_percent: 7, settings: configuredSandbox },
    final_code: repaired ? { [repaired.path]: repaired.content } : {},
    quality_summary: qualitySummary,
    workflow_summary: { workflow_status: "SUCCESS", total_findings: findings.length, findings_by_severity: { critical: 0, high: 0, medium: findings.length, low: 0, info: 0 }, quality_score: 92, quality_gate: "PASS", repair_attempts: [], validation_result: { status: "PASS" }, agent_failures: [], workflow_duration_seconds: 0.5 },
  };
  return { run_id: runId, project_id: payload.project_id, validation_status: "passed", retry_count: 1, findings, final_report: report };
}
