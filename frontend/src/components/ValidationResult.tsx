import type { FinalReport, WorkflowRunResponse } from "../types/finding";

interface ValidationResultProps {
  report: FinalReport;
  sandboxConfiguration: WorkflowRunResponse["analysis_configuration"];
}

interface SandboxOutputProps {
  report: FinalReport;
}

function settingText(...values: unknown[]): string {
  const value = values.find((candidate) => typeof candidate === "string" || typeof candidate === "number" || typeof candidate === "boolean");
  return value === undefined ? "Not reported" : String(value);
}

export function SandboxOutput({ report }: Readonly<SandboxOutputProps>) {
  const sandbox = report.sandbox_result;
  if (sandbox?.executed !== true) return null;

  const stdout = typeof sandbox.stdout === "string" ? sandbox.stdout : "";
  const stderr = typeof sandbox.stderr === "string" ? sandbox.stderr : "";
  const exitCode = typeof sandbox.exit_code === "number" ? sandbox.exit_code : undefined;
  const testResults = Array.isArray(sandbox.results)
    ? sandbox.results.filter((item): item is Record<string, unknown> => item !== null && typeof item === "object" && !Array.isArray(item))
    : [];
  const comparisonStatus = typeof sandbox.comparison_status === "string" ? sandbox.comparison_status : undefined;

  return (
    <section className="sandbox-output" aria-label="Sandbox execution output">
      <h2>Program execution{exitCode !== undefined ? ` · exit code ${exitCode}` : ""}</h2>
      {testResults.length > 0
        ? <ol>{testResults.map((item, index) => <li key={`${String(item.test_case ?? index + 1)}-${index}`}>
          <h3>Test case {String(item.test_case ?? index + 1)}</h3>
          <p>Execution: {typeof item.status === "string" ? item.status : "not reported"} · exit code {typeof item.exit_code === "number" ? item.exit_code : "not reported"}</p>
          {typeof item.input === "string" && <><strong>Input</strong><pre><code>{item.input || "(empty)"}</code></pre></>}
          {typeof item.expected === "string" && <><strong>Expected output</strong><pre><code>{item.expected}</code></pre></>}
          {typeof item.actual === "string" && <><strong>Actual output</strong><pre><code>{item.actual || "(empty)"}</code></pre></>}
          <p>Output comparison: {typeof item.comparison_status === "string" ? item.comparison_status.replace(/_/g, " ") : "not reported"}</p>
          {typeof item.stderr === "string" && item.stderr && <pre><code>{item.stderr}</code></pre>}
        </li>)}</ol>
        : stdout
          ? <pre><code>{stdout}</code></pre>
          : <p className="sandbox-output__empty">The program produced no standard output.</p>}
      {comparisonStatus && <p>Expected-output comparison: {comparisonStatus.replace(/_/g, " ")}. Execution success does not by itself establish output correctness.</p>}
      {stderr && <div className="sandbox-output__stderr">
        <h3>Errors and diagnostics</h3>
        <pre><code>{stderr}</code></pre>
      </div>}
    </section>
  );
}

export function ValidationResult({ report, sandboxConfiguration }: Readonly<ValidationResultProps>) {
  const validationTone = report.validation_status === "passed"
    ? "pass"
    : report.validation_status === "failed" || report.validation_status === "error"
      ? "fail"
      : "review";
  const validationEvidence = report.validation_results;
  const validatorStatus = typeof validationEvidence.validator_status === "string"
    ? validationEvidence.validator_status.replace(/_/g, " ")
    : typeof validationEvidence.validator_passed === "boolean"
      ? validationEvidence.validator_passed ? "passed" : "failed"
      : "not reported";
  const sandboxStatus = typeof report.sandbox_result?.execution_status === "string"
    ? report.sandbox_result.execution_status.replace(/_/g, " ")
    : report.sandbox_result?.passed === true
      ? "completed successfully"
    : typeof report.sandbox_result?.status === "string"
      ? report.sandbox_result.status.replace(/_/g, " ")
      : report.sandbox_result?.executed === false ? "not executed" : "not reported";
  const outputComparison = typeof report.sandbox_result?.comparison_status === "string"
    ? report.sandbox_result.comparison_status.replace(/_/g, " ")
    : "not provided";
  const statusLabel = report.validation_status === "needs_review"
    ? "Validation completed — review findings"
    : report.validation_status === "error"
      ? "Validation could not complete"
      : report.validation_status === "blocked"
        ? "Validation blocked"
        : report.validation_status === "not_run"
          ? "Validation was not run"
          : report.validation_status === "pending"
            ? "Validation is still in progress"
            : report.validation_status === "inconclusive"
              ? "Validation result is inconclusive"
              : report.validation_status === "unknown" ? "Validation status not available"
                : report.validation_status === "passed" ? "Validation passed" : "Validation failed";
  const validationError = typeof validationEvidence.error === "string"
    ? validationEvidence.error
    : typeof report.sandbox_result?.error === "string" ? report.sandbox_result.error : undefined;
  const workflowStatus = report.workflow_status
    ?? report.workflow_summary?.workflow_status
    ?? "Not available";
  const repairStatus = report.repair_status
    ?? (typeof report.workflow_summary?.repair_status === "string" ? report.workflow_summary.repair_status : "Not available");
  const attempts = report.repair_attempts ?? report.workflow_summary?.repair_attempts;
  const finalAttempt = attempts?.[attempts.length - 1];
  const finalAttemptSourceChanged = Array.isArray(finalAttempt?.changed_files)
    ? finalAttempt.changed_files.length > 0 ? "Yes" : "No"
    : "Not reported";
  const sandboxEvidence = validationEvidence.sandbox_settings ?? report.sandbox_result?.settings ?? report.sandbox_result?.configuration;
  const comparison = report.comparison;
  const remainingFindings = comparison?.remaining_findings;
  const finalFindingCount = comparison?.status === "complete"
    ? comparison.findings_after ?? remainingFindings?.length
    : comparison?.status === "partial"
      ? remainingFindings?.length
      : report.findings_summary.total;
  const remainingSeverityCounts = { critical: 0, high: 0, medium: 0, low: 0, info: 0 };
  remainingFindings?.forEach((finding) => {
    if (finding.severity in remainingSeverityCounts) {
      remainingSeverityCounts[finding.severity] += 1;
    }
  });
  const severitySummary = comparison?.status === "complete" && comparison.findings_by_severity_after
    ? comparison.findings_by_severity_after
    : remainingFindings !== undefined
      ? remainingSeverityCounts
      : comparison?.status === "partial" || comparison?.status === "complete"
        ? { critical: "Not reported", high: "Not reported", medium: "Not reported", low: "Not reported", info: "Not reported" }
        : report.findings_summary.by_severity;
  const effectiveSettings = sandboxEvidence !== null && typeof sandboxEvidence === "object" ? sandboxEvidence as Record<string, unknown> : undefined;
  const displayedSettings: Record<string, unknown> = effectiveSettings ?? sandboxConfiguration ?? {};
  const hasSettings = Object.keys(displayedSettings).length > 0;

  return (
    <div className={`validation-result validation-result--${validationTone}`}>
      <h3>{statusLabel}</h3>
      {validationError && <p role="alert">{validationError}</p>}
      <p>Overall workflow result: {workflowStatus.replace(/_/g, " ")}</p>
      <ul className="validation-result__breakdown">
        <li>Static validator: {validatorStatus}</li>
        <li>Program execution: {sandboxStatus}</li>
        <li>Expected-output comparison: {outputComparison}</li>
        <li>Quality gate: {comparison?.status === "complete"
          ? report.reanalysis_summary?.quality_gate_status ?? report.reanalysis_summary?.quality_gate?.status ?? "not reported"
          : report.quality_summary?.quality_gate_status ?? report.quality_summary?.quality_gate?.status ?? "not reported"}</li>
        <li>Repair status: {repairStatus.replace(/_/g, " ")}</li>
        <li>Repair attempts: {attempts?.length ?? "Not reported"}</li>
        {finalAttempt && <li>Source changed in final attempt: {finalAttemptSourceChanged}</li>}
      </ul>
      <p>
        Retries used: {report.retry_count ?? "Not available"} / {report.max_retries ?? "Not available"}
      </p>
      <p>Remaining findings: {finalFindingCount ?? "Not reported"}</p>
      <ul className="validation-result__breakdown">
        {Object.entries(severitySummary).map(([severity, count]) => (
          <li key={severity}>
            {severity}: {count}
          </li>
        ))}
      </ul>
      <details className="sandbox-settings-evidence">
        <summary>{effectiveSettings ? "Effective sandbox settings" : "Sandbox options sent for this run"}</summary>
        {hasSettings ? <dl>
          <dt>Timeout</dt><dd>{settingText(displayedSettings.sandbox_timeout_seconds, displayedSettings.timeout_seconds)} seconds</dd>
          <dt>Memory</dt><dd>{settingText(displayedSettings.memory_limit_mb, displayedSettings.memory_limit)} MB</dd>
          <dt>CPU</dt><dd>{settingText(displayedSettings.cpu_limit)}</dd>
          <dt>Network</dt><dd>{settingText(displayedSettings.network_policy)}</dd>
        </dl> : <p>Sandbox settings were not returned by the workflow.</p>}
        {!effectiveSettings && sandboxConfiguration && <p>The gateway has not confirmed that these requested options were applied.</p>}
      </details>
    </div>
  );
}
