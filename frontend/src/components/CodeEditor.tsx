import { useEffect, useRef } from "react";
import Editor, { type OnMount } from "@monaco-editor/react";
import { projectLanguageForPath } from "../api/files";

interface CodeEditorProps {
  path: string;
  value: string;
  readOnly?: boolean;
  onChange?: (value: string) => void;
  height?: string;
  highlightLine?: number | null;
}

type MonacoEditor = Parameters<OnMount>[0];
type MonacoApi = Parameters<OnMount>[1];
type MonacoDecorationCollection = ReturnType<MonacoEditor["createDecorationsCollection"]>;

function editorLanguageForPath(path: string): string {
  const language = projectLanguageForPath(path);
  if (language === "c") return "c";
  if (language === "cpp") return "cpp";
  if (language === "csharp") return "csharp";
  return language ?? "plaintext";
}

function setLineHighlight(
  editor: MonacoEditor,
  monaco: MonacoApi,
  decorations: MonacoDecorationCollection,
  line: number | null | undefined,
): void {
  if (!line) {
    decorations.clear();
    return;
  }

  decorations.set([{
    range: new monaco.Range(line, 1, line, 1),
    options: { isWholeLine: true, className: "code-editor__highlight-line", glyphMarginClassName: "code-editor__highlight-glyph" },
  }]);
  editor.revealLineInCenter(line);
  editor.setPosition({ lineNumber: line, column: 1 });
}

export function CodeEditor({ path, value, readOnly = false, onChange, height = "360px", highlightLine }: Readonly<CodeEditorProps>) {
  const editorRef = useRef<MonacoEditor | null>(null);
  const monacoRef = useRef<MonacoApi | null>(null);
  const decorationsRef = useRef<MonacoDecorationCollection | null>(null);

  const handleMount: OnMount = (editor, monaco) => {
    editorRef.current = editor;
    monacoRef.current = monaco;
    decorationsRef.current = editor.createDecorationsCollection([]);
    setLineHighlight(editor, monaco, decorationsRef.current, highlightLine);
  };

  useEffect(() => {
    const editor = editorRef.current;
    const monaco = monacoRef.current;
    const decorations = decorationsRef.current;
    if (editor && monaco && decorations) setLineHighlight(editor, monaco, decorations, highlightLine);
    return () => decorationsRef.current?.clear();
  }, [highlightLine]);

  return (
    <div className="code-editor">
      <div className="code-editor__filename">{path}</div>
      <Editor
        height={height}
        language={editorLanguageForPath(path)}
        path={path}
        value={value}
        theme="vs-dark"
        options={{
          readOnly,
          minimap: { enabled: false },
          fontSize: 13,
          scrollBeyondLastLine: false,
        }}
        onChange={(next) => onChange?.(next ?? "")}
        onMount={handleMount}
      />
    </div>
  );
}
