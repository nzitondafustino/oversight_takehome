import { QueryClientProvider, type QueryClient } from "@tanstack/react-query";
import { useState, type ReactNode } from "react";
import { createBrowserRouter, RouterProvider, type RouteObject } from "react-router-dom";
import { createQueryClient } from "@/api/queries";
import { AppLayout } from "@/components/layout/AppLayout";
import { DashboardPage } from "@/pages/dashboard/DashboardPage";
import { ComparePage } from "@/pages/evaluations/ComparePage";
import { DatasetDetailPage } from "@/pages/evaluations/DatasetDetailPage";
import { DatasetsPage } from "@/pages/evaluations/DatasetsPage";
import { NewEvaluationPage } from "@/pages/evaluations/NewEvaluationPage";
import { EvaluationDetailPage } from "@/pages/evaluations/EvaluationDetailPage";
import { EvaluationsPage } from "@/pages/evaluations/EvaluationsPage";
import { NotFoundPage } from "@/pages/NotFoundPage";
import { DocumentDetailPage } from "@/pages/policy/DocumentDetailPage";
import { DocumentsRedirect } from "@/pages/policy/DocumentsPage";
import { CompilePage } from "@/pages/policy/CompilePage";
import { DocumentComparePage } from "@/pages/policy/DocumentComparePage";
import { ExtractionPage } from "@/pages/policy/ExtractionPage";
import { PoliciesPage } from "@/pages/policy/PoliciesPage";
import { RulesetDetailPage } from "@/pages/policy/RulesetDetailPage";
import { RunDetailPage } from "@/pages/runs/RunDetailPage";
import { RunHistoryPage } from "@/pages/runs/RunHistoryPage";
import { ValidationDraftProvider } from "@/pages/validate/draft";
import { ReceiptValidationPage } from "@/pages/validate/ReceiptValidationPage";

export const routes: RouteObject[] = [
  {
    element: <AppLayout />,
    children: [
      { index: true, element: <DashboardPage /> },
      { path: "validate", element: <ReceiptValidationPage /> },
      { path: "evaluations", element: <EvaluationsPage /> },
      { path: "evaluations/compare", element: <ComparePage /> },
      { path: "evaluations/new", element: <NewEvaluationPage /> },
      { path: "evaluations/datasets", element: <DatasetsPage /> },
      { path: "evaluations/datasets/:datasetId", element: <DatasetDetailPage /> },
      { path: "evaluations/:evaluationId", element: <EvaluationDetailPage /> },
      { path: "policies", element: <PoliciesPage /> },
      { path: "policies/:rulesetId", element: <RulesetDetailPage /> },
      { path: "documents", element: <DocumentsRedirect /> },
      { path: "documents/compile", element: <CompilePage /> },
      { path: "documents/compare", element: <DocumentComparePage /> },
      { path: "documents/extractions/:extractionId", element: <ExtractionPage /> },
      { path: "documents/:documentId", element: <DocumentDetailPage /> },
      { path: "runs", element: <RunHistoryPage /> },
      { path: "runs/:runId", element: <RunDetailPage /> },
      { path: "*", element: <NotFoundPage /> },
    ],
  },
];

export function Providers({ children, client }: { children: ReactNode; client: QueryClient }) {
  return (
    <QueryClientProvider client={client}>
      <ValidationDraftProvider>{children}</ValidationDraftProvider>
    </QueryClientProvider>
  );
}

export default function App() {
  const [client] = useState(createQueryClient);
  const [router] = useState(() => createBrowserRouter(routes));
  return (
    <Providers client={client}>
      <RouterProvider router={router} />
    </Providers>
  );
}
