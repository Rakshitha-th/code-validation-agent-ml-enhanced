import { useState } from "react";
import { Link } from "react-router-dom";
import type { WorkflowRunResponse } from "../types/finding";
import { recordOf, repairAttempts, safeEvidence, textOf } from "./model";

interface TraceabilityTabProps { readonly result: WorkflowRunResponse | null; }

export function TraceabilityTab({ result }: Readonly<TraceabilityTabProps>) {
  if (!result) return <section className="analysis-view"><p className="empty-state">Traceability appears after the workflow returns evidence.</p></section>;
  const attempts = repairAttempts(result);
  const report = recordOf(result.final_report);
  const validation = recordOf(report.validation_results);
  const runId = textOf(result.run_id);
  const validationId = textOf(validation.validation_id) ?? textOf(validation.id);
  const reportPath = `/reports/${encodeURIComponent(result.project_id)}`;
  return <section className="analysis-view traceability-view">
    <div className="section-label"><h2>Evidence trace</h2><span>Finding → repair → validation → report</span></div>
    <div className="trace-identifiers"><Identifier label="Run ID" value={runId} /><Identifier label="Validation ID" value={validationId} /><Link className="btn btn--link" to={reportPath}>Open report entry</Link></div>
    {result.findings.length === 0 && <p>No findings were returned for this run.</p>}
    {result.findings.map((finding) => <article className="trace-finding" key={finding.finding_id} id={`trace-${finding.finding_id}`}>
      <h3>{safeEvidence(finding.issue)}</h3>
      <ol><li><span>Finding</span><strong>{finding.finding_id}</strong></li>
        <li><span>Repair attempt(s)</span>{attempts.length ? <ul>{attempts.map((attempt, index) => { const attemptId = textOf(attempt.attempt_id); const rawIteration = attempt.iteration; const iteration = typeof rawIteration === "number" || typeof rawIteration === "string" ? rawIteration : index + 1; return <li key={attemptId ?? `iteration-${iteration}`}><span>Iteration {iteration}</span>{attemptId ? <><Link to={`/analysis/${encodeURIComponent(result.project_id)}/repair`}>{attemptId}</Link><Identifier label="Repair Attempt ID" value={attemptId} /></> : <span>ID not provided</span>}</li>; })}</ul> : <span>No repair attempts returned</span>}</li>
        <li><span>Validation result</span><Link to={`/analysis/${encodeURIComponent(result.project_id)}/validation`}>{result.validation_status}</Link>{validationId && <Identifier label="Validation ID" value={validationId} />}</li>
        <li><span>Report entry</span><Link to={`${reportPath}#trace-${encodeURIComponent(finding.finding_id)}`}>Finding report</Link></li>
      </ol>
    </article>)}
  </section>;
}

interface IdentifierProps { readonly label: string; readonly value?: string; }
export function Identifier({ label, value }: Readonly<IdentifierProps>) {
  const [message, setMessage] = useState("");
  if (!value) return <span className="trace-id trace-id--missing">{label}: not provided</span>;
  const identifier = value;
  async function copy() {
    try { await navigator.clipboard.writeText(identifier); setMessage(`${label} copied`); }
    catch { setMessage(`Could not copy ${label.toLowerCase()}.`); }
  }
  return <span className="trace-id"><span>{label}: <code>{value}</code></span><button className="btn" aria-label={`Copy ${label}`} onClick={() => void copy()}>Copy</button>{message && <output aria-live="polite">{message}</output>}</span>;
}