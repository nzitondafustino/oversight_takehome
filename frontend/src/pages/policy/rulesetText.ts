import type { RulesetStatus } from "@/api/types";

/** A ruleset version's lifecycle status in words. */
export const RULESET_STATUS_TEXT: Record<RulesetStatus, string> = { draft: "Draft", in_review: "In review", approved: "Approved", published: "Published" };
