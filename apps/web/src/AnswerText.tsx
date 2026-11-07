import { Fragment } from "react";

// React escapes every text node. No model-generated HTML is executed.
function inline(text: string) {
  return text.split(/(\*\*[^*]+\*\*|`[^`]+`)/g).map((part, i) =>
    part.startsWith("**") && part.endsWith("**") ? <strong key={i}>{part.slice(2, -2)}</strong>
      : part.startsWith("`") && part.endsWith("`") ? <code key={i}>{part.slice(1, -1)}</code>
        : <Fragment key={i}>{part}</Fragment>);
}

export function AnswerText({text}: {text: string}) {
  const blocks: {kind: string; lines: string[]}[] = [];
  for (const line of text.split("\n")) {
    const kind = /^#{1,6}\s/.test(line) ? "heading" : /^\s*[-*]\s/.test(line) ? "ul"
      : /^\s*\d+[.)、]\s*/.test(line) ? "ol" : "p";
    if (!line.trim()) { blocks.push({kind: "break", lines: []}); continue; }
    const previous = blocks[blocks.length - 1];
    if (previous?.kind === kind && kind !== "heading") previous.lines.push(line);
    else blocks.push({kind, lines: [line]});
  }
  return <div className="answer">{blocks.map((block, i) => {
    if (block.kind === "break") return null;
    if (block.kind === "heading") return <h4 key={i}>{inline(block.lines[0].replace(/^#{1,6}\s+/, ""))}</h4>;
    if (block.kind === "ul" || block.kind === "ol") {
      const List = block.kind;
      return <List key={i}>{block.lines.map((line, j) => <li key={j}>{inline(line.replace(/^\s*(?:[-*]|\d+[.)、])\s*/, ""))}</li>)}</List>;
    }
    return <p key={i}>{inline(block.lines.join("\n"))}</p>;
  })}</div>;
}
