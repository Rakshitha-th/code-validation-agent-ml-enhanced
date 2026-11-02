import { useEffect, useRef, useState } from "react";
import JSZip from "jszip";
import { Link } from "react-router-dom";
import { applyRepairToProject } from "../api/repair";
import { usePermission } from "../auth/permissions";
import { CodeComparison } from "./CodeComparison";
import { repairComparisonState } from "../analysis/codeComparison";
import type { ProjectFile, WorkflowRunResponse } from "../types/finding";
import { recordOf, repairAttempts, textOf } from "../analysis/model";

interface RepairActionsProps {
  readonly originalFiles: ProjectFile[];
  readonly repairedFiles: ProjectFile[];
  readonly result: WorkflowRunResponse;
}

function triggerDownload(blob: Blob, filename: string): void {
  const url = URL.createObjectURL(blob);
  const anchor = document.createElement("a");
  anchor.href = url;
  anchor.download = filename;
  anchor.click();
  URL.revokeObjectURL(url);
}

export function RepairActions({ originalFiles, repairedFiles, result }: Readonly<RepairActionsProps>) {
  const canApply = usePermission("runRepair");
  const [previewOpen, setPreviewOpen] = useState(false);
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState("");
  const attempts = repairAttempts(result);
  const attempt = attempts.find((item) => item.status === "accepted");
  const attemptId = textOf(attempt?.attempt_id);
  const attemptValidation = recordOf(attempt?.validation);
  const validationStatus = attemptValidation.status;
  const validationPassed = result.validation_status === "passed" && (validationStatus === undefined || typeof validationStatus === "string" && validationStatus.toUpperCase() === "PASS");
  const acceptedAndValidated = Boolean(attemptId && attempt && validationPassed);
  const requiresReview = result.findings.some((finding) => finding.requires_human_review || finding.status === "Needs human review");
  const [selectedPath, setSelectedPath] = useState(repairedFiles[0]?.path ?? "");
  const previewDialog = useRef<HTMLDialogElement>(null);

  useEffect(() => {
    if (previewOpen && previewDialog.current && !previewDialog.current.open) previewDialog.current.showModal();
    if (!previewOpen && previewDialog.current?.open) previewDialog.current.close();
  }, [previewOpen]);

  async function apply() {
    if (!canApply || !attemptId || !acceptedAndValidated) return;
    setBusy(true); setMessage("");
    try {
      const response = await applyRepairToProject(result.project_id, attemptId, repairedFiles);
      setMessage(response.success ? "Validated repair applied." : "The backend did not confirm the repair was applied.");
      if (response.success) setPreviewOpen(false);
    } catch (error) { setMessage(error instanceof Error ? error.message : "Repair could not be applied."); }
    finally { setBusy(false); }
  }

  async function downloadZip() {
    const archive = new JSZip();
    repairedFiles.forEach((file) => archive.file(file.path, file.content));
    triggerDownload(await archive.generateAsync({ type: "blob" }), `${result.project_id}-repaired.zip`);
  }

  async function copyCode() {
    const file = repairedFiles.find((item) => item.path === selectedPath);
    if (!file) return;
    try { await navigator.clipboard.writeText(file.content); setMessage(`${file.path} copied.`); }
    catch { setMessage("Code could not be copied."); }
  }

  return <section className="repair-actions"><div className="section-label"><h2>Repair candidate</h2><span>{acceptedAndValidated ? "Accepted · PASS" : "Candidate not validated PASS"}</span></div>
    {!acceptedAndValidated && <p className="repair-badge">AI-generated – unvalidated</p>}
    {requiresReview && <p className="review-badge">Requires human review</p>}
    {repairedFiles.length === 0 ? <p>No repaired files were returned.</p> : <>
      <label>Repaired file<select value={selectedPath} onChange={(event) => setSelectedPath(event.target.value)}>{repairedFiles.map((file) => <option key={file.path}>{file.path}</option>)}</select></label>
      <div className="repair-actions__buttons"><button className="btn btn--primary" disabled={!canApply || !acceptedAndValidated || busy} onClick={() => setPreviewOpen(true)}>Preview and apply</button><button className="btn" onClick={() => { const file = repairedFiles.find((item) => item.path === selectedPath); if (file) triggerDownload(new Blob([file.content], { type: "text/x-python" }), file.path.split("/").pop() ?? "repaired.py"); }}>Download repaired file</button><button className="btn" onClick={() => void downloadZip()}>Download repaired project (.zip)</button><button className="btn" onClick={() => void copyCode()}>Copy code</button></div>
      {!canApply && <p>Read-only role: applying repairs is disabled.</p>}
    </>}
    {message && <output aria-live="polite">{message}</output>}
    <p>Apply is enabled only when an accepted repair has PASS validation evidence.</p>
    {previewOpen && <dialog ref={previewDialog} className="repair-preview" aria-labelledby="repair-preview-title" onCancel={(event) => { event.preventDefault(); setPreviewOpen(false); }}><h2 id="repair-preview-title">Review repair diff</h2><CodeComparison original={originalFiles} repaired={repairedFiles} {...repairComparisonState(result)} /><div className="repair-actions__buttons"><button className="btn btn--primary" disabled={busy || !canApply || !acceptedAndValidated} onClick={() => void apply()}>{busy ? "Applying..." : "Confirm apply"}</button><button className="btn" onClick={() => setPreviewOpen(false)}>Cancel</button><Link className="btn btn--link" to={`/analysis/${encodeURIComponent(result.project_id)}/validation`}>Validation evidence</Link></div></dialog>}
  </section>;
}