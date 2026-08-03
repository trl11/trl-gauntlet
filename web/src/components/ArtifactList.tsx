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

/** Props for {@link ArtifactList}. */
export interface ArtifactListProps {
  /** Poll for new files while the run is in flight. */
  live?: boolean;
  /** Run whose directory is listed. */
  runId: string;
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
export const ArtifactList: React.FC<ArtifactListProps> = ({ live = false, runId }) => {
  const [preview, setPreview] = useState<string | null>(null);

  const artifacts = useQuery({
    queryKey: ["artifacts", runId],
    queryFn: () => listArtifacts(runId),
    refetchInterval: live ? LIVE_POLL_MS : false,
  });

  const content = useQuery({
    queryKey: ["artifact-text", runId, preview],
    queryFn: () => getArtifactText(runId, preview as string),
    enabled: preview !== null,
  });

  if (artifacts.isPending) return <Spinner />;

  if (artifacts.isError) {
    return (
      <p className="artifact-list__error" role="alert">
        {(artifacts.error as Error).message}
      </p>
    );
  }

  const files = artifacts.data.artifacts;
  if (files.length === 0) {
    return <EmptyState title="No artifacts" message="This run has not written any files yet." />;
  }

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
            {files.map((file) => (
              <tr key={file.path}>
                <td className="artifact-list__path">{file.path}</td>
                <td className="artifact-list__mono">{extensionOf(file.path)}</td>
                <td className="artifact-list__mono">{formatBytes(file.size)}</td>
                <td className="artifact-list__actions">
                  {file.text && (
                    <Button
                      size="small"
                      onClick={() => setPreview(preview === file.path ? null : file.path)}
                    >
                      {preview === file.path ? "Hide" : "Preview"}
                    </Button>
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
          {content.isPending && <Spinner />}
          {content.isError && <p role="alert">{(content.error as Error).message}</p>}
          {content.data !== undefined && <pre>{prettify(preview, content.data)}</pre>}
        </div>
      )}
    </section>
  );
};

export default ArtifactList;
