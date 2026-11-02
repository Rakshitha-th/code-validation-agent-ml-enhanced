export type StepStatus = "done" | "active" | "pending";

export interface ProgressStep {
  label: string;
  status: StepStatus;
}
interface AnalysisProgressProps {
  steps: ProgressStep[];
}

const ICON: Record<StepStatus, string> = {
  done: "\u2713",
  active: "\u25CF",
  pending: "\u25CB",
};

export function AnalysisProgress({ steps }: Readonly<AnalysisProgressProps>) {
  return (
    <ul className="analysis-progress">
      {steps.map((step) => (
        <li key={step.label} className={`analysis-progress__step analysis-progress__step--${step.status}`}>
          <span className="analysis-progress__icon">{ICON[step.status]}</span>
          <span>{step.label}</span>
        </li>
      ))}
    </ul>
  );
}
