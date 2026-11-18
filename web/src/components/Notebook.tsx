// The run's notebook as it stands: every query, computation and finding, in order, with its outputs. While the run
// works it is fetched again after every step; in the simple view code is folded away under each cell's purpose.

import { useEffect, useRef } from "react";
import type { NotebookCell } from "../api";
import { useApi } from "../session";
import { useSimple } from "../viewMode";
import { Chip, Code, Collapsible, ErrorNote } from "./bits";
import Icon from "./icons";

const MARKER = "ALC_RESULT ";

/** What a cell does, in plain words. */
const KIND: Record<string, string> = {
  query: "Data read",
  python: "Calculation",
  penguineer: "Statistics",
  finding: "Finding",
  check: "Check",
};

/** Outputs without the machine-readable result lines the kernel prints for the agent. */
function cleanText(t: string) {
  return t
    .split("\n")
    .filter((l) => !l.startsWith(MARKER))
    .join("\n")
    .replace(/\n+$/, "");
}

/** How a comment names the cell (and figure) it is about; the server looks the cell up so the agent sees what the
 * person saw. */
function cellRef(c: NotebookCell, figure?: number) {
  const kind = KIND[c.meta.kind ?? ""] ?? "Step";
  const purpose = c.meta.purpose || c.meta.finding_id || c.meta.query_id || "";
  const what = purpose ? `${kind}: ${purpose.length > 80 ? `${purpose.slice(0, 77)}...` : purpose}` : kind;
  return `About ${figure ? `figure ${figure} in ` : ""}cell ${c.meta.cell_id} (${what}): `;
}

function CommentButton({ onClick, label = "Comment" }: { onClick: () => void; label?: string }) {
  return (
    <button type="button" className="link small cell-comment" onClick={onClick} title="Start a note to the agent about this">
      <Icon name="note" size={14} /> {label}
    </button>
  );
}

/** A markdown cell as plain paragraphs and headings (no HTML is rendered). The simple view leaves out the replay
 * instructions. */
function Markdown({ source, simple }: { source: string; simple: boolean }) {
  const blocks = source
    .split(/\n\s*\n/)
    .map((b) => b.trim())
    .filter((b) => b && !(simple && /run_mode|re-execute/.test(b)));
  return (
    <div className="nbmd">
      {blocks.map((b, i) => {
        const h = /^(#{1,6})\s+(.*)$/s.exec(b);
        const text = (h ? h[2] : b).replace(/`([^`]*)`/g, "$1").replace(/\*([^*]+)\*/g, "$1");
        return h ? (
          <h3 key={i} className="nb-h">
            {text}
          </h3>
        ) : (
          <p key={i}>{text}</p>
        );
      })}
    </div>
  );
}

export default function NotebookView({
  runId,
  refresh,
  focus,
  live = false,
  onComment,
}: {
  runId: string;
  refresh?: unknown;
  focus?: string | null;
  live?: boolean;
  /** Offered on each cell and figure while someone can still steer this run or ask a follow-up: start a note
   * (or follow-up question) about it. */
  onComment?: (ref: string) => void;
}) {
  const simple = useSimple();
  const { data, error } = useApi<{ cells: NotebookCell[] }>(`/runs/${runId}/notebook`, [refresh]);
  const end = useRef<HTMLDivElement | null>(null);
  const count = data?.cells.length ?? 0;
  useEffect(() => {
    if (data && focus) document.getElementById(`cell-${focus}`)?.scrollIntoView({ behavior: "smooth", block: "center" });
  }, [data, focus]);

  const cells = (data?.cells ?? []).filter(
    (c) => !simple || c.type === "markdown" || !["parameters", "setup", "check"].includes(c.meta.kind ?? ""),
  );
  return (
    <div className={`nb ${simple ? "nb-simple" : ""}`}>
      <ErrorNote error={error} />
      {data && !cells.length && <p className="muted small pad">{live ? "The first cell appears here when it has run." : "The notebook is empty."}</p>}
      {cells.map((c, i) => {
        const anchor = c.meta.finding_id ?? c.meta.query_id ?? c.meta.cell_id ?? `md${i}`;
        const hl = focus && (focus === c.meta.finding_id || focus === c.meta.cell_id || focus === c.meta.query_id);
        if (c.type === "markdown") return <Markdown key={i} source={c.source} simple={simple} />;
        const outputs = c.outputs
          .map((o) => (o.type === "image" ? o : { ...o, text: cleanText(o.text ?? "") }))
          .filter((o) => o.type === "image" || o.text);
        const kind = c.meta.kind ?? "code";
        // Offered on cells that show something (a table, a figure, printed output), not on the set-up cells.
        const comment =
          onComment && c.meta.cell_id && outputs.length && !["parameters", "setup"].includes(kind)
            ? (figure?: number) => onComment(cellRef(c, figure))
            : undefined;
        const figures = outputs.filter((o) => o.type === "image").length;
        let fig = 0;
        return (
          <div key={i} id={`cell-${anchor}`} className={`nbcell ${hl ? "highlight" : ""}`}>
            {simple ? (
              <div className="nbmeta">
                <Chip>{KIND[kind] ?? "Step"}</Chip> {c.meta.purpose || c.meta.finding_id || c.meta.query_id || ""}
                {comment && <CommentButton onClick={() => comment()} />}
              </div>
            ) : (
              <div className="nbmeta small">
                <code>{c.meta.cell_id}</code> <Chip>{kind}</Chip> {c.meta.query_id} {c.meta.finding_id} {c.meta.purpose}
                {comment && <CommentButton onClick={() => comment()} />}
              </div>
            )}
            {simple ? (
              <Collapsible title={<span className="small muted">Show code</span>}>
                <Code lang="python">{c.source}</Code>
              </Collapsible>
            ) : (
              <Code lang="python">{c.source}</Code>
            )}
            {outputs.map((o, j) =>
              o.type === "image" ? (
                ((n) => (
                  <figure key={j} className="nbfig">
                    <img src={`data:image/png;base64,${o.png}`} alt={`output of ${c.meta.cell_id}`} />
                    {comment && figures > 1 && (
                      <CommentButton onClick={() => comment(n)} label={`Comment on figure ${n}`} />
                    )}
                  </figure>
                ))(++fig)
              ) : simple && o.type === "error" ? (
                <div key={j} className="small warn-text">
                  This step did not work; the agent saw the error and carried on.
                </div>
              ) : simple && kind === "query" ? (
                <Collapsible key={j} title={<span className="small muted">Show the first rows</span>}>
                  <pre className="nbout">{o.text}</pre>
                </Collapsible>
              ) : (
                <pre key={j} className={`nbout ${o.type === "error" ? "err" : ""}`}>
                  {o.text}
                </pre>
              ),
            )}
          </div>
        );
      })}
      {live && (
        <div className="log-live" aria-live="polite" ref={end}>
          <span className="live-dot" aria-hidden="true" />
          {count ? `${count} ${count === 1 ? "cell" : "cells"} so far; new cells appear as the agent runs them.` : "Waiting for the first cell…"}
        </div>
      )}
    </div>
  );
}
