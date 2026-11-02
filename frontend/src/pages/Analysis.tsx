import { AnalysisProgress, type ProgressStep, type StepStatus } from "../components/AnalysisProgress";
import { DEFAULT_STEPS } from "../components/analysisProgressSteps";

interface AnalysisPageProps {
  /** Index of the step currently in progress, or DEFAULT_STEPS.length once done. */
  activeStepIndex: number;
  progressMessage?: string;
  workflowStep: string;
  workflowStatus: string;
  retryCount: number;
}

function buildSteps(activeStepIndex: number): ProgressStep[] {
  return DEFAULT_STEPS.map((label, i) => {
    let status: StepStatus = "pending";
    if (i < activeStepIndex) status = "done";
    if (i === activeStepIndex) status = "active";
    return { label, status };
  });
}

export function AnalysisPage({ activeStepIndex, progressMessage, workflowStep, workflowStatus, retryCount }: Readonly<AnalysisPageProps>) {
  return (
    <section className="page page--analysis analysis-shell">
      <div className="kicker">Live workflow <span>02 / 04</span></div>
      <h1>Reading the shape<br /><em>of your code.</em></h1>
      <output className={`workflow-state workflow-state--${workflowStatus}`} aria-live="polite">Workflow: {workflowStatus} · Stage: {workflowStep || "queued"} · Repair retries: {retryCount}</output>
      {progressMessage && <p className="analysis-pulse"><i />{progressMessage}</p>}
      <AnalysisProgress steps={buildSteps(activeStepIndex)} />
      <p className="analysis-pulse"><i /> Findings and evidence are being assembled in the background</p>
    </section>
  );
}
