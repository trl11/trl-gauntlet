import { faCompass } from "@fortawesome/free-solid-svg-icons";
import { Button } from "@trl11/components/ui";
import { useLocation, useNavigate } from "react-router";

import EmptyState from "@components/EmptyState";
import PageHeader from "@components/PageHeader";

import "./NotFoundPage.scss";

/** Shown for any route the app does not know. */
export const NotFoundPage: React.FC = () => {
  const navigate = useNavigate();
  const location = useLocation();

  return (
    <div className="not-found-page">
      <PageHeader title="Not found" subtitle={location.pathname} />
      <span className="not-found-page__code" aria-hidden="true">
        404
      </span>
      <EmptyState
        icon={faCompass}
        title="There is nothing at this address"
        message="The link may be stale, or the run it pointed at has been removed."
        action={
          <Button color="blue" onClick={() => navigate("/")}>
            Back to the dashboard
          </Button>
        }
      />
    </div>
  );
};

export default NotFoundPage;
