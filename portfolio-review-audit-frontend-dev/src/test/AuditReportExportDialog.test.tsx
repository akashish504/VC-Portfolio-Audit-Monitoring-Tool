/**
 * Tests for the currency-selector visibility behaviour in AuditReportExportDialog.
 *
 * Key rules under test:
 *  1. Currency selector is HIDDEN when report type is "reconciliation" (default).
 *  2. Currency selector is VISIBLE when report type is "extracted_financials".
 *  3. "Generate report" button is enabled for reconciliation WITHOUT selecting currency.
 *  4. "Generate report" button is DISABLED for extracted_financials UNTIL currency is chosen.
 *  5. Switching back from extracted_financials → reconciliation hides the selector again.
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';
import { AuditReportExportDialog } from '@/components/AuditReportExportDialog';

// Minimal stub: sonner toast
vi.mock('sonner', () => ({ toast: { error: vi.fn() } }));

// Stub the API module — we don't want real network calls
vi.mock('@/api/auditReportExport', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/api/auditReportExport')>();
  return {
    ...actual,
    startAuditReportExport: vi.fn().mockResolvedValue({
      job_id: 'test-job-1',
      status: 'pending',
      report_type: 'reconciliation',
      output_currency: null,
      filename: null,
      error_message: null,
      created_at: null,
      updated_at: null,
    }),
  };
});

const CYCLES = [
  { id: 'cy-2024', label: 'FY 2024' },
  { id: 'cy-2025', label: 'FY 2025' },
];

function renderDialog() {
  return render(
    <AuditReportExportDialog onClose={vi.fn()} reviewCycles={CYCLES} />,
  );
}

describe('AuditReportExportDialog — currency selector visibility', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it('1. hides the currency selector when reconciliation is selected (default)', () => {
    renderDialog();
    expect(screen.queryByLabelText(/output currency/i)).not.toBeInTheDocument();
    expect(screen.queryByRole('combobox', { name: /output currency/i })).not.toBeInTheDocument();
  });

  it('2. shows the currency selector when extracted_financials is selected', () => {
    renderDialog();
    fireEvent.click(screen.getByRole('radio', { name: /extracted financials/i }));
    expect(screen.getByText(/output currency/i)).toBeInTheDocument();
    expect(screen.getByTestId('currency-select')).toBeInTheDocument();
  });

  it('3. Generate report button is ENABLED for reconciliation without currency', () => {
    renderDialog();
    // reconciliation is default — no currency selected
    const btn = screen.getByRole('button', { name: /generate report/i });
    expect(btn).not.toBeDisabled();
  });

  it('4. Generate report button is DISABLED for extracted_financials until currency is chosen', () => {
    renderDialog();
    fireEvent.click(screen.getByRole('radio', { name: /extracted financials/i }));
    const btn = screen.getByRole('button', { name: /generate report/i });
    expect(btn).toBeDisabled();
  });

  it('4b. Generate report button becomes ENABLED after currency is chosen for extracted_financials', () => {
    renderDialog();
    fireEvent.click(screen.getByRole('radio', { name: /extracted financials/i }));
    const select = screen.getByTestId('currency-select');
    fireEvent.change(select, { target: { value: 'USD' } });
    const btn = screen.getByRole('button', { name: /generate report/i });
    expect(btn).not.toBeDisabled();
  });

  it('5. switching back to reconciliation hides the currency selector again', () => {
    renderDialog();
    // switch to extracted
    fireEvent.click(screen.getByRole('radio', { name: /extracted financials/i }));
    expect(screen.getByText(/output currency/i)).toBeInTheDocument();

    // switch back to reconciliation
    fireEvent.click(screen.getByRole('radio', { name: /reconciliation report/i }));
    expect(screen.queryByText(/output currency/i)).not.toBeInTheDocument();
  });
});
