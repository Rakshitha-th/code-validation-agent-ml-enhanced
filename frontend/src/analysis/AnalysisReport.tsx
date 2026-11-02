import { useMemo, useState } from "react";
import { CodeComparison } from "../components/CodeComparison";
import { repairComparisonState, repairedProjectFiles } from "./codeComparison";
import { numberOf, recordOf, safeEvidence, textOf } from "./model";
import type { FinalReport, Finding, ProjectFile, QualitySummary, Severity, WorkflowRunResponse } from "../types/finding";

interface AnalysisReportProps {
  readonly result: WorkflowRunResponse;
  readonly sourceFiles: ProjectFile[];
  readonly completedAt?: string;
  readonly onViewFile: (path: string) => void;
}

const SEVERITIES: Severity[] = ["critical", "high", "medium", "low"];
const TOOLS = ["ruff", "pylint", "radon", "mypy"];
const TOOL_CODE_PATTERN = /\b[A-Z][A-Z0-9]{0,16}\d{3,}\b/;
const PIPELINE = [
  { label: "Understand", keys: ["understand"] },
  { label: "Analyze", keys: ["analyze"] },
  { label: "Aggregate", keys: ["aggregate"] },
  { label: "Prioritize", keys: ["explainprioritize", "prioritize"] },
  { label: "AI report & recommendations", keys: ["reportrecommend"] },
  { label: "Repair", keys: ["repair"] },
  { label: "Re-analyze", keys: ["reanalyze", "reanalyzecode"] },
  { label: "Validate", keys: ["validate"] },
  { label: "Compare", keys: ["compare"] },
  { label: "Report", keys: ["finalize", "report"] },
];

type FileMetrics = NonNullable<QualitySummary["per_file"]>[string];

interface ReportFileRow {
  readonly path: string;
  readonly source?: ProjectFile;
  readonly findings: Finding[];
  readonly metrics?: FileMetrics;
}

function toList(value: unknown): Record<string, unknown>[] | undefined {
  return Array.isArray(value) ? value.filter((item) => item !== null && typeof item === "object") as Record<string, unknown>[] : undefined;
}

function toFindings(value: unknown): Finding[] | undefined {
  return Array.isArray(value) ? value as Finding[] : undefined;
}

function display(value: string | number | undefined): string {
  return value === undefined || value === "" ? "Not available" : String(value);
}

function safeText(value: unknown): string | undefined {
  if (typeof value === "string" && value.trim()) return safeEvidence(value);
  if (typeof value === "number" && Number.isFinite(value)) return String(value);
  return undefined;
}

function severityCounts(findings: Finding[]): Record<Severity, number> {
  return {
    critical: findings.filter((finding) => finding.severity === "critical").length,
    high: findings.filter((finding) => finding.severity === "high").length,
    medium: findings.filter((finding) => finding.severity === "medium").length,
    low: findings.filter((finding) => finding.severity === "low").length,
    info: findings.filter((finding) => finding.severity === "info").length,
  };
}

function validationLabel(status: string): string {
  if (status === "passed") return "PASSED";
  if (status === "failed") return "FAILED";
  if (status === "blocked") return "BLOCKED";
  if (status === "inconclusive") return "INCONCLUSIVE";
  if (status === "needs_review") return "NEEDS REVIEW";
  if (status === "error") return "ERROR";
  if (status === "not_run") return "NOT RUN";
  if (status === "pending" || status === "in_progress") return "IN PROGRESS";
  return "NOT AVAILABLE";
}

function validationClass(status: string): string {
  if (status === "passed") return "passed";
  if (status === "failed") return "failed";
  if (status === "error" || status === "blocked") return "failed";
  if (status === "needs_review") return "partial";
  if (status === "pending" || status === "in_progress") return "progress";
  return "unknown";
}

function workflowLabel(status: string | undefined): string {
  if (!status) return "NOT AVAILABLE";
  const labels: Record<string, string> = {
    SUCCESS: "SUCCESS",
    REVIEW_REQUIRED: "REVIEW REQUIRED",
    AGENT_FAILURE: "AGENT FAILURE",
    PARTIAL_RESULT: "PARTIAL RESULT",
    MAX_REPAIR_ATTEMPTS_REACHED: "REVIEW REQUIRED",
    FAILED: "FAILED",
    ERROR: "ERROR",
    BLOCKED: "BLOCKED",
  };
  return labels[status.toUpperCase()] ?? statusDisplay(status);
}

function workflowClass(status: string | undefined): string {
  const normalized = status?.toUpperCase();
  if (normalized === "SUCCESS") return "passed";
  if (normalized === "REVIEW_REQUIRED" || normalized === "PARTIAL_RESULT" || normalized === "MAX_REPAIR_ATTEMPTS_REACHED") return "partial";
  if (normalized === "FAILED" || normalized === "ERROR" || normalized === "BLOCKED" || normalized === "AGENT_FAILURE") return "failed";
  return "unknown";
}

function severityCountsFromRecord(values: Record<string, unknown>): Record<Severity, number | undefined> {
  return {
    critical: numberOf(values.critical),
    high: numberOf(values.high),
    medium: numberOf(values.medium),
    low: numberOf(values.low),
    info: numberOf(values.info),
  };
}

function stageKey(value: unknown): string {
  return typeof value === "string" ? value.toLowerCase().replace(/[^a-z]/g, "") : "";
}

function stageStatusLabel(value: unknown): string {
  if (typeof value !== "string") return "Not reported";
  const status = value.toLowerCase().replace(/[ -]/g, "_");
  if (status === "completed" || status === "complete" || status === "passed" || status === "pass" || status === "success") return "Completed";
  if (status === "skipped" || status === "not_required" || status === "not_run") return "Skipped";
  if (status === "failed" || status === "error") return "Failed";
  if (status === "blocked") return "Blocked";
  if (status === "needs_review") return "Needs review";
  if (status === "pending" || status === "queued") return "Pending";
  if (status === "working" || status === "running" || status === "in_progress" || status === "started") return "Running";
  if (status === "partial") return "Partial";
  return "Not reported";
}

function stageStatusClass(label: string): string {
  if (label === "Completed") return "completed";
  if (label === "Skipped") return "skipped";
  if (label === "Failed") return "failed";
  if (label === "Blocked") return "failed";
  if (label === "Running") return "progress";
  if (label === "Partial" || label === "Needs review") return "partial";
  return "unknown";
}

function formatDate(value?: string): string {
  if (!value) return "Not available";
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? "Not available" : date.toLocaleString();
}

function findingCode(finding: Finding): string {
  const toolCode = TOOL_CODE_PATTERN.exec(finding.issue);
  return toolCode?.[0] ?? finding.issue;
}

function findingPriority(severity: Severity): string {
  const priorities: Record<Severity, string> = {
    critical: "P0",
    high: "P1",
    medium: "P2",
    low: "P3",
    info: "P4",
  };
  return priorities[severity];
}

function validationExplanation(status: string, findingCount: number, reasons: string[], counts: Record<Severity, number | undefined>): string {
  const criticalOrHigh = (counts.critical ?? 0) + (counts.high ?? 0);
  if (status === "passed") {
    if (findingCount === 0) return "Validation passed and no findings were returned.";
    const suffix = findingCount === 1 ? "" : "s";
    return `Validation passed with ${findingCount} finding${suffix} returned for review.`;
  }
  if (status === "error") return reasons[0] ? `Validation could not complete: ${reasons[0]}` : "Validation could not complete because a required service or execution step failed.";
  if (status === "failed") {
    if (reasons[0]) return `Validation did not pass because the workflow reported: ${reasons[0]}`;
    if (criticalOrHigh > 0) {
      const suffix = criticalOrHigh === 1 ? "" : "s";
      return `Validation failed with ${counts.critical} critical and ${counts.high} high-severity finding${suffix}.`;
    }
    return "The workflow reported a failed validation. No specific validation error was returned.";
  }
  if (status === "needs_review") return "Validation checks completed, but findings require human review before accepting the result.";
  if (status === "blocked") return reasons[0] ? `Validation was blocked: ${reasons[0]}` : "Validation could not run because a required dependency or project file was unavailable.";
  if (status === "pending") return "This workflow is still in progress; a final validation decision has not been returned.";
  if (status === "not_run") return "Final validation was not run for this analysis. Review the available analyzer results.";
  return "The workflow did not return a validation explanation.";
}

function validationHeading(status: string): string {
  if (status === "not_run") return "Validation not run";
  if (status === "passed") return "Why did validation pass?";
  if (status === "failed") return "Why did validation fail?";
  if (status === "needs_review") return "What needs review?";
  return "Why did validation stop?";
}

function toolStatusClass(status: string): string {
  if (status === "Completed" || status === "Passed") return "completed";
  if (status === "Failed") return "failed";
  return "unknown";
}

function availabilityMessage(agent: string, error: { message: string } | undefined, reported: boolean, count: number): string {
  if (error) return `Not analyzed: ${error.message}`;
  if (!reported) return `${agent} analysis was not run or its results were not returned for this validation.`;
  const suffix = count === 1 ? "" : "s";
  return `${count} ${agent.toLowerCase()} finding${suffix} returned.`;
}

function findingSummary(hasFindingData: boolean, findings: Finding[]): string {
  if (!hasFindingData) return "Finding data not returned";
  const suffix = findings.length === 1 ? "" : "s";
  return `${findings.length} finding${suffix} returned`;
}

function recommendationStatusLabel(status: string): string {
  if (status === "passed") return "REVIEW RESULTS";
  if (status === "failed") return "REVIEW REQUIRED";
  return validationLabel(status);
}

function getFindingData(result: WorkflowRunResponse, report: FinalReport, rawReport: Record<string, unknown>) {
  const findings = toFindings(result.findings) ?? toFindings(rawReport.findings) ?? [];
  const hasFindingData = result.findings_reported ?? (Array.isArray(result.findings) || Array.isArray(rawReport.findings));
  const bySeverity = recordOf(recordOf(report.findings_summary).by_severity);
  const counts = hasFindingData ? severityCounts(findings) : {
    critical: numberOf(bySeverity.critical),
    high: numberOf(bySeverity.high),
    medium: numberOf(bySeverity.medium),
    low: numberOf(bySeverity.low),
    info: numberOf(bySeverity.info),
  };
  return { findings, hasFindingData, counts };
}

function getAgentData(result: WorkflowRunResponse, report: FinalReport, rawReport: Record<string, unknown>, findings: Finding[]) {
  const errors = toList(result.agent_errors) ?? toList(report.agent_errors) ?? [];
  const errorSummary = errors.map((error) => ({
    agent: safeText(error.agent) ?? "Agent",
    message: safeText(error.error) ?? "Error detail not available",
  }));
  const findError = (names: string[]) => errorSummary.find((error) => names.some((name) => error.agent.toLowerCase().includes(name)));
  const securityFindings = toFindings(result.security_findings) ?? toFindings(report.security_findings) ?? findings.filter((finding) => finding.category === "security");
  const resourceFindings = toFindings(result.resource_findings) ?? toFindings(report.resource_findings) ?? findings.filter((finding) => finding.category === "resource" || finding.category === "bug");
  const securityError = findError(["security"]);
  const resourceError = findError(["bug", "resource"]);
  const securityReported = !securityError && (Array.isArray(result.security_findings) || Array.isArray(report.security_findings) || findings.some((finding) => finding.category === "security"));
  const resourceReported = !resourceError && (Array.isArray(result.resource_findings) || Array.isArray(report.resource_findings) || findings.some((finding) => finding.category === "resource" || finding.category === "bug"));
  return {
    errors,
    errorSummary,
    securityFindings,
    resourceFindings,
    securityError,
    resourceError,
    securityReported,
    resourceReported,
    securityTools: toList(result.security_tool_runs) ?? toList(rawReport.security_tool_runs) ?? [],
  };
}

function buildFileRows(sourceFiles: ProjectFile[], perFile: Record<string, FileMetrics | undefined>, findings: Finding[]): ReportFileRow[] {
  const paths = new Set([
    ...sourceFiles.map((file) => file.path),
    ...Object.keys(perFile),
    ...findings.map((finding) => finding.file),
  ]);
  return [...paths].sort((first, second) => first.localeCompare(second)).map((path) => ({
    path,
    source: sourceFiles.find((file) => file.path === path),
    findings: findings.filter((finding) => finding.file === path),
    metrics: perFile[path],
  }));
}

function buildRecommendations(report: FinalReport, findings: Finding[]): string[] {
  const reportRecommendations = Array.isArray(report.recommendations)
    ? report.recommendations.filter((item): item is string => typeof item === "string")
    : [];
  return [...reportRecommendations, ...findings.map((finding) => finding.recommendation).filter((item): item is string => Boolean(item))]
    .filter((recommendation, index, values) => values.indexOf(recommendation) === index);
}

function buildComparisonData(comparison: Record<string, unknown>, quality: QualitySummary | undefined, reanalysis: QualitySummary | undefined) {
  const hasReanalysis = Boolean(reanalysis && Object.keys(reanalysis).length > 0);
  const comparisonPartial = comparison.status === "partial";
  const beforeScore = numberOf(comparison.quality_score_before) ?? (hasReanalysis ? qualityScoreOf(quality) : undefined);
  const afterScore = numberOf(comparison.quality_score_after) ?? (hasReanalysis ? qualityScoreOf(reanalysis) : undefined);
  const beforeFindings = numberOf(comparison.findings_before) ?? (hasReanalysis ? numberOf(quality?.total_findings) : undefined);
  const afterFindings = numberOf(comparison.findings_after) ?? (!comparisonPartial && hasReanalysis ? numberOf(reanalysis?.total_findings) : undefined);
  return {
    beforeScore,
    afterScore,
    beforeFindings,
    afterFindings,
    reported: Object.keys(comparison).length > 0 || hasReanalysis,
    improvementScore: beforeScore !== undefined && afterScore !== undefined ? afterScore - beforeScore : undefined,
    improvementFindings: beforeFindings !== undefined && afterFindings !== undefined ? beforeFindings - afterFindings : undefined,
  };
}

function qualityScoreOf(quality: QualitySummary | undefined): number | undefined {
  return numberOf(quality?.quality_score)
    ?? numberOf(recordOf(quality).overall_score)
    ?? numberOf(recordOf(quality?.quality_gate).overall_score);
}

function buildStageRows(stageHistory: Record<string, unknown>[], reportedRepairStatus?: string) {
  return PIPELINE.map((stage) => {
    const matching = stageHistory.filter((entry) => stage.keys.includes(stageKey(entry.stage)));
    const current = matching[matching.length - 1];
    let status = stageStatusLabel(current?.status);
    if (stage.label === "Repair" && reportedRepairStatus === "partial") {
      status = "Partial";
    } else if (stage.label === "Repair" && reportedRepairStatus === "failed") {
      status = "Failed";
    } else if (stage.label === "Repair" && ["completed", "success"].includes(reportedRepairStatus ?? "")) {
      status = "Completed";
    } else if (stage.label === "Repair" && reportedRepairStatus === "needs_review") {
      status = "Needs review";
    } else if (stage.label === "Repair" && ["not_attempted", "not_run"].includes(reportedRepairStatus ?? "")) {
      status = "Skipped";
    } else if (stage.label === "Repair" && reportedRepairStatus === "running") {
      status = "Running";
    } else if (
      stage.label === "Repair"
      && matching.some((entry) => stageStatusLabel(entry.status) === "Partial")
      && status === "Failed"
    ) {
      status = "Partial";
    }
    return {
      ...stage,
      status,
      detail: safeText(current?.message) ?? safeText(current?.error),
      startedAt: typeof current?.started_at === "string" ? formatDate(current.started_at) : undefined,
      completedAt: typeof current?.completed_at === "string" ? formatDate(current.completed_at) : undefined,
      duration: numberOf(current?.duration_ms),
    };
  });
}

function buildValidationReasons(validation: Record<string, unknown>, sandbox: Record<string, unknown>, errors: Array<{ agent: string; message: string }>, failed: boolean): string[] {
  const validationError = safeText(validation.error);
  const sandboxError = safeText(sandbox.error);
  return [validationError, sandboxError, ...(failed ? errors.map((error) => `${error.agent}: ${error.message}`) : [])]
    .filter((reason, index, values): reason is string => Boolean(reason) && values.indexOf(reason) === index);
}

function getToolResult(tool: string, runs: QualitySummary["tool_runs"], results: QualitySummary["tool_results"]) {
  const run = runs?.find((item) => item.tool.toLowerCase() === tool);
  const resultRecord = recordOf(results?.[tool]);
  const status = run?.status ?? safeText(resultRecord.status);
  const unavailable = run?.available === false || resultRecord.available === false || status === "skipped" || status === "not_run";
  const findingCount = typeof run?.finding_count === "number" ? run.finding_count : numberOf(resultRecord.finding_count);
  const statusText = safeText(status);
  return { status: unavailable || !statusText ? "Not analyzed" : statusDisplay(statusText), findingCount };
}

function findingCategory(finding: Finding): string {
  const detail = `${finding.subcategory ?? ""} ${finding.issue} ${finding.description}`.toLowerCase();
  if (/syntax|parse error|syntaxerror/.test(detail)) return "Syntax";
  if (finding.category === "security") return "Security";
  if (/error.handling|uncaught|exception handling/.test(detail)) return "Error Handling";
  if (finding.category === "code_quality" || finding.category === "convention") return "Code Quality";
  if (finding.category === "bug") return "Bugs";
  if (finding.category === "resource") return "Resource Issues";
  if (finding.category === "maintainability") return "Maintainability";
  if (finding.category === "style") return "Style";
  if (finding.category === "complexity") return "Complexity";
  return "Other";
}

function findingStatusLabel(status: string): string {
  const normalized = status.trim().toLowerCase().replace(/[_-]/g, " ");
  if (["open", "new"].includes(normalized)) return "Open";
  if (["repaired", "fixed", "fixed (validated)", "resolved"].includes(normalized)) return "Resolved";
  if (["ignored", "false positive", "won't fix", "wont fix", "suppressed"].includes(normalized)) return "Suppressed";
  if (["needs human review", "unverified", "pending verification"].includes(normalized)) return "Unverified";
  return status;
}

function findingIdentity(finding: Finding): string {
  return [finding.category, finding.file.replace(/\\/g, "/"), finding.tool ?? "", finding.issue.trim().toLowerCase()].join("\u0000");
}

type FindingRepairFilter = "all" | "not_attempted" | "attempted" | "failed" | "confirmed" | "unresolved" | "unverified";

function matchesRepairFilter(
  filter: FindingRepairFilter,
  resolved: boolean,
  comparisonComplete: boolean,
  attempted: boolean,
  failed: boolean,
): boolean {
  if (filter === "all") return true;
  if (filter === "not_attempted") return !attempted;
  if (filter === "attempted") return attempted && !failed;
  if (filter === "failed") return failed;
  if (filter === "confirmed") return resolved && comparisonComplete;
  if (filter === "unresolved") return !resolved && comparisonComplete;
  return attempted && !comparisonComplete;
}

function compareFindingLists(before: Finding[], after: Finding[]): { unchanged: number; resolved: Finding[]; added: Finding[] } {
  const remaining = [...after];
  const resolved: Finding[] = [];
  let unchanged = 0;
  for (const finding of before) {
    let index = remaining.findIndex((candidate) => candidate.finding_id === finding.finding_id);
    if (index < 0) index = remaining.findIndex((candidate) => findingIdentity(candidate) === findingIdentity(finding));
    if (index < 0) resolved.push(finding);
    else {
      unchanged += 1;
      remaining.splice(index, 1);
    }
  }
  return { unchanged, resolved, added: remaining };
}

function buildQualityGateReason(quality: QualitySummary | undefined): string | undefined {
  const gate = recordOf(quality?.quality_gate);
  const status = (safeText(quality?.quality_gate_status) ?? safeText(gate.status))?.toUpperCase();
  const thresholds = recordOf(gate.thresholds);
  const measures: Array<{ label: string; value?: number; limit?: number; direction: "maximum" | "minimum" }> = [
    { label: "errors", value: numberOf(quality?.errors), limit: numberOf(thresholds.error_limit), direction: "maximum" },
    { label: "warnings", value: numberOf(quality?.warnings), limit: numberOf(thresholds.warning_limit), direction: "maximum" },
    { label: "complex functions", value: numberOf(quality?.complex_functions), limit: numberOf(thresholds.complexity_limit), direction: "maximum" },
    { label: "quality score", value: numberOf(gate.overall_score), limit: numberOf(thresholds.minimum_score), direction: "minimum" },
  ];
  const blockers = toList(gate.blocking_tools) ?? [];

  if (status === "FAIL") {
    const exceeded = measures.filter((measure) => {
      if (measure.value === undefined || measure.limit === undefined) return false;
      return measure.direction === "minimum" ? measure.value < measure.limit : measure.value > measure.limit;
    }).map((measure) => `${measure.label}: ${measure.value} against ${measure.direction} ${measure.limit}`);
    const blockedTools = blockers.map((tool) => `${safeText(tool.tool) ?? "Analyzer"} ${safeText(tool.status) ?? "did not complete"}`);
    return [...exceeded, ...blockedTools].join("; ") || "The backend reported a failed quality gate but did not return a specific reason.";
  }

  if (status === "PASS") {
    if (measures.some((measure) => measure.value === undefined || measure.limit === undefined)) {
      return "The backend reported a pass; configured threshold details were incomplete.";
    }
    const details = measures.map((measure) => `${measure.label} ${measure.value}/${measure.limit}`).join(", ");
    return `Passed configured limits: ${details}.`;
  }

  return undefined;
}

function buildRepairPresentation(
  attempts: Record<string, unknown>[] | undefined,
  stageRows: ReturnType<typeof buildStageRows>,
  reportedRepairStatus: string | undefined,
  errorSummary: Array<{ agent: string; message: string }>,
  validationStatus: string,
  findings: Finding[],
  validationReasons: string[],
  counts: Record<Severity, number | undefined>,
) {
  const lastAttempt = attempts?.[attempts.length - 1];
  let status: string | undefined;
  if (attempts !== undefined) {
    status = attempts.length === 0
      ? "No attempts reported"
      : safeText(lastAttempt?.outcome) ?? safeText(lastAttempt?.status) ?? "Status not available";
  }

  const failureReason = [...(attempts ?? [])].reverse().map((attempt) =>
    safeText(attempt.candidate_rejection_reason) ?? safeText(attempt.error)
  ).find(Boolean)
    ?? errorSummary.find((error) => error.agent.toLowerCase().includes("repair"))?.message;
  const attemptStatus = (
    safeText(lastAttempt?.repair_status)
    ?? safeText(lastAttempt?.outcome)
    ?? safeText(lastAttempt?.status)
  )?.toLowerCase();
  const attemptCount = attempts?.length ?? 0;
  const earlierChanges = (attempts ?? []).slice(0, -1).some((attempt) =>
    Array.isArray(attempt.changed_files) && attempt.changed_files.length > 0
  );
  const lastAttemptChanged = Array.isArray(lastAttempt?.changed_files) && lastAttempt.changed_files.length > 0;
  const partial = attemptStatus === "partial"
    || (earlierChanges && !lastAttemptChanged && ["failed", "error"].includes(attemptStatus ?? ""))
    || reportedRepairStatus === "partial"
    || stageRows.some((stage) => stage.label === "Repair" && stage.status === "Partial");
  const failed = !partial && reportedRepairStatus !== "success" && (reportedRepairStatus === "failed" || (
    (Boolean(lastAttempt) && ["failed", "error"].includes(attemptStatus ?? ""))
    || stageRows.some((stage) => stage.label === "Repair" && stage.status === "Failed")
  ));
  if (partial) status = "Partial";
  else if (["completed", "success"].includes(reportedRepairStatus ?? "")) status = "Completed";
  else if (["not_attempted", "not_run"].includes(reportedRepairStatus ?? "")) status = "Not run";
  else if (reportedRepairStatus === "needs_review") status = "Needs review";
  else if (reportedRepairStatus === "running") status = "Running";
  let explanation: string;
  if (partial) {
    const remaining = findings.length;
    const remainingText = remaining === 1 ? "1 finding remains" : `${remaining} findings remain`;
    explanation = failureReason
      ? `Repair made partial progress after ${attemptCount} attempts; ${remainingText}. The last attempt stopped: ${failureReason}`
      : `Repair made partial progress after ${attemptCount} attempts; ${remainingText}.`;
  } else if (reportedRepairStatus === "needs_review") {
    explanation = failureReason
      ? `Repair candidate requires review because analyzer verification was incomplete: ${failureReason}`
      : "Repair candidate requires review because the final analyzer comparison was incomplete.";
  } else if (!failed) {
    explanation = validationExplanation(validationStatus, findings.length, validationReasons, counts);
  } else {
    const attemptLabel = `${attemptCount} attempt${attemptCount === 1 ? "" : "s"}`;
    explanation = failureReason
      ? `Repair did not complete after ${attemptLabel}: ${failureReason}`
      : `Repair did not complete after ${attemptLabel}. No failure detail was returned.`;
  }

  return { status, failed, attemptCount, explanation };
}

function securityExecutionStatus(enabled: boolean, agentStatus: string | undefined, error: { message: string } | undefined, findings: Finding[]): string {
  const normalizedStatus = agentStatus?.toLowerCase();
  if (!enabled || normalizedStatus === "skipped") return "Skipped";
  if (error || normalizedStatus === "failed") return "Failed";
  if (normalizedStatus && ["completed", "complete", "passed", "done"].includes(normalizedStatus)) return "Completed";
  if (normalizedStatus && ["running", "working", "in_progress"].includes(normalizedStatus)) return "Running";
  if (findings.length > 0) return "Completed";
  return "Not reported";
}

function securityStatusMessage(status: string, error: { message: string } | undefined, findings: Finding[]): string {
  if (status === "Failed") {
    if (error) return `Security scan failed: ${error.message}`;
    return "Security scan failed. No execution details were returned.";
  }
  if (status === "Skipped") return "Security scanning was skipped for this run. No clean-scan conclusion is available.";
  if (status === "Running") return "Security scanning is still running; results are not final.";
  if (status === "Completed") {
    const suffix = findings.length === 1 ? "" : "s";
    return `Security scan completed; ${findings.length} finding${suffix} returned. Automated analysis does not establish that an application is completely secure.`;
  }
  return "Security execution status was not reported. An empty findings list is not evidence of a completed clean scan.";
}

function buildSecurityPresentation(
  enabled: boolean,
  agentStatus: string | undefined,
  error: { message: string } | undefined,
  findings: Finding[],
) {
  const status = securityExecutionStatus(enabled, agentStatus, error, findings);
  return { status, message: securityStatusMessage(status, error, findings) };
}

function buildComparisonPresentation({
  comparison,
  initialAnalysisCompleted,
  stageRows,
  initialFindings,
  reanalysisFindings,
  comparisonReported,
  hasReanalysis,
  beforeScore,
  afterScore,
  improvementScore,
}: {
  comparison: Record<string, unknown>;
  initialAnalysisCompleted: boolean;
  stageRows: ReturnType<typeof buildStageRows>;
  initialFindings: Finding[] | undefined;
  reanalysisFindings: Finding[] | undefined;
  comparisonReported: boolean;
  hasReanalysis: boolean;
  beforeScore: number | undefined;
  afterScore: number | undefined;
  improvementScore: number | undefined;
}) {
  const comparisonComplete = comparison.status === "complete";
  const completed = initialAnalysisCompleted
    && stageRows.some((stage) => stage.label === "Re-analyze" && stage.status === "Completed")
    && comparison.status !== "partial"
    && (comparisonComplete || initialFindings !== undefined && Array.isArray(reanalysisFindings));
  const serverResolved = toFindings(comparison.resolved_findings);
  const serverAdded = toFindings(comparison.new_findings);
  const serverUnchanged = numberOf(comparison.unchanged_count);
  const findingChanges = completed && comparisonComplete && serverResolved && serverAdded && serverUnchanged !== undefined
    ? { resolved: serverResolved, added: serverAdded, unchanged: serverUnchanged }
    : completed && initialFindings !== undefined
      ? compareFindingLists(initialFindings, reanalysisFindings ?? [])
    : undefined;
  const beforeFindings = completed
    ? numberOf(comparison.findings_before) ?? (findingChanges ? initialFindings?.length : undefined)
    : undefined;
  const afterFindings = completed
    ? numberOf(comparison.findings_after) ?? (findingChanges ? reanalysisFindings?.length : undefined)
    : undefined;
  const findingDelta = beforeFindings !== undefined && afterFindings !== undefined
    ? beforeFindings - afterFindings
    : undefined;
  return {
    completed,
    findingChanges,
    reported: completed && (comparisonReported || hasReanalysis),
    beforeScore: completed ? beforeScore : undefined,
    afterScore: completed ? afterScore : undefined,
    beforeFindings,
    afterFindings,
    improvementScore: completed ? improvementScore : undefined,
    findingDelta,
  };
}

function findingChangeLabel(change: number): string {
  if (change > 0) return `${change} fewer`;
  if (change < 0) return `${Math.abs(change)} more`;
  return "unchanged";
}

export function AnalysisReport({ result, sourceFiles, completedAt, onViewFile }: Readonly<AnalysisReportProps>) {
  const [selectedFilePath, setSelectedFilePath] = useState<string | null>(null);
  const report = result.final_report;
  const repairedFiles = repairedProjectFiles(result);
  const repairState = repairComparisonState(result);
  const rawReport = recordOf(report);
  const reportedRepairStatus = safeText(report.repair_status) ?? safeText(recordOf(report.workflow_summary).repair_status);
  const { findings, hasFindingData, counts } = getFindingData(result, report, rawReport);
  const quality = result.quality_summary ?? report.quality_summary;
  const perFile = quality?.per_file ?? {};
  const stageHistory = useMemo(
    () => toList(result.stage_history) ?? toList(report.stage_history) ?? [],
    [result.stage_history, report.stage_history],
  );
  const analysisStage = [...stageHistory].reverse().find((entry) => stageKey(entry.stage) === "analyze");
  const analysisAgents = recordOf(analysisStage?.agents);
  const initialQualityFindings = findings;
  const securityAgentStatus = safeText(
  typeof analysisAgents.security === "string"
    ? analysisAgents.security
    : recordOf(analysisAgents.security).status
);
  const attempts = toList(result.repair_attempts) ?? toList(report.repair_attempts) ?? toList(recordOf(report.workflow_summary).repair_attempts);
  const validation = recordOf(result.validation_results ?? report.validation_results);
  const sandbox = recordOf(result.sandbox_result ?? report.sandbox_result);
  const comparison = recordOf(result.comparison ?? report.comparison);
  const reanalysisSummary = result.reanalysis_summary ?? report.reanalysis_summary;
  const comparisonComplete = comparison.status === "complete";
  const comparisonFindings = toFindings(comparison.remaining_findings);
  const reanalysisFindings = toFindings(result.reanalysis_findings) ?? toFindings(report.reanalysis_findings);
  const resolvedFindings = comparisonComplete ? toFindings(comparison.resolved_findings) ?? [] : [];
  const currentFindings = comparisonFindings ?? reanalysisFindings ?? findings;
  const currentFindingDataReported = comparisonFindings !== undefined
    || reanalysisFindings !== undefined
    || (!comparison.status && hasFindingData);
  const finalQuality = comparisonComplete && reanalysisSummary ? reanalysisSummary : quality;
  const finalSourceFiles = comparisonComplete && report.final_code
    ? Object.entries(report.final_code).map(([path, content]) => ({ path, content }))
    : sourceFiles;
  const agentData = getAgentData(result, report, rawReport, findings);
  const {
    securityFindings,
    resourceFindings,
    securityError,
    resourceError,
    securityReported: securityDataReported,
    resourceReported: resourceDataReported,
    securityTools,
  } = agentData;
  const {
    beforeScore: rawComparisonBeforeScore,
    afterScore: rawComparisonAfterScore,
    reported: comparisonDataReported,
    improvementScore: rawImprovementScore,
  } = buildComparisonData(comparison, quality, reanalysisSummary);
  const reportedValidationStatus = result.validation_status ?? report.validation_status;
  const validationStatus = reportedValidationStatus === "pending" && report.analysis_checks?.final_validation === false
    ? "not_run"
    : reportedValidationStatus;
  const workflowStatus = safeText(report.workflow_status)
    ?? safeText(recordOf(report.workflow_summary).workflow_status);
  const qualityScore = qualityScoreOf(finalQuality);
  const comparisonSeverity = recordOf(comparison.findings_by_severity_after);
  const comparisonSeverityReported = comparisonComplete && Object.keys(comparisonSeverity).length > 0;
  const currentCounts = comparisonSeverityReported
    ? severityCountsFromRecord(comparisonSeverity)
    : currentFindingDataReported ? severityCounts(currentFindings) : counts;
  const currentFindingCount = comparisonComplete
    ? numberOf(comparison.findings_after) ?? (currentFindingDataReported ? currentFindings.length : undefined)
    : currentFindingDataReported ? currentFindings.length
      : comparison.status === "partial" ? undefined
        : hasFindingData ? findings.length : numberOf(recordOf(report.findings_summary).total);
  const fileRows = buildFileRows(finalSourceFiles, finalQuality?.per_file ?? perFile, currentFindings);
  const selectedFile = fileRows.find((file) => file.path === selectedFilePath);
  const recommendations = buildRecommendations(report, currentFindings);

  const stageRows = useMemo(
    () => buildStageRows(stageHistory, reportedRepairStatus),
    [stageHistory, reportedRepairStatus],
  );

  const validationReasons = buildValidationReasons(validation, sandbox, agentData.errorSummary, validationStatus === "failed" || validationStatus === "error");
  const workflowStatusReason = safeText(report.workflow_status_reason)
    ?? safeText(recordOf(report.workflow_summary).workflow_status_reason);
  const qualityGateReason = buildQualityGateReason(finalQuality);
  const repairPresentation = buildRepairPresentation(
    attempts,
    stageRows,
    reportedRepairStatus,
    agentData.errorSummary,
    validationStatus,
    currentFindings,
    validationReasons,
    currentCounts,
  );
  const { status: repairStatus, failed: repairFailed, explanation } = repairPresentation;
  const hasReanalysis = Boolean(reanalysisSummary && Object.keys(reanalysisSummary).length > 0);
  const comparisonPresentation = buildComparisonPresentation({
    comparison,
    initialAnalysisCompleted: stageRows.some((stage) => stage.label === "Analyze" && stage.status === "Completed"),
    stageRows,
    initialFindings: initialQualityFindings,
    reanalysisFindings: toFindings(result.reanalysis_findings) ?? toFindings(report.reanalysis_findings),
    comparisonReported: comparisonDataReported,
    hasReanalysis,
    beforeScore: rawComparisonBeforeScore,
    afterScore: rawComparisonAfterScore,
    improvementScore: rawImprovementScore,
  });
  const securityPresentation = buildSecurityPresentation(
    result.final_report.analysis_checks?.security !== false,
    securityAgentStatus,
    securityError,
    securityFindings,
  );

  return (
    <article className="analysis-report" id="analysis-report" aria-label="Code validation analysis report">
      <header className="report-cover">
        <div>
          <p className="report-eyebrow">CODE VALIDATION AGENT</p>
          <h2>Analysis report</h2>
          <p className="report-project-id">{safeEvidence(result.project_id)}</p>
        </div>
        <span className={`report-status report-status--${workflowClass(workflowStatus)}`}>Overall: {workflowLabel(workflowStatus)}</span>
      </header>

      <section className="report-section report-executive" aria-labelledby="report-summary-heading">
        <div className="report-section-heading"><div><p className="report-eyebrow">01 · EXECUTIVE SUMMARY</p><h2 id="report-summary-heading">At a glance</h2></div><p>Actual results returned by this analysis run</p></div>
        <dl className="report-meta-grid">
          <Meta label="Project name" value={safeText(result.project_name) ?? safeText(rawReport.project_name)} />
          <Meta label="Project ID" value={result.project_id} />
          <Meta label="Analysis run ID" value={safeText(result.run_id) ?? safeText(result.analysis_id) ?? safeText(report.analysis_id)} />
          <Meta label="Date and time" value={formatDate(completedAt)} note={completedAt ? "Recorded when this browser received the completed run." : undefined} />
        </dl>
        <div className="report-metric-grid">
          <Metric label="Overall result" value={workflowLabel(workflowStatus)} tone={workflowClass(workflowStatus)} />
          <Metric label="Validation status" value={validationLabel(validationStatus)} tone={validationClass(validationStatus)} />
          <Metric label="Workflow duration" value={report.workflow_duration_seconds === undefined ? undefined : `${report.workflow_duration_seconds} s`} />
          <Metric label="Quality score" value={qualityScore} />
          <Metric label="Files submitted" value={sourceFiles.length} />
          <Metric label="Files analyzed" value={finalQuality?.files_analyzed} />
          <Metric label="Current findings" value={currentFindingCount} />
          <Metric label="Repair attempts" value={attempts ? attempts.length : undefined} />
          <Metric
            label="Repair status"
            value={repairStatus}
            tone={repairFailed ? "failed" : repairStatus?.toLowerCase() === "partial" ? "partial" : undefined}
          />
        </div>
        <SeveritySummary
          counts={currentCounts}
          known={currentFindingDataReported || comparisonSeverityReported || (!comparison.status && Boolean(report.findings_summary?.by_severity))}
        />
      </section>

      <section className={`report-section report-explanation report-explanation--${validationClass(validationStatus)}`} aria-labelledby="report-explanation-heading">
        <div className="report-section-heading"><div><p className="report-eyebrow">02 · VALIDATION RESULT</p><h2 id="report-explanation-heading">{validationHeading(validationStatus)}</h2></div></div>
        <p className="report-explanation__lead">{explanation}</p>
        <div className="report-explanation__facts">
          <p><strong>Critical/high-risk findings:</strong> {hasFindingData ? `${(currentCounts.critical ?? 0) + (currentCounts.high ?? 0)} remaining` : "Not available"}</p>
          <p><strong>Cause reported by workflow:</strong> {workflowStatusReason ?? (validationReasons.length > 0 ? validationReasons.join(" · ") : "Not available")}</p>
          <p><strong>Next step:</strong> {nextStep(validationStatus, validationReasons, recommendations, currentFindings)}</p>
        </div>
        {hasFindingData && currentFindings.length > 0 && <a className="report-action-link" href="#report-findings-heading">Review findings</a>}
      </section>

      <section className="report-section" aria-labelledby="report-health-heading">
        <div className="report-section-heading"><div><p className="report-eyebrow">03 · CODE HEALTH</p><h2 id="report-health-heading">Severity and quality</h2></div></div>
        <div className="report-metric-grid report-metric-grid--compact">
          <Metric label="Errors reported by quality analyzer" value={finalQuality?.errors} />
          <Metric label="Warnings reported by quality analyzer" value={finalQuality?.warnings} />
          <Metric label="Complex functions" value={finalQuality?.complex_functions} />
          <Metric label="Quality gate" value={safeText(finalQuality?.quality_gate_status) ?? safeText(finalQuality?.quality_gate?.status)} />
        </div>
        {qualityGateReason && <p className="report-availability"><strong>Quality gate reason:</strong> {qualityGateReason}</p>}
        {finalQuality?.hotspots && finalQuality.hotspots.length > 0 && <div className="report-subsection"><h3>Complexity hotspots</h3><ul className="report-compact-list">{finalQuality.hotspots.map((hotspot) => <li key={`${hotspot.file}-${hotspot.line}-${hotspot.function_name}`}><strong>{safeEvidence(hotspot.function_name)}</strong><span>{safeEvidence(hotspot.file ?? "Not available")}:{hotspot.line ?? "Not available"} · complexity {hotspot.complexity} · {safeEvidence(hotspot.risk_level)} risk</span></li>)}</ul></div>}
      </section>

      <section className="report-section" aria-labelledby="report-pipeline-heading">
        <div className="report-section-heading"><div><p className="report-eyebrow">04 · WORKFLOW</p><h2 id="report-pipeline-heading">Validation pipeline</h2></div><p>Stages without a backend status are marked Not reported.</p></div>
        <ol className="report-pipeline">{stageRows.map((stage, index) => <li key={stage.label}><details className={`report-pipeline__stage report-pipeline__stage--${stageStatusClass(stage.status)}`}><summary><span className="report-pipeline__number">{String(index + 1).padStart(2, "0")}</span><span className="report-pipeline__name">{stage.label}</span><strong>{stage.status}</strong></summary>{stage.detail && <small>{safeEvidence(stage.detail)}</small>}<div className="report-pipeline__metadata">{stage.startedAt && <span>Started {stage.startedAt}</span>}{stage.completedAt && <span>Completed {stage.completedAt}</span>}{stage.duration !== undefined && <span>{stage.duration} ms</span>}{!stage.detail && !stage.startedAt && !stage.completedAt && stage.duration === undefined && <span>No additional stage details were returned.</span>}</div></details></li>)}</ol>
      </section>

      <section className="report-section" aria-labelledby="report-tools-heading">
        <div className="report-section-heading"><div><p className="report-eyebrow">05 · TOOL ANALYSIS</p><h2 id="report-tools-heading">Analyzer results</h2></div></div>
        <div className="report-table-wrap"><table className="report-table"><thead><tr><th>Tool</th><th>Status</th><th>Findings</th></tr></thead><tbody>{TOOLS.map((tool) => { const outcome = getToolResult(tool, finalQuality?.tool_runs, finalQuality?.tool_results); return <tr key={tool}><th scope="row">{tool[0].toUpperCase() + tool.slice(1)}</th><td><span className={`report-tool-status report-tool-status--${toolStatusClass(outcome.status)}`}>{outcome.status}</span></td><td>{display(outcome.findingCount)}</td></tr>; })}</tbody></table></div>
      </section>

      <section className="report-section" aria-labelledby="report-files-heading">
        <div className="report-section-heading"><div><p className="report-eyebrow">06 · FILE ANALYSIS</p><h2 id="report-files-heading">Files and findings</h2></div><p>{fileRows.length} file{fileRows.length === 1 ? "" : "s"} available in this report</p></div>
        <FileAnalysisTable fileRows={fileRows} hasFindingData={hasFindingData} selectedFilePath={selectedFilePath} onSelectFile={setSelectedFilePath} />
        {selectedFile && <FileDetail path={selectedFile.path} source={selectedFile.source} findings={selectedFile.findings} onViewFile={onViewFile} />}
      </section>

      <section className="report-section" aria-labelledby="report-findings-heading">
        <FindingDetailsSection
          findings={currentFindings}
          resolvedFindings={resolvedFindings}
          detectedCount={numberOf(comparison.findings_before) ?? findings.length}
          unresolvedOriginalCount={numberOf(comparison.remaining_original_count) ?? numberOf(comparison.unchanged_count)}
          newFindingCount={numberOf(comparison.new_count)}
          newFindingIds={new Set((toFindings(comparison.new_findings) ?? []).map((finding) => finding.finding_id))}
          criticalHighCount={(currentCounts.critical ?? 0) + (currentCounts.high ?? 0)}
          hasFindingData={hasFindingData}
          comparisonComplete={comparisonComplete}
          repairAttempted={Boolean(attempts?.length)}
          repairFailed={repairFailed}
          validationStatus={validationStatus}
          changedFiles={Array.isArray(comparison.changed_files) ? comparison.changed_files.filter((path): path is string => typeof path === "string") : []}
          repairedCode={recordOf(report.final_code)}
          sourceFiles={finalSourceFiles}
          originalSourceFiles={sourceFiles}
          onViewFile={onViewFile}
        />
      </section>

      <SecuritySection error={securityError} reported={securityDataReported} findings={securityFindings} tools={securityTools} status={securityPresentation.status} statusMessage={securityPresentation.message} />
      <ResourceSection error={resourceError} reported={resourceDataReported} findings={resourceFindings} />

      <section className="report-section" aria-labelledby="report-repair-heading">
        <div className="report-section-heading"><div><p className="report-eyebrow">10 · REPAIR HISTORY</p><h2 id="report-repair-heading">Repair attempts</h2></div></div>
        <RepairHistorySection attempts={attempts} repairSkipped={stageRows.find((stage) => stage.label === "Repair")?.status === "Skipped"} />
      </section>

      <section className="report-section" aria-labelledby="report-comparison-heading">
        <div className="report-section-heading"><div><p className="report-eyebrow">11 · BEFORE / AFTER</p><h2 id="report-comparison-heading">Re-analysis comparison</h2></div></div>
          <ComparisonSection reported={comparisonPresentation.reported} comparison={comparison} beforeFindings={comparisonPresentation.beforeFindings} afterFindings={comparisonPresentation.afterFindings} beforeScore={comparisonPresentation.beforeScore} afterScore={comparisonPresentation.afterScore} improvementFindings={comparisonPresentation.findingDelta} improvementScore={comparisonPresentation.improvementScore} />
        {comparisonPresentation.findingChanges && <div className="report-finding-changes" aria-label="Quality finding comparison"><Metric label="Original findings" value={comparisonPresentation.beforeFindings} /><Metric label="Resolved findings" value={comparisonPresentation.findingChanges.resolved.length} /><Metric label="Remaining findings" value={comparisonPresentation.afterFindings} /><Metric label="New findings" value={comparisonPresentation.findingChanges.added.length} /><Metric label="Unchanged findings" value={comparisonPresentation.findingChanges.unchanged} />{comparisonPresentation.findingChanges.resolved.length > 0 && <p><strong>Resolved:</strong> {comparisonPresentation.findingChanges.resolved.map((finding) => safeEvidence(finding.issue)).join(" · ")}</p>}{comparisonPresentation.findingChanges.added.length > 0 && <p><strong>New:</strong> {comparisonPresentation.findingChanges.added.map((finding) => safeEvidence(finding.issue)).join(" · ")}</p>}</div>}
        <div className="report-code-comparison">
          <h3>Original and repaired code</h3>
          <p className="report-availability">Compare your submitted source with the repair candidate. Changed lines are highlighted; if no repair was produced, the reason is shown here.</p>
          <CodeComparison original={sourceFiles} repaired={repairedFiles} {...repairState} />
        </div>
      </section>

      <AiRecommendationSection report={report} />

      <section className="report-section report-recommendation" aria-labelledby="report-recommendation-heading">
        <div className="report-section-heading"><div><p className="report-eyebrow">12 · NEXT STEPS</p><h2 id="report-recommendation-heading">Final recommendation</h2></div><span className={`report-status report-status--${validationClass(validationStatus)}`}>{recommendationStatusLabel(validationStatus)}</span></div>
        <p>{nextStep(validationStatus, validationReasons, recommendations, currentFindings)}</p>
        {recommendations.length > 0 ? <><h3>Analyzer recommendations</h3><ol>{recommendations.map((recommendation, index) => <li key={`${index}-${recommendation}`}>{safeEvidence(recommendation)}</li>)}</ol></> : <p>No analyzer recommendations were returned.</p>}
      </section>
      <footer className="report-footnote">The report displays only data returned by the connected analysis services. Unavailable measurements are not estimated.</footer>
    </article>
  );
}

function FileAnalysisTable({ fileRows, hasFindingData, selectedFilePath, onSelectFile }: Readonly<{
  fileRows: ReportFileRow[];
  hasFindingData: boolean;
  selectedFilePath: string | null;
  onSelectFile: (path: string | null) => void;
}>) {
  if (fileRows.length === 0) return <p className="report-unavailable">File-level analysis data was not returned.</p>;
  return <div className="report-table-wrap"><table className="report-table report-file-table"><thead><tr><th>File</th><th>Findings</th><th>Quality score</th><th>Status</th><th>Details</th></tr></thead><tbody>{fileRows.map((file) => {
    const metrics = file.metrics;
    let fileStatus = safeText(metrics?.status);
    if (!fileStatus && file.findings.length > 0) fileStatus = "Findings returned";
    if (!fileStatus && hasFindingData) fileStatus = "No findings returned";
    if (!fileStatus) fileStatus = "Not available";
    const selected = selectedFilePath === file.path;
    return <tr key={file.path}><th scope="row">{safeEvidence(file.path)}</th><td>{file.findings.length}</td><td>{display(numberOf(metrics?.quality_score))}</td><td>{fileStatus}</td><td><button className="report-text-button" type="button" onClick={() => onSelectFile(selected ? null : file.path)} aria-expanded={selected}>{selected ? "Hide" : "View file"}</button></td></tr>;
  })}</tbody></table></div>;
}

function SecuritySection({ error, reported, findings, tools, status, statusMessage }: Readonly<{
  error?: { message: string };
  reported: boolean;
  findings: Finding[];
  tools: Record<string, unknown>[];
  status: string;
  statusMessage: string;
}>) {
  const toolText = tools.length > 0
    ? tools.map((tool) => `${safeText(tool.tool) ?? "Tool"} (${safeText(tool.status) ?? "status not reported"})`).join(", ")
    : "Not available";
  return <section className="report-section report-optional-analysis" aria-labelledby="report-security-heading">
    <div className="report-section-heading"><div><p className="report-eyebrow">08 · SECURITY</p><h2 id="report-security-heading">Security analysis</h2></div></div>
    <p className="report-availability"><strong>{status}:</strong> {statusMessage || availabilityMessage("Security", error, reported, findings.length)}</p>
    {reported && <><SeveritySummary counts={severityCounts(findings)} known /><p><strong>Security tools:</strong> {toolText}</p>{findings.map((finding) => <p className="report-mini-finding" key={finding.finding_id}>{safeEvidence(finding.severity)} · {safeEvidence(finding.issue)} · {safeEvidence(finding.file)}</p>)}</>}
  </section>;
}

function ResourceSection({ error, reported, findings }: Readonly<{
  error?: { message: string };
  reported: boolean;
  findings: Finding[];
}>) {
  return <section className="report-section report-optional-analysis" aria-labelledby="report-resource-heading">
    <div className="report-section-heading"><div><p className="report-eyebrow">09 · BUGS &amp; RESOURCES</p><h2 id="report-resource-heading">Bug and resource analysis</h2></div></div>
    <p className="report-availability">{availabilityMessage("Bug/resource", error, reported, findings.length)}</p>
    {reported && <div className="report-resource-grid"><Metric label="Potential bugs" value={findings.filter((finding) => finding.category === "bug").length} /><Metric label="Resource issues" value={findings.filter((finding) => finding.category === "resource").length} /><Metric label="Performance issues" value={findings.filter((finding) => /performance|slow|inefficient/i.test(`${finding.category} ${finding.subcategory ?? ""} ${finding.issue}`)).length} /><Metric label="Error-handling issues" value={findings.filter((finding) => /error.handling|exception|uncaught|failure path/i.test(`${finding.subcategory ?? ""} ${finding.issue} ${finding.description}`)).length} /></div>}
  </section>;
}

function AiRecommendationSection({ report }: Readonly<{ report: FinalReport }>) {
  const status = report.ai_recommendations_status;
  if (!status || status.status === "disabled") return null;
  const content = status.status === "completed"
    ? <AiRecommendationContent report={report} />
    : <p className="report-availability">{status.message ?? "The AI report/recommendation stage did not return results."}</p>;
  return <section className="report-section" aria-labelledby="report-ai-heading">
    <div className="report-section-heading"><div><p className="report-eyebrow">AI REVIEW</p><h2 id="report-ai-heading">Report and recommendations</h2></div><span>{status.provider ? `${status.status.toUpperCase()} · ${status.provider.toUpperCase()}` : status.status.toUpperCase()}</span></div>
    {content}
  </section>;
}

function AiRecommendationContent({ report }: Readonly<{ report: FinalReport }>) {
  const issues = report.code_report?.issues ?? [];
  const fixes = report.recommendation_report?.fixes ?? [];
  const suggestions = report.recommendation_report?.suggestions ?? [];
  const provider = report.ai_recommendations_status?.provider;
  const providerLabel = provider === "ollama" ? "Ollama" : "configured provider";
  return <>
    <p className="report-availability">Independent {providerLabel} review of {report.code_report?.scanned_files ?? 0} submitted files. Suggested changes are review-only and have not been applied.</p>
    <h3>Additional issues</h3>
    {issues.length > 0 ? <ol className="report-ai-list">{issues.map((issue, index) => <li key={`${issue.file_path}-${issue.line_number}-${index}`}><strong>{safeEvidence(issue.issue_type)}</strong> · {safeEvidence(issue.file_path)}:{issue.line_number}<p>{safeEvidence(issue.description)}</p></li>)}</ol> : <p className="report-empty">No additional issues were returned.</p>}
    <h3>Proposed fixes</h3>
    {fixes.length > 0 ? <ol className="report-ai-list">{fixes.map((fix, index) => <li key={`${fix.file_path}-${fix.line_number}-${index}`}><strong>{safeEvidence(fix.file_path)}:{fix.line_number}</strong><p>{safeEvidence(fix.explanation)}</p><div className="report-ai-code"><span>Original</span><pre><code>{safeEvidence(fix.original_code)}</code></pre></div><div className="report-ai-code"><span>Suggested</span><pre><code>{safeEvidence(fix.suggested_code)}</code></pre></div></li>)}</ol> : <p className="report-empty">No code fixes were returned.</p>}
    <h3>Project suggestions</h3>
    {suggestions.length > 0 ? <ul className="report-ai-list">{suggestions.map((suggestion, index) => <li key={`${suggestion.category}-${index}`}><strong>{safeEvidence(suggestion.category)}:</strong> {safeEvidence(suggestion.suggestion)}<p>{safeEvidence(suggestion.benefit)}</p></li>)}</ul> : <p className="report-empty">No project-level suggestions were returned.</p>}
  </>;
}

function FindingDetailsSection({ findings, resolvedFindings, detectedCount, unresolvedOriginalCount, newFindingCount, newFindingIds, criticalHighCount, hasFindingData, comparisonComplete, repairAttempted, repairFailed, validationStatus, changedFiles, repairedCode, sourceFiles, originalSourceFiles, onViewFile }: Readonly<{
  findings: Finding[];
  resolvedFindings: Finding[];
  detectedCount: number;
  unresolvedOriginalCount?: number;
  newFindingCount?: number;
  newFindingIds: Set<string>;
  criticalHighCount: number;
  hasFindingData: boolean;
  comparisonComplete: boolean;
  repairAttempted: boolean;
  repairFailed: boolean;
  validationStatus: string;
  changedFiles: string[];
  repairedCode: Record<string, unknown>;
  sourceFiles: ProjectFile[];
  originalSourceFiles: ProjectFile[];
  onViewFile: (path: string) => void;
}>) {
  const [severityFilter, setSeverityFilter] = useState("all");
  const [categoryFilter, setCategoryFilter] = useState("all");
  const [repairFilter, setRepairFilter] = useState<FindingRepairFilter>("all");
  const allFindings = [
    ...resolvedFindings.map((finding) => ({ finding, resolved: true, isNew: false })),
    ...findings.map((finding) => ({ finding, resolved: false, isNew: newFindingIds.has(finding.finding_id) })),
  ];
  const categories = [...new Set(allFindings.map(({ finding }) => findingCategory(finding)))].sort();
  const filteredFindings = allFindings
    .filter(({ finding }) => severityFilter === "all" || finding.severity === severityFilter)
    .filter(({ finding }) => categoryFilter === "all" || findingCategory(finding) === categoryFilter)
    .filter(({ resolved }) => matchesRepairFilter(repairFilter, resolved, comparisonComplete, repairAttempted, repairFailed))
    .sort((first, second) => severityRank(first.finding.severity) - severityRank(second.finding.severity) || first.finding.file.localeCompare(second.finding.file));
  const repairedPaths = new Set(changedFiles);
  const statusText = validationLabel(validationStatus);
  const unresolvedCount = comparisonComplete ? unresolvedOriginalCount : undefined;
  const resolvedCount = comparisonComplete ? resolvedFindings.length : undefined;

  return <>
    <div className="report-section-heading"><div><p className="report-eyebrow">07 · FINDINGS &amp; PRIORITY</p><h2 id="report-findings-heading">Validation results</h2></div><p>{findingSummary(hasFindingData, findings)} · ordered by severity</p></div>
    <dl className="validation-issue-summary" aria-label="Validation issue summary">
      <SummaryMetric label="Original findings" value={hasFindingData ? detectedCount : "Not available"} />
      <SummaryMetric label="Critical / High remaining" value={hasFindingData ? criticalHighCount : "Not available"} />
      <SummaryMetric label="Resolved original findings" value={resolvedCount ?? "Not confirmed"} />
      <SummaryMetric label="Remaining original findings" value={unresolvedCount ?? "Not confirmed"} />
      <SummaryMetric label="New findings" value={comparisonComplete ? newFindingCount ?? 0 : "Not confirmed"} />
      <SummaryMetric label="Validation status" value={statusText} />
    </dl>
    {hasFindingData && allFindings.length > 0 && <div className="validation-issue-filters">
      <label>Severity<select aria-label="Filter issues by severity" value={severityFilter} onChange={(event) => setSeverityFilter(event.target.value)}><option value="all">All severities</option>{SEVERITIES.map((severity) => <option key={severity} value={severity}>{severityLabel(severity)}</option>)}</select></label>
      <label>Category<select aria-label="Filter issues by category" value={categoryFilter} onChange={(event) => setCategoryFilter(event.target.value)}><option value="all">All categories</option>{categories.map((category) => <option key={category} value={category}>{category}</option>)}</select></label>
      <label>Repair status<select aria-label="Filter issues by repair status" value={repairFilter} onChange={(event) => setRepairFilter(event.target.value as FindingRepairFilter)}><option value="all">All repair statuses</option><option value="not_attempted">Not attempted</option><option value="attempted">Repair attempted</option><option value="failed">Repair failed</option><option value="confirmed">Confirmed by re-analysis</option><option value="unresolved">Unresolved after re-analysis</option><option value="unverified">Unverified (repair attempted)</option></select></label>
    </div>}
    {!hasFindingData
      ? <p className="report-unavailable">Finding details are not available for this run.</p>
      : allFindings.length === 0
        ? <p className="report-empty">No findings were returned by the available analyzers.</p>
        : filteredFindings.length === 0
          ? <p className="report-empty">No findings match these filters.</p>
          : <div className="report-finding-list">{filteredFindings.map(({ finding, resolved, isNew }) => {
            const repairedSource = comparisonComplete && repairedPaths.has(finding.file) && typeof repairedCode[finding.file] === "string"
              ? { path: finding.file, content: repairedCode[finding.file] as string }
              : undefined;
            return <FindingDetail
              key={`${finding.finding_id}-${resolved ? "resolved" : "active"}`}
              finding={finding}
              source={(resolved ? originalSourceFiles : sourceFiles).find((file) => file.path === finding.file)}
              repairedSource={repairedSource}
              repairStatus={repairStatusLabel(resolved, repairAttempted, repairFailed)}
              resolutionStatus={resolutionStatusLabel(resolved, statusText === "PASSED", isNew)}
              validationStatus={statusText}
              onViewFile={onViewFile}
            />;
          })}</div>}
  </>;
}

function SummaryMetric({ label, value }: Readonly<{ label: string; value: number | string }>) {
  return <div><dt>{label}</dt><dd>{value}</dd></div>;
}

function repairStatusLabel(resolved: boolean, attempted: boolean, failed: boolean): string {
  if (resolved) return "Repair confirmed by re-analysis";
  if (failed) return "Repair stage failed; issue-level outcome unavailable";
  if (attempted) return "Repair attempted; issue-level outcome unavailable";
  return "Repair not attempted";
}

function resolutionStatusLabel(resolved: boolean, validationPassed: boolean, isNew: boolean): string {
  if (resolved && validationPassed) return "Fixed · Validation passed for this issue";
  if (resolved) return "Fixed · Re-analysis confirmed";
  if (isNew) return "New · Introduced after repair";
  return "Unresolved · Revalidation required";
}

function RepairHistorySection({ attempts, repairSkipped }: Readonly<{ attempts?: Record<string, unknown>[]; repairSkipped: boolean }>) {
  if (attempts === undefined) return <p className="report-unavailable">Repair attempt history was not returned by the workflow.</p>;
  if (attempts.length === 0) return <p className="report-empty">{repairSkipped ? "No repair was required." : "No repair attempts were returned."}</p>;
  return <ol className="report-attempt-list">{attempts.map((attempt, index) => {
    const repairDetails = toList(attempt.repair_details) ?? [];
    return <li key={`${safeText(attempt.attempt_id) ?? "attempt"}-${index}`}>
      <div className="report-attempt-heading"><h3>Attempt {safeText(attempt.iteration) ?? index + 1}</h3><span>{safeEvidence(safeText(attempt.outcome) ?? safeText(attempt.status) ?? "Status not available")}</span></div>
      <dl><Meta label="Mode" value={safeText(attempt.mode)} /><Meta label="Candidate" value={safeText(attempt.candidate_status)} /><Meta label="Files changed" value={Array.isArray(attempt.changed_files) ? attempt.changed_files.length : undefined} /><Meta label="Findings detected" value={numberOf(attempt.findings_detected)} /><Meta label="Findings before" value={numberOf(attempt.findings_before)} /><Meta label="Findings after" value={numberOf(attempt.findings_after)} /><Meta label="Validation" value={safeText(recordOf(attempt.validation).status) ?? safeText(attempt.validation_status)} /></dl>
      {safeText(attempt.error) && <p className="report-availability">{safeEvidence(safeText(attempt.error))}</p>}
      {safeText(attempt.candidate_rejection_reason) && <p className="report-availability">Candidate rejected: {safeEvidence(safeText(attempt.candidate_rejection_reason))}</p>}
      {safeText(attempt.candidate_acceptance_reason) && <p className="report-availability">{safeEvidence(safeText(attempt.candidate_acceptance_reason))}</p>}
      {repairDetails.length > 0 && <ul className="report-compact-list">{repairDetails.map((detail, detailIndex) => <li key={`${safeText(detail.path) ?? "file"}-${detailIndex}`}><strong>{safeEvidence(safeText(detail.path) ?? "File")}</strong><span>{detail.changed === true ? "Changed" : "Unchanged"} · {safeEvidence(safeText(detail.explanation) ?? "No per-file repair explanation returned.")}</span></li>)}</ul>}
    </li>;
  })}</ol>;
}

function ComparisonSection({ reported, comparison, beforeFindings, afterFindings, beforeScore, afterScore, improvementFindings, improvementScore }: Readonly<{
  reported: boolean;
  comparison: Record<string, unknown>;
  beforeFindings?: number;
  afterFindings?: number;
  beforeScore?: number;
  afterScore?: number;
  improvementFindings?: number;
  improvementScore?: number;
}>) {
  const hasComparisonValues = beforeFindings !== undefined || afterFindings !== undefined || beforeScore !== undefined || afterScore !== undefined;
  if (!reported || !hasComparisonValues) {
    if (comparison.status === "partial") {
      const unverified = numberOf(comparison.unverified_count);
      return <p className="report-availability">Comparison is partial because one or more re-analysis agents did not complete. Findings could not be confirmed: {unverified ?? "count unavailable"}.</p>;
    }
    return <p className="report-unavailable">Before/after comparison data was not returned for this run.</p>;
  }
  return <>
    <div className="report-comparison-grid"><ComparisonColumn title="Before repair" qualityScore={beforeScore} findingCount={beforeFindings} severity={recordOf(comparison.findings_by_severity_before)} /><ComparisonColumn title="After repair" qualityScore={afterScore} findingCount={afterFindings} severity={recordOf(comparison.findings_by_severity_after)} /></div>
    {improvementScore !== undefined && <p className="report-improvement">Quality score change: {improvementScore > 0 ? "+" : ""}{improvementScore}</p>}
    {improvementFindings !== undefined && <p className="report-improvement">Finding change: {findingChangeLabel(improvementFindings)}</p>}
  </>;
}

function Meta({ label, value, note }: Readonly<{ label: string; value?: string | number; note?: string }>) {
  return <div className="report-meta"><dt>{label}</dt><dd>{display(value)}</dd>{note && <small>{note}</small>}</div>;
}

function Metric({ label, value, tone }: Readonly<{ label: string; value?: string | number; tone?: string }>) {
  const className = ["report-metric", tone ? `report-metric--${tone}` : ""].filter(Boolean).join(" ");
  return <div className={className}><span>{label}</span><strong>{display(value)}</strong></div>;
}

function SeveritySummary({ counts, known, large = false }: Readonly<{ counts: Record<Severity, number | undefined>; known: boolean; large?: boolean }>) {
  return <div className={`report-severity-grid${large ? " report-severity-grid--large" : ""}`}>{SEVERITIES.map((severity) => <div className={`report-severity-card report-severity--${severity}`} key={severity}><span>{severityLabel(severity)}</span><strong>{known && counts[severity] !== undefined ? counts[severity] : "Not available"}</strong></div>)}</div>;
}

function FindingDetail({ finding, source, repairedSource, repairStatus, resolutionStatus, validationStatus, onViewFile }: Readonly<{ finding: Finding; source?: ProjectFile; repairedSource?: ProjectFile; repairStatus: string; resolutionStatus: string; validationStatus: string; onViewFile: (path: string) => void }>) {
  const code = findingCode(finding);
  const displayTitle = textOf(finding.issue) ?? textOf(finding.description) ?? "Finding details not available";
  const findingStatus = findingStatusLabel(finding.status);
  return <article className={`report-finding report-finding--${finding.severity}`}>
    <header className="report-finding__header">
      <div className="report-finding__badges"><span className={`report-priority-pill report-priority-pill--${finding.severity}`}>{findingPriority(finding.severity)}</span><span className={`report-severity-pill report-severity-pill--${finding.severity}`}>{severityLabel(finding.severity)}</span><span className="report-finding__category">{findingCategory(finding)}</span></div>
      <h3>{safeEvidence(displayTitle)}</h3>
      <span className="report-finding__location">{safeEvidence(finding.file)}{finding.line_number === null ? "" : ` · Line ${finding.line_number}`}</span>
      <span className={`report-finding__status${resolutionStatus.startsWith("Fixed") ? " report-finding__status--fixed" : resolutionStatus.startsWith("New") ? " report-finding__status--new" : " report-finding__status--unresolved"}`}>{resolutionStatus}</span>
    </header>
    <div className="report-finding__body">
      <p className="report-finding__description">{textOf(finding.description) ? safeEvidence(finding.description) : "The analyzer did not provide an explanation."}</p>
      <p className="report-finding__recommendation"><strong>Suggested fix</strong><span>{textOf(finding.recommendation) ? safeEvidence(finding.recommendation) : "No fix suggestion was returned by the analyzer."}</span></p>
      <details className="report-finding__details">
        <summary>Evidence and workflow details</summary>
        <dl className="report-finding__meta"><Meta label="Category" value={findingCategory(finding)} /><Meta label="Rule / code" value={code} /><Meta label="File" value={safeEvidence(finding.file)} /><Meta label="Line" value={finding.line_number === null ? undefined : String(finding.line_number)} /><Meta label="Repair status" value={repairStatus} /><Meta label="Issue resolution" value={resolutionStatus} /><Meta label="Finding status" value={findingStatus} /><Meta label="Overall validation" value={validationStatus} /><Meta label="Detected by" value={safeEvidence(finding.tool ?? finding.detected_by_tools?.join(", ") ?? "Not available")} /></dl>
        {finding.evidence && <p><strong>Evidence:</strong> {safeEvidence(finding.evidence)}</p>}
        {source ? <><SourceExcerpt source={source} line={finding.line_number} /><button className="report-text-button report-view-file" type="button" onClick={() => onViewFile(finding.file)}>View File</button></> : <p className="report-unavailable">Source code was not available for this file. Location: {safeEvidence(finding.file)}{finding.line_number === null ? "" : ` · line ${finding.line_number}`}</p>}
        {repairedSource && <details className="report-source-full"><summary>Show repaired code</summary><SourceExcerpt source={repairedSource} line={finding.line_number} /></details>}
      </details>
    </div>
  </article>;
}

function FileDetail({ path, source, findings, onViewFile }: Readonly<{ path: string; source?: ProjectFile; findings: Finding[]; onViewFile: (path: string) => void }>) {
  return <section className="report-file-detail"><div className="report-section-heading"><div><p className="report-eyebrow">SELECTED FILE</p><h3>{safeEvidence(path)}</h3></div><button className="report-text-button report-view-file" type="button" onClick={() => onViewFile(path)}>View File</button></div>
    {findings.length > 0 ? <p className="report-availability">{findings.length} finding{findings.length === 1 ? "" : "s"} for this file are listed once in <a href="#report-findings-heading">What needs attention</a>.</p> : <p className="report-empty">No findings were returned for this file.</p>}
    {source ? <details className="report-source-full"><summary>Show full source file</summary><SourceExcerpt source={source} /></details> : <p className="report-unavailable">Source content is not available in this browser session.</p>}
  </section>;
}

function SourceExcerpt({ source, line }: Readonly<{ source: ProjectFile; line?: number | null }>) {
  const lines = source.content.split(/\r?\n/);
  const start = line === undefined || line === null ? 0 : Math.max(0, line - 3);
  const end = line === undefined || line === null ? lines.length : Math.min(lines.length, line + 2);
  return <div className="report-code-block"><p>{safeEvidence(source.path)}{line ? ` · around line ${line}` : ""}</p><pre>{lines.slice(start, end).map((content, index) => { const lineNumber = start + index + 1; return <span className={lineNumber === line ? "report-code-line report-code-line--highlight" : "report-code-line"} key={lineNumber}><b>{lineNumber}</b><code>{content ? safeEvidence(content) : " "}</code></span>; })}</pre></div>;
}

function ComparisonColumn({ title, qualityScore, findingCount, severity }: Readonly<{ title: string; qualityScore?: number; findingCount?: number; severity: Record<string, unknown> }>) {
  return <section className="report-comparison-column"><h3>{title}</h3><Metric label="Quality score" value={qualityScore} /><Metric label="Findings" value={findingCount} />{SEVERITIES.map((level) => <p key={level}><span>{severityLabel(level)}</span><strong>{numberOf(severity[level]) ?? "Not available"}</strong></p>)}</section>;
}

function nextStep(status: string, reasons: string[], recommendations: string[], findings: Finding[]): string {
  if (status === "not_run") return "Review the available analysis results; final validation was not run.";
  if (status === "failed" && reasons.length > 0) return "Resolve the reported workflow or validation issue, then run validation again.";
  if (findings.length > 0) return "Review the remaining findings and apply appropriate fixes. Run the analysis and validation workflow again to verify the changes.";
  if (recommendations.length > 0) return safeEvidence(recommendations[0]);
  if (status === "passed") return "Review the returned evidence and proceed with your normal code review.";
  return "Wait for a final workflow result, or check the reported service availability.";
}

function statusDisplay(status: string): string {
  const normalized = status.toLowerCase().replace(/[_-]/g, " ");
  return normalized.replace(/\b\w/g, (letter) => letter.toUpperCase());
}

function severityRank(severity: Severity): number {
  return SEVERITIES.indexOf(severity);
}

function severityLabel(severity: Severity): string {
  return severity[0].toUpperCase() + severity.slice(1);
}