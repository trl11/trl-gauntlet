import { createElement, Fragment } from "react";

import "./Markdown.scss";

/** Props for {@link Markdown}. */
export interface MarkdownProps {
  text: string;
}

/** `**bold**` and `` `code` `` inside an otherwise plain line. */
function inline(text: string, key: string): React.ReactNode[] {
  const parts = text.split(/(\*\*[^*]+\*\*|`[^`]+`)/g).filter((part) => part !== "");
  return parts.map((part, index) => {
    if (part.startsWith("**") && part.endsWith("**")) {
      return <strong key={`${key}-${index}`}>{part.slice(2, -2)}</strong>;
    }
    if (part.startsWith("`") && part.endsWith("`")) {
      return <code key={`${key}-${index}`}>{part.slice(1, -1)}</code>;
    }
    return <Fragment key={`${key}-${index}`}>{part}</Fragment>;
  });
}

/** A line of a pipe table, split into its cells. */
function tableRow(line: string): string[] {
  return line
    .trim()
    .replace(/^\|/, "")
    .replace(/\|$/, "")
    .split("|")
    .map((cell) => cell.trim());
}

/** A `|---|---|` divider, the second line of every pipe table. */
function isDivider(line: string): boolean {
  return /^\|?\s*:?-+:?\s*(\|\s*:?-+:?\s*)*\|?$/.test(line.trim());
}

/**
 * The suite's own free-text rollup (usually `summary.md`), rendered instead of
 * shown as raw source. Suites write plain, small documents — headings, a pipe
 * table, prose — so this covers only that, not the full CommonMark grammar.
 */
export const Markdown: React.FC<MarkdownProps> = ({ text }) => {
  const lines = text.replace(/\r\n/g, "\n").split("\n");
  const blocks: React.ReactNode[] = [];
  let paragraph: string[] = [];

  const flushParagraph = (key: string) => {
    if (paragraph.length === 0) return;
    blocks.push(<p key={key}>{inline(paragraph.join(" "), key)}</p>);
    paragraph = [];
  };

  for (let i = 0; i < lines.length; i++) {
    const line = lines[i];
    const heading = /^(#{1,6})\s+(.*)$/.exec(line);

    if (line.trim() === "") {
      flushParagraph(`p-${i}`);
      continue;
    }

    if (heading) {
      flushParagraph(`p-${i}`);
      const level = Math.min(heading[1].length + 2, 6);
      blocks.push(createElement(`h${level}`, { key: `h-${i}` }, inline(heading[2], `h-${i}`)));
      continue;
    }

    if (line.trim().startsWith("|") && isDivider(lines[i + 1] ?? "")) {
      flushParagraph(`p-${i}`);
      const header = tableRow(line);
      const body: string[][] = [];
      let j = i + 2;
      while (j < lines.length && lines[j].trim().startsWith("|")) {
        body.push(tableRow(lines[j]));
        j++;
      }
      blocks.push(
        <table key={`t-${i}`}>
          <thead>
            <tr>
              {header.map((cell, index) => (
                <th key={index}>{inline(cell, `t-${i}-h-${index}`)}</th>
              ))}
            </tr>
          </thead>
          <tbody>
            {body.map((row, rowIndex) => (
              <tr key={rowIndex}>
                {row.map((cell, index) => (
                  <td key={index}>{inline(cell, `t-${i}-${rowIndex}-${index}`)}</td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      );
      i = j - 1;
      continue;
    }

    paragraph.push(line.trim());
  }
  flushParagraph("p-end");

  return <div className="markdown">{blocks}</div>;
};

export default Markdown;
