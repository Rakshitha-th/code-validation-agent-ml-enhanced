import type { Finding, ProjectFile, WorkflowRunResponse } from "../types/finding";

export interface StructureSymbol {
  readonly name: string;
  readonly kind: "class" | "function";
  readonly line: number;
}

export interface ImportReference {
  readonly name: string;
  readonly line: number;
  readonly kind: "stdlib" | "third-party" | "local";
}

export interface FileStructure {
  readonly path: string;
  readonly loc: number;
  readonly symbols: StructureSymbol[];
  readonly imports: ImportReference[];
}

const DECLARATION_PATTERN = /^\s*(?:async\s+)?(def|class)\s+([A-Za-z_]\w*)/;
const IMPORT_PATTERN = /^\s*(?:from\s+([\w.]+)\s+import|import\s+([\w.]+))/;

function replaceEvery(value: string, search: string, replacement: string): string {
  return [...value].map((character) => character === search ? replacement : character).join("");
}

export function localModuleFilePath(module: string): string {
  return `${replaceEvery(module.replace(/^\.+/, ""), ".", "/")}.py`;
}

export function parseStructure(files: ProjectFile[]): FileStructure[] {
  const paths = new Set(files.map((file) => replaceEvery(file.path, "\\", "/")));
  const standardLibrary = new Set("abc argparse asyncio collections csv dataclasses datetime decimal enum functools hashlib heapq io itertools json logging math pathlib re secrets shutil socket sqlite3 statistics string subprocess sys tempfile threading time typing unittest urllib uuid xml zipfile os os.path pathlib platform random subprocess textwrap warnings".split(" "));
  return files.map((file) => {
    const lines = file.content.split(/\r?\n/);
    const symbols: StructureSymbol[] = [];
    const imports: ImportReference[] = [];
    lines.forEach((line, index) => {
      const declaration = DECLARATION_PATTERN.exec(line);
      if (declaration) symbols.push({ name: declaration[2], kind: declaration[1] === "class" ? "class" : "function", line: index + 1 });
      const imported = IMPORT_PATTERN.exec(line);
      const module = imported?.[1] ?? imported?.[2];
      if (module) {
        const localModule = localModuleFilePath(module).replace(/\.py$/, "");
        const localPath = `${localModule}.py`;
        const isLocal = module.startsWith(".") || paths.has(localPath) || [...paths].some((path) => path.endsWith(`/${localPath}`) || path === `${localModule}/__init__.py`);
        const root = module.split(".")[0];
        let kind: ImportReference["kind"] = "third-party";
        if (isLocal) kind = "local";
        else if (standardLibrary.has(root)) kind = "stdlib";
        imports.push({ name: module, line: index + 1, kind });
      }
    });
    return { path: file.path, loc: lines.filter((line) => line.trim()).length, symbols, imports };
  });
}

export function recordOf(value: unknown): Record<string, unknown> {
  return value !== null && typeof value === "object" ? value as Record<string, unknown> : {};
}

export function textOf(value: unknown): string | undefined {
  return typeof value === "string" && value.trim() ? value : undefined;
}

export function numberOf(value: unknown): number | undefined {
  return typeof value === "number" && Number.isFinite(value) ? value : undefined;
}

export function runtimeNumber(report: WorkflowRunResponse["final_report"] | undefined, keys: string[]): number | undefined {
  const sandbox = recordOf(report?.sandbox_result);
  const metrics = recordOf(sandbox.metrics);
  const validation = recordOf(report?.validation_results);
  for (const key of keys) {
    const value = numberOf(metrics[key]) ?? numberOf(sandbox[key]) ?? numberOf(validation[key]);
    if (value !== undefined) return value;
  }
  return undefined;
}

export function classifyFinding(finding: Finding): "Logical bugs" | "Runtime risks" | "Resource-management" | "Performance" {
  const category = `${finding.category} ${finding.subcategory ?? ""} ${finding.issue}`.toLowerCase();
  if (/resource|memory|leak|unclosed|file handle/.test(category)) return "Resource-management";
  if (/performance|slow|inefficient|complexity/.test(category)) return "Performance";
  if (/runtime|exception|crash|unsafe/.test(category)) return "Runtime risks";
  return "Logical bugs";
}

export function repairAttempts(result: WorkflowRunResponse): Array<Record<string, unknown>> {
  const report = recordOf(result.final_report);
  const attempts = report.repair_attempts ?? recordOf(report.workflow_summary).repair_attempts;
  return Array.isArray(attempts) ? attempts.map(recordOf) : [];
}

export function safeEvidence(value: string | undefined): string {
  if (!value) return "Evidence not provided.";
  const redacted = value
    .replace(/(api[_-]?key|token|password|secret)\s*[:=]\s*[^\s,;]+/gi, "$1=[REDACTED]")
    .replace(/\b(?:sk-[A-Za-z0-9_-]{12,}|gh[pousr]_[A-Za-z0-9]{20,}|AKIA[A-Z0-9]{16}|ASIA[A-Z0-9]{16})\b/g, "[REDACTED]")
    .replace(/\bBearer\s+[^\s,;]+/gi, "Bearer [REDACTED]")
    .replace(/\beyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{8,}\b/g, "[JWT REDACTED]")
    .replace(/-----BEGIN [A-Z ]*PRIVATE KEY-----[\s\S]*?-----END [A-Z ]*PRIVATE KEY-----/g, "[PRIVATE KEY REDACTED]")
    .replace(/(?:[A-Za-z]:\\(?:[^\\\s]+\\)*[^\\\s]*|\/(?:[^/\s]+\/)+[^/\s]*)/g, "[PATH REDACTED]");
  return redacted.split(/\r?\n/).map((line) => line.includes('File "') && /, line \d+/.test(line) ? "[STACK FRAME REDACTED]" : line).join("\n");
}

export function isComplexityFinding(finding: Finding): boolean {
  return finding.category === "complexity" || /complexity|radon/i.test(`${finding.issue} ${finding.subcategory ?? ""}`);
}

export function isStyleFinding(finding: Finding): boolean {
  return finding.category === "style" || finding.category === "convention" || /style|convention/i.test(`${finding.issue} ${finding.subcategory ?? ""}`);
}

export function isErrorFinding(finding: Finding): boolean {
  return finding.severity === "critical" || finding.severity === "high";
}

export function isWarningFinding(finding: Finding): boolean {
  return finding.severity === "medium";
}