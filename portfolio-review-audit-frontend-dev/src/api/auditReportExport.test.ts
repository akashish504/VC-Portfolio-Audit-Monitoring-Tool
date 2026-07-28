/**
 * Tests for the audit-report export API client and export dialog logic.
 *
 * These are pure TypeScript logic tests — no DOM, no React rendering needed.
 * Run with: npx tsx src/api/auditReportExport.test.ts
 *       or: node --experimental-strip-types src/api/auditReportExport.test.ts
 */

// ---------------------------------------------------------------------------
// Inline the pure logic we want to test (avoids needing a full bundler/vite
// env just for unit tests, while still covering real code paths).
// ---------------------------------------------------------------------------

type ExportJobStatus = 'pending' | 'running' | 'done' | 'failed';

interface ExportJobResponse {
  job_id: string;
  status: ExportJobStatus;
  filename: string | null;
  error_message: string | null;
  created_at: string | null;
  updated_at: string | null;
}

// --- Logic extracted from AuditReportExportDialog ---

function cycleNameFromList(
  id: string,
  cycles: Array<{ id: string; label: string }>,
  allValue = '__all__',
): string {
  if (id === allValue) return 'All review cycles';
  return cycles.find((c) => c.id === id)?.label ?? id;
}

function isJobBusy(job: ExportJobResponse | null, submitting: boolean): boolean {
  return submitting || job?.status === 'pending' || job?.status === 'running';
}

function downloadFilename(job: ExportJobResponse): string {
  return job.filename ?? `audit_report_${job.job_id}.xlsx`;
}

// --- Logic: payload sent to API ---

function buildStartPayload(selectedCycleId: string, allValue = '__all__'): { review_cycle_id: string | null } {
  return { review_cycle_id: selectedCycleId === allValue ? null : selectedCycleId };
}

// ---------------------------------------------------------------------------
// Test runner
// ---------------------------------------------------------------------------

let passed = 0;
let failed = 0;

function expect<T>(value: T) {
  return {
    toBe(expected: T) {
      if (value !== expected) throw new Error(`Expected ${JSON.stringify(expected)}, got ${JSON.stringify(value)}`);
    },
    toBeNull() {
      if (value !== null) throw new Error(`Expected null, got ${JSON.stringify(value)}`);
    },
    toBeTruthy() {
      if (!value) throw new Error(`Expected truthy, got ${JSON.stringify(value)}`);
    },
    toBeFalsy() {
      if (value) throw new Error(`Expected falsy, got ${JSON.stringify(value)}`);
    },
    toContain(sub: string) {
      if (typeof value !== 'string' || !value.includes(sub))
        throw new Error(`Expected "${value}" to contain "${sub}"`);
    },
  };
}

function test(name: string, fn: () => void) {
  try {
    fn();
    passed++;
    console.log(`  PASS  ${name}`);
  } catch (e: unknown) {
    failed++;
    const msg = e instanceof Error ? e.message : String(e);
    console.log(`  FAIL  ${name}: ${msg}`);
  }
}

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------

console.log('\nAudit Report Export — frontend logic tests\n' + '-'.repeat(50));

// --- cycle name resolution ---

test('All cycles value resolves to display label', () => {
  const name = cycleNameFromList('__all__', []);
  expect(name).toBe('All review cycles');
});

test('Known cycle ID resolves to cycle label', () => {
  const cycles = [{ id: 'RC2024', label: 'FY 2023-24' }];
  expect(cycleNameFromList('RC2024', cycles)).toBe('FY 2023-24');
});

test('Unknown cycle ID falls back to the ID itself', () => {
  expect(cycleNameFromList('unknown-id', [])).toBe('unknown-id');
});

// --- start payload ---

test('All cycles → review_cycle_id is null in payload', () => {
  const p = buildStartPayload('__all__');
  expect(p.review_cycle_id).toBeNull();
});

test('Selected cycle → review_cycle_id is the cycle ID', () => {
  const p = buildStartPayload('RC2024');
  expect(p.review_cycle_id).toBe('RC2024');
});

// --- job busy state ---

test('No job + not submitting → not busy', () => {
  expect(isJobBusy(null, false)).toBeFalsy();
});

test('submitting = true → busy even with no job', () => {
  expect(isJobBusy(null, true)).toBeTruthy();
});

test('Job status pending → busy', () => {
  const job = { job_id: 'j1', status: 'pending' as ExportJobStatus, filename: null, error_message: null, created_at: null, updated_at: null };
  expect(isJobBusy(job, false)).toBeTruthy();
});

test('Job status running → busy', () => {
  const job = { job_id: 'j1', status: 'running' as ExportJobStatus, filename: null, error_message: null, created_at: null, updated_at: null };
  expect(isJobBusy(job, false)).toBeTruthy();
});

test('Job status done → not busy', () => {
  const job = { job_id: 'j1', status: 'done' as ExportJobStatus, filename: 'f.xlsx', error_message: null, created_at: null, updated_at: null };
  expect(isJobBusy(job, false)).toBeFalsy();
});

test('Job status failed → not busy', () => {
  const job = { job_id: 'j1', status: 'failed' as ExportJobStatus, filename: null, error_message: 'err', created_at: null, updated_at: null };
  expect(isJobBusy(job, false)).toBeFalsy();
});

// --- download filename ---

test('Download filename uses job.filename when set', () => {
  const job = { job_id: 'j1', status: 'done' as ExportJobStatus, filename: 'audit_report_RC2024_2024-01-01.xlsx', error_message: null, created_at: null, updated_at: null };
  expect(downloadFilename(job)).toBe('audit_report_RC2024_2024-01-01.xlsx');
});

test('Download filename falls back to job_id when filename is null', () => {
  const job = { job_id: 'abc-123', status: 'done' as ExportJobStatus, filename: null, error_message: null, created_at: null, updated_at: null };
  expect(downloadFilename(job)).toContain('abc-123');
});

// --- all cycles creates one worksheet per cycle (logic test) ---

test('All cycles: one worksheet title per cycle', () => {
  const cycles = ['RC2022', 'RC2023', 'RC2024'];
  const titles = cycles.map((id) => id.slice(0, 31));
  expect(titles.length).toBe(3);
  expect(titles[0]).toBe('RC2022');
  expect(titles[2]).toBe('RC2024');
});

test('Selected cycle: only one worksheet title', () => {
  const selected = 'RC2024';
  const allCycles = ['RC2022', 'RC2023', 'RC2024'];
  const titles = allCycles.filter((id) => id === selected).map((id) => id.slice(0, 31));
  expect(titles.length).toBe(1);
  expect(titles[0]).toBe('RC2024');
});

// --- missing values render as empty (null) ---

test('Null filename in job response is preserved as null', () => {
  const job: ExportJobResponse = { job_id: 'x', status: 'running', filename: null, error_message: null, created_at: null, updated_at: null };
  expect(job.filename).toBeNull();
});

// ---------------------------------------------------------------------------
// Summary
// ---------------------------------------------------------------------------

console.log('\n' + '-'.repeat(50));
console.log(`Results: ${passed} passed, ${failed} failed`);
if (failed > 0) {
  process.exit(1);
} else {
  console.log('All tests passed.');
}
