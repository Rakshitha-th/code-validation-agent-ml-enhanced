import { useMemo, useState } from "react";
import { CodeEditor } from "../components/CodeEditor";
import type { ProjectFile } from "../types/finding";
import { localModuleFilePath, parseStructure } from "./model";

interface StructureTabProps { readonly files: ProjectFile[]; }

export function StructureTab({ files }: Readonly<StructureTabProps>) {
  const structure = useMemo(() => parseStructure(files), [files]);
  const [active, setActive] = useState<{ readonly path: string; readonly line: number } | null>(null);
  const selectedFile = files.find((file) => file.path === active?.path);
  const graphNodes = structure.map((file, index) => ({ ...file, x: 40 + (index % 3) * 230, y: 34 + Math.floor(index / 3) * 105 }));
  const edges = structure.flatMap((file) => file.imports.filter((item) => item.kind === "local").map((item) => ({ from: file.path, to: localModuleFilePath(item.name) })));

  return <section className="analysis-view">
    <div className="section-label"><h2>Code structure</h2><span>{structure.length} modules</span></div>
    {structure.length === 0 ? <p className="empty-state">Source files are unavailable for this run.</p> : <>
      <div className="structure-files">{structure.map((file) => <article key={file.path}><h3>{file.path}</h3><p>{file.loc} LOC · {file.symbols.filter((symbol) => symbol.kind === "function").length} functions · {file.symbols.filter((symbol) => symbol.kind === "class").length} classes</p>
        <ul>{file.symbols.map((symbol) => <li key={`${symbol.kind}-${symbol.line}`}><button className="btn btn--link" onClick={() => setActive({ path: file.path, line: symbol.line })}>{symbol.kind} {symbol.name} · line {symbol.line}</button></li>)}</ul>
        <details><summary>Imports ({file.imports.length})</summary><ul>{file.imports.map((item) => <li key={`${item.name}-${item.line}`}>{item.name} · {item.kind} · line {item.line}</li>)}</ul></details>
      </article>)}</div>
      <section className="dependency-graph"><h3>Local dependency graph</h3>{graphNodes.length ? <svg viewBox={`0 0 ${Math.max(270, Math.ceil(graphNodes.length / 3) * 230)} ${Math.max(110, Math.ceil(graphNodes.length / 3) * 105)}`} role="img" aria-label="Local Python module dependency graph">
        {edges.map((edge, index) => { const source = graphNodes.find((node) => node.path === edge.from); const target = graphNodes.find((node) => node.path.endsWith(edge.to)); return source && target ? <path key={`${edge.from}-${edge.to}-${index}`} d={`M ${source.x + 160} ${source.y + 24} C ${source.x + 190} ${source.y + 55}, ${target.x - 30} ${target.y + 55}, ${target.x} ${target.y + 24}`} /> : null; })}
        {graphNodes.map((node) => <g key={node.path}><rect x={node.x} y={node.y} width="160" height="48" rx="4" /><text x={node.x + 8} y={node.y + 28}>{node.path.split("/").pop()}</text></g>)}
      </svg> : <p>No local imports found.</p>}</section>
      {selectedFile && <section className="structure-editor"><h3>{active?.path} · line {active?.line}</h3><CodeEditor path={selectedFile.path} value={selectedFile.content} readOnly highlightLine={active?.line} height="320px" /></section>}
    </>}
  </section>;
}