export type ReviewStage =
  | 'Not applicable'
  | 'Financials to be received'
  | 'In review'
  | 'Discrepancy identified'
  | 'No discrepancy identified'
  | 'Not comparable'
  | 'Query sent'
  | 'Query response reminder sent 1'
  | 'Query response reminder sent 2'
  | 'Response received'
  | 'Response received - Partially answered'
  | 'Response received - Call to be scheduled'
  | 'Approved'
  | 'Approved - Flagged'
  | 'Not Approved'
  | 'Not Approved - Flagged';

export interface ReviewCycle {
  id: string;
  label: string;
  createdAt: string;
  startsAt: string | null;
  endsAt: string | null;
}

export interface ReviewCompanyEntry {
  id: string;
  reviewCycleId: string;
  updatedAt: string;

  // Canonical link to backend PortfolioCompany record
  portfolioCompanyId: number;

  // Display fields (kept for convenience; should match PortfolioCompany)
  companyName: string;
  stage: ReviewStage;
  contactName: string;
  contactEmail: string;
}

export interface ReviewCycleLog {
  id: string;
  action: string;
  timestamp: string;
  user: string;
  details: string;
  reviewCycleId?: string;
}

