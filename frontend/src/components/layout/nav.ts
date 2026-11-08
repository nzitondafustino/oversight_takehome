import { FileCheck2, FileText, FlaskConical, History, LayoutDashboard, ShieldCheck, type LucideIcon } from "lucide-react";

export interface NavItem {
  to: string;
  label: string;
  icon: LucideIcon;
  description: string;
}

export const NAV_ITEMS: readonly NavItem[] = [
  { to: "/", label: "Dashboard", icon: LayoutDashboard, description: "Validation activity, evaluation jobs, quality and spending." },
  { to: "/validate", label: "Validate Receipts", icon: FileCheck2, description: "Validate a batch of receipts against one exact published ruleset version." },
  { to: "/evaluations", label: "Evaluations", icon: FlaskConical, description: "Labelled datasets evaluated as background jobs, with quality metrics and spending." },
  { to: "/policies", label: "Policy Management", icon: ShieldCheck, description: "Ruleset versions through review, approval and publication." },
  { to: "/documents", label: "Documents", icon: FileText, description: "Policy documents as immutable revisions, and their extractions." },
  { to: "/runs", label: "Run History", icon: History, description: "Every persisted validation run, its findings and exports." },
];
