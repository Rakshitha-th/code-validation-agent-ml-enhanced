import { apiClient, mocksEnabled } from "./client";
import type { Project, ProjectDetail, ProjectFile, ProjectsResponse } from "./types";
import { deleteMockProjectFiles, setMockProjectFiles } from "./files";

const mockProjects = new Map<string, ProjectDetail>();
window.addEventListener("coding-validation:clear-user-data", () => mockProjects.clear());

export interface CreateProjectRequest {
  readonly name: string;
  readonly description?: string;
  readonly files: ProjectFile[];
  readonly project_context?: Record<string, unknown>;
}

export async function createProject(request: CreateProjectRequest, signal?: AbortSignal): Promise<Project> {
  if (mocksEnabled()) {
    const project = makeMockProject(request.name, request.description, request.files);
    mockProjects.set(project.project_id, project);
    setMockProjectFiles(project.project_id, request.files);
    return project;
  }
  return apiClient.post<Project>("/api/projects", request, signal);
}

export async function uploadProject(name: string, files: File[], signal?: AbortSignal): Promise<Project> {
  const form = new FormData();
  form.set("name", name);
  files.forEach((file) => form.append("files", file));
  if (mocksEnabled()) {
    const projectFiles = await Promise.all(files.filter((file) => file.name.toLowerCase().endsWith(".py")).map(async (file) => ({ path: file.webkitRelativePath || file.name, content: await file.text() })));
    const project = makeMockProject(name, undefined, projectFiles);
    mockProjects.set(project.project_id, project);
    setMockProjectFiles(project.project_id, projectFiles);
    return project;
  }
  const result = await apiClient.postForm<Project>("/projects/upload", form, signal);
  return { ...result, files_persisted: false };
}

export async function getProjects(options: { readonly page?: number; readonly pageSize?: number; readonly search?: string; readonly sort?: string } = {}, signal?: AbortSignal): Promise<ProjectsResponse> {
  const page = options.page ?? 1;
  const pageSize = options.pageSize ?? 10;
  if (mocksEnabled()) {
    const items = [...mockProjects.values()].filter((project) => project.name.toLowerCase().includes((options.search ?? "").toLowerCase()));
    items.sort((left, right) => (options.sort === "name" ? left.name.localeCompare(right.name) : right.created_at.localeCompare(left.created_at)));
    const start = (page - 1) * pageSize;
    return { items: items.slice(start, start + pageSize), total: items.length, page, page_size: pageSize };
  }
  const query = new URLSearchParams({ page: String(page), page_size: String(pageSize), search: options.search ?? "", sort: options.sort ?? "created_at" });
  return apiClient.get<ProjectsResponse>(`/api/projects?${query}`, signal);
}

export async function getProject(projectId: string, signal?: AbortSignal): Promise<ProjectDetail> {
  if (mocksEnabled()) {
    const project = mockProjects.get(projectId);
    if (!project) throw new Error("Project was not found in mock data.");
    return project;
  }
  return apiClient.get<ProjectDetail>(`/api/projects/${encodeURIComponent(projectId)}`, signal);
}

export async function updateProject(projectId: string, changes: { readonly name?: string; readonly description?: string }, signal?: AbortSignal): Promise<ProjectDetail> {
  if (mocksEnabled()) {
    const current = await getProject(projectId);
    const updated = { ...current, ...changes };
    mockProjects.set(projectId, updated);
    return updated;
  }
  return apiClient.patch<ProjectDetail>(`/api/projects/${encodeURIComponent(projectId)}`, changes, signal);
}

export async function deleteProject(projectId: string, signal?: AbortSignal): Promise<void> {
  if (mocksEnabled()) {
    mockProjects.delete(projectId);
    deleteMockProjectFiles(projectId);
    return;
  }
  await apiClient.delete<void>(`/api/projects/${encodeURIComponent(projectId)}`, signal);
}

function makeMockProject(name: string, description: string | undefined, files: ProjectFile[]): ProjectDetail {
  const now = new Date().toISOString();
  return {
    project_id: `mock-proj-${crypto.randomUUID()}`,
    name,
    description,
    owner: "Mock developer",
    status: "Ready",
    file_count: files.length,
    created_at: now,
    last_run: null,
    files_persisted: true,
    files: files.map((file) => ({ path: file.path, size: new Blob([file.content]).size, last_modified: now })),
    run_history: [],
  };
}
