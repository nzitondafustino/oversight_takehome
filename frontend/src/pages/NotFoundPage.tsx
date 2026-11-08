import { ArrowLeft } from "lucide-react";
import { Link } from "react-router-dom";
import { PageHeader } from "@/components/layout/AppLayout";
import { Button } from "@/components/ui/button";

export function NotFoundPage() {
  return (
    <>
      <PageHeader title="Page not found" description="This address does not match any page." />
      <Button asChild variant="outline" size="sm">
        <Link to="/">
          <ArrowLeft aria-hidden />
          Back to dashboard
        </Link>
      </Button>
    </>
  );
}
