import { patchPortfolioCompany } from '@/api/portfolio';
import type { CompanyReviewStage } from '@/constants/statusEnums';

export type CompanyStageConfirmState = {
  portfolioCompanyId: number;
  label: string;
  from: CompanyReviewStage;
  to: CompanyReviewStage;
};

/**
 * Persist a company's review-stage change. The In Review Tracker and the company detail header both
 * edit the same `review_stage` column that the Review Cycle Adjustments page shows, so a change made
 * in any of those places stays in sync across all of them.
 */
export async function applyCompanyStageChange(state: CompanyStageConfirmState): Promise<void> {
  await patchPortfolioCompany(state.portfolioCompanyId, { review_stage: state.to });
}
