import type { Finding, QualitySummary } from "../types/finding";
import { safeEvidence } from "./model";

interface QualityTabProps { readonly summary?: QualitySummary; readonly findings: Finding[]; }

function complexityRank(complexity: number): string {
  if (complexity <= 5) return "A";
  if (complexity <= 10) return "B";
  if (complexity <= 20) return "C";
  if (complexity <= 30) return "D";
  if (complexity <= 40) return "E";
  return "F";
}

function hotspotRank(file: string | null, line: number | null, complexity: number, findings: Finding[]): string {
  const evidence = findings.find((finding) => finding.file === file && finding.line_number === line);
  const grade = /COMPLEXITY[_\s]([A-F])|grade\s+([A-F])/i.exec(`${evidence?.issue ?? ""} ${evidence?.description ?? ""}`);
  return grade?.[1] ?? grade?.[2] ?? complexityRank(complexity);
}

export function QualityTab({ summary, findings }: Readonly<QualityTabProps>) {
  const hotspots = summary?.hotspots ?? [];
  const actual = findings.flatMap((finding) => {
    const match = /cyclomatic complexity\s+(\d+)/i.exec(`${finding.issue} ${finding.description}`);
    return match ? [Number(match[1])] : [];
  });
  const average = actual.length ? (actual.reduce((total, value) => total + value, 0) / actual.length).toFixed(1) : "Not reported";
  const maximum = actual.length ? Math.max(...actual) : undefined;
  const fileEntries = Object.entries(summary?.per_file ?? {});
  const maxIssues = Math.max(1, ...fileEntries.map(([, value]) => value.issues));
  const styleCount = findings.filter((finding) => /style|convention/i.test(finding.subcategory ?? "") || finding.category === "code_quality" && /style|convention/i.test(finding.issue)).length;

  return <section className="analysis-view">
    <div className="section-label"><h2>Quality metrics</h2><span>{summary ? `${summary.files_analyzed} files analyzed` : "Quality summary unavailable"}</span></div>
    <div className="analysis-metric-grid">
      <Metric label="Maintainability index" value={summary?.maintainability?.maintainability_index ?? summary?.maintainability?.index ?? "Not reported"} />
      <Metric label="Average reported complexity" value={average} /><Metric label="Maximum reported complexity" value={maximum ?? "Not reported"} />
      <Metric label="Ruff issues" value={summary?.by_tool.ruff ?? "Not reported"} /><Metric label="Pylint issues" value={summary?.by_tool.pylint ?? "Not reported"} />
      <Metric label="Code-style findings" value={styleCount} />
    </div>
    <section className="quality-summary"><h3>Most complex functions</h3>{hotspots.length ? <div className="table-scroll"><table className="findings-table"><thead><tr><th>Function</th><th>File</th><th>Line</th><th>Complexity</th><th>Rank</th></tr></thead><tbody>{hotspots.map((item) => <tr key={`${item.file}-${item.line}-${item.function_name}`}><td>{safeEvidence(item.function_name)}</td><td>{safeEvidence(item.file ?? "-")}</td><td>{item.line ?? "-"}</td><td>{item.complexity}</td><td>{hotspotRank(item.file, item.line, item.complexity, findings)}</td></tr>)}</tbody></table></div> : <p>Radon function-level records were not returned.</p>}</section>
    <section className="quality-summary"><h3>Per-file quality</h3>{fileEntries.length ? fileEntries.map(([path, info]) => <div className="quality-file-bar" key={path}><span>{safeEvidence(path)}</span><meter min="0" max={maxIssues} value={info.issues} aria-label={`${safeEvidence(path)}: ${info.issues} issues`} /><small>{info.issues} issues · {safeEvidence(info.complexity)}</small></div>) : <p>Per-file quality metrics were not returned.</p>}</section>
  </section>;
}

interface MetricProps { readonly label: string; readonly value: unknown; }
function Metric({ label, value }: Readonly<MetricProps>) { return <div className="analysis-metric"><span>{label}</span><strong>{typeof value === "string" || typeof value === "number" ? value : "Not reported"}</strong></div>; }