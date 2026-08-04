import { faArrowDown, faArrowUp } from "@fortawesome/free-solid-svg-icons";
import { FontAwesomeIcon } from "@fortawesome/react-fontawesome";
import { Button, Checkbox, FilterMenu, Input, Tooltip } from "@trl11/components/ui";
import clsx from "clsx";
import { useEffect, useId, useMemo, useRef, useState } from "react";

import type { LogLevel } from "@api/types";
import { toDate } from "../utils/format";

import "./LogStream.scss";

/** Height of one rendered row, in pixels. Kept in step with the stylesheet. */
const ROW_HEIGHT = 20;

/** Extra rows kept above and below the viewport so scrolling never shows a gap. */
const OVERSCAN = 20;

/** Rows rendered at once while wrapping, where a row is no longer one line tall. */
const WRAP_LIMIT = 2000;

/** Distance from the bottom, in pixels, that still counts as being at the bottom. */
const STICK_MARGIN = 24;

/** Gap kept between the log view's bottom edge and the viewport's, once expanded. */
const BOTTOM_PADDING = 32;

/** The view never shrinks below this, however little room the viewport has. */
const MIN_VIEW_HEIGHT = 280;

const LEVEL_OPTIONS = [
  { value: "all", label: "All levels" },
  { value: "info", label: "Info" },
  { value: "warning", label: "Warning" },
  { value: "error", label: "Error" },
];

/** The filter values, in the shape the ui-kit `FilterMenu` holds them. */
type Filters = React.ComponentProps<typeof FilterMenu>["filterState"];

/**
 * One captured output line.
 *
 * A live `log` event satisfies this, and so does a line parsed out of
 * `test.log`, which has no timestamp of its own.
 */
export interface LogLine {
  level: LogLevel;
  message: string;
  seq: number;
  ts: number | null;
}

/** Props for {@link LogStream}. */
export interface LogStreamProps {
  /** Every line captured so far, oldest first. */
  lines: LogLine[];
}

function logTime(ts: number | null): string {
  const date = toDate(ts);
  if (date === null) return "";
  return date.toLocaleTimeString(undefined, {
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
    hour12: false,
  });
}

/** Split a message so that every occurrence of `needle` is wrapped in a `mark`. */
function highlight(message: string, needle: string): React.ReactNode {
  if (!needle) return message;
  const haystack = message.toLowerCase();
  const target = needle.toLowerCase();
  const parts: React.ReactNode[] = [];
  let at = 0;
  let found = haystack.indexOf(target, at);
  while (found !== -1) {
    if (found > at) parts.push(message.slice(at, found));
    parts.push(<mark key={found}>{message.slice(found, found + target.length)}</mark>);
    at = found + target.length;
    found = haystack.indexOf(target, at);
  }
  parts.push(message.slice(at));
  return parts;
}

/**
 * Append-only log viewer.
 *
 * Only the rows around the scroll position are rendered, with padding elements
 * standing in for the rest, so a stream of tens of thousands of lines stays
 * responsive. Wrapping makes rows taller than `ROW_HEIGHT`, which that
 * arithmetic depends on, so wrapping instead renders the last `WRAP_LIMIT`
 * rows outright.
 */
export const LogStream: React.FC<LogStreamProps> = ({ lines }) => {
  const fieldId = useId();
  const viewRef = useRef<HTMLDivElement>(null);

  const [filters, setFilters] = useState<Filters>({ level: "all" });
  const [needle, setNeedle] = useState("");
  const [wrap, setWrap] = useState(false);
  const [stick, setStick] = useState(true);
  const [scrollTop, setScrollTop] = useState(0);
  const [viewport, setViewport] = useState(480);
  const [cursor, setCursor] = useState(0);
  const [viewHeight, setViewHeight] = useState(MIN_VIEW_HEIGHT);

  const filtered = useMemo(() => {
    const target = needle.trim().toLowerCase();
    return lines.filter((line) => {
      if (filters.level !== "all" && line.level !== filters.level) return false;
      return target === "" || line.message.toLowerCase().includes(target);
    });
  }, [lines, filters.level, needle]);

  const matches = needle.trim() === "" ? 0 : filtered.length;

  useEffect(() => {
    setCursor(0);
  }, [needle, filters.level]);

  useEffect(() => {
    const element = viewRef.current;
    if (!element) return;
    setViewport(element.clientHeight);
    const observer = new ResizeObserver(() => setViewport(element.clientHeight));
    observer.observe(element);
    return () => observer.disconnect();
  }, []);

  // Stretches the view down to the viewport's bottom edge, leaving room to spare,
  // rather than the fixed height that left a gap below on tall screens.
  useEffect(() => {
    const element = viewRef.current;
    if (!element) return;
    const measure = () => {
      const top = element.getBoundingClientRect().top;
      setViewHeight(Math.max(MIN_VIEW_HEIGHT, window.innerHeight - top - BOTTOM_PADDING));
    };
    measure();
    window.addEventListener("resize", measure);
    return () => window.removeEventListener("resize", measure);
  }, []);

  useEffect(() => {
    const element = viewRef.current;
    if (!element || !stick) return;
    element.scrollTop = element.scrollHeight;
  }, [filtered.length, stick, wrap]);

  const onScroll = () => {
    const element = viewRef.current;
    if (!element) return;
    setScrollTop(element.scrollTop);
    const fromBottom = element.scrollHeight - element.scrollTop - element.clientHeight;
    setStick(fromBottom <= STICK_MARGIN);
  };

  const jump = (index: number) => {
    const element = viewRef.current;
    if (!element || matches === 0) return;
    const wrapped = (index + matches) % matches;
    setCursor(wrapped);
    setStick(false);
    element.scrollTop = Math.max(0, wrapped * ROW_HEIGHT - element.clientHeight / 2);
  };

  const total = filtered.length;
  const start = wrap
    ? Math.max(0, total - WRAP_LIMIT)
    : Math.max(0, Math.floor(scrollTop / ROW_HEIGHT) - OVERSCAN);
  const end = wrap
    ? total
    : Math.min(total, Math.ceil((scrollTop + viewport) / ROW_HEIGHT) + OVERSCAN);
  const visible = filtered.slice(start, end);

  return (
    <section className="log-stream" aria-label="Run log">
      <div className="log-stream__controls">
        <span className="log-stream__matches">
          {matches === 0 ? "no matches" : `${cursor + 1} / ${matches}`}
        </span>
        <div className="log-stream__find">
          <Input
            id={`${fieldId}-find`}
            aria-label="Find"
            type="search"
            placeholder="Filter lines"
            value={needle}
            onChange={(event) => setNeedle(event.target.value)}
          />
        </div>
        <Tooltip content="Previous match">
          <Button
            size="small"
            square
            disabled={matches === 0}
            aria-label="Previous match"
            onClick={() => jump(cursor - 1)}
          >
            <FontAwesomeIcon icon={faArrowUp} />
          </Button>
        </Tooltip>
        <Tooltip content="Next match">
          <Button
            size="small"
            square
            disabled={matches === 0}
            aria-label="Next match"
            onClick={() => jump(cursor + 1)}
          >
            <FontAwesomeIcon icon={faArrowDown} />
          </Button>
        </Tooltip>
        <Checkbox
          id={`${fieldId}-wrap`}
          label="Wrap lines"
          checked={wrap}
          onChange={(event) => setWrap(event.target.checked)}
        />
        <div className="log-stream__filter">
          <FilterMenu
            filterState={filters}
            setFilterState={setFilters}
            filters={[{ id: "level", options: LEVEL_OPTIONS }]}
          />
        </div>
      </div>

      <div
        className={clsx("log-stream__view", wrap && "log-stream__view--wrap")}
        style={{ height: viewHeight }}
        ref={viewRef}
        onScroll={onScroll}
        tabIndex={0}
        role="log"
        aria-label="Log lines"
      >
        {total === 0 ? (
          <p className="log-stream__empty">No log lines yet.</p>
        ) : (
          <>
            <div style={{ height: start * ROW_HEIGHT }} />
            {visible.map((line, index) => (
              <div
                key={line.seq}
                className={clsx(
                  "log-stream__row",
                  `log-stream__row--${line.level}`,
                  matches > 0 && start + index === cursor && "log-stream__row--active"
                )}
              >
                <span className="log-stream__time">{logTime(line.ts)}</span>
                <span className="log-stream__level">{line.level}</span>
                <span className="log-stream__message">
                  {highlight(line.message, needle.trim())}
                </span>
              </div>
            ))}
            <div style={{ height: (total - end) * ROW_HEIGHT }} />
          </>
        )}
      </div>

      <div className="log-stream__status">
        <span>
          {total} of {lines.length} lines
        </span>
        {!stick && (
          <Button size="small" onClick={() => setStick(true)}>
            Follow output
          </Button>
        )}
      </div>
    </section>
  );
};

export default LogStream;
