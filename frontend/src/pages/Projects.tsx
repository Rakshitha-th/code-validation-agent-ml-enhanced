import { useEffect, useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { deleteProject, getProject, getProjects, updateProject } from "../api/projects";
import type { ProjectDetail, ProjectSummary } from "../api/types";
import { usePermission } from "../auth/permissions";
import { ProjectFilesManager } from "../components/ProjectFilesManager";
import { Identifier } from "../analysis/TraceabilityTab";

export function ProjectsPage() {
  const canCreate = usePermission("createAnalysis");
  const [items, setItems] = useState<ProjectSummary[]>([]);
  const [search, setSearch] = useState("");
  const [sort, setSort] = useState("created_at");
  const [page, setPage] = useState(1);
  const [total, setTotal] = useState(0);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    const controller = new AbortController();
    setLoading(true);
    setError("");
    void getProjects({ page, pageSize: 10, search, sort }, controller.signal)
      .then((response) => { setItems(response.items); setTotal(response.total); })
      .catch((reason: unknown) => { if (!controller.signal.aborted) setError(reason instanceof Error ? reason.message : "Projects could not be loaded."); })
      .finally(() => { if (!controller.signal.aborted) setLoading(false); });
    return () => controller.abort();
  }, [page, search, sort]);

  return (
    <section className="page projects-page">
      <div className="section-label"><h1>Projects</h1>{canCreate && <Link className="btn btn--primary" to="/projects/new">New analysis</Link>}</div>
      <div className="projects-controls">
        <label>Search projects<input type="search" value={search} onChange={(event) => { setPage(1); setSearch(event.target.value); }} /></label>
        <label>Sort by<select value={sort} onChange={(event) => setSort(event.target.value)}><option value="created_at">Recently created</option><option value="name">Name</option></select></label>
      </div>
      {loading && <p aria-live="polite">Loading projects...</p>}
      {error && <p className="inline-error" role="alert">{error}</p>}
      {!loading && !error && items.length === 0 && <div className="empty-state"><strong>No projects found.</strong><span>Create a project from a Python upload to start an analysis.</span></div>}
      {items.length > 0 && <div className="table-scroll"><table className="findings-table"><thead><tr><th>Name</th><th>Owner</th><th>Status</th><th>Files</th><th>Last run</th><th>Created</th></tr></thead><tbody>{items.map((project) => <tr key={project.project_id}><td><Link to={`/projects/${encodeURIComponent(project.project_id)}`}>{project.name}</Link></td><td>{project.owner ?? "-"}</td><td>{project.status ?? "Unknown"}</td><td>{project.file_count}</td><td>{formatDate(project.last_run)}</td><td>{formatDate(project.created_at)}</td></tr>)}</tbody></table></div>}
      <div className="projects-pagination"><span>{total} projects</span><button className="btn" disabled={page <= 1} onClick={() => setPage((value) => value - 1)}>Previous</button><span>Page {page}</span><button className="btn" disabled={page * 10 >= total} onClick={() => setPage((value) => value + 1)}>Next</button></div>
    </section>
  );
}

export function ProjectDetailPage() {
  const canEdit = usePermission("editProject");
  const canDelete = usePermission("deleteProject");
  const canCreate = usePermission("createAnalysis");
  const { id = "" } = useParams();
  const navigate = useNavigate();
  const [project, setProject] = useState<ProjectDetail | null>(null);
  const [name, setName] = useState("");
  const [description, setDescription] = useState("");
  const [error, setError] = useState("");
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    const controller = new AbortController();
    void getProject(id, controller.signal).then((value) => { setProject(value); setName(value.name); setDescription(value.description ?? ""); }).catch((reason: unknown) => { if (!controller.signal.aborted) setError(reason instanceof Error ? reason.message : "Project could not be loaded."); });
    return () => controller.abort();
  }, [id]);

  async function saveMetadata() {
    setSaving(true);
    setError("");
    try { setProject(await updateProject(id, { name: name.trim(), description })); }
    catch (reason) { setError(reason instanceof Error ? reason.message : "Project changes could not be saved."); }
    finally { setSaving(false); }
  }

  async function removeProject() {
    if (!window.confirm("Delete this project and its saved files? This cannot be undone.")) return;
    try { await deleteProject(id); navigate("/projects"); }
    catch (reason) { setError(reason instanceof Error ? reason.message : "Project could not be deleted."); }
  }

  if (!project) return <section className="page"><Link to="/projects">Back to projects</Link>{error ? <p role="alert">{error}</p> : <p>Loading project...</p>}</section>;
  return <section className="page project-detail">
    <div className="section-label"><div><Link to="/projects">Projects</Link><h1>{project.name}</h1></div><div>{canCreate && <Link className="btn btn--primary" to={`/projects/new?project=${encodeURIComponent(id)}`}>New analysis</Link>}{canDelete && <button className="btn btn--danger" onClick={() => void removeProject()}>Delete</button>}</div></div>
    {error && <p className="inline-error" role="alert">{error}</p>}
    <fieldset className="project-metadata" disabled={!canEdit}><legend>Project details</legend><label>Name<input value={name} onChange={(event) => setName(event.target.value)} maxLength={120} /></label><label>Description<textarea value={description} onChange={(event) => setDescription(event.target.value)} rows={3} /></label>{canEdit && <button className="btn" disabled={saving || !name.trim()} onClick={() => void saveMetadata()}>{saving ? "Saving..." : "Save changes"}</button>}</fieldset>
    <section><div className="section-label"><h2>Files</h2><span>{project.files.length} files</span></div>{project.files.length ? <ul className="project-file-list">{project.files.map((file) => <li key={file.path}><code>{file.path}</code><span>{formatSize(file.size)}</span><span>{formatDate(file.last_modified)}</span></li>)}</ul> : <p>No persisted files are available for this project.</p>}</section>
    <ProjectFilesManager projectId={id} />
    <section><div className="section-label"><h2>Run history</h2><span>{project.run_history.length} runs</span></div>{project.run_history.length ? <ul className="project-runs">{project.run_history.map((run) => <li key={run.run_id ?? `${run.project_id}-${run.started_at}`}><Link to={`/reports/${encodeURIComponent(run.run_id ?? run.project_id)}`}>{run.run_id ?? run.project_id}</Link>{run.run_id && <Identifier label="Run ID" value={run.run_id} />}<span>{run.status}</span><span>{formatDate(run.started_at)}</span></li>)}</ul> : <p>No runs are recorded for this project.</p>}</section>
  </section>;
}

function formatDate(value: string | null | undefined): string { return value ? new Date(value).toLocaleString() : "-"; }
function formatSize(size: number): string { return size < 1024 ? `${size} B` : `${(size / 1024).toFixed(1)} KB`; }