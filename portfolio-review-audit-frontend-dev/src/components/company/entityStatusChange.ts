import { patchEntity } from '@/api/portfolio';
import type { EntityReviewStatusValue } from '@/constants/statusEnums';

/**
 * Entity status is the audit state of record (it replaced the company-level review_stage).
 * Changing it is a single, self-contained write to the entity — there is no longer any
 * coupled company-level stage to update.
 */
export type EntityStatusConfirmState = {
  entityId: number;
  portfolioCompanyId: number;
  entityLabel: string;
  previousStatus: EntityReviewStatusValue;
  newStatus: EntityReviewStatusValue;
};

export async function applyEntityStatusChange(state: EntityStatusConfirmState): Promise<void> {
  await patchEntity(state.entityId, { status: state.newStatus });
}
