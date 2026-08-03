import type { IconDefinition } from "@fortawesome/fontawesome-svg-core";
import { faInbox } from "@fortawesome/free-solid-svg-icons";
import { FontAwesomeIcon } from "@fortawesome/react-fontawesome";
import clsx from "clsx";

import "./EmptyState.scss";

/** Props for {@link EmptyState}. */
export interface EmptyStateProps {
  /** A button or link offering the obvious next step. */
  action?: React.ReactNode;
  className?: string;
  /** Icon shown above the title. Defaults to an empty inbox. */
  icon?: IconDefinition;
  /** One sentence explaining why there is nothing here. */
  message?: React.ReactNode;
  /** Headline, such as "No runs yet". */
  title: React.ReactNode;
}

/** Placeholder for a view with no data to show. */
export const EmptyState: React.FC<EmptyStateProps> = ({
  action,
  className,
  icon = faInbox,
  message,
  title,
}) => (
  <div className={clsx("empty-state", className)}>
    <FontAwesomeIcon icon={icon} className="empty-state__icon" aria-hidden="true" />
    <p className="empty-state__title">{title}</p>
    {message && <p className="empty-state__message">{message}</p>}
    {action && <div className="empty-state__action">{action}</div>}
  </div>
);

export default EmptyState;
