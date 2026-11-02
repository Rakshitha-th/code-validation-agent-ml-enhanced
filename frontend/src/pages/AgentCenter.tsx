import { useEffect, useState } from "react";
import { apiClient } from "../api/client";

interface AgentHealth {
  readonly status: string;
  readonly healthy: boolean;
}

interface AgentHealthResponse {
  readonly services: Record<string, AgentHealth>;
}

const AGENTS = [
  { number: "01", name: "Code Analysis Agent", service: "orchestrator", role: "Understands the project", output: "AST structure · dependencies · code context", color: "teal" },
  { number: "02", name: "Security Agent", service: "security", role: "Finds security risk", output: "SAST · secrets · OWASP/CWE mapping", color: "amber" },
  { number: "03", name: "Code Quality Agent", service: "code_quality", role: "Measures maintainability", output: "Ruff · Pylint · Radon findings", color: "teal" },
  { number: "04", name: "Bug & Resource Agent", service: "bug_resource", role: "Identifies runtime risk", output: "Logic · performance · resource evidence", color: "blue" },
  { number: "05", name: "Fix & Regeneration Agent", service: "repair", role: "Creates repair candidates", output: "Targeted patch · full regeneration", color: "blue" },
  { number: "06", name: "Validation Agent", service: "validation", role: "Verifies acceptance", output: "Tests · scans · pass/fail evidence", color: "amber" },
  { number: "07", name: "Report Agent", service: "report_recommendation", role: "Makes the result traceable", output: "Explanation · recommendations · report", color: "teal" },
];

function serviceLabel(service?: AgentHealth): string {
  if (!service) return "Checking";
  if (service.healthy) return "Connected";
  if (service.status === "configured") return "Provider unverified";
  return service.status === "not_configured" ? "Not configured" : "Unavailable";
}

export function AgentCenter() {
  const [health, setHealth] = useState<AgentHealthResponse>();
  const [error, setError] = useState("");

  useEffect(() => {
    const controller = new AbortController();
    void apiClient.get<AgentHealthResponse>("/api/system/agents", controller.signal)
      .then((response) => { setHealth(response); setError(""); })
      .catch((requestError: unknown) => {
        if (!controller.signal.aborted) setError(requestError instanceof Error ? requestError.message : "Agent health could not be loaded.");
      });
    return () => controller.abort();
  }, []);

  const services = health?.services ?? {};
  const connectedCount = Object.values(services).filter((service) => service.healthy).length;
  const totalCount = Object.keys(services).length;

  return (
    <section className="page agent-center-page">
      <div className="page-heading">
        <div><div className="kicker">Agent control room <span>Live health</span></div><h1>Know what’s<br /><em>connected.</em></h1><p>{error || (health ? "Reachability is based on live service health checks. Per-run results show which agents executed for an analysis." : "Checking connected services…")}</p></div>
        <div className="heading-stamp"><span>{health ? `${connectedCount}/${totalCount}` : "--"}</span><strong>Healthy<br />services</strong></div>
      </div>

      <section className="orchestrator-rail">
        <div className="orchestrator-rail__node"><span className="orchestrator-rail__pulse" /><div><strong>LangGraph Orchestrator</strong><small>State, routing, retry limits and final acceptance</small></div><b>{serviceLabel(services.orchestrator)}</b></div>
        <div className="orchestrator-rail__line" />
        <div className="orchestrator-rail__node orchestrator-rail__node--sandbox"><span className="orchestrator-rail__pulse" /><div><strong>Docker Sandbox</strong><small>Isolated code execution service</small></div><b>{serviceLabel(services.sandbox)}</b></div>
      </section>

      <div className="agent-center-grid">
        {AGENTS.map((agent) => <article className={`agent-detail-card agent-detail-card--${agent.color}`} key={agent.number}><div className="agent-detail-card__top"><span>{agent.number}</span><b>{serviceLabel(services[agent.service])}</b></div><h2>{agent.name}</h2><p>{agent.role}</p><div className="agent-detail-card__output"><small>Produces</small><strong>{agent.output}</strong></div></article>)}
      </div>

      <section className="workflow-contract">
        <div><span className="kicker">Workflow design</span><h2>The orchestrator owns<br />the workflow.</h2></div>
        <div className="workflow-contract__steps"><span>Understand</span><i>→</i><span>Analyze</span><i>→</i><span>Repair</span><i>→</i><span>Validate</span><i>→</i><span>Report</span></div>
      </section>
    </section>
  );
}
