import { Database, Plus } from "lucide-react";
import { Link } from "react-router-dom";
import { PageHeader } from "@/components/layout/AppLayout";
import { Button } from "@/components/ui/button";
import { EvaluationList } from "./EvaluationList";

/** The Evaluation Workbench landing page: recent evaluations, comparison, and one way to start a new one. */
export function EvaluationsPage() {
  return (
    <>
      <PageHeader
        title="Evaluations"
        description="Score a classifier and a ruleset version against labelled receipts, as background jobs. Replay reads recorded model answers and spends nothing new."
        actions={
          <>
            <Button asChild variant="outline" size="sm">
              <Link to="/evaluations/datasets">
                <Database aria-hidden />
                Datasets
              </Link>
            </Button>
            <Button asChild size="sm">
              <Link to="/evaluations/new">
                <Plus aria-hidden />
                New evaluation
              </Link>
            </Button>
          </>
        }
      />
      <EvaluationList />
    </>
  );
}
