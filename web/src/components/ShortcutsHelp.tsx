import { Modal } from "@trl11/components/ui";

import type { Shortcut } from "@hooks/useGlobalShortcuts";

import "./ShortcutsHelp.scss";

/** Props for {@link ShortcutsHelp}. */
export interface ShortcutsHelpProps {
  onClose: () => void;
  /** Rows to list, in order. */
  shortcuts: Shortcut[];
}

/** Overlay listing every global keyboard shortcut. */
export const ShortcutsHelp: React.FC<ShortcutsHelpProps> = ({ onClose, shortcuts }) => (
  <Modal title="Keyboard shortcuts" onClose={onClose} className="shortcuts-help">
    <dl className="shortcuts-help__list">
      {shortcuts.map((shortcut) => (
        <div key={shortcut.keys} className="shortcuts-help__row">
          <dt className="shortcuts-help__keys">{shortcut.keys}</dt>
          <dd className="shortcuts-help__description">{shortcut.description}</dd>
        </div>
      ))}
    </dl>
  </Modal>
);

export default ShortcutsHelp;
