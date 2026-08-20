import { faChevronLeft, faChevronRight, faDownload } from "@fortawesome/free-solid-svg-icons";
import { FontAwesomeIcon } from "@fortawesome/react-fontawesome";
import { Button, Modal } from "@trl11/components/ui";
import { useEffect, useState } from "react";

import { artifactUrl } from "@api/client";
import EmptyState from "@components/EmptyState";

import "./SnapshotGallery.scss";

/** One image a run recorded, and the iteration it came from. */
export interface Snapshot {
  /** Iteration the image was recorded against, when the record carried one. */
  iteration: number | null;
  /** Path inside the run directory, as `metrics.images` gave it. */
  path: string;
}

/** Props for {@link SnapshotGallery}. */
export interface SnapshotGalleryProps {
  /** Images in the order they were recorded. */
  snapshots: Snapshot[];
  /** Run the images belong to, used to build their URLs. */
  runId: string;
}

/** The file's own name, which is what identifies it in the run directory. */
function nameOf(path: string): string {
  return path.slice(path.lastIndexOf("/") + 1);
}

/**
 * Every image a run recorded, as a grid that opens one full size.
 *
 * The images are whatever the suite named in `metrics.images`; nothing here
 * knows which suite wrote them or what they are of.
 */
export const SnapshotGallery: React.FC<SnapshotGalleryProps> = ({ runId, snapshots }) => {
  const [openAt, setOpenAt] = useState<number | null>(null);

  // A live run keeps appending, so an index that outlives its image would
  // otherwise open a blank frame.
  const at = openAt !== null && openAt < snapshots.length ? openAt : null;

  useEffect(() => {
    if (at === null) return;
    const step = (event: KeyboardEvent) => {
      if (event.key === "ArrowLeft") setOpenAt(Math.max(0, at - 1));
      if (event.key === "ArrowRight") setOpenAt(Math.min(snapshots.length - 1, at + 1));
    };
    document.addEventListener("keydown", step);
    return () => document.removeEventListener("keydown", step);
  }, [at, snapshots.length]);

  if (snapshots.length === 0) {
    return <EmptyState title="No snapshots" message="This run has not recorded any images." />;
  }

  const open = at === null ? null : snapshots[at];

  return (
    <section className="snapshot-gallery" aria-label="Snapshots">
      <ul className="snapshot-gallery__grid">
        {snapshots.map((snapshot, index) => (
          <li className="snapshot-gallery__item" key={`${snapshot.path}-${index}`}>
            <button
              className="snapshot-gallery__thumb"
              onClick={() => setOpenAt(index)}
              type="button"
              aria-label={`Open ${nameOf(snapshot.path)}`}
            >
              <img
                alt={nameOf(snapshot.path)}
                loading="lazy"
                src={artifactUrl(runId, snapshot.path)}
              />
            </button>
            <div className="snapshot-gallery__caption">
              <span className="snapshot-gallery__iteration">
                {snapshot.iteration === null ? nameOf(snapshot.path) : `#${snapshot.iteration}`}
              </span>
              <a
                aria-label={`Download ${nameOf(snapshot.path)}`}
                className="snapshot-gallery__download"
                download
                href={artifactUrl(runId, snapshot.path)}
              >
                <FontAwesomeIcon icon={faDownload} />
              </a>
            </div>
          </li>
        ))}
      </ul>

      {open !== null && at !== null && (
        <Modal title={nameOf(open.path)} onClose={() => setOpenAt(null)}>
          <div className="snapshot-gallery__viewer">
            <img alt={nameOf(open.path)} src={artifactUrl(runId, open.path)} />
            <div className="snapshot-gallery__controls">
              <Button
                aria-label="Previous snapshot"
                disabled={at === 0}
                size="small"
                onClick={() => setOpenAt(at - 1)}
              >
                <FontAwesomeIcon icon={faChevronLeft} />
              </Button>
              <span className="snapshot-gallery__position">
                {`${at + 1} of ${snapshots.length}`}
                {open.iteration !== null && ` — iteration ${open.iteration}`}
              </span>
              <Button
                aria-label="Next snapshot"
                disabled={at === snapshots.length - 1}
                size="small"
                onClick={() => setOpenAt(at + 1)}
              >
                <FontAwesomeIcon icon={faChevronRight} />
              </Button>
            </div>
          </div>
        </Modal>
      )}
    </section>
  );
};

export default SnapshotGallery;
