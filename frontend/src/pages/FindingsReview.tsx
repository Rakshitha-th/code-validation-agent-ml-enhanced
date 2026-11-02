import { FindingsTable } from "../components/FindingsTable";
import type { Finding } from "../types/finding";
import { RequirePermission } from "../components/RequirePermission";
import { ComplianceNotice } from "../components/ComplianceNotice";

interface FindingsReviewPageProps {
  findings: Finding[];
  onNewAnalysis: () => void;
}

export function FindingsReviewPage({ findings, onNewAnalysis }: Readonly<FindingsReviewPageProps>) {
  return <section className="page review-page"><div className="kicker">Findings review <span>02 / 04</span></div><h1>Evidence,<br /><em>in context.</em></h1><ComplianceNotice /><p className="review-page__intro">Search every finding returned by the validation pipeline, then open a row for its evidence and remediation guidance.</p><div className="review-page__count">{findings.length} findings in current workspace</div>{findings.length === 0 ? <div className="empty-state"><strong>No findings yet.</strong><span>Run an analysis first, then return here to review file, line, tool, and remediation evidence.</span><RequirePermission capability="createAnalysis"><button className="btn btn--primary" onClick={onNewAnalysis}>Start new analysis →</button></RequirePermission></div> : <FindingsTable findings={findings} />}</section>;
}