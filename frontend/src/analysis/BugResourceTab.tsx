import type { Finding, WorkflowRunResponse } from "../types/finding";
import { classifyFinding, runtimeNumber, safeEvidence } from "./model";

interface BugResourceTabProps { readonly result: WorkflowRunResponse | null; }
const GROUPS = ["Logical bugs", "Runtime risks", "Resource-management", "Performance"] as const;

export function BugResourceTab({ result }: Readonly<BugResourceTabProps>) {
  const findings = result?.findings ?? [];
  const metrics = [
    { label: "Peak memory", value: runtimeNumber(result?.final_report, ["peak_memory_mb", "memory_peak_mb"]), unit: "MB" },
    { label: "CPU", value: runtimeNumber(result?.final_report, ["cpu_percent", "cpu_usage_percent"]), unit: "%" },
    { label: "Duration", value: runtimeNumber(result?.final_report, ["duration_ms", "execution_duration_ms"]), unit: "ms" },
  ];
  return <section className="analysis-view">
    <div className="section-label"><h2>Bug &amp; resource evidence</h2><span>{result ? `${findings.length} findings` : "Run data unavailable"}</span></div>
    <section className="runtime-metrics"><h3>Runtime evidence</h3>{metrics.map((metric) => <div className="runtime-metric" key={metric.label}><span>{metric.label}</span>{metric.value === undefined ? <small>Not provided by this run</small> : <><strong>{metric.value} {metric.unit}</strong><meter min="0" max={Math.max(1, metric.value)} value={metric.value} aria-label={`${metric.label}: ${metric.value} ${metric.unit}`} /></>}</div>)}</section>
    {!findings.length && <p className="empty-state">No findings are available for grouping.</p>}
    {GROUPS.map((group) => {
      const grouped = findings.filter((finding) => classifyFinding(finding) === group);
      return <section className="bug-resource-group" key={group}><h3>{group} <span>{grouped.length}</span></h3>{grouped.length ? grouped.map((finding) => <FindingEvidence key={finding.finding_id} finding={finding} />) : <p>No findings in this group.</p>}</section>;
    })}
  </section>;
}

interface FindingEvidenceProps { readonly finding: Finding; }
function FindingEvidence({ finding }: Readonly<FindingEvidenceProps>) {
  return <article className="bug-resource-item"><strong>{safeEvidence(finding.issue)}</strong><span>{finding.file}:{finding.line_number ?? "-"} · {finding.severity}</span><p>Potential issue – based on available evidence</p><details><summary>Evidence and recommendation</summary><p>{safeEvidence(finding.description)}</p>{finding.evidence && <p>{safeEvidence(finding.evidence)}</p>}{finding.recommendation && <p>{safeEvidence(finding.recommendation)}</p>}</details></article>;
}