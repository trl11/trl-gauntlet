import { Badge } from "@trl11/components/ui";
import clsx from "clsx";

import "./ListToolbar.scss";

/** Props for {@link ListToolbar}. */
export interface ListToolbarProps {
  /** The filter control, typically a ui-kit `FilterMenu`. */
  filter: React.ReactNode;
  /** Row/page status text, shown beside the filter. */
  status?: React.ReactNode;
  /** How many rows are selected. Batch actions render only when this is above zero. */
  selectedCount: number;
  /** Buttons offered for the current selection, e.g. export or delete. */
  batchActions: React.ReactNode;
  className?: string;
}

/** A bar pairing a filter control with batch actions that appear once something is selected. */
export const ListToolbar: React.FC<ListToolbarProps> = ({
  filter,
  status,
  selectedCount,
  batchActions,
  className,
}) => (
  <div className={clsx("list-toolbar", className)}>
    {status && <span className="list-toolbar__status">{status}</span>}
    <div className="list-toolbar__right">
      {selectedCount > 0 && (
        <div className="list-toolbar__batch">
          <Badge aria-live="polite" color="blue">
            {`${selectedCount} selected`}
          </Badge>
          {batchActions}
        </div>
      )}
      {filter}
    </div>
  </div>
);

export default ListToolbar;
