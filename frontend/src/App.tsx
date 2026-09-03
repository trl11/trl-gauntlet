import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { HashRouter, Route, Routes } from "react-router";

import { ApiError } from "@api/client";
import ErrorBoundary from "@components/ErrorBoundary";
import Layout from "@components/Layout";
import DashboardPage from "@pages/DashboardPage";
import HistoryPage from "@pages/HistoryPage";
import InstrumentsPage from "@pages/InstrumentsPage";
import NotFoundPage from "@pages/NotFoundPage";
import RunPage from "@pages/RunPage";
import SystemPage from "@pages/SystemPage";
import TestsPage from "@pages/TestsPage";
import UnitsPage from "@pages/UnitsPage";

/** A 4xx is the operator's answer, not a transient failure; do not retry it. */
function shouldRetry(failureCount: number, error: unknown): boolean {
  if (error instanceof ApiError && error.status >= 400 && error.status < 500) return false;
  return failureCount < 2;
}

const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      refetchOnWindowFocus: false,
      retry: shouldRetry,
      staleTime: 5000,
    },
  },
});

/** Root of the operator UI. */
export const App: React.FC = () => (
  <QueryClientProvider client={queryClient}>
    <ErrorBoundary>
      <HashRouter>
        <Routes>
          <Route path="/" element={<Layout />}>
            <Route index element={<DashboardPage />} />
            <Route path="tests" element={<TestsPage />} />
            <Route path="history" element={<HistoryPage />} />
            <Route path="runs/:runId" element={<RunPage />} />
            <Route path="units" element={<UnitsPage />} />
            <Route path="units/:serial" element={<UnitsPage />} />
            <Route path="instruments" element={<InstrumentsPage />} />
            <Route path="system" element={<SystemPage />} />
            <Route path="*" element={<NotFoundPage />} />
          </Route>
        </Routes>
      </HashRouter>
    </ErrorBoundary>
  </QueryClientProvider>
);

export default App;
