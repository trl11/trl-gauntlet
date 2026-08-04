import clsx from "clsx";

import "./PageHeader.scss";

/** Props for {@link PageHeader}. */
export interface PageHeaderProps {
  /** Controls aligned to the right of the title, such as buttons. */
  actions?: React.ReactNode;
  /** Extra content rendered below the title block. */
  children?: React.ReactNode;
  className?: string;
  /** The page name. Rendered as the page's only `h1`. */
  title: React.ReactNode;
}

/** The title block every page opens with. */
export const PageHeader: React.FC<PageHeaderProps> = ({ actions, children, className, title }) => (
  <header className={clsx("page-header", className)}>
    <div className="page-header__bar">
      <div className="page-header__titles">
        <h1 className="page-header__title">{title}</h1>
      </div>
      {actions && <div className="page-header__actions">{actions}</div>}
    </div>
    {children}
  </header>
);

export default PageHeader;
