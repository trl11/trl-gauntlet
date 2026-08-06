import clsx from "clsx";

import "./Panel.scss";

/** Props for {@link Panel}. */
export interface PanelProps {
  /** A link or a small button, aligned right in the header row. */
  action?: React.ReactNode;
  children: React.ReactNode;
  className?: string;
  /** The header label. Drawn uppercase and letter-spaced. */
  title?: string;
}

/** A dark card with a micro-label header, used for every block of the UI. */
export const Panel: React.FC<PanelProps> = ({ action, children, className, title }) => (
  <section className={clsx("panel", className)}>
    {(title || action) && (
      <div className="panel__head">
        {title && <h2 className="panel__title">{title}</h2>}
        {action}
      </div>
    )}
    {children}
  </section>
);

export default Panel;
