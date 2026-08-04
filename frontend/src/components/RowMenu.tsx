import { faEllipsisVertical } from "@fortawesome/free-solid-svg-icons";
import { FontAwesomeIcon } from "@fortawesome/react-fontawesome";
import { Button, Popover } from "@trl11/components/ui";
import clsx from "clsx";
import { useRef } from "react";

import "./RowMenu.scss";

/** The subset of the kit's `PopoverHandle` this component uses. */
interface PopoverCloseHandle {
  close: () => void;
}

/** One action offered by a {@link RowMenu}. */
export interface RowMenuItem {
  /** Styles the item as a destructive action. */
  danger?: boolean;
  label: string;
  onSelect: () => void;
}

/** Props for {@link RowMenu}. */
export interface RowMenuProps {
  /** Read to the trigger button, e.g. "Actions for run r1". */
  ariaLabel: string;
  items: RowMenuItem[];
}

/** A row's "more actions" menu: an ellipsis button that opens a small dropdown. */
export const RowMenu: React.FC<RowMenuProps> = ({ ariaLabel, items }) => {
  const popover = useRef<PopoverCloseHandle>(null);

  return (
    <Popover
      ref={popover}
      align="right"
      trigger={
        <Button className="row-menu__trigger" size="small" square aria-label={ariaLabel}>
          <FontAwesomeIcon icon={faEllipsisVertical} />
        </Button>
      }
    >
      <div className="row-menu">
        {items.map((item) => (
          <button
            key={item.label}
            type="button"
            className={clsx("row-menu__item", item.danger && "row-menu__item--danger")}
            onClick={() => {
              popover.current?.close();
              item.onSelect();
            }}
          >
            {item.label}
          </button>
        ))}
      </div>
    </Popover>
  );
};

export default RowMenu;
