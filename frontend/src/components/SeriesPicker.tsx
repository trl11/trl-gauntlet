import { Accordion, Button, Checkbox, Input, Popover } from "@trl11/components/ui";
import { useId, useMemo, useState } from "react";

import { groupSeriesNames, naturalCompare } from "../utils/metrics";

import "./SeriesPicker.scss";

/** Below this many names, grouping and a search box only add clicks, so the picker stays flat. */
const FLAT_THRESHOLD = 12;

/** Props for {@link SeriesPicker}. */
export interface SeriesPickerProps {
  /** Every series name on offer, in any order. */
  names: string[];
  /** Names currently picked. */
  selected: string[];
  onChange: (next: string[]) => void;
}

/** A name's own label within its group: the part after the first dot, or itself if there is none. */
function shortLabel(name: string): string {
  const dot = name.indexOf(".");
  return dot === -1 ? name : name.slice(dot + 1);
}

/**
 * Picks series out of a set that can run into the hundreds: a search box that
 * flattens to a matching list, and otherwise one collapsed group per dotted
 * prefix (`cpu`, `memory`, `disk`, ...) so the picker opens small. Shared by
 * the Metrics chart and the Iterations table so both pick series the same way.
 *
 * Sits behind a `Popover`, the kit's own convention for filter UI (see
 * `FilterMenu`): closed by default, floats over the page instead of pushing
 * it around, and closes on an outside click or Escape. The panel itself
 * carries its own max-height and scroll, since `Popover`'s doesn't bound one,
 * and a flat (ungrouped) list can run long.
 */
export const SeriesPicker: React.FC<SeriesPickerProps> = ({ names, onChange, selected }) => {
  const fieldId = useId();
  const [search, setSearch] = useState("");

  const groups = useMemo(() => groupSeriesNames(names), [names]);
  const grouped = names.length > FLAT_THRESHOLD && groups.length > 1;
  const query = search.trim().toLowerCase();
  const filtered = useMemo(
    () =>
      query === ""
        ? null
        : names.filter((name) => name.toLowerCase().includes(query)).sort(naturalCompare),
    [names, query]
  );

  const isSelected = (name: string) => selected.includes(name);
  const toggle = (name: string) => {
    onChange(isSelected(name) ? selected.filter((entry) => entry !== name) : [...selected, name]);
  };
  const setGroup = (groupNames: string[], on: boolean) => {
    const rest = selected.filter((name) => !groupNames.includes(name));
    onChange(on ? [...rest, ...groupNames.filter((name) => !rest.includes(name))] : rest);
  };
  const clearAll = () => onChange([]);

  return (
    <Popover
      align="left"
      className="series-picker"
      trigger={
        <Button size="small" indicator={selected.length > 0}>
          Measurements ({selected.length}/{names.length})
        </Button>
      }
    >
      {!grouped ? (
        <>
          {names.length > FLAT_THRESHOLD / 2 && (
            <div className="series-picker__header">
              <Input
                id={`${fieldId}-search`}
                type="search"
                placeholder="Measurements"
                value={search}
                onChange={(event) => setSearch(event.target.value)}
              />
              {selected.length > 0 && (
                <Button size="small" onClick={clearAll}>
                  Clear all
                </Button>
              )}
            </div>
          )}
          {(filtered ?? names).length === 0 ? (
            <p className="series-picker__empty">No series match "{search}".</p>
          ) : (
            <div className="series-picker__flat">
              {(filtered ?? names).map((name) => (
                <Checkbox
                  key={name}
                  id={`${fieldId}-${name}`}
                  label={name}
                  checked={isSelected(name)}
                  onChange={() => toggle(name)}
                />
              ))}
            </div>
          )}
        </>
      ) : (
        <>
          <div className="series-picker__header">
            <Input
              id={`${fieldId}-search`}
              type="search"
              placeholder="Measurements"
              value={search}
              onChange={(event) => setSearch(event.target.value)}
            />
            {selected.length > 0 && (
              <Button size="small" onClick={clearAll}>
                Clear all
              </Button>
            )}
          </div>

          {filtered !== null ? (
            filtered.length === 0 ? (
              <p className="series-picker__empty">No series match "{search}".</p>
            ) : (
              <div className="series-picker__flat">
                {filtered.map((name) => (
                  <Checkbox
                    key={name}
                    id={`${fieldId}-${name}`}
                    label={name}
                    checked={isSelected(name)}
                    onChange={() => toggle(name)}
                  />
                ))}
              </div>
            )
          ) : (
            <div className="series-picker__groups">
              {groups.map(({ group, names: groupNames }) => {
                const allOn = groupNames.every(isSelected);
                const someOn = groupNames.some(isSelected);
                return (
                  <Accordion
                    key={group}
                    title={
                      <span className="series-picker__group-title">
                        {group}
                        <span className="series-picker__group-count">
                          {groupNames.filter(isSelected).length}/{groupNames.length}
                        </span>
                      </span>
                    }
                  >
                    <div className="series-picker__group-actions">
                      <Button size="small" onClick={() => setGroup(groupNames, !allOn)}>
                        {allOn ? "Clear group" : someOn ? "Select rest" : "Select group"}
                      </Button>
                    </div>
                    <div className="series-picker__flat">
                      {groupNames.map((name) => (
                        <Checkbox
                          key={name}
                          id={`${fieldId}-${name}`}
                          label={shortLabel(name)}
                          checked={isSelected(name)}
                          onChange={() => toggle(name)}
                        />
                      ))}
                    </div>
                  </Accordion>
                );
              })}
            </div>
          )}
        </>
      )}
    </Popover>
  );
};

export default SeriesPicker;
