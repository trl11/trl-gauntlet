import { faDownload } from "@fortawesome/free-solid-svg-icons";
import { FontAwesomeIcon } from "@fortawesome/react-fontawesome";
import { useQuery } from "@tanstack/react-query";
import { Button, Spinner } from "@trl11/components/ui";
import { useState } from "react";

import { artifactUrl, getArtifactText, listArtifacts } from "@api/client";
import EmptyState from "@components/EmptyState";
import { formatBytes } from "../utils/format";

import "./ArtifactList.scss";

/** How often the file list is refreshed while the run is still writing. */
const LIVE_POLL_MS = 5000;

/** Above this, a file is downloaded instead of fetched and pretty-printed inline. */
const MAX_PREVIEW_BYTES = 500_000;

/** One gallery tab's files, folded into a row of their own. */
export interface ArtifactGallery {
  /** Paths that tab already shows. */
  paths: string[];
  /** The tab they are shown in, which the folded row sends the operator to. */
  tab: string;
}

/** Props for {@link ArtifactList}. */
export interface ArtifactListProps {
  /**
   * Artifact paths a gallery tab already shows, one folded row each.
   *
   * A run samples for as long as it is asked to, so an image per sample runs
   * to hundreds of files. Listing them individually would bury the handful of
   * artifacts an operator comes to this tab for.
   */
  galleries?: ArtifactGallery[];
  /** Poll for new files while the run is in flight. */
  live?: boolean;
  /** Sends the operator to the tab that draws a file. */
  onOpen?: (tab: string) => void;
  /** Run whose directory is listed. */
  runId: string;
  /**
   * The tab that draws a file, by path.
   *
   * A file with one is previewed by going there rather than inline: its
   * contents are for the view that understands them, not for reading.
   */
  viewers?: Record<string, string>;
}

function extensionOf(path: string): string {
  const name = path.slice(path.lastIndexOf("/") + 1);
  const dot = name.lastIndexOf(".");
  return dot > 0 ? name.slice(dot + 1) : "file";
}

/** Pretty-print JSON, and leave anything else exactly as it arrived. */
function prettify(path: string, text: string): string {
  if (!path.endsWith(".json")) return text;
  try {
    return JSON.stringify(JSON.parse(text), null, 2);
  } catch {
    return text;
  }
}

/** Every file a run wrote, with an inline preview for the text ones. */
export const ArtifactList: React.FC<ArtifactListProps> = ({
  galleries = [],
  live = false,
  onOpen,
  runId,
  viewers = {},
}) => {
  const [preview, setPreview] = useState<string | null>(null);

  const artifacts = useQuery({
    queryKey: ["artifacts", runId],
    queryFn: () => listArtifacts(runId),
    refetchInterval: live ? LIVE_POLL_MS : false,
  });

  const previewSize = artifacts.data?.artifacts.find((file) => file.path === preview)?.size ?? 0;
  const content = useQuery({
    queryKey: ["artifact-text", runId, preview],
    queryFn: () => getArtifactText(runId, preview as string),
    enabled: preview !== null && previewSize <= MAX_PREVIEW_BYTES,
  });

  if (artifacts.isPending) return <Spinner />;

  if (artifacts.isError) {
    return (
      <p className="artifact-list__error" role="alert">
        {(artifacts.error as Error).message}
      </p>
    );
  }

  const gathered = new Set(galleries.flatMap((gallery) => gallery.paths));
  const files = artifacts.data.artifacts.filter((file) => !gathered.has(file.path));
  const folded = galleries
    .map((gallery) => {
      const shown = new Set(gallery.paths);
      const held = artifacts.data.artifacts.filter((file) => shown.has(file.path));
      return {
        bytes: held.reduce((total, file) => total + file.size, 0),
        count: held.length,
        // The directory they share, so the row names a real place on disk.
        directory: held.length > 0 ? held[0].path.slice(0, held[0].path.indexOf("/") + 1) : "",
        extension: held.length > 0 ? extensionOf(held[0].path) : "file",
        tab: gallery.tab,
      };
    })
    .filter((row) => row.count > 0);
  if (files.length === 0 && folded.length === 0) {
    return <EmptyState title="No artifacts" message="This run has not written any files yet." />;
  }

  const tooLarge = previewSize > MAX_PREVIEW_BYTES;

  return (
    <section className="artifact-list" aria-label="Artifacts">
      <div className="artifact-list__scroll">
        <table className="artifact-list__table">
          <thead>
            <tr>
              <th scope="col">Path</th>
              <th scope="col">Type</th>
              <th scope="col">Size</th>
              <th scope="col">Actions</th>
            </tr>
          </thead>
          <tbody>
            {folded.map((row) => (
              <tr className="artifact-list__folded" key={row.tab}>
                <td className="artifact-list__path">{row.directory || row.tab}</td>
                <td className="artifact-list__mono">{row.extension}</td>
                <td className="artifact-list__mono">{formatBytes(row.bytes)}</td>
                <td className="artifact-list__actions">
                  {row.count} in the {row.tab} tab
                </td>
              </tr>
            ))}
            {files.map((file) => (
              <tr key={file.path}>
                <td className="artifact-list__path">{file.path}</td>
                <td className="artifact-list__mono">{extensionOf(file.path)}</td>
                <td className="artifact-list__mono">{formatBytes(file.size)}</td>
                <td className="artifact-list__actions">
                  {viewers[file.path] !== undefined ? (
                    <Button size="small" onClick={() => onOpen?.(viewers[file.path])}>
                      Preview
                    </Button>
                  ) : (
                    file.text && (
                      <Button
                        size="small"
                        onClick={() => setPreview(preview === file.path ? null : file.path)}
                      >
                        {preview === file.path ? "Hide" : "Preview"}
                      </Button>
                    )
                  )}
                  <a
                    className="artifact-list__download"
                    href={artifactUrl(runId, file.path)}
                    download
                    aria-label={`Download ${file.path}`}
                  >
                    <FontAwesomeIcon icon={faDownload} />
                  </a>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      {preview !== null && (
        <div className="artifact-list__preview">
          <h3 className="artifact-list__preview-title">{preview}</h3>
          {tooLarge ? (
            <p className="artifact-list__too-large">
              {`${formatBytes(previewSize)} is too large to preview inline.`}{" "}
              <a href={artifactUrl(runId, preview)} download>
                Download it instead.
              </a>
            </p>
          ) : (
            <>
              {content.isPending && <Spinner />}
              {content.isError && <p role="alert">{(content.error as Error).message}</p>}
              {content.data !== undefined && <pre>{prettify(preview, content.data)}</pre>}
            </>
          )}
        </div>
      )}
    </section>
  );
};

export default ArtifactList;
