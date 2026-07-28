import { createContext, Fragment, useCallback, useContext, useEffect, useLayoutEffect, useMemo, useRef, useState } from 'react';
import { useLocation, useNavigate, useParams } from 'react-router-dom';
import {
  AlertTriangle,
  ArrowLeft,
  ArrowLeftRight,
  Check,
  CheckCircle2,
  ChevronRight,
  Clock,
  Download,
  EyeOff,
  FileText,
  Info,
  Link2,
  Loader2,
  Paperclip,
  Pencil,
  Plus,
  ScanSearch,
  ScrollText,
  Trash2,
  TrendingUp,
} from 'lucide-react';

import {
  getAuditFinancialParentPaths,
  getFileExtractionStatus,
  getFileSourceRefs,
  detachAuditFinancialField,
  dismissAuditFinancialUnmatched,
  mapAuditFinancialUnmatched,
  restoreAuditFinancialUnmatched,
  setAuditFinancialFieldComponents,
  patchAuditFinancialExtractedValue,
  addAuditFinancialCompositeField,
  applyFileExtractionConvertCurrency,
  setFileExtractionCurrency,
  type FieldComponent,
  type SourceRef,
} from '@/api/fileProcessing';
import { fetchFileFxPreviewRate, type FxRatesResponse } from '@/api/fx';
import PdfSourceViewer from '@/components/PdfSourceViewer';
import SpreadsheetPreview from '@/components/SpreadsheetPreview';
import { getApiErrorMessage } from '@/api/apiError';
import { resolveReviewCycleFromFyEnd } from '@/api/settings';
import { getFile, getFileDownloadUrl, getFileStreamUrl, listEntities, listPortfolioCompanies, patchFile, type MappingBreakdown, type ManualEditMarker } from '@/api/portfolio';
import { AfsBreakdownTooltip } from '@/components/company/AfsBreakdownTooltip';
import { EditedIndicator } from '@/components/common/EditedIndicator';
import { formatFileSizeFromBytes } from '@/utils/formatFileSize';
import apiClient from '@/api/axios';
import {
  AlertDialog,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from '@/components/ui/alert-dialog';
import { Button } from '@/components/ui/button';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select';
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs';
import { Tooltip, TooltipContent, TooltipProvider, TooltipTrigger } from '@/components/ui/tooltip';
import { toast } from '@/components/ui/sonner';
import { taggedFiles, type TaggedFile } from '@/data/mockData';
import { QualitativeReportContent } from '@/components/qualitative/QualitativeReportContent';
import { parseQualitativeReport } from '@/components/qualitative/qualitativeReportModel';
import { FyEndMonthYearPicker } from '@/components/files/FyEndMonthYearPicker';
import { DuplicateFileWarning } from '@/components/files/DuplicateFileWarning';

const statusConfig: Record<string, { icon: React.ElementType; badge: string; label: string }> = {
  processed: { icon: CheckCircle2, badge: 'bg-green-100 text-green-800', label: 'Processed' },
  pending: { icon: Clock, badge: 'bg-yellow-100 text-yellow-800', label: 'Pending' },
  error: { icon: AlertTriangle, badge: 'bg-red-100 text-red-800', label: 'Error' },
};

const AUDIT_FINANCIALS_REQUIRED_TOP_LEVEL = ['profit_and_loss', 'balance_sheet', 'cash_flow_statement'] as const;

function isFinancialStatementValuePath(path: string): boolean {
  if (!path) return false;
  return AUDIT_FINANCIALS_REQUIRED_TOP_LEVEL.some(
    (r) => path === r || path.startsWith(`${r}.`) || path.startsWith(`${r}[`),
  );
}
const TREE_LEVEL_INDENT_PX = 24;

/** Same box as `ChevronRight` `h-4 w-4` so leaf rows align with expandable rows. */
const TREE_ROW_CHEVRON_SLOT = 'inline-flex h-4 w-4 shrink-0 items-center justify-center';

const TOP_TRADED_CURRENCIES: Array<{ code: string; name: string }> = [
  { code: 'USD', name: 'US Dollar' },
  { code: 'INR', name: 'Indian Rupee' },
  { code: 'EUR', name: 'Euro' },
  { code: 'JPY', name: 'Japanese Yen' },
  { code: 'GBP', name: 'British Pound' },
  { code: 'AUD', name: 'Australian Dollar' },
  { code: 'CAD', name: 'Canadian Dollar' },
  { code: 'CHF', name: 'Swiss Franc' },
  { code: 'CNY', name: 'Chinese Renminbi' },
  { code: 'HKD', name: 'Hong Kong Dollar' },
  { code: 'NZD', name: 'New Zealand Dollar' },
  { code: 'SGD', name: 'Singapore Dollar' },
];

/** Keys rendered as all-caps acronyms instead of Title Case (e.g. ebitda → EBITDA, total_oci → Total OCI). */
const KEY_ACRONYMS = new Set(['ebitda', 'oci']);

/** Display label: snake_case JSON keys → Title Case words (e.g. changes_in_… → Changes In …). */
function formatKey(k: string) {
  return k
    .split('_')
    .filter(Boolean)
    .map((word) =>
      KEY_ACRONYMS.has(word)
        ? word.toUpperCase()
        : word.charAt(0).toUpperCase() + word.slice(1).toLowerCase(),
    )
    .join(' ');
}

/** Convert any user-typed label to a valid snake_case key. */
function toSnakeCase(s: string): string {
  return s
    .trim()
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, '_')
    .replace(/^_+|_+$/g, '');
}

function isPlainObject(v: unknown): v is Record<string, unknown> {
  return typeof v === 'object' && v !== null && !Array.isArray(v);
}

/** Int/float or parseable numeric string (LLM sometimes stores amounts as strings). */
function parseJsonNumericLeaf(v: unknown): number | null {
  if (typeof v === 'number' && Number.isFinite(v)) return v;
  if (typeof v === 'string') {
    let s = v.trim().replace(/,/g, '').replace(/\s+/g, '');
    if (!s) return null;
    if (s.startsWith('(') && s.endsWith(')')) s = `-${s.slice(1, -1)}`;
    const n = Number(s);
    return Number.isFinite(n) ? n : null;
  }
  return null;
}

function normalizeIsoCurrency(v: unknown): string | null {
  if (typeof v !== 'string') return null;
  const s = v.trim().toUpperCase();
  return /^[A-Z]{3}$/.test(s) ? s : null;
}

type CurrencySource = 'manual' | 'detected' | 'converted';

type ResolvedCurrency = { code: string | null; source: CurrencySource | null };

function pickCurrencyFromContainer(obj: Record<string, unknown>): string | null {
  const direct = normalizeIsoCurrency(obj.currency);
  if (direct) return direct;
  const rm = obj.report_metadata;
  if (isPlainObject(rm)) {
    const fromRm = normalizeIsoCurrency((rm as Record<string, unknown>).currency);
    if (fromRm) return fromRm;
  }
  return null;
}

function resolveCurrency(meta: Record<string, unknown> | null | undefined): ResolvedCurrency {
  if (!meta || !isPlainObject(meta)) return { code: null, source: null };
  const m = meta as Record<string, unknown>;

  // Canonical target: `meta.currency` written by the extraction pipeline or
  // the manual PATCH endpoint. `meta.currency_source` differentiates the two.
  const direct = normalizeIsoCurrency(m.currency);
  if (direct) {
    const srcRaw = m.currency_source;
    const source: CurrencySource =
      srcRaw === 'manual' ? 'manual' : srcRaw === 'converted' ? 'converted' : 'detected';
    return { code: direct, source };
  }

  // Back-compat for records extracted before the pipeline began writing
  // `meta.currency`: check the mapped tree, then the raw pass-1 output.
  const extracted = m.extracted;
  if (isPlainObject(extracted)) {
    const c = pickCurrencyFromContainer(extracted as Record<string, unknown>);
    if (c) return { code: c, source: 'detected' };
  }
  const raw = m.raw_extracted;
  if (isPlainObject(raw)) {
    const c = pickCurrencyFromContainer(raw as Record<string, unknown>);
    if (c) return { code: c, source: 'detected' };
  }

  return { code: null, source: null };
}


/**
 * True when an extracted value carries nothing worth showing — null/blank, or an
 * object/array that bottoms out with no real leaf (e.g. `{}`, `[]`, `{ x: {} }`).
 * Such rows are skipped instead of rendering an empty `{}` placeholder.
 */
function isEmptyExtractedValue(v: unknown): boolean {
  if (v === null || v === undefined) return true;
  if (typeof v === 'string') return v.trim() === '';
  if (Array.isArray(v)) return v.every(isEmptyExtractedValue);
  if (isPlainObject(v)) {
    const vals = Object.values(v);
    return vals.length === 0 || vals.every(isEmptyExtractedValue);
  }
  return false; // numbers, booleans, etc. are meaningful
}

/**
 * Walk the extracted tree and collect all items inside `other` catch-all buckets so they
 * can be surfaced in the unmatched panel instead of being silently hidden.
 */
function collectOtherBucketItems(
  node: unknown,
  path: string,
): Array<{ id: string; document_label: string; value: unknown; section_hint: string; _from_other: true }> {
  const out: Array<{ id: string; document_label: string; value: unknown; section_hint: string; _from_other: true }> = [];
  if (!isPlainObject(node)) return out;
  for (const [k, v] of Object.entries(node as Record<string, unknown>)) {
    const childPath = path ? `${path}.${k}` : k;
    if (k === 'other' && isPlainObject(v)) {
      for (const [itemKey, itemVal] of Object.entries(v as Record<string, unknown>)) {
        if (isEmptyExtractedValue(itemVal)) continue;
        out.push({
          id: `other:${childPath}.${itemKey}`,
          document_label: formatKey(itemKey),
          value: itemVal,
          section_hint: path,
          _from_other: true,
        });
      }
    } else if (isPlainObject(v)) {
      out.push(...collectOtherBucketItems(v, childPath));
    }
  }
  return out;
}

// --- Source-position rendering (increment 3) ------------------------------------------------
//
// Increment 3's contract: a line that the mapper couldn't tag to a canonical slot is NOT dumped
// into a review queue — it is rendered **in place**, in its original pass-1 source position, so the
// statement view never has holes. The canonical mapped tree is the display truth; each unmatched
// row is spliced back in at its ``raw_path`` as an "untagged-but-visible" leaf. Because these leaves
// carry their numeric value, the per-bucket reconciliation totals close just as they would if the
// line had been mapped — which is exactly what makes "absence of a tag" a non-event.
//
// The leaf is wrapped in a sentinel object so the recursive renderer can recognise it, style it
// distinctly, and offer inline Accept / Attach affordances (the canonical tag becomes an overlay,
// not a precondition for being shown).

const UNMATCHED_LEAF_MARKER = '__unmatched_source_line__';

type UnmatchedSourceLeaf = {
  [UNMATCHED_LEAF_MARKER]: true;
  row: Record<string, unknown>;
  value: unknown;
};

function isUnmatchedSourceLeaf(v: unknown): v is UnmatchedSourceLeaf {
  return isPlainObject(v) && v[UNMATCHED_LEAF_MARKER] === true;
}

/** A safe object key derived from a source label, kept stable + collision-resistant per parent. */
function sourceLeafKey(row: Record<string, unknown>, taken: Set<string>): string {
  const raw = String(row.raw_path ?? '').trim();
  const fromPath = raw ? raw.slice(raw.lastIndexOf('.') + 1) : '';
  const base =
    (fromPath || String(row.document_label ?? row.key ?? row.id ?? 'line'))
      .toLowerCase()
      .replace(/[^a-z0-9]+/g, '_')
      .replace(/^_+|_+$/g, '') || 'line';
  let key = base;
  let n = 2;
  while (taken.has(key)) key = `${base}__${n++}`;
  taken.add(key);
  return key;
}

/**
 * Splice unmatched rows back into the canonical tree at their ``raw_path`` source position,
 * creating intermediate source-header buckets as needed. Returns a NEW merged tree (the input is
 * never mutated) plus the set of dotted paths that were injected (so the renderer can find them).
 *
 * ``raw_path`` is the pass-1 header chain, e.g. ``balance_sheet.current_liabilities.borrowings``.
 * The statement root (``balance_sheet`` / ``profit_and_loss`` / ``cash_flow_statement``) is shared
 * with the canonical tree, so injected lines land under the right statement; the header levels
 * beneath it preserve the document's own grouping. Rows without a usable ``raw_path`` are dropped
 * onto the statement root from their ``section_hint`` so they're still visible somewhere sensible.
 */
// The three canonical statement roots. Used to detect when an untagged line's source ``raw_path``
// is headed by a document-specific statement label (e.g. "consolidated_statement_of_cash_flows")
// rather than the canonical key, so we can re-root it instead of spawning a duplicate section.
const CANONICAL_STATEMENTS: ReadonlySet<string> = new Set([
  'profit_and_loss',
  'balance_sheet',
  'cash_flow_statement',
]);

function buildSourcePositionTree(
  base: Record<string, unknown> | null,
  unmatchedRows: Record<string, unknown>[],
): { tree: Record<string, unknown> | null; injectedPaths: Set<string> } {
  const injectedPaths = new Set<string>();
  if (!base) return { tree: base, injectedPaths };
  if (unmatchedRows.length === 0) return { tree: base, injectedPaths };

  // Structural deep-clone of plain objects/arrays only; leaf values are shared (never mutated).
  const clone = (node: unknown): unknown => {
    if (Array.isArray(node)) return node.map(clone);
    if (isPlainObject(node)) {
      const out: Record<string, unknown> = {};
      for (const [k, v] of Object.entries(node)) out[k] = clone(v);
      return out;
    }
    return node;
  };
  const tree = clone(base) as Record<string, unknown>;

  // Track the child keys already used under each parent so generated leaf keys never collide.
  const takenByParent = new Map<string, Set<string>>();
  const takenFor = (parentPath: string): Set<string> => {
    let s = takenByParent.get(parentPath);
    if (!s) {
      const parent = dottedGet(tree, parentPath);
      s = new Set<string>(isPlainObject(parent) ? Object.keys(parent) : []);
      takenByParent.set(parentPath, s);
    }
    return s;
  };

  for (const row of unmatchedRows) {
    const raw = String(row.raw_path ?? '').trim();
    const sectionHint = String(row.section_hint ?? '').trim();
    const rawParts = raw ? raw.split('.').filter(Boolean) : [];
    // Header chain = the source headers ABOVE the leaf (its parent path). Hybrid placement, all of
    // which keep the line INSIDE the existing canonical statement (never a duplicate top-level one):
    //   1) Suggestion-anchored: if the row has a canonical suggestion, root it under the suggested
    //      field's parent so it lands beside the field it would be accepted into.
    //   2) Section-root fallback: otherwise take the document's own header chain (raw_path minus the
    //      leaf) but normalize its first segment to the canonical statement (``section_hint``), so a
    //      document heading like "consolidated_statement_of_cash_flows" merges into "cash_flow_statement"
    //      instead of creating a second section.
    //   3) Last resort: nest directly under ``section_hint`` / the raw single segment.
    const suggestion = readSuggestion(row);
    const suggestionChain =
      suggestion && CANONICAL_STATEMENTS.has(suggestion.parent_path.split('.')[0])
        ? suggestion.parent_path.split('.').filter(Boolean)
        : null;
    // 0) Authoritative section tag from the backend (``placement_path``): nest the line inside that
    //    section node regardless of its raw source position. This is what routes a terse subtotal
    //    like "Operating Activities" into ``cash_flows_from_operating_activities`` instead of being
    //    dumped at the statement root (the floater bug). Takes precedence over the heuristics below.
    const placementPath = String(row.placement_path ?? '').trim();
    const placementChain =
      placementPath && CANONICAL_STATEMENTS.has(placementPath.split('.')[0])
        ? placementPath.split('.').filter(Boolean)
        : null;
    let headerChain: string[];
    if (placementChain) {
      headerChain = placementChain;
    } else if (suggestionChain) {
      headerChain = suggestionChain;
    } else if (rawParts.length >= 2) {
      const docChain = rawParts.slice(0, -1);
      headerChain =
        CANONICAL_STATEMENTS.has(sectionHint) && docChain[0] !== sectionHint
          ? [sectionHint, ...docChain.slice(1)]
          : docChain;
    } else if (sectionHint) {
      headerChain = [sectionHint];
    } else if (rawParts.length === 1) {
      headerChain = rawParts;
    } else {
      headerChain = [];
    }
    const parentPath = headerChain.join('.');

    // Walk/create the source-header buckets down to the parent of the leaf.
    let cursor: Record<string, unknown> = tree;
    let walked = '';
    for (const part of headerChain) {
      walked = walked ? `${walked}.${part}` : part;
      const next = cursor[part];
      if (isPlainObject(next)) {
        cursor = next as Record<string, unknown>;
      } else if (next === undefined) {
        const created: Record<string, unknown> = {};
        cursor[part] = created;
        cursor = created;
      } else {
        // A leaf already occupies this header slot — can't nest under it; stop here.
        break;
      }
    }

    const leafKey = sourceLeafKey(row, takenFor(parentPath));
    cursor[leafKey] = { [UNMATCHED_LEAF_MARKER]: true, row, value: row.value } as UnmatchedSourceLeaf;
    injectedPaths.add(parentPath ? `${parentPath}.${leafKey}` : leafKey);
  }

  return { tree, injectedPaths };
}

function dottedGet(tree: unknown, path: string): unknown {
  if (!path) return tree;
  let cur: unknown = tree;
  for (const part of path.split('.')) {
    if (!isPlainObject(cur)) return undefined;
    cur = cur[part];
  }
  return cur;
}

/**
 * Returns true when a key at the given dotted path is not part of the canonical schema order.
 * Used to mark user-created / overflow fields with a subtle indicator in the tree.
 */
function isNonCanonicalKey(key: string, pathPrefix: string): boolean {
  const order = SCHEMA_KEY_ORDER[pathPrefix];
  if (!order) return false;
  return !order.includes(key);
}

const DUPLICATE_SUMMARY_LEAF_PATHS = new Set(['balance_sheet.assets.total_assets']);

function isDuplicateSummaryLeafPath(path: string): boolean {
  return DUPLICATE_SUMMARY_LEAF_PATHS.has(path);
}

/**
 * Canonical field set mirroring AUDIT_FINANCIALS_SCHEMA_FALLBACK (src/llm/prompts.py).
 *
 * NOTE: field *ordering* is now owned by the backend — `finalize_audit_financials_extracted`
 * returns the tree already in canonical schema order, and we render in received order. This
 * map is used ONLY to flag non-canonical (document-specific / overflow) keys with a subtle
 * marker (see `isNonCanonicalKey`); it no longer drives layout, so a stale entry here is at
 * worst a wrong cosmetic dot, never a wrong order.
 */
const SCHEMA_KEY_ORDER: Readonly<Record<string, readonly string[]>> = {
  '': ['profit_and_loss', 'balance_sheet', 'cash_flow_statement'],
  'profit_and_loss': [
    'revenue', 'expenses',
    'profit_loss_before_exceptional_items_or_tax', 'exceptional_items',
    'profit_loss_before_tax', 'ebitda', 'tax_expense',
    'profit_loss_for_the_period_of_continuing_operation',
    'profit_loss_from_discontinued_operations',
    'tax_expense_for_discontinued_operation',
    'profit_loss_from_discontinued_operations_after_tax',
    'profit_loss_for_the_period',
    'other_comprehensive_income',
    'total_comprehensive_income_for_the_period',
    'other',
  ],
  'profit_and_loss.revenue': ['revenue_from_operations', 'other_income', 'total_income', 'other'],
  'profit_and_loss.expenses': [
    'cost_of_material_consumed', 'purchase_of_stock_in_trade',
    'changes_in_inventories_of_finished_goods_stock_in_trade_and_work_in_progress',
    'employee_benefit_expense', 'finance_costs',
    'depreciation_and_amortization_expense', 'other_expenses', 'total_expenses', 'other',
  ],
  'profit_and_loss.tax_expense': ['current_tax', 'deferred_tax', 'total_tax_expense', 'other'],
  'profit_and_loss.other_comprehensive_income': [
    'a_items_that_will_not_be_reclassified_to_profit_or_loss',
    'a_income_tax_relating_to_items_that_will_not_be_reclassified_to_profit_or_loss',
    'b_items_that_will_be_reclassified_to_profit_or_loss',
    'b_income_tax_relating_to_items_that_will_be_reclassified_to_profit_or_loss',
    'total_oci', 'other',
  ],
  'balance_sheet': ['assets', 'equity', 'liabilities'],
  'balance_sheet.assets': ['non_current_assets', 'current_assets', 'total_assets', 'other'],
  'balance_sheet.assets.non_current_assets': [
    'property_plant_and_equipment', 'capital_work_in_progress', 'investment_property',
    'goodwill', 'other_intangible_assets', 'intangible_assets_under_development',
    'biological_assets_other_than_bearer_plants', 'financial_assets',
    'deferred_tax_assets_net', 'other_non_current_assets', 'total_non_current_assets', 'other',
  ],
  'balance_sheet.assets.non_current_assets.financial_assets': [
    'investments', 'trade_receivables', 'loans', 'others', 'total_financial_assets', 'other',
  ],
  'balance_sheet.assets.current_assets': [
    'inventories', 'financial_assets', 'current_tax_assets_net',
    'other_current_assets', 'total_current_assets', 'other',
  ],
  'balance_sheet.assets.current_assets.financial_assets': [
    'investments', 'trade_receivables', 'cash_and_cash_equivalents',
    'bank_balances_other_than_cash_and_cash_equivalents',
    'loans', 'others', 'total_financial_assets', 'other',
  ],
  'balance_sheet.equity': [
    'equity_share_capital', 'preference_share_capital', 'other_equity', 'total_equity', 'other',
  ],
  'balance_sheet.liabilities': [
    'non_current_liabilities', 'current_liabilities',
    'total_liabilities', 'total_equity_and_liabilities', 'other',
  ],
  'balance_sheet.liabilities.non_current_liabilities': [
    'financial_liabilities', 'provisions', 'deferred_tax_liabilities_net',
    'other_non_current_liabilities', 'total_non_current_liabilities', 'other',
  ],
  'balance_sheet.liabilities.non_current_liabilities.financial_liabilities': [
    'borrowings', 'lease_liabilities', 'trade_payables',
    'other_financial_liabilities', 'total_financial_liabilities', 'other',
  ],
  'balance_sheet.liabilities.non_current_liabilities.financial_liabilities.trade_payables': [
    'dues_of_micro_enterprises_and_small_enterprises',
    'dues_of_creditors_other_than_micro_enterprises_and_small_enterprises',
    'total_trade_payables',
  ],
  'balance_sheet.liabilities.current_liabilities': [
    'financial_liabilities', 'other_current_liabilities', 'provisions',
    'current_tax_liabilities_net', 'total_current_liabilities', 'other',
  ],
  'balance_sheet.liabilities.current_liabilities.financial_liabilities': [
    'borrowings', 'lease_liabilities', 'trade_payables',
    'other_financial_liabilities', 'total_financial_liabilities', 'other',
  ],
  'balance_sheet.liabilities.current_liabilities.financial_liabilities.trade_payables': [
    'dues_of_micro_enterprises_and_small_enterprises',
    'dues_of_creditors_other_than_micro_enterprises_and_small_enterprises',
    'total_trade_payables',
  ],
  'cash_flow_statement': [
    'cash_flows_from_operating_activities',
    'cash_flows_from_investing_activities',
    'cash_flows_from_financing_activities',
    'effect_of_exchange_rate_changes_on_cash_cash_equivalents_and_restricted_cash',
    'cash_cash_equivalents_and_restricted_cash',
    'other',
  ],
  'cash_flow_statement.cash_flows_from_operating_activities': [
    // Ind-AS indirect method (listed first — matches schema order)
    'profit_before_tax', 'adjustments', 'operating_profit_before_working_capital_changes',
    'changes_in_working_capital', 'cash_generated_from_operations',
    'income_taxes_paid', 'net_cash_from_operating_activities',
    // US-GAAP style
    'net_income', 'adjustments_to_reconcile_net_income',
    'changes_in_operating_assets_and_liabilities',
    'net_cash_provided_by_used_in_operating_activities',
    'other',
  ],
  'cash_flow_statement.cash_flows_from_operating_activities.adjustments': [
    'depreciation_and_amortization', 'finance_costs', 'interest_income',
    'loss_gain_on_sale_of_assets', 'provision_for_doubtful_debts',
    'unrealized_foreign_exchange_loss_gain', 'share_based_payment_expense', 'other',
  ],
  'cash_flow_statement.cash_flows_from_operating_activities.changes_in_working_capital': [
    'decrease_increase_in_trade_receivables', 'decrease_increase_in_inventories',
    'decrease_increase_in_other_current_assets', 'increase_decrease_in_trade_payables',
    'increase_decrease_in_other_current_liabilities', 'increase_decrease_in_provisions', 'other',
  ],
  'cash_flow_statement.cash_flows_from_operating_activities.adjustments_to_reconcile_net_income': [
    'accretion_amortization_of_discount_premium_on_issued_debt_securities',
    'gain_loss_on_extinguishment_of_debt',
    'depreciation_and_amortization',
    'amortization_of_debt_issue_costs',
    'share_based_incentive_compensation',
    'impairment_of_assets',
    'provision_for_bad_debt_expense',
    'inventory_obsolescence_impairment',
    'deferred_taxes',
    'noncash_provisions_for_exit_costs',
    'loss_gain_on_disposal_of_property_and_equipment',
    'income_loss_from_equity_method_investments_net_of_dividends_received',
    'foreign_currency_transactions',
    'other',
  ],
  'cash_flow_statement.cash_flows_from_operating_activities.changes_in_operating_assets_and_liabilities': [
    'decrease_increase_in_trade_receivables',
    'cash_received_on_sale_of_accounts_receivable',
    'decrease_increase_in_inventories',
    'decrease_increase_in_other_assets_net',
    'increase_decrease_in_operating_accounts_payable',
    'increase_decrease_in_accrued_liabilities',
    'increase_decrease_in_income_taxes_payable',
    'increase_decrease_in_other_liabilities_net',
    'other',
  ],
  'cash_flow_statement.cash_flows_from_investing_activities': [
    'acquisition_sale_of_equity_securities',
    'acquisition_proceeds_from_sale_of_property_plant_and_equipment',
    'acquisition_sale_of_a_business_net_of_cash_and_cash_equivalents_acquired_or_sold',
    'impact_to_cash_resulting_from_initial_consolidation_deconsolidation',
    'contributions_and_advances_to_joint_ventures',
    'subsequent_collections_of_receivables_sold_and_reacquired',
    'net_cash_provided_by_used_in_investing_activities',
    'other',
  ],
  'cash_flow_statement.cash_flows_from_financing_activities': [
    'bank_overdrafts', 'payment_of_contingent_consideration',
    'proceeds_from_debt', 'repayments_of_debt', 'payments_of_debt_issue_costs',
    'dividends_paid', 'net_payments_of_short_term_borrowings',
    'repurchases_of_equity_securities',
    'acquisition_of_common_stock_for_tax_withholding_obligations',
    'distributions_to_noncontrolling_interests',
    'principal_payments_under_capital_lease_obligations',
    'net_activity_from_derivatives_with_an_other_than_insignificant_financing_element',
    'net_cash_provided_by_used_in_financing_activities',
    'other',
  ],
  'cash_flow_statement.cash_cash_equivalents_and_restricted_cash': [
    'net_change_during_the_period', 'balance_beginning_of_period', 'balance_end_of_period', 'other',
  ],
};

/** A node is renderable if it has a leaf that survives the hide rules
 *  (null leaves, hidden total rows). A numeric 0 renders. `other` catch-all buckets are now
 *  rendered in-context (empty ones still drop out below), so they count toward renderability. */
function hasRenderableDescendant(node: unknown, pathPrefix: string): boolean {
  if (node === null || node === undefined) return false;
  // Source-position lines (increment 3) are always renderable leaves, even with a 0 / null value.
  if (isUnmatchedSourceLeaf(node)) return true;
  if (Array.isArray(node)) return node.some((it, i) => hasRenderableDescendant(it, `${pathPrefix}[${i}]`));
  if (isPlainObject(node)) {
    return Object.entries(node).some(([k, v]) => {
      const path = pathPrefix ? `${pathPrefix}.${k}` : k;
      const complex = isPlainObject(v) || Array.isArray(v);
      if (!complex) return v !== null && v !== undefined && !isDuplicateSummaryLeafPath(path);
      return hasRenderableDescendant(v, path);
    });
  }
  return true;
}

/** Whether a child entry should be rendered (matches the filters in StructuredJson). */
function isRenderableEntry(k: string, v: unknown, pathPrefix: string): boolean {
  const path = pathPrefix ? `${pathPrefix}.${k}` : k;
  // EBITDA is derived — always surface it (even when empty) so the user sees it wasn't calculated.
  if (path === 'profit_and_loss.ebitda') return true;
  if (isUnmatchedSourceLeaf(v)) return true;
  const complex = isPlainObject(v) || Array.isArray(v);
  if (!complex) return v !== null && v !== undefined && !isDuplicateSummaryLeafPath(path);
  // Empty plain objects are user-created composite buckets — always show them so children can be added.
  if (isPlainObject(v) && Object.keys(v as object).length === 0) return true;
  return hasRenderableDescendant(v, path);
}

/** Collapse a chain of single-child objects into one row: returns the combined
 *  key chain (e.g. ["tax_expense","current_tax"]), the final value and its path.
 *  Lets the UI show "Tax Expense / Current Tax" on one line instead of a dropdown. */
function collapseSingleChild(entryKey: string, value: unknown, pathPrefix: string) {
  const keys = [entryKey];
  let curVal = value;
  let curPath = pathPrefix ? `${pathPrefix}.${entryKey}` : entryKey;
  while (isPlainObject(curVal) && !isUnmatchedSourceLeaf(curVal)) {
    const kids = Object.entries(curVal as Record<string, unknown>).filter(([ck, cv]) =>
      isRenderableEntry(ck, cv, curPath),
    );
    if (kids.length !== 1) break;
    const [ck, cv] = kids[0];
    // A single source-position child must stay a distinct inline row, not get folded into its
    // parent's "Parent / Child" label.
    if (isUnmatchedSourceLeaf(cv)) break;
    // Keep the innermost dict as an expandable bucket — only collapse through nested
    // single-child dict chains, never flatten a dict into its single scalar child. This
    // ensures a composite with one numeric child (e.g. a user-converted leaf) still renders
    // as a collapsible group rather than a plain leaf row.
    if (!isPlainObject(cv)) break;
    keys.push(ck);
    curPath = `${curPath}.${ck}`;
    curVal = cv;
  }
  return { keys, value: curVal, path: curPath };
}

/** How monetary amounts are shown in the extraction UI (stored values remain full base units). */
export type AmountDisplaySystem =
  | 'indian_compact'
  | 'indian_full'
  | 'western_compact'
  | 'western_full'
  | 'usd_millions'
  | 'inr_crores';


export const AMOUNT_DISPLAY_OPTIONS: ReadonlyArray<{ value: AmountDisplaySystem; label: string; description: string }> =
  [
    { value: 'indian_compact', label: 'Indian — Cr / L', description: 'Crores and lakhs (compact)' },
    { value: 'indian_full', label: 'Indian — full', description: 'All digits, en‑IN grouping' },
    { value: 'western_compact', label: 'US — M / B', description: 'Millions / billions / K (compact)' },
    { value: 'western_full', label: 'US — full', description: 'All digits, Western grouping' },
  ];

export function parseAmountDisplaySystem(raw: string | null | undefined): AmountDisplaySystem | null {
  if (raw === 'indian_compact' || raw === 'indian_full' || raw === 'western_compact' || raw === 'western_full') return raw;
  return null;
}

/** Derive the display system from the file's currency — USD → Mn, INR → Cr, other → full numbers. */
export function displaySystemForCurrency(currency: string | null | undefined): AmountDisplaySystem {
  const c = (currency ?? '').toUpperCase();
  if (c === 'USD') return 'usd_millions';
  if (c === 'INR') return 'inr_crores';
  return 'western_full';
}

export function formatAmountDisplay(
  value: number,
  currency: string | null | undefined,
  system: AmountDisplaySystem,
): string {
  if (!Number.isFinite(value)) return '—';
  const pref = currency ? `${currency} ` : '';
  const sign = value < 0 ? '-' : '';
  const abs = Math.abs(value);
  const withSign = (body: string) => `${sign}${pref}${body}`;

  switch (system) {
    case 'inr_crores': {
      if (abs === 0) return withSign('0 Cr');
      const x = abs / 1e7;
      return withSign(`${x.toLocaleString('en-IN', { minimumFractionDigits: 2, maximumFractionDigits: 2 })} Cr`);
    }
    case 'usd_millions': {
      if (abs === 0) return withSign('0 Mn');
      const x = abs / 1e6;
      return withSign(`${x.toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 })} Mn`);
    }
    case 'indian_full':
      return withSign(abs.toLocaleString('en-IN', { minimumFractionDigits: 0, maximumFractionDigits: 4 }));
    case 'western_full':
      return withSign(abs.toLocaleString('en-US', { minimumFractionDigits: 0, maximumFractionDigits: 4 }));
    case 'indian_compact': {
      if (abs === 0) return withSign('0');
      if (abs >= 1e7) {
        const x = abs / 1e7;
        return withSign(
          `${x.toLocaleString('en-IN', { minimumFractionDigits: 0, maximumFractionDigits: 2 })} Cr`,
        );
      }
      if (abs >= 1e5) {
        const x = abs / 1e5;
        return withSign(
          `${x.toLocaleString('en-IN', { minimumFractionDigits: 0, maximumFractionDigits: 2 })} L`,
        );
      }
      return withSign(abs.toLocaleString('en-IN', { minimumFractionDigits: 0, maximumFractionDigits: 2 }));
    }
    case 'western_compact': {
      if (abs === 0) return withSign('0');
      if (abs >= 1e9) {
        const x = abs / 1e9;
        return withSign(
          `${x.toLocaleString('en-US', { minimumFractionDigits: 0, maximumFractionDigits: 2 })} B`,
        );
      }
      if (abs >= 1e6) {
        const x = abs / 1e6;
        return withSign(
          `${x.toLocaleString('en-US', { minimumFractionDigits: 0, maximumFractionDigits: 2 })} M`,
        );
      }
      if (abs >= 1e3) {
        const x = abs / 1e3;
        return withSign(
          `${x.toLocaleString('en-US', { minimumFractionDigits: 0, maximumFractionDigits: 2 })} K`,
        );
      }
      return withSign(abs.toLocaleString('en-US', { minimumFractionDigits: 0, maximumFractionDigits: 2 }));
    }
    default: {
      return withSign(abs.toLocaleString('en-US', { minimumFractionDigits: 0, maximumFractionDigits: 4 }));
    }
  }
}

/**
 * Same scaling as {@link formatAmountDisplay} but WITHOUT the currency prefix or the Mn/Cr
 * suffix — used in the extracted tree where the unit is shown once in a caption above it.
 */
function formatAmountBare(value: number, system: AmountDisplaySystem): string {
  if (!Number.isFinite(value)) return '—';
  const sign = value < 0 ? '-' : '';
  const abs = Math.abs(value);
  // Show up to 3 decimal places (keep precision after FX conversion) but drop trailing zeros.
  switch (system) {
    case 'inr_crores':
      return `${sign}${(abs / 1e7).toLocaleString('en-IN', { minimumFractionDigits: 0, maximumFractionDigits: 3 })}`;
    case 'usd_millions':
      return `${sign}${(abs / 1e6).toLocaleString('en-US', { minimumFractionDigits: 0, maximumFractionDigits: 3 })}`;
    case 'indian_full':
      return `${sign}${abs.toLocaleString('en-IN', { minimumFractionDigits: 0, maximumFractionDigits: 3 })}`;
    case 'western_full':
      return `${sign}${abs.toLocaleString('en-US', { minimumFractionDigits: 0, maximumFractionDigits: 3 })}`;
    default:
      // Compact systems aren't used by the tree; keep their per-magnitude suffix, drop currency.
      return formatAmountDisplay(value, null, system);
  }
}

/** Unit shown once above the tree (caption). null → full numbers with no single denomination. */
function amountUnitLabel(system: AmountDisplaySystem): { word: string; suffix: string } | null {
  if (system === 'usd_millions') return { word: 'millions', suffix: 'Mn' };
  if (system === 'inr_crores') return { word: 'crores', suffix: 'Cr' };
  return null;
}

// Canonical roll-up slot keys that are subtotals/totals yet do NOT start with `total_`. Mirrors the
// backend `_BUCKET_TOTAL_KEYS` / `_INTERMEDIATE_SUBTOTAL_KEYS` (financial_audit_schema.py). Excluding
// these from a bucket's component sum is what stops a subtotal from being double-counted alongside
// the very lines it summarises.
//   * bucket-final → the container's OWN total (returned as the bucket value, like a `total_*`)
//   * intermediate → a running roll-up whose own total is a different, outer line (excluded only)
const BUCKET_TOTAL_KEYS = new Set<string>([
  'net_cash_from_operating_activities',
  'net_cash_provided_by_used_in_operating_activities',
  'net_cash_provided_by_used_in_investing_activities',
  'net_cash_provided_by_used_in_financing_activities',
]);
const INTERMEDIATE_SUBTOTAL_KEYS = new Set<string>([
  'gross_profit',
  'operating_profit_before_working_capital_changes',
  'cash_generated_from_operations',
  'net_operating_income',
]);

/** A canonical leaf key that is a subtotal/total → excluded from its parent's component sum. */
function isSubtotalKey(key: string): boolean {
  return key.startsWith('total_') || BUCKET_TOTAL_KEYS.has(key) || INTERMEDIATE_SUBTOTAL_KEYS.has(key);
}

// Conservative label match for an in-place source line that is itself a subtotal/total but carries
// no canonical key yet. Only strong, unambiguous roll-up phrasings (so a real component line is
// never wrongly dropped from the sum) — deliberately omits context-dependent phrases like
// "profit before tax" (a genuine component inside the indirect operating cash flow).
const SUBTOTAL_LABEL_RE =
  /\bsub[-\s]?totals?\b|\btotals?\b|net\s+cash\b[\s\S]*\bactivit|attributable\s+to\b[\s\S]*\b(owner|equity\s+holder|shareholder|member)|\bgross\s+profit\b/i;

/**
 * True when an in-place source line is itself a subtotal/total and so must NOT be summed with its
 * sibling components. Prefers the backend `is_total` flag and the suggested canonical slot; falls
 * back to a conservative label match so it still works before the backend flag is present.
 */
function isSubtotalSourceLeaf(leaf: UnmatchedSourceLeaf): boolean {
  const row = leaf.row;
  if (row.is_total === true) return true;
  const sug = readSuggestion(row);
  if (sug && isSubtotalKey(String(sug.key))) return true;
  const label = String(row.document_label ?? row.key ?? '');
  return SUBTOTAL_LABEL_RE.test(label);
}

function sumNumericLeaves(value: unknown): number | null {
  // Source-position lines (increment 3) carry their numeric value behind a sentinel so bucket
  // totals/reconciliation include them exactly as if they had been canonically mapped — UNLESS the
  // line is itself a subtotal/total, which would double-count it against its own components.
  if (isUnmatchedSourceLeaf(value)) {
    // Section-tagged rows (Tier 1/2, carrying ``placement_tier``) are display-only context shown
    // inside their section and are NEVER summed — a line enters a total only when a reviewer
    // explicitly attaches it to a canonical leaf. (Legacy in-place rows without a tier still count,
    // minus their own subtotals, preserving the original increment-3 behaviour.)
    if (value.row.placement_tier !== undefined) return null;
    if (isSubtotalSourceLeaf(value)) return null;
    return parseJsonNumericLeaf(value.value);
  }
  const direct = parseJsonNumericLeaf(value);
  if (direct != null) return direct;
  if (Array.isArray(value)) {
    let total = 0;
    let found = false;
    for (const item of value) {
      const n = sumNumericLeaves(item);
      if (typeof n === 'number') {
        total += n;
        found = true;
      }
    }
    return found ? total : null;
  }
  if (isPlainObject(value)) {
    const entries = Object.entries(value);
    // 1. An explicit reported `total_*` is the bucket's value (unchanged behaviour).
    for (const [k, v] of entries) {
      if (k.startsWith('total_')) {
        const t = parseJsonNumericLeaf(v);
        if (t != null) return t;
      }
    }
    // 2. Else a bucket-final subtotal (e.g. `net_cash_*_activities`) stands in as the bucket total.
    //    Take the outermost (last in canonical order) so a leading intermediate subtotal can't win.
    let bucketTotal: number | null = null;
    for (const [k, v] of entries) {
      if (BUCKET_TOTAL_KEYS.has(k)) {
        const t = parseJsonNumericLeaf(v);
        if (t != null) bucketTotal = t;
      }
    }
    if (bucketTotal != null) return bucketTotal;
    // 3. Else sum components, excluding any subtotal-keyed child (subtotal source leaves are
    //    excluded inside the recursive call above).
    let total = 0;
    let found = false;
    for (const [k, v] of entries) {
      if (isSubtotalKey(k)) continue;
      const n = sumNumericLeaves(v);
      if (typeof n === 'number') {
        total += n;
        found = true;
      }
    }
    return found ? total : null;
  }
  return null;
}

/**
 * Per-bucket reconciliation: does the sum of the (attached) children reach the reported `total_*`?
 * Returns null when there's no numeric `total_*` child or no other numeric children to compare —
 * i.e. when the question doesn't apply. `mismatch` is true only beyond a rounding tolerance.
 */
function reconcileBucket(
  value: unknown,
): { reportedTotal: number; childSum: number; gap: number; mismatch: boolean } | null {
  if (!isPlainObject(value)) return null;
  const entries = Object.entries(value);
  let reportedTotal: number | null = null;
  for (const [k, v] of entries) {
    if (k.startsWith('total_')) {
      const t = parseJsonNumericLeaf(v);
      if (t != null) {
        reportedTotal = t;
        break;
      }
    }
  }
  // No explicit total_*: a bucket-final subtotal (e.g. net_cash_*_activities) is the reported total.
  if (reportedTotal == null) {
    for (const [k, v] of entries) {
      if (BUCKET_TOTAL_KEYS.has(k)) {
        const t = parseJsonNumericLeaf(v);
        if (t != null) reportedTotal = t;
      }
    }
  }
  if (reportedTotal == null) return null;
  let childSum = 0;
  let found = false;
  for (const [k, v] of entries) {
    if (isSubtotalKey(k)) continue;
    const n = sumNumericLeaves(v);
    if (typeof n === 'number') {
      childSum += n;
      found = true;
    }
  }
  if (!found) return null;
  const gap = Math.abs(reportedTotal - childSum);
  // Combined tolerance: ignore rounding noise (0.5% of the reported total, floor of 1 raw unit).
  const tol = Math.max(1, Math.abs(reportedTotal) * 0.005);
  return { reportedTotal, childSum, gap, mismatch: gap > tol };
}

/**
 * True when a bucket's displayed figure is a DERIVED sum of its children with no reported
 * `total_*` in the source to check it against — so it can't be reconciled. Drives the grey
 * "not verified" marker. Pure and disjoint from reconcileBucket (which REQUIRES a `total_*`),
 * so buckets that already reconcile — amber or clean — are never affected.
 */
function isUnverifiedBucketSum(value: unknown): boolean {
  if (!isPlainObject(value)) return false;
  const entries = Object.entries(value);
  const hasReportedTotal = entries.some(
    ([k, v]) => (k.startsWith('total_') || BUCKET_TOTAL_KEYS.has(k)) && parseJsonNumericLeaf(v) != null,
  );
  if (hasReportedTotal) return false;
  // Only meaningful when at least one numeric child produces the derived sum.
  return entries.some(([k, v]) => !isSubtotalKey(k) && sumNumericLeaves(v) != null);
}

/**
 * Lets a deeply-nested bucket trigger the page's "jump to the unattached lines" action
 * (scroll + highlight) without threading a callback through every recursive render.
 */
const ReconcileContext = createContext<((path: string) => void) | null>(null);
// Configured-formula EBITDA breakdown (same shape + formula the /company discrepancy dashboard uses).
// Provided once around the financial tree so the EBITDA leaf can show the mapping total + per-term
// breakdown instead of the raw LLM-derived `profit_and_loss.ebitda` value. Null when not yet computed.
const EbitdaBreakdownContext = createContext<MappingBreakdown | null>(null);
// Per-path manual-edit markers for the financial tree, keyed by dotted path. Provided once around
// the tree so any leaf can flag itself as manually edited (amber + "edited manually" + justification).
const ManualEditsContext = createContext<Record<string, ManualEditMarker>>({});
// Per-leaf roll-up breakdown (audit_financials_field_components), keyed by canonical dotted path.
// Provided around the tree so a leaf built by combining several document lines can reveal its signed
// contributors inline (on the leaf row), not only in the "Combined fields" panel far below.
const FieldComponentsContext = createContext<Record<string, FieldComponent[]>>({});

/**
 * A source line that the canonical mapper left untagged, rendered **in place** inside the statement
 * tree at its original pass-1 position (increment 3). It looks like a normal leaf row — same indent,
 * same value column — but is visually marked "untagged" and carries inline Accept / Attach actions so
 * a reviewer can promote it to a canonical slot without leaving the statement. Its value still feeds
 * the parent bucket's reconciliation total, so the tree has no holes and nothing is forced to HITL.
 */
function SourcePositionRow({
  leaf,
  amountDisplaySystem,
  fxPreviewEnabled,
  fxPreviewRate,
  fxPreviewCurrency,
  path,
  onAccept,
  onAttach,
  onDismiss,
  onShowSourceRef,
  busyId,
}: {
  leaf: UnmatchedSourceLeaf;
  amountDisplaySystem: AmountDisplaySystem;
  fxPreviewEnabled: boolean;
  fxPreviewRate: number;
  fxPreviewCurrency: string | null;
  path: string;
  onAccept?: (row: Record<string, unknown>) => void;
  onAttach?: (row: Record<string, unknown>) => void;
  onDismiss?: (row: Record<string, unknown>) => void;
  onShowSourceRef?: (label: string, ref: SourceRef) => void;
  busyId?: string | null;
}) {
  const row = leaf.row;
  const uid = String(row.id ?? '');
  const label = String(row.document_label ?? row.key ?? uid ?? '');
  const num = parseJsonNumericLeaf(leaf.value);
  const showFx =
    num != null && fxPreviewEnabled && !!fxPreviewCurrency && fxPreviewRate !== 1 && isFinancialStatementValuePath(path);
  const displayNum = num != null ? (showFx ? num * fxPreviewRate : num) : null;
  const sug = readSuggestion(row);
  const conf = typeof sug?.confidence === 'number' ? sug.confidence : null;
  // A subtotal/total line is shown but excluded from its bucket total (it sums its own siblings).
  const isSubtotal = isSubtotalSourceLeaf(leaf);
  const busy = Boolean(busyId && busyId === uid);
  const srcRefRaw = row.source_ref;
  const srcRef =
    srcRefRaw && typeof srcRefRaw === 'object' && typeof (srcRefRaw as SourceRef).page === 'number'
      ? (srcRefRaw as SourceRef)
      : null;

  return (
    <div
      id={uid ? `uf-row-${uid}` : undefined}
      className="flex items-center justify-between gap-3 px-2 -mx-2 py-1 first:pt-0 rounded-md border-l-2 border-amber-300 bg-amber-50/40"
    >
      <div className="flex min-w-0 flex-1 items-center gap-2">
        <span className={TREE_ROW_CHEVRON_SLOT} aria-hidden />
        <span className="truncate text-[13px] font-normal text-gray-700" title={label}>
          {label || '—'}
        </span>
        <span
          className="shrink-0 rounded-full bg-amber-100 px-1.5 py-0.5 text-[10px] font-medium text-amber-700 select-none"
          title="Shown in its source position — not yet mapped to a canonical field"
        >
          untagged
        </span>
        {isSubtotal ? (
          <span
            className="shrink-0 rounded-full bg-slate-100 px-1.5 py-0.5 text-[10px] font-medium text-slate-600 select-none"
            title="This is a subtotal/total of the lines around it — shown for reference but excluded from the section total to avoid double-counting"
          >
            subtotal
          </span>
        ) : null}
        {sug ? (
          <span
            className="shrink-0 truncate text-[11px] text-gray-400"
            title={`Suggested: ${sug.full_path || `${sug.parent_path}.${sug.key}`}${
              conf != null ? ` (${Math.round(conf * 100)}%${sug.source === 'alias' ? ', learned' : ''})` : ''
            }`}
          >
            → {String(sug.key).replace(/_/g, ' ')}
            {conf != null ? ` ${Math.round(conf * 100)}%` : ''}
          </span>
        ) : null}
        {srcRef && onShowSourceRef ? (
          <button
            type="button"
            className="shrink-0 p-0.5 rounded hover:bg-amber-100 text-amber-400 hover:text-amber-600 transition-colors"
            title="View highlighted source in PDF"
            onClick={() => onShowSourceRef(label, srcRef)}
          >
            <ScanSearch className="h-3.5 w-3.5" />
          </button>
        ) : null}
      </div>
      <div className="flex shrink-0 items-center gap-2">
        <div className="text-[13px] font-normal font-mono text-gray-900 text-right">
          {displayNum != null ? formatAmountBare(displayNum, amountDisplaySystem) : '—'}
        </div>
        {sug && onAccept ? (
          <button
            type="button"
            className="inline-flex items-center gap-1 rounded border border-emerald-200 bg-emerald-50 px-1.5 py-0.5 text-[11px] text-emerald-700 hover:bg-emerald-100 disabled:opacity-50 transition-colors"
            title={`Map to ${sug.full_path || `${sug.parent_path}.${sug.key}`}`}
            onClick={() => onAccept(row)}
            disabled={busy}
          >
            {busy ? <Loader2 className="h-3 w-3 animate-spin" /> : <Check className="h-3 w-3" />}
            Accept
          </button>
        ) : null}
        {onAttach ? (
          <button
            type="button"
            className="inline-flex items-center gap-1 rounded border border-gray-200 bg-white px-1.5 py-0.5 text-[11px] text-gray-600 hover:bg-gray-50 disabled:opacity-50 transition-colors"
            title="Attach to a canonical field or create a new one"
            onClick={() => onAttach(row)}
            disabled={busy}
          >
            <Link2 className="h-3 w-3" />
            {sug ? 'Change' : 'Attach'}
          </button>
        ) : null}
        {onDismiss ? (
          <button
            type="button"
            className="p-0.5 rounded hover:bg-red-50 text-gray-400 hover:text-red-600 transition-colors disabled:opacity-50"
            title="Remove from tree — moves to Unidentified fields below (recoverable)"
            onClick={() => onDismiss(row)}
            disabled={busy}
          >
            <Trash2 className="h-3.5 w-3.5" />
          </button>
        ) : null}
      </div>
    </div>
  );
}

/**
 * Expandable bucket row. Chevron rotation uses React state — Tailwind v3.4 has `open:` but not `group-open:`,
 * so `group-open:rotate-90` never compiled and the icon stayed static.
 */
function StructuredJsonBucket({
  entryKey,
  displayLabel,
  value: bucketValue,
  depth,
  currency,
  amountDisplaySystem,
  pathPrefix,
  editable,
  editingPath,
  editingDraft,
  onStartEdit,
  onDraftChange,
  onSaveEdit,
  onCancelEdit,
  editBusy,
  sourceRefs,
  onShowSource,
  onDetach,
  fxPreviewEnabled = false,
  fxPreviewRate = 1,
  fxPreviewCurrency = null,
  onAcceptUnmatched,
  onAttachUnmatched,
  onDismissUnmatched,
  onShowSourceRefRow,
  unmatchedBusyId = null,
  onRetag,
  onAddChild,
}: {
  entryKey: string;
  displayLabel?: string;
  value: unknown;
  depth: number;
  currency: string | null;
  amountDisplaySystem: AmountDisplaySystem;
  pathPrefix: string;
  editable: boolean;
  editingPath: string | null;
  editingDraft: string;
  onStartEdit?: (path: string, current: unknown) => void;
  onDraftChange?: (next: string) => void;
  onSaveEdit?: () => void;
  onCancelEdit?: () => void;
  editBusy: boolean;
  sourceRefs?: Record<string, SourceRef> | null;
  onShowSource?: (path: string) => void;
  onDetach?: (path: string) => void;
  fxPreviewEnabled?: boolean;
  fxPreviewRate?: number;
  fxPreviewCurrency?: string | null;
  onAcceptUnmatched?: (row: Record<string, unknown>) => void;
  onAttachUnmatched?: (row: Record<string, unknown>) => void;
  onDismissUnmatched?: (row: Record<string, unknown>) => void;
  onShowSourceRefRow?: (label: string, ref: SourceRef) => void;
  unmatchedBusyId?: string | null;
  onRetag?: (path: string) => void;
  onAddChild?: (parentPath: string) => void;
}) {
  const detailsRef = useRef<HTMLDetailsElement>(null);
  const [isOpen, setIsOpen] = useState(false);
  const onReconcileClick = useContext(ReconcileContext);

  useLayoutEffect(() => {
    const el = detailsRef.current;
    if (el) {
      el.open = true;
      setIsOpen(true);
    }
  }, []);

  const path = pathPrefix ? `${pathPrefix}.${entryKey}` : entryKey;
  const bucketTotal = sumNumericLeaves(bucketValue);
  const recon = reconcileBucket(bucketValue);
  // Hide totals for the three main statement headings — summing their children is meaningless.
  const TOP_LEVEL_STATEMENTS = ['profit_and_loss', 'balance_sheet', 'cash_flow_statement'];
  const suppressTotal = TOP_LEVEL_STATEMENTS.some(s => path === s);
  const showFxPrev = fxPreviewEnabled && fxPreviewCurrency && fxPreviewRate !== 1 && isFinancialStatementValuePath(path);
  const displayBucketTotal =
    typeof bucketTotal === 'number' && showFxPrev ? bucketTotal * fxPreviewRate : bucketTotal;
  // Show the gap against the same scale as the reported total.
  const displayChildSum = recon && showFxPrev ? recon.childSum * fxPreviewRate : recon?.childSum;
  const showReconWarning = Boolean(recon?.mismatch && !suppressTotal);

  return (
    <details
      ref={detailsRef}
      className="py-1.5 first:pt-0 [&:last-of-type]:pb-0"
      onToggle={(e) => setIsOpen(e.currentTarget.open)}
    >
      <summary
        className="group flex cursor-pointer select-none list-none items-center justify-between gap-3 rounded-md px-2 py-1.5 -mx-2 text-sm font-normal text-gray-900 outline-none transition-colors hover:bg-gray-50 active:bg-gray-100/80 [&::-webkit-details-marker]:hidden focus-visible:ring-2 focus-visible:ring-gray-300 focus-visible:ring-offset-2"
        aria-expanded={isOpen}
      >
        <span className="flex min-w-0 flex-1 items-center gap-2">
          <ChevronRight
            aria-hidden
            className={`h-4 w-4 shrink-0 text-gray-600 transition-transform duration-200 ease-out ${
              isOpen ? 'rotate-90' : 'rotate-0'
            }`}
          />
          <span className="truncate text-[13px]">{displayLabel ?? formatKey(entryKey)}</span>
          {isNonCanonicalKey(entryKey, pathPrefix) && (
            <span className="shrink-0 text-[10px] text-gray-400 select-none" title="Non-canonical field">·</span>
          )}
          {editable && onAddChild ? (
            <button
              type="button"
              className="shrink-0 opacity-0 group-hover:opacity-100 p-0.5 rounded hover:bg-gray-200 text-gray-400 hover:text-gray-700 transition-opacity"
              title="Add child field"
              onClick={(e) => { e.preventDefault(); e.stopPropagation(); onAddChild(path); }}
            >
              <Plus className="h-3 w-3" />
            </button>
          ) : null}
        </span>
        {typeof displayBucketTotal === 'number' && !suppressTotal ? (
          <span className="flex shrink-0 items-center gap-1.5">
            {isUnverifiedBucketSum(bucketValue) ? (
              <TooltipProvider delayDuration={0}>
                <Tooltip>
                  <TooltipTrigger asChild>
                    <span
                      className="inline-flex items-center text-gray-400 cursor-help"
                      aria-label="No reported total — this section's sum could not be reconciled"
                    >
                      <Info className="h-3.5 w-3.5" />
                    </span>
                  </TooltipTrigger>
                  <TooltipContent side="top" align="end" className="max-w-xs">
                    Not reconciled — no reported total for this section in the source.
                  </TooltipContent>
                </Tooltip>
              </TooltipProvider>
            ) : null}
            {showReconWarning && typeof displayChildSum === 'number' ? (
              <>
                <button
                  type="button"
                  onClick={(e) => {
                    e.preventDefault();
                    e.stopPropagation();
                    onReconcileClick?.(path);
                  }}
                  className="inline-flex items-center text-amber-500 hover:text-amber-600 focus:outline-none focus-visible:ring-2 focus-visible:ring-amber-300 rounded"
                  aria-label="Children don't reconcile to the reported total — find unattached lines"
                  title={
                    `Children don't reconcile to the reported total.\n` +
                    `Reported: ${formatAmountBare(displayBucketTotal, amountDisplaySystem)}\n` +
                    `Attached children: ${formatAmountBare(displayChildSum, amountDisplaySystem)}\n` +
                    `Gap: ${formatAmountBare(Math.abs(displayBucketTotal - displayChildSum), amountDisplaySystem)}\n` +
                    `Some lines may be unattached — click to find them.`
                  }
                >
                  <AlertTriangle className="h-3.5 w-3.5" />
                </button>
                <span
                  className="text-[11px] font-normal font-mono text-amber-600"
                  title="Sum of the children currently attached"
                >
                  Σ {formatAmountBare(displayChildSum, amountDisplaySystem)}
                </span>
              </>
            ) : null}
            <span className="text-[13px] font-normal font-mono text-gray-500">
              {formatAmountBare(displayBucketTotal, amountDisplaySystem)}
            </span>
          </span>
        ) : null}
      </summary>
      <div className="pb-0.5 pt-1">
        <StructuredJson
          value={bucketValue}
          depth={depth + 1}
          currency={currency}
          amountDisplaySystem={amountDisplaySystem}
          pathPrefix={path}
          editable={editable}
          editingPath={editingPath}
          editingDraft={editingDraft}
          onStartEdit={onStartEdit}
          onDraftChange={onDraftChange}
          onSaveEdit={onSaveEdit}
          onCancelEdit={onCancelEdit}
          editBusy={editBusy}
          sourceRefs={sourceRefs}
          onShowSource={onShowSource}
          onDetach={onDetach}
          fxPreviewEnabled={fxPreviewEnabled}
          fxPreviewRate={fxPreviewRate}
          fxPreviewCurrency={fxPreviewCurrency}
          onAcceptUnmatched={onAcceptUnmatched}
          onAttachUnmatched={onAttachUnmatched}
          onDismissUnmatched={onDismissUnmatched}
          onShowSourceRefRow={onShowSourceRefRow}
          unmatchedBusyId={unmatchedBusyId}
          onRetag={onRetag}
          onAddChild={onAddChild}
        />
      </div>
    </details>
  );
}

function StructuredJson({
  value,
  depth = 0,
  currency = null,
  amountDisplaySystem = 'western_full',
  pathPrefix = '',
  editable = false,
  editingPath = null,
  editingDraft = '',
  onStartEdit,
  onDraftChange,
  onSaveEdit,
  onCancelEdit,
  editBusy = false,
  sourceRefs,
  onShowSource,
  onDetach,
  fxPreviewEnabled = false,
  fxPreviewRate = 1,
  fxPreviewCurrency = null,
  onAcceptUnmatched,
  onAttachUnmatched,
  onDismissUnmatched,
  onShowSourceRefRow,
  unmatchedBusyId = null,
  onRetag,
  onAddChild,
}: {
  value: unknown;
  depth?: number;
  currency?: string | null;
  amountDisplaySystem?: AmountDisplaySystem;
  pathPrefix?: string;
  editable?: boolean;
  editingPath?: string | null;
  editingDraft?: string;
  onStartEdit?: (path: string, current: unknown) => void;
  onDraftChange?: (next: string) => void;
  onSaveEdit?: () => void;
  onCancelEdit?: () => void;
  editBusy?: boolean;
  sourceRefs?: Record<string, SourceRef> | null;
  onShowSource?: (path: string) => void;
  onDetach?: (path: string) => void;
  fxPreviewEnabled?: boolean;
  fxPreviewRate?: number;
  fxPreviewCurrency?: string | null;
  // Increment 3: act on a source-position (unmatched) line rendered inline in the tree.
  onAcceptUnmatched?: (row: Record<string, unknown>) => void;
  onAttachUnmatched?: (row: Record<string, unknown>) => void;
  onDismissUnmatched?: (row: Record<string, unknown>) => void;
  onShowSourceRefRow?: (label: string, ref: SourceRef) => void;
  unmatchedBusyId?: string | null;
  onRetag?: (path: string) => void;
  onAddChild?: (parentPath: string) => void;
}) {
  // Configured-formula EBITDA breakdown (from settings mapping), used to source the EBITDA leaf's
  // value + breakdown tooltip. Hook must run before any early return.
  const ebitdaBreakdown = useContext(EbitdaBreakdownContext);
  const manualEdits = useContext(ManualEditsContext);
  // Per-leaf roll-up components, so a combined leaf can reveal its contributors on the row itself.
  const leafComponentsMap = useContext(FieldComponentsContext);
  if (value == null) return <span className="text-sm font-normal text-gray-400">—</span>;

  if (Array.isArray(value)) {
    if (value.length === 0) return <span className="text-sm font-normal text-gray-400">[]</span>;
    return (
      <div
        className="flex flex-col divide-y divide-gray-100"
        style={{ paddingLeft: depth > 0 ? TREE_LEVEL_INDENT_PX : 0 }}
      >
        {value.map((item, idx) => (
          <div key={idx} className="border-l-2 border-gray-200 py-1.5 pl-4 first:pt-0 last:pb-0">
            <StructuredJson
              value={item}
              depth={depth + 1}
              currency={currency}
              amountDisplaySystem={amountDisplaySystem}
              pathPrefix={`${pathPrefix}[${idx}]`}
              editable={editable}
              editingPath={editingPath}
              editingDraft={editingDraft}
              onStartEdit={onStartEdit}
              onDraftChange={onDraftChange}
              onSaveEdit={onSaveEdit}
              onCancelEdit={onCancelEdit}
              editBusy={editBusy}
              sourceRefs={sourceRefs}
              onShowSource={onShowSource}
              onDetach={onDetach}
              fxPreviewEnabled={fxPreviewEnabled}
              fxPreviewRate={fxPreviewRate}
              fxPreviewCurrency={fxPreviewCurrency}
              onAcceptUnmatched={onAcceptUnmatched}
              onAttachUnmatched={onAttachUnmatched}
              onDismissUnmatched={onDismissUnmatched}
              onShowSourceRefRow={onShowSourceRefRow}
              unmatchedBusyId={unmatchedBusyId}
              onRetag={onRetag}
              onAddChild={onAddChild}
            />
          </div>
        ))}
      </div>
    );
  }

  if (isPlainObject(value)) {
    // Order is owned by the backend (canonical schema order); render as received.
    // Only render entries that survive the hide rules (null leaves, hidden totals, "other").
    const entries = Object.entries(value).filter(([k, v]) => isRenderableEntry(k, v, pathPrefix));
    // EBITDA may be omitted entirely (not just null) when its components weren't found. Always show
    // it in the P&L so the user can see it wasn't calculated — inserted just above Tax Expense.
    if (pathPrefix === 'profit_and_loss' && !entries.some(([k]) => k === 'ebitda')) {
      const taxIdx = entries.findIndex(([k]) => k === 'tax_expense');
      const ebitdaEntry: [string, unknown] = ['ebitda', null];
      if (taxIdx >= 0) entries.splice(taxIdx, 0, ebitdaEntry);
      else entries.push(ebitdaEntry);
    }
    // Index of the first section-tagged unplaced row (Tier 1/2, carrying ``placement_tier``). These
    // are rendered below this bucket's canonical rows; we drop a labelled divider before the first
    // one so the reviewer sees clearly where the "located here but not yet placed / not summed" lines
    // begin. Legacy in-place rows (no tier) keep counting and get no divider.
    const firstUnplacedIdx = entries.findIndex(
      ([, v]) => isUnmatchedSourceLeaf(v) && (v as UnmatchedSourceLeaf).row.placement_tier !== undefined,
    );
    return (
      <div
        className="flex flex-col divide-y divide-gray-100"
        style={{ paddingLeft: depth > 0 ? TREE_LEVEL_INDENT_PX : 0 }}
      >
        {entries.map(([k, v], idx) => {
          const entryPath = pathPrefix ? `${pathPrefix}.${k}` : k;
          // Source-position line (increment 3): an unmatched source line spliced back into the tree
          // at its raw_path. Render it inline as an "untagged-but-visible" row with inline
          // Accept/Attach, instead of treating its sentinel wrapper as an expandable bucket.
          if (isUnmatchedSourceLeaf(v)) {
            const row = (
              <SourcePositionRow
                key={k}
                leaf={v}
                amountDisplaySystem={amountDisplaySystem}
                fxPreviewEnabled={fxPreviewEnabled}
                fxPreviewRate={fxPreviewRate}
                fxPreviewCurrency={fxPreviewCurrency}
                path={entryPath}
                onAccept={onAcceptUnmatched}
                onAttach={onAttachUnmatched}
                onDismiss={onDismissUnmatched}
                onShowSourceRef={onShowSourceRefRow}
                busyId={unmatchedBusyId}
              />
            );
            // Labelled divider before the first tagged unplaced row in this bucket.
            if (idx === firstUnplacedIdx) {
              return (
                <Fragment key={k}>
                  <div className="pt-2 text-[11px] font-medium uppercase tracking-wide text-amber-700/70">
                    Unplaced lines · located in this section, not included in the total
                  </div>
                  {row}
                </Fragment>
              );
            }
            return row;
          }
          // EBITDA is a single derived figure — never an expandable bucket. Collapse any object
          // value to its numeric total (or null) and render it as one leaf ("Not calculated" empty).
          const isEbitdaEntry = entryPath === 'profit_and_loss.ebitda';
          const complex = !isEbitdaEntry && (isPlainObject(v) || Array.isArray(v));

          // EBITDA reflects the configured metric mapping — the SAME formula + breakdown the /company
          // discrepancy dashboard uses — instead of the raw LLM-derived value.
          const ebitdaBd = isEbitdaEntry ? ebitdaBreakdown : null;
          const ebitdaBdHasTerms =
            !!ebitdaBd && Array.isArray(ebitdaBd.terms) && ebitdaBd.terms.length > 0;
          const ebitdaBdHasOcr = ebitdaBdHasTerms && ebitdaBd!.terms.some((t) => t.source === 'ocr');

          // Collapse single-child chains into one "Parent / Child" line instead of a dropdown.
          let label = formatKey(k);
          // When a mapping is configured, show its total when terms resolved from OCR, else
          // "Not calculated" (leafVal=null) — keeping the leaf consistent with the breakdown tooltip.
          // Only when no EBITDA mapping is configured at all do we fall back to the raw LLM value.
          let leafVal: unknown = isEbitdaEntry
            ? ebitdaBdHasTerms
              ? ebitdaBdHasOcr
                ? ebitdaBd!.total
                : null
              : sumNumericLeaves(v) ?? null
            : v;
          let path = entryPath;
          if (complex) {
            const collapsed = collapseSingleChild(k, v, pathPrefix);
            label = collapsed.keys.map(formatKey).join(' / ');
            const finalComplex = isPlainObject(collapsed.value) || Array.isArray(collapsed.value);
            if (finalComplex) {
              const lastKey = collapsed.keys[collapsed.keys.length - 1];
              const parentPath = collapsed.path.includes('.')
                ? collapsed.path.slice(0, collapsed.path.lastIndexOf('.'))
                : '';
              return (
                <StructuredJsonBucket
                  key={k}
                  entryKey={lastKey}
                  displayLabel={label}
                  value={collapsed.value}
                  depth={depth}
                  currency={currency}
                  amountDisplaySystem={amountDisplaySystem}
                  pathPrefix={parentPath}
                  editable={Boolean(editable)}
                  editingPath={editingPath ?? null}
                  editingDraft={editingDraft}
                  onStartEdit={onStartEdit}
                  onDraftChange={onDraftChange}
                  onSaveEdit={onSaveEdit}
                  onCancelEdit={onCancelEdit}
                  editBusy={editBusy}
                  sourceRefs={sourceRefs}
                  onShowSource={onShowSource}
                  onDetach={onDetach}
                  fxPreviewEnabled={fxPreviewEnabled}
                  fxPreviewRate={fxPreviewRate}
                  fxPreviewCurrency={fxPreviewCurrency}
                  onAcceptUnmatched={onAcceptUnmatched}
                  onAttachUnmatched={onAttachUnmatched}
                  onDismissUnmatched={onDismissUnmatched}
                  onShowSourceRefRow={onShowSourceRefRow}
                  unmatchedBusyId={unmatchedBusyId}
                  onRetag={onRetag}
                  onAddChild={onAddChild}
                />
              );
            }
            // Chain collapsed all the way to a single leaf.
            leafVal = collapsed.value;
            path = collapsed.path;
          }

          const markerKey = path.includes('.') ? path.slice(path.lastIndexOf('.') + 1) : path;
          const markerParent = path.includes('.') ? path.slice(0, path.lastIndexOf('.')) : '';
          // Manual-edit marker for this leaf (amber value + "edited manually" badge + justification).
          const editMarker = manualEdits?.[path];
          const isManuallyEdited = !!editMarker && editMarker.edited !== false;
          // Signed contributors when this leaf is a roll-up of several document lines (mapped via the
          // sum/total conflict flow). Drives the inline "Σ n" chip + breakdown tooltip below.
          const leafComps = leafComponentsMap[path];
          const hasLeafComps = Array.isArray(leafComps) && leafComps.length > 0;
          // An ``other_*`` catch-all canonical slot (e.g. other_current_liabilities) — flag it so the
          // reviewer can see what the system auto-classified as "other". Excludes the real OCI section.
          const isOtherCatchAll = markerKey.startsWith('other_') && markerKey !== 'other_comprehensive_income';
          // EBITDA is a derived/calculated figure (not extracted from the document) — flag it visually.
          const isDerivedEbitda = markerParent === 'profit_and_loss' && markerKey === 'ebitda';
          const leafNum = parseJsonNumericLeaf(leafVal);
          // Derived EBITDA with no numeric value → show "Not calculated" rather than an empty dash.
          const ebitdaNotCalculated = isDerivedEbitda && leafNum == null;
          const showFxLeaf =
            leafNum != null &&
            fxPreviewEnabled &&
            !!fxPreviewCurrency &&
            fxPreviewRate !== 1 &&
            isFinancialStatementValuePath(path);
          const displayLeafNum =
            leafNum != null ? (showFxLeaf ? leafNum * fxPreviewRate : leafNum) : null;
          const canEdit = editable && (leafNum != null || leafVal == null) && !path.includes('[');
          // Detach (move to unmatched): any value-bearing leaf except the derived EBITDA and array
          // items. The manual fallback for a duplicate the auto-dedupe missed.
          const canDetach =
            editable && leafNum != null && !isDerivedEbitda && !path.includes('[') && !!onDetach;
          const canRetag =
            editable && leafNum != null && !isDerivedEbitda && !!onRetag;
          const inEdit = canEdit && editingPath === path;
          const _srcRef = sourceRefs?.[path];
          // Show the source icon only for a located page: `verified`, or legacy records with no
          // `source` (kept for back-compat; the backfill upgrades them). Hide `inherited`/`unverified`.
          const hasSourceRef =
            leafNum != null && !!_srcRef && (!_srcRef.source || _srcRef.source === 'verified');
          const rowEl = (
            <div
              key={k}
              className={`flex items-center justify-between gap-3 px-2 -mx-2 py-1 first:pt-0 rounded-md${
                isDerivedEbitda ? ' cursor-help' : ''
              }`}
            >
              {/* Left: chevron placeholder · field label · source icon */}
              <div className="flex min-w-0 flex-1 items-center gap-2">
                <span className={TREE_ROW_CHEVRON_SLOT} aria-hidden />
                <span
                  className={`truncate text-[13px] ${
                    isDerivedEbitda
                      ? 'font-medium text-violet-800 bg-violet-100 rounded px-1.5 py-0.5'
                      : 'font-normal text-gray-700'
                  }`}
                >
                  {formatKey(k)}
                </span>
                {hasSourceRef ? (
                  <button
                    type="button"
                    className="shrink-0 p-0.5 rounded hover:bg-amber-50 text-amber-400 hover:text-amber-600 transition-colors"
                    title="View highlighted source in PDF"
                    onClick={() => onShowSource?.(path)}
                  >
                    <ScanSearch className="h-3.5 w-3.5" />
                  </button>
                ) : null}
                {isNonCanonicalKey(k, pathPrefix) && (
                  <span className="shrink-0 text-[10px] text-gray-400 select-none" title="Non-canonical field">·</span>
                )}
                {isOtherCatchAll && leafNum != null ? (
                  <span
                    className="shrink-0 rounded-full bg-amber-50 px-1.5 py-0.5 text-[10px] font-medium text-amber-600 select-none"
                    title={
                      hasLeafComps
                        ? "Catch-all “other” classification — hover the Σ badge to see what was auto-tagged here."
                        : 'Catch-all “other” classification — review what was auto-tagged here against the source document.'
                    }
                  >
                    other
                  </span>
                ) : null}
                {hasLeafComps ? (
                  <span
                    className="shrink-0 inline-flex items-center gap-0.5 rounded-full bg-sky-50 px-1.5 py-0.5 text-[10px] font-medium text-sky-700 select-none"
                    title={`Combined from ${leafComps!.length} document line(s) — hover to see the breakdown`}
                  >
                    Σ {leafComps!.length}
                  </span>
                ) : null}
                {isManuallyEdited ? <EditedIndicator marker={editMarker} className="shrink-0" /> : null}
              </div>
              {inEdit ? (
                <div className="flex shrink-0 items-center gap-1">
                  <input
                    type="text"
                    inputMode="decimal"
                    className="w-28 border border-gray-200 rounded px-2 py-1 text-sm font-mono font-normal"
                    value={editingDraft}
                    onChange={(e) => onDraftChange?.(e.target.value)}
                    disabled={editBusy}
                  />
                  <Button type="button" size="sm" className="h-7 text-xs px-2" disabled={editBusy} onClick={onSaveEdit}>
                    Save
                  </Button>
                  <Button type="button" size="sm" variant="outline" className="h-7 text-xs px-2" disabled={editBusy} onClick={onCancelEdit}>
                    Cancel
                  </Button>
                </div>
              ) : (
                <div className="flex shrink-0 items-center gap-2">
                  <div
                    className={`text-[13px] font-mono text-right break-words ${
                      isManuallyEdited ? 'font-semibold text-amber-700' : 'font-normal text-gray-900'
                    }`}
                  >
                    {ebitdaNotCalculated ? (
                      <span className="font-sans text-xs italic text-gray-400">Not calculated</span>
                    ) : v === null || v === undefined ? (
                      '—'
                    ) : displayLeafNum != null ? (
                      formatAmountBare(displayLeafNum, amountDisplaySystem)
                    ) : (
                      String(leafVal)
                    )}
                  </div>
                  {canEdit ? (
                    <button
                      type="button"
                      className="p-1 rounded hover:bg-gray-100 text-gray-500 hover:text-gray-700"
                      title="Edit field"
                      onClick={() => onStartEdit?.(path, leafVal)}
                    >
                      <Pencil className="h-3.5 w-3.5" />
                    </button>
                  ) : null}
                  {canDetach ? (
                    <button
                      type="button"
                      className="p-1 rounded hover:bg-red-50 text-gray-400 hover:text-red-600 transition-colors"
                      title="Remove this field (moves it to the unmatched panel — recoverable)"
                      onClick={() => onDetach?.(path)}
                    >
                      <Trash2 className="h-3.5 w-3.5" />
                    </button>
                  ) : null}
                  {canRetag ? (
                    <button
                      type="button"
                      className="p-1 rounded hover:bg-sky-50 text-gray-400 hover:text-sky-600 transition-colors"
                      title="Retag: move this field to a different canonical slot"
                      onClick={() => onRetag?.(path)}
                    >
                      <ArrowLeftRight className="h-3.5 w-3.5" />
                    </button>
                  ) : null}
                  {editable && onAddChild ? (
                    <button
                      type="button"
                      className="p-1 rounded hover:bg-gray-100 text-gray-400 hover:text-gray-600 transition-colors"
                      title="Convert to composite field (add a child field inside this one)"
                      onClick={() => onAddChild(path)}
                    >
                      <Plus className="h-3.5 w-3.5" />
                    </button>
                  ) : null}
                </div>
              )}
            </div>
          );
          // EBITDA: the whole row is hoverable. When the configured mapping produced a breakdown,
          // show the same per-term breakdown the /company discrepancy dashboard shows; otherwise fall
          // back to the short "calculated figure" explanation.
          if (isDerivedEbitda) {
            if (ebitdaBdHasTerms) {
              // Own TooltipProvider — the file page has no global one (matches the other tooltips here).
              return (
                <TooltipProvider key={k} delayDuration={150}>
                  <AfsBreakdownTooltip
                    metricLabel="EBITDA"
                    breakdown={ebitdaBd}
                    currency={currency}
                    sourceRefs={sourceRefs}
                    onShowSource={onShowSource}
                    fileAvailable={Boolean(onShowSource)}
                    side="bottom"
                  >
                    {rowEl}
                  </AfsBreakdownTooltip>
                </TooltipProvider>
              );
            }
            return (
              <TooltipProvider key={k} delayDuration={150}>
                <Tooltip>
                  <TooltipTrigger asChild>{rowEl}</TooltipTrigger>
                  <TooltipContent side="top" align="start" className="max-w-xs">
                    {ebitdaNotCalculated ? (
                      <p className="text-xs leading-relaxed">
                        <span className="font-semibold">Not calculated.</span> EBITDA is derived from
                        Profit Before Tax, finance costs and depreciation &amp; amortization. It
                        couldn&apos;t be computed because Profit Before Tax or depreciation &amp;
                        amortization wasn&apos;t found in the extraction.
                      </p>
                    ) : (
                      <p className="text-xs leading-relaxed">
                        <span className="font-semibold">Calculated figure.</span> EBITDA is computed from
                        Profit Before Tax, finance costs and depreciation &amp; amortization — it is not
                        reported directly in the audited financials.
                      </p>
                    )}
                  </TooltipContent>
                </Tooltip>
              </TooltipProvider>
            );
          }
          // A combined (rolled-up) leaf reveals its signed contributors on hover — the breakdown is
          // otherwise only visible in the "Combined fields" panel far below the tree.
          if (hasLeafComps) {
            return (
              <TooltipProvider key={k} delayDuration={150}>
                <Tooltip>
                  <TooltipTrigger asChild>{rowEl}</TooltipTrigger>
                  <TooltipContent side="top" align="end" className="max-w-xs">
                    <div className="text-xs space-y-1">
                      <div className="font-semibold">
                        Combined from {leafComps!.length} line{leafComps!.length > 1 ? 's' : ''}
                      </div>
                      <ul className="space-y-0.5">
                        {leafComps!.map((c, i) => (
                          <li key={c.id ?? i} className="flex items-center justify-between gap-4">
                            <span>
                              <span className="font-mono mr-1">{c.sign === '-' ? '−' : '+'}</span>
                              {c.label}
                            </span>
                            <span className="font-mono">
                              {formatAmountBare(typeof c.value === 'number' ? c.value : 0, amountDisplaySystem)}
                            </span>
                          </li>
                        ))}
                      </ul>
                      <div className="pt-1 mt-1 border-t border-white/20 text-[11px] opacity-80">
                        Edit the breakdown in “Combined fields” below.
                      </div>
                    </div>
                  </TooltipContent>
                </Tooltip>
              </TooltipProvider>
            );
          }
          return rowEl;
        })}
      </div>
    );
  }

  if (typeof value === 'number') {
    const showFx = fxPreviewEnabled && !!fxPreviewCurrency && fxPreviewRate !== 1 && isFinancialStatementValuePath(pathPrefix);
    const n = showFx ? value * fxPreviewRate : value;
    return (
      <span className="text-sm font-normal font-mono text-gray-900">
        {formatAmountBare(n, amountDisplaySystem)}
      </span>
    );
  }
  return <span className="text-sm font-normal font-mono text-gray-900">{String(value)}</span>;
}

type UiFile = TaggedFile & {
  portfolioCompanyId?: number | null;
  portfolioCompanyName?: string | null;
};

function mapApiFileToTagged(f: {
  id: number;
  filename: string;
  created_at: string;
  updated_at: string;
  status?: string | null;
  portfolio_company_id?: number | null;
  portfolio_company_name?: string | null;
  entity_id?: number | null;
  entity_name?: string | null;
  size_bytes?: number | null;
}): UiFile {
  const ext = (f.filename.split('.').pop() || 'file').toLowerCase();
  const up = (f.status || '').toLowerCase();
  const uiStatus: TaggedFile['status'] =
    up.includes('error') || up.includes('fail')
      ? 'error'
      : up.includes('processed') || up.includes('complete') || up.includes('done')
        ? 'processed'
        : 'pending'; // 'uploaded' / 'pending' / 'verified' all treated as pending
  return {
    id: String(f.id),
    fileName: f.filename,
    fileType: ext,
    uploadedAt: f.updated_at || f.created_at,
    size: formatFileSizeFromBytes(f.size_bytes),
    taggedEntityId: f.entity_id != null ? String(f.entity_id) : null,
    taggedEntityName:
      (f.entity_name && String(f.entity_name).trim()) ||
      (f.entity_id != null ? `Entity #${f.entity_id}` : null),
    status: uiStatus,
    portfolioCompanyId: f.portfolio_company_id ?? null,
    portfolioCompanyName: f.portfolio_company_name ?? null,
  };
}

/** Server-computed canonical-mapping suggestion attached to an unmatched row. */
type UnmatchedSuggestion = {
  parent_path: string;
  key: string;
  full_path?: string;
  confidence?: number;
  source?: string;
  rationale?: string;
};

/** Read a usable (parent_path + key) suggestion off an unmatched row, or null. */
function readSuggestion(row: Record<string, unknown>): UnmatchedSuggestion | null {
  const s = row?.suggestion;
  if (!s || typeof s !== 'object') return null;
  const sg = s as Partial<UnmatchedSuggestion>;
  if (typeof sg.parent_path !== 'string' || !sg.parent_path || typeof sg.key !== 'string' || !sg.key) {
    return null;
  }
  return sg as UnmatchedSuggestion;
}

function UnmatchedTable({
  rows,
  treeCurrency,
  amountDisplaySystem,
  parentPaths,
  onAttach,
  onAccept,
  onDismiss,
  onRestore,
  acceptBusyId,
  dismissBusyId,
  restoreBusyId,
  highlightIds,
  onShowSourceRef,
}: {
  rows: Record<string, unknown>[];
  treeCurrency: string | null;
  amountDisplaySystem: AmountDisplaySystem;
  parentPaths: string[];
  onAttach: (row: Record<string, unknown>) => void;
  onAccept?: (row: Record<string, unknown>) => void;
  onDismiss?: (row: Record<string, unknown>) => void;
  onRestore?: (row: Record<string, unknown>) => void;
  acceptBusyId?: string | null;
  dismissBusyId?: string | null;
  restoreBusyId?: string | null;
  highlightIds?: Set<string>;
  /** Open the PDF source viewer for a row's located page (unmatched rows carry their own ref). */
  onShowSourceRef?: (label: string, ref: SourceRef) => void;
}) {
  const activeRows = rows.filter((r) => !r.dismissed);
  const dismissedRows = rows.filter((r) => r.dismissed);
  const [showDismissed, setShowDismissed] = useState(false);

  const renderRow = (row: Record<string, unknown>, isDismissed: boolean) => {
    const uid = String(row.id ?? '');
    const label = String(row.document_label ?? row.key ?? uid ?? '');
    const val = row.value;
    const sectionHint = String(row.section_hint ?? '—');
    const sug = readSuggestion(row);
    const conf = typeof sug?.confidence === 'number' ? sug.confidence : null;
    const accepting = Boolean(acceptBusyId && acceptBusyId === uid);
    const dismissing = Boolean(dismissBusyId && dismissBusyId === uid);
    const restoring = Boolean(restoreBusyId && restoreBusyId === uid);
    const highlighted = Boolean(uid && highlightIds?.has(uid));
    const srcRefRaw = row.source_ref;
    const srcRef =
      srcRefRaw && typeof srcRefRaw === 'object' && typeof (srcRefRaw as SourceRef).page === 'number'
        ? (srcRefRaw as SourceRef)
        : null;
    return (
      <tr
        key={uid || label}
        id={uid ? `uf-row-${uid}` : undefined}
        className={`border-b border-amber-50 align-top transition-colors ${
          isDismissed ? 'opacity-50' : highlighted ? 'bg-amber-100 ring-2 ring-inset ring-amber-300' : ''
        }`}
      >
        <td className="p-3 text-gray-900 max-w-[260px] break-words">
          {label || '—'}
          {srcRef && onShowSourceRef ? (
            <button
              type="button"
              className="ml-1.5 inline-flex align-middle p-0.5 rounded hover:bg-amber-50 text-amber-400 hover:text-amber-600 transition-colors"
              title="View highlighted source in PDF"
              onClick={() => onShowSourceRef(label, srcRef)}
            >
              <ScanSearch className="h-3.5 w-3.5" />
            </button>
          ) : null}
        </td>
        <td className="p-3 font-mono text-gray-900">
          {typeof val === 'number'
            ? formatAmountDisplay(val, treeCurrency, amountDisplaySystem)
            : val === null || val === undefined
              ? '—'
              : typeof val === 'object'
                ? (() => {
                    const n = sumNumericLeaves(val);
                    if (n != null) return formatAmountDisplay(n, treeCurrency, amountDisplaySystem);
                    return (
                      <span className="text-xs text-gray-500 whitespace-pre-wrap break-all">
                        {JSON.stringify(val, null, 2)}
                      </span>
                    );
                  })()
                : String(val)}
        </td>
        <td className="p-3">
          {sug ? (
            <div className="flex flex-col gap-0.5">
              <span className="font-mono text-xs text-gray-700 break-all" title={sug.full_path || `${sug.parent_path}.${sug.key}`}>
                {String(sug.key).replace(/_/g, ' ')}
              </span>
              {conf != null && (
                <span className={conf >= 0.85 ? 'text-[11px] text-emerald-600' : 'text-[11px] text-amber-600'}>
                  {Math.round(conf * 100)}% match{sug.source === 'alias' ? ' · learned' : ''}
                </span>
              )}
            </div>
          ) : (
            <span className="text-gray-400">—</span>
          )}
        </td>
        <td className="p-3 text-gray-500 text-xs font-mono">{sectionHint}</td>
        <td className="p-3">
          <div className="flex flex-col gap-1.5">
            {isDismissed ? (
              onRestore && (
                <Button
                  type="button"
                  size="sm"
                  variant="outline"
                  className="h-7 text-xs"
                  onClick={() => onRestore(row)}
                  disabled={restoring}
                >
                  {restoring ? 'Restoring…' : 'Restore'}
                </Button>
              )
            ) : (
              <>
                {sug && onAccept && (
                  <Button
                    type="button"
                    size="sm"
                    className="h-7 text-xs"
                    onClick={() => onAccept(row)}
                    disabled={accepting}
                    title={`Map to ${sug.full_path || `${sug.parent_path}.${sug.key}`}`}
                  >
                    {accepting ? 'Accepting…' : 'Accept'}
                  </Button>
                )}
                <Button
                  type="button"
                  size="sm"
                  variant="outline"
                  className="h-7 text-xs"
                  onClick={() => onAttach(row)}
                  disabled={parentPaths.length === 0 || accepting}
                  title={parentPaths.length === 0 ? 'No assignable canonical parent paths available' : 'Attach to a field or create new'}
                >
                  {sug ? 'Change…' : 'Attach'}
                </Button>
                {onDismiss && (
                  <Button
                    type="button"
                    size="sm"
                    variant="ghost"
                    className="h-7 text-xs text-gray-400 hover:text-gray-600"
                    onClick={() => onDismiss(row)}
                    disabled={dismissing}
                    title="Dismiss — keeps the row visible at the bottom, always recoverable"
                  >
                    {dismissing ? 'Dismissing…' : 'Dismiss'}
                  </Button>
                )}
              </>
            )}
          </div>
        </td>
      </tr>
    );
  };

  return (
    <div className="space-y-2">
      <div className="overflow-x-auto rounded-md border border-amber-100 bg-amber-50/40">
        <table className="w-full text-sm font-normal">
          <thead>
            <tr className="border-b border-amber-100 text-left text-gray-600">
              <th className="p-3 font-normal">Field</th>
              <th className="p-3 font-normal">Value</th>
              <th className="p-3 font-normal">Suggested mapping</th>
              <th className="p-3 font-normal">Section hint</th>
              <th className="p-3 font-normal w-[180px]">Action</th>
            </tr>
          </thead>
          <tbody>
            {activeRows.length > 0
              ? activeRows.map((row) => renderRow(row, false))
              : (
                <tr>
                  <td colSpan={5} className="p-3 text-center text-xs text-gray-400">No unidentified fields</td>
                </tr>
              )}
          </tbody>
        </table>
      </div>

      {dismissedRows.length > 0 && (
        <div>
          <button
            type="button"
            className="flex items-center gap-1.5 text-xs text-gray-400 hover:text-gray-600 transition-colors"
            onClick={() => setShowDismissed((v) => !v)}
          >
            <span>{showDismissed ? '▾' : '▸'}</span>
            <span>Dismissed ({dismissedRows.length})</span>
          </button>
          {showDismissed && (
            <div className="mt-1 overflow-x-auto rounded-md border border-gray-100 bg-gray-50/40">
              <table className="w-full text-sm font-normal">
                <thead>
                  <tr className="border-b border-gray-100 text-left text-gray-400">
                    <th className="p-3 font-normal">Field</th>
                    <th className="p-3 font-normal">Value</th>
                    <th className="p-3 font-normal">Suggested mapping</th>
                    <th className="p-3 font-normal">Section hint</th>
                    <th className="p-3 font-normal w-[180px]">Action</th>
                  </tr>
                </thead>
                <tbody>
                  {dismissedRows.map((row) => renderRow(row, true))}
                </tbody>
              </table>
            </div>
          )}
        </div>
      )}
    </div>
  );
}

export default function FileDetailPage() {
  const { fileId } = useParams<{ fileId: string }>();
  const navigate = useNavigate();
  const location = useLocation();
  const [currencyBusy, setCurrencyBusy] = useState(false);
  const [currencyEditMode, setCurrencyEditMode] = useState(false);
  const [currencyDraft, setCurrencyDraft] = useState('');
  const [editingPath, setEditingPath] = useState<string | null>(null);
  const [editingDraft, setEditingDraft] = useState('');
  const [fieldSaveBusy, setFieldSaveBusy] = useState(false);
  const [isMinimized, setIsMinimized] = useState(false);
  // amountDisplaySystem is derived from treeCurrency below — not kept in state
  const [currencyConfirmOpen, setCurrencyConfirmOpen] = useState(false);
  const [pendingCurrency, setPendingCurrency] = useState<string | null>(null);

  /** View-only FX preview for Info tab numeric tree (persisted Apply uses `/extraction/convert-currency`). */
  const [fxPreviewTarget, setFxPreviewTarget] = useState('');
  const [fxPreviewRates, setFxPreviewRates] = useState<FxRatesResponse | null>(null);
  const [fxPreviewFetchBusy, setFxPreviewFetchBusy] = useState(false);
  const [persistConvertConfirmOpen, setPersistConvertConfirmOpen] = useState(false);
  const [persistConvertBusy, setPersistConvertBusy] = useState(false);

  // Reconciliation → jump to and transiently highlight the unattached lines for a bucket.
  const unmatchedSectionRef = useRef<HTMLDivElement>(null);
  const [highlightedUnmatchedIds, setHighlightedUnmatchedIds] = useState<Set<string>>(new Set());
  const highlightTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  useEffect(
    () => () => {
      if (highlightTimerRef.current) clearTimeout(highlightTimerRef.current);
    },
    [],
  );

  // Attach-to-company dialog state
  const [attachCompanyOpen, setAttachCompanyOpen] = useState(false);
  const [attachCycleId, setAttachCycleId] = useState('');
  const [attachCycleResolving, setAttachCycleResolving] = useState(false);
  const [attachCycleError, setAttachCycleError] = useState(false);
  const [attachCompanyId, setAttachCompanyId] = useState<number | null>(null);
  const [attachEntityId, setAttachEntityId] = useState<number | null>(null);
  const [attachFyEnd, setAttachFyEnd] = useState('');
  const [attachCompanies, setAttachCompanies] = useState<{ id: number; name: string }[]>([]);
  const [attachEntities, setAttachEntities] = useState<{ id: number; name: string }[]>([]);
  const [attachSaving, setAttachSaving] = useState(false);

  const openCompanyAttachDialog = () => {
    setAttachCycleId('');
    setAttachCycleResolving(false);
    setAttachCycleError(false);
    setAttachCompanyId(null);
    setAttachEntityId(null);
    setAttachFyEnd('');
    setAttachCompanies([]);
    setAttachEntities([]);
    setAttachCompanyOpen(true);
  };

  const [fieldConfirmOpen, setFieldConfirmOpen] = useState(false);
  const [pendingFieldPath, setPendingFieldPath] = useState<string | null>(null);
  const [pendingFieldCurrent, setPendingFieldCurrent] = useState<unknown>(null);
  // Justification capture for an inline value edit: holds the validated value awaiting a reason.
  const [justifyOpen, setJustifyOpen] = useState(false);
  const [justifyReason, setJustifyReason] = useState('');
  const [pendingEditValue, setPendingEditValue] = useState<number | null>(null);
  const [unmatchedAttachOpen, setUnmatchedAttachOpen] = useState(false);
  const [unmatchedAttachBusy, setUnmatchedAttachBusy] = useState(false);
  const [acceptingId, setAcceptingId] = useState<string | null>(null);
  const [dismissBusyId, setDismissBusyId] = useState<string | null>(null);
  const [restoreBusyId, setRestoreBusyId] = useState<string | null>(null);
  const [parentPaths, setParentPaths] = useState<string[]>([]);
  const [leafPaths, setLeafPaths] = useState<string[]>([]);
  // Conflict resolution when several document lines target one canonical leaf (no data loss).
  type ConflictPayload = {
    unmatched_id: string;
    target_parent_path: string;
    target_key: string;
    full_path: string;
    existing_components: FieldComponent[];
    incoming: { document_label: string; value: number; inferred_sign: '+' | '-' };
    double_count_warning: string | null;
  };
  const [conflict, setConflict] = useState<ConflictPayload | null>(null);
  const [conflictBusy, setConflictBusy] = useState(false);
  // Roll-up breakdown editor (reversible: drop a contributor / flip a sign).
  const [editComponentsPath, setEditComponentsPath] = useState<string | null>(null);
  const [editComponentsDraft, setEditComponentsDraft] = useState<FieldComponent[]>([]);
  const [editComponentsBusy, setEditComponentsBusy] = useState(false);
  const [attachTargetParentPath, setAttachTargetParentPath] = useState('');
  const [attachTargetKey, setAttachTargetKey] = useState('');
  const [attachConfirmOverwrite, setAttachConfirmOverwrite] = useState(false);
  const [attachRow, setAttachRow] = useState<Record<string, unknown> | null>(null);
  const [attachMode, setAttachMode] = useState<'existing' | 'new'>('existing');
  const [attachNewPath, setAttachNewPath] = useState('');
  const [attachNewIsComposite, setAttachNewIsComposite] = useState(false);
  // Retag: canonical path being retagged (detach → re-map flow). Null when in normal attach mode.
  const [retagPath, setRetagPath] = useState<string | null>(null);
  const [retagComponents, setRetagComponents] = useState<FieldComponent[] | null>(null);
  const [attachSaveError, setAttachSaveError] = useState<string | null>(null);

  // "Add field" dialog — lets users add a new numeric field at any path in the tree.
  const [addFieldOpen, setAddFieldOpen] = useState(false);
  const [addFieldParent, setAddFieldParent] = useState('');
  const [addFieldKey, setAddFieldKey] = useState('');
  const [addFieldValue, setAddFieldValue] = useState('');
  const [addFieldIsComposite, setAddFieldIsComposite] = useState(false);
  const [addFieldLeafKey, setAddFieldLeafKey] = useState('');
  const [addFieldLeafValue, setAddFieldLeafValue] = useState('');
  // When converting an existing scalar leaf to a composite, we track its current value and let
  // the user optionally name a child leaf to preserve it under the new composite container.
  const [addFieldConvertingLeafPath, setAddFieldConvertingLeafPath] = useState<string | null>(null);
  const [addFieldConvertingLeafCurrentValue, setAddFieldConvertingLeafCurrentValue] = useState<number | null>(null);
  const [addFieldKeepExistingKey, setAddFieldKeepExistingKey] = useState('');
  const [addFieldBusy, setAddFieldBusy] = useState(false);
  const [addFieldError, setAddFieldError] = useState<string | null>(null);

  // Source-ref viewer state
  const [sourceRefs, setSourceRefs] = useState<Record<string, SourceRef> | null>(null);
  const [sourceViewerOpen, setSourceViewerOpen] = useState(false);
  const [sourceViewerPath, setSourceViewerPath] = useState<string>('');
  // Direct source ref (unmatched rows carry their own page ref, not in the sourceRefs map).
  const [sourceViewerRef, setSourceViewerRef] = useState<SourceRef | null>(null);
  const from = (
    location.state as
      | { kind?: string; companyId?: number; fileId?: string; from?: { kind?: string; companyId?: number; fileId?: string } }
      | null
      | undefined
  )?.from;

  const stateFile = (location.state as { file?: UiFile } | null)?.file;
  const mockFile = fileId ? taggedFiles.find((f) => f.id === fileId) : undefined;
  const [remoteFile, setRemoteFile] = useState<UiFile | null>(null);
  const [remoteFileError, setRemoteFileError] = useState(false);

  const file: UiFile | null = (stateFile as UiFile | undefined) || (mockFile as UiFile | undefined) || remoteFile;
  const [downloadUrl, setDownloadUrl] = useState<string>('');
  const [downloadError, setDownloadError] = useState<string | null>(null);
  const [downloadLoading, setDownloadLoading] = useState(true);
  const [viewerPdfUrl, setViewerPdfUrl] = useState<string>('');
  const [extractStatus, setExtractStatus] = useState<{
    status?: string | null;
    error_message?: string | null;
    meta?: Record<string, unknown>;
  } | null>(null);

  const extractionMetaEarly =
    extractStatus?.meta &&
    typeof extractStatus.meta === 'object' &&
    !Array.isArray(extractStatus.meta)
      ? (extractStatus.meta as Record<string, unknown>)
      : null;
  const fxPreviewSourceCode = resolveCurrency(extractionMetaEarly).code;

  const fxPreviewConversionRate = useMemo(() => {
    if (!fxPreviewRates || !fxPreviewTarget.trim()) return null;
    const tgt = fxPreviewTarget.trim().toUpperCase();
    const fromMid = fxPreviewRates.from.mid || 1;
    const quote = fxPreviewRates.to.find((t) => t.quotecurrency.toUpperCase() === tgt);
    if (!quote) return null;
    return quote.mid / fromMid;
  }, [fxPreviewRates, fxPreviewTarget]);

  const fxPreviewActive =
    Boolean(fxPreviewSourceCode && fxPreviewTarget.trim() && fxPreviewConversionRate != null) &&
    fxPreviewSourceCode !== fxPreviewTarget.trim().toUpperCase();


  useEffect(() => {
    if (!attachFyEnd) { setAttachCycleId(''); setAttachCycleError(false); return; }
    setAttachCycleResolving(true);
    setAttachCycleError(false);
    setAttachCycleId('');
    void resolveReviewCycleFromFyEnd(attachFyEnd)
      .then((res) => { setAttachCycleId(res.review_cycle_id); })
      .catch(() => { setAttachCycleError(true); })
      .finally(() => { setAttachCycleResolving(false); });
  }, [attachFyEnd]);

  useEffect(() => {
    if (!attachCycleId) { setAttachCompanies([]); setAttachCompanyId(null); return; }
    void listPortfolioCompanies({ limit: 500, offset: 0, review_cycle_id: attachCycleId })
      .then((res) => {
        const sorted = (res.items ?? [])
          .filter((c) => c.name !== 'Unassigned (audit files)')
          .map((c) => ({ id: c.id, name: c.name }))
          .sort((a, b) => a.name.localeCompare(b.name));
        setAttachCompanies(sorted);
      })
      .catch(() => setAttachCompanies([]));
    setAttachCompanyId(null);
    setAttachEntityId(null);
    setAttachEntities([]);
  }, [attachCycleId]);

  useEffect(() => {
    if (!attachCompanyId) { setAttachEntities([]); setAttachEntityId(null); return; }
    void listEntities({ portfolio_company_id: attachCompanyId, limit: 500, offset: 0 })
      .then((res) => setAttachEntities((res.items ?? []).map((e) => ({ id: e.id, name: e.name }))))
      .catch(() => setAttachEntities([]));
    setAttachEntityId(null);
  }, [attachCompanyId]);

  const handleAttachToCompany = async () => {
    if (!fileId || !attachCompanyId || !attachEntityId) {
      toast.error('Select review cycle, company, and entity');
      return;
    }
    if (!attachFyEnd) {
      toast.error('Select FY end');
      return;
    }
    setAttachSaving(true);
    try {
      await patchFile(Number(fileId), {
        portfolio_company_id: attachCompanyId,
        entity_id: attachEntityId,
        fy_end: attachFyEnd,
        entity_detached_acknowledged: true,
      });
      const entityName = attachEntities.find((e) => e.id === attachEntityId)?.name;
      const companyName = attachCompanies.find((c) => c.id === attachCompanyId)?.name;
      setRemoteFile((prev) =>
        prev
          ? {
              ...prev,
              portfolioCompanyId: attachCompanyId,
              portfolioCompanyName: companyName ?? prev.portfolioCompanyName,
              taggedEntityId: String(attachEntityId),
              taggedEntityName: entityName ?? prev.taggedEntityName,
            }
          : prev,
      );
      setAttachCompanyOpen(false);
      toast.success(entityName ? `Attached to entity "${entityName}"` : 'Attached');
    } catch (e) {
      toast.error(getApiErrorMessage(e, 'Failed to attach'));
    } finally {
      setAttachSaving(false);
    }
  };

  const refreshExtractStatus = useCallback(async () => {
    const idNum = Number(fileId);
    if (!Number.isFinite(idNum)) return;
    try {
      const res = await getFileExtractionStatus(idNum);
      setExtractStatus({ status: res.status, error_message: res.error_message, meta: res.meta });
    } catch (e) {
      console.warn('Failed to load extraction status', e);
    }
  }, [fileId]);

  useEffect(() => {
    const idNum = Number(fileId);
    if (!Number.isFinite(idNum)) return;
    if (stateFile || mockFile) {
      setRemoteFile(null);
      setRemoteFileError(false);
      return;
    }
    let cancelled = false;
    setRemoteFileError(false);
    void getFile(idNum)
      .then((r) => {
        if (!cancelled) setRemoteFile(mapApiFileToTagged(r));
      })
      .catch(() => {
        if (!cancelled) {
          setRemoteFile(null);
          setRemoteFileError(true);
        }
      });
    return () => {
      cancelled = true;
    };
  }, [fileId, stateFile, mockFile]);

  useEffect(() => {
    const run = async () => {
      const idNum = Number(fileId);
      if (!Number.isFinite(idNum)) {
        setDownloadLoading(false);
        return;
      }
      setDownloadLoading(true);
      setDownloadError(null);
      try {
        const res = await getFileDownloadUrl(idNum);
        setDownloadUrl(res.download_url);
      } catch (e) {
        console.warn('Failed to load download URL', e);
        setDownloadUrl('');
        setDownloadError(getApiErrorMessage(e, 'Could not load download URL'));
      } finally {
        setDownloadLoading(false);
      }
    };
    void run();
  }, [fileId]);

  // Download the original file via its presigned S3 URL. If the URL is missing
  // (no S3 object, expired, or the lookup failed) we surface a clear message
  // instead of doing nothing on click.
  const handleDownload = () => {
    if (downloadLoading) {
      toast.info('Still preparing the download link — try again in a moment');
      return;
    }
    if (!downloadUrl) {
      toast.error(downloadError || 'This file is not available for download');
      return;
    }
    const a = document.createElement('a');
    a.href = downloadUrl;
    a.download = file?.fileName ?? '';
    a.target = '_blank';
    a.rel = 'noopener';
    document.body.appendChild(a);
    a.click();
    a.remove();
  };

  useEffect(() => {
    void refreshExtractStatus();
  }, [refreshExtractStatus]);

  // no localStorage persistence — display system is derived from currency, not a user preference

  // Load source refs once extraction is complete.
  useEffect(() => {
    const idNum = Number(fileId);
    if (!Number.isFinite(idNum)) return;
    let cancelled = false;
    void getFileSourceRefs(idNum)
      .then((res) => {
        if (!cancelled && res.source_refs && Object.keys(res.source_refs).length > 0) {
          setSourceRefs(res.source_refs);
        }
      })
      .catch(() => {});
    return () => { cancelled = true; };
  }, [fileId]);

  const st = (extractStatus?.status || '').toLowerCase();
  const meta = extractStatus?.meta;
  // Configured-formula EBITDA breakdown the backend attaches to the status meta (same evaluator the
  // /company dashboard uses). Feeds the EBITDA leaf's value + breakdown tooltip via context.
  const ebitdaBreakdownForTree = useMemo<MappingBreakdown | null>(() => {
    const mb = meta && isPlainObject(meta) ? (meta as Record<string, unknown>).mapping_breakdown : null;
    if (!mb || !isPlainObject(mb)) return null;
    const eb = (mb as Record<string, unknown>).ebitda;
    return eb && isPlainObject(eb) ? (eb as unknown as MappingBreakdown) : null;
  }, [meta]);
  // Per-path manual-edit markers from the status meta (keyed by dotted path). Drives the
  // "edited manually" badge + amber styling on edited leaves.
  const manualEditsForTree = useMemo<Record<string, ManualEditMarker>>(() => {
    const me = meta && isPlainObject(meta) ? (meta as Record<string, unknown>).manual_edits : null;
    return me && isPlainObject(me) ? (me as Record<string, ManualEditMarker>) : {};
  }, [meta]);
  const extractKind = typeof meta?.kind === 'string' ? meta.kind : '';
  const isExtractDone = st === 'completed' || st === 'processed';
  const isAuditFinancials =
    extractKind === 'audit_financials' || extractKind === 'financials' || extractKind === 'audit-financials';

  // Detect XLSX from stored file_format (set during extraction) or the filename extension.
  const isXlsxFile = (() => {
    const fmtFromMeta = typeof meta?.file_format === 'string' ? meta.file_format : '';
    if (fmtFromMeta === 'xlsx') return true;
    const fn = (file?.fileName ?? '').toLowerCase();
    return fn.endsWith('.xlsx') || fn.endsWith('.xls');
  })();

  // Fast poll while extraction is in flight.
  useEffect(() => {
    const idNum = Number(fileId);
    if (!Number.isFinite(idNum)) return;
    const poll = st === 'running' || st === 'queued';
    if (!poll) return;
    const t = setInterval(() => {
      void refreshExtractStatus().catch(() => {});
    }, 3000);
    return () => clearInterval(t);
  }, [fileId, st, refreshExtractStatus]);

  // Slow background poll (every 20 s) even in terminal states so that a retrigger
  // initiated from another page is picked up without requiring a hard refresh.
  useEffect(() => {
    const idNum = Number(fileId);
    if (!Number.isFinite(idNum)) return;
    const t = setInterval(() => {
      void refreshExtractStatus().catch(() => {});
    }, 20_000);
    return () => clearInterval(t);
  }, [fileId, refreshExtractStatus]);

  // Re-check as soon as the user tabs back to this page.
  useEffect(() => {
    const handler = () => {
      if (document.visibilityState === 'visible') {
        void refreshExtractStatus().catch(() => {});
      }
    };
    document.addEventListener('visibilitychange', handler);
    return () => document.removeEventListener('visibilitychange', handler);
  }, [refreshExtractStatus]);

  useEffect(() => {
    setFxPreviewTarget('');
    setFxPreviewRates(null);
  }, [fileId]);

  useEffect(() => {
    const src = fxPreviewSourceCode?.toUpperCase();
    const tgt = fxPreviewTarget.trim().toUpperCase();
    const idNum = Number(fileId);
    if (!src || !tgt || src === tgt || !Number.isFinite(idNum)) {
      setFxPreviewRates(null);
      setFxPreviewFetchBusy(false);
      return;
    }
    let cancelled = false;
    setFxPreviewFetchBusy(true);
    void fetchFileFxPreviewRate(idNum, tgt)
      .then((rates) => {
        if (!cancelled) setFxPreviewRates(rates);
      })
      .catch(() => {
        if (!cancelled) {
          setFxPreviewRates(null);
          toast.error('Could not load exchange rates for preview.');
        }
      })
      .finally(() => {
        if (!cancelled) setFxPreviewFetchBusy(false);
      });
    return () => {
      cancelled = true;
    };
  }, [fxPreviewSourceCode, fxPreviewTarget, fileId]);

  const refreshParentPaths = useCallback(async () => {
    const idNum = Number(fileId);
    if (!Number.isFinite(idNum) || !isAuditFinancials) {
      setParentPaths([]);
      setLeafPaths([]);
      return;
    }
    try {
      const res = await getAuditFinancialParentPaths(idNum);
      setParentPaths(Array.isArray(res.parent_paths) ? res.parent_paths : []);
      setLeafPaths(Array.isArray(res.leaf_paths) ? res.leaf_paths : []);
    } catch (err) {
      console.warn('Failed to load assignable parent paths', err);
      setParentPaths([]);
      setLeafPaths([]);
    }
  }, [fileId, isAuditFinancials]);

  useEffect(() => {
    void refreshParentPaths();
  }, [refreshParentPaths]);

  // Read-only view: manual field editing/mapping removed.

  if ((!file && remoteFileError) || (!file && fileId && !stateFile && !mockFile && remoteFile === null && !remoteFileError)) {
    return (
      <div className="h-full flex items-center justify-center">
        <p className="text-gray-500">{remoteFileError ? 'File not found.' : 'Loading…'}</p>
      </div>
    );
  }

  if (!file) {
    return (
      <div className="h-full flex items-center justify-center">
        <p className="text-gray-500">File not found.</p>
      </div>
    );
  }

  const extractedRaw =
    meta && isPlainObject(meta) && 'extracted' in meta ? meta.extracted : meta && isPlainObject(meta) && 'audit_financials' in meta ? meta.audit_financials : null;
  const extractedObj: Record<string, unknown> | null =
    extractedRaw != null && typeof extractedRaw === 'object' && !Array.isArray(extractedRaw)
      ? (extractedRaw as Record<string, unknown>)
      : null;

  /** Hide LLM diagnostic blobs from tree; surface parse errors compactly above. */
  const AUDIT_DIAGNOSTIC_KEYS = ['raw_model_text', 'parse_error'] as const;
  const auditDisplay = (() => {
    if (!extractedObj) {
      return {
        treeWithoutDiagnostics: null as Record<string, unknown> | null,
        parseErrorShort: null as string | null,
        rawTechnicalSnippet: null as string | null,
        hasNumericLineItems: false,
      };
    }
    const rest: Record<string, unknown> = { ...extractedObj };
    for (const k of AUDIT_DIAGNOSTIC_KEYS) {
      delete rest[k];
    }
    const pe = extractedObj.parse_error;
    const raw = extractedObj.raw_model_text;
    const parseErrStr = typeof pe === 'string' ? pe.trim() : '';
    const parseErrorShort =
      parseErrStr.length > 0
        ? parseErrStr.split('\n')[0].slice(0, 180) + (parseErrStr.length > 180 ? '…' : '')
        : null;
    const rawTechnicalSnippet = typeof raw === 'string' && raw.length > 0 ? raw.slice(0, 12000) : null;
    const hasNumericLineItems = sumNumericLeaves(rest) != null;
    return { treeWithoutDiagnostics: rest, parseErrorShort, rawTechnicalSnippet, hasNumericLineItems };
  })();

  const financialTreeBase = auditDisplay.treeWithoutDiagnostics ?? extractedObj;

  const displayedExtractedObj: Record<string, unknown> | null = (() => {
    if (!financialTreeBase) return null;
    if (!isAuditFinancials || !isMinimized) return financialTreeBase;
    const filtered: Record<string, unknown> = {};
    for (const k of AUDIT_FINANCIALS_REQUIRED_TOP_LEVEL) {
      if (k in financialTreeBase) filtered[k] = financialTreeBase[k];
    }
    return filtered;
  })();
  const minimizedEntries = AUDIT_FINANCIALS_REQUIRED_TOP_LEVEL.map((k) => [k, financialTreeBase?.[k]] as const);

  // Normalized identity for an overflow row, so the same field can't show twice.
  const overflowRowKey = (r: { document_label?: unknown; key?: unknown; id?: unknown; value?: unknown }): string => {
    const label = String(r.document_label ?? r.key ?? r.id ?? '').trim().toLowerCase();
    let val: string;
    try {
      val = JSON.stringify(r.value ?? null);
    } catch {
      val = String(r.value);
    }
    return `${label}|${val}`;
  };

  const unmatchedRows: Record<string, unknown>[] = (() => {
    const u = meta && isPlainObject(meta) ? meta.audit_financials_unmatched : undefined;
    if (!Array.isArray(u)) return [];
    const rows = u.filter((x): x is Record<string, unknown> => isPlainObject(x));
    const seen = new Set<string>();
    return rows.filter((r) => {
      const k = overflowRowKey(r);
      if (seen.has(k)) return false;
      seen.add(k);
      return true;
    });
  })();

  // "Additional extracted fields" — drop rows already shown under "Unidentified
  // fields" (or duplicated within the overflow set itself).
  const otherBucketRows = (() => {
    if (!isAuditFinancials || !financialTreeBase) return [];
    const seen = new Set<string>(unmatchedRows.map(overflowRowKey));
    return collectOtherBucketItems(financialTreeBase, '')
      .filter((r) => {
        const k = overflowRowKey(r);
        if (seen.has(k)) return false;
        seen.add(k);
        return true;
      })
      // Overflow items live in the canonical tree, so their page ref is already in the source_refs
      // map (keyed by dotted path). Surface it so these rows get the same "view source" link.
      .map((r) => {
        const path = String(r.id ?? '').replace(/^other:/, '');
        const ref = path ? sourceRefs?.[path] : null;
        return ref ? { ...r, source_ref: ref } : r;
      });
  })();

  // Increment 3: merge the unmatched source lines back into the canonical tree at their raw_path
  // source position, so they render in place (untagged-but-visible) instead of in a side panel.
  // Only rows that carry a usable source position (raw_path or a statement section_hint) can be
  // placed in the tree; anything left over still gets a small fallback panel so nothing is lost.
  const sourcePositionView = (() => {
    const base = displayedExtractedObj ?? financialTreeBase ?? extractedObj;
    if (!isAuditFinancials || !isPlainObject(base)) {
      return { tree: base as Record<string, unknown> | null, leftover: unmatchedRows };
    }
    const placeable: Record<string, unknown>[] = [];
    const leftover: Record<string, unknown>[] = [];
    for (const r of unmatchedRows) {
      // Rows that went through backend section tagging carry ``placement_tier``: Tier 1/2 have a
      // ``placement_path`` and nest inside that section; Tier 3 (no section) drops to the trailing
      // "Unidentified fields" tray instead of the statement root. Rows from pre-tagging extractions
      // (no ``placement_tier``) keep the legacy raw_path / section_hint routing for compatibility.
      const tagged = r.placement_tier !== undefined;
      if (tagged) {
        (String(r.placement_path ?? '').trim() ? placeable : leftover).push(r);
      } else {
        const raw = String(r.raw_path ?? '').trim();
        const hint = String(r.section_hint ?? '').trim();
        (raw || hint ? placeable : leftover).push(r);
      }
    }
    const { tree } = buildSourcePositionTree(base as Record<string, unknown>, placeable);
    return { tree, leftover };
  })();
  const unmatchedLeftover = sourcePositionView.leftover;

  // Clicking a bucket's "doesn't reconcile" ⚠️ scrolls to the unattached lines that could close
  // the gap (matched by the row's suggested target or section hint) and highlights them. If none
  // look attachable to this bucket, just scroll to the unmatched section. Plain function (not a
  // hook) so it can sit after the row IIFEs above without breaking rules-of-hooks.
  const handleReconcileJump = (bucketPath: string) => {
    const inBucket = (r: Record<string, unknown>) => {
      const sug = readSuggestion(r);
      const hint = String(r.section_hint ?? '');
      return sug?.parent_path === bucketPath || hint === bucketPath || hint.startsWith(`${bucketPath}.`);
    };
    const ids = [...unmatchedRows, ...otherBucketRows]
      .filter(inBucket)
      .map((r) => String(r.id ?? ''))
      .filter(Boolean);

    if (ids.length > 0) {
      setHighlightedUnmatchedIds(new Set(ids));
      if (highlightTimerRef.current) clearTimeout(highlightTimerRef.current);
      highlightTimerRef.current = setTimeout(() => setHighlightedUnmatchedIds(new Set()), 4000);
      // Let the highlight class land before scrolling to the first candidate.
      requestAnimationFrame(() => {
        const el = document.getElementById(`uf-row-${ids[0]}`);
        if (el) el.scrollIntoView({ behavior: 'smooth', block: 'center' });
        else unmatchedSectionRef.current?.scrollIntoView({ behavior: 'smooth', block: 'start' });
      });
    } else {
      unmatchedSectionRef.current?.scrollIntoView({ behavior: 'smooth', block: 'end' });
    }
  };

  // Canonical leaves backed by a signed roll-up of multiple document lines (lead schedules).
  const fieldComponents: Record<string, FieldComponent[]> = (() => {
    const fc = meta && isPlainObject(meta) ? meta.audit_financials_field_components : undefined;
    if (!isPlainObject(fc)) return {};
    const out: Record<string, FieldComponent[]> = {};
    for (const [path, comps] of Object.entries(fc as Record<string, unknown>)) {
      if (Array.isArray(comps) && comps.length > 0) out[path] = comps as FieldComponent[];
    }
    return out;
  })();
  const rolledUpPaths = Object.keys(fieldComponents).sort();

  const currencyInfo = resolveCurrency(meta);
  const resolvedCurrency = currencyInfo.code;
  const currencySource = currencyInfo.source;
  const treeCurrency = isAuditFinancials ? resolvedCurrency : null;

  // When FX preview is active, scale according to the target currency; otherwise use the file's own currency.
  const amountDisplaySystem: AmountDisplaySystem = fxPreviewActive
    ? displaySystemForCurrency(fxPreviewTarget.trim())
    : displaySystemForCurrency(treeCurrency);

  // Currency + denomination shown ONCE above the tree (instead of repeating on every row).
  // Currency comes from the data (treeCurrency) / FX target — never hardcoded.
  const figuresUnit = amountUnitLabel(amountDisplaySystem);
  const figuresCurrency = fxPreviewActive ? fxPreviewTarget.trim().toUpperCase() : treeCurrency;
  const figuresCaption = figuresUnit
    ? `All figures in ${figuresCurrency ? `${figuresCurrency} ` : ''}${figuresUnit.word} (${figuresUnit.suffix})`
    : figuresCurrency
      ? `All figures in ${figuresCurrency}`
      : null;


  const infoTitle =
    extractKind === 'audit_report'
      ? 'Extracted report'
      : extractKind === 'org_chart'
        ? 'Extracted data'
        : 'Financial numbers';

  const isExtractRunning = st === 'running' || st === 'queued';
  const isExtractError = st === 'error';

  const qualitativeVm = parseQualitativeReport(meta, {
    extractKind,
    extractStatus: st,
    showPlaceholderSample: true,
  });

  const status = statusConfig[file.status];
  const StatusIcon = status?.icon || Clock;

  const effectiveCompanyName = (() => {
    const n = (file.portfolioCompanyName || '').trim();
    if (!n) return null;
    // Backend uses a per-cycle placeholder company for "unassigned" uploads; treat that as "no company" in UI.
    if (n.toLowerCase() === 'unassigned (audit files)') return null;
    return n;
  })();

  const _loadViewerPdf = async () => {
    setViewerPdfUrl('');
    try {
      // Fetch through apiClient so the auth interceptor adds the Bearer token.
      // Convert to a blob URL — pdfjs can load it without any auth headers.
      const res = await apiClient.get(getFileStreamUrl(Number(fileId)), { responseType: 'blob' });
      setViewerPdfUrl(URL.createObjectURL(res.data));
    } catch {
      // PdfSourceViewer will show its own "failed to load" error
    }
  };

  const openSourceViewer = async (path: string) => {
    setSourceViewerRef(null); // path-based: resolve the ref from the sourceRefs map
    setSourceViewerPath(path);
    setSourceViewerOpen(true);
    await _loadViewerPdf();
  };

  // Open the viewer with a ref carried directly on a row (unmatched lines), bypassing the map.
  const openSourceRef = async (label: string, ref: SourceRef) => {
    setSourceViewerRef(ref);
    setSourceViewerPath(label);
    setSourceViewerOpen(true);
    await _loadViewerPdf();
  };

  const backAction = () => {
    if (from?.kind === 'company' && from.companyId) {
      navigate(`/company/${from.companyId}`, { state: { tab: 'files' } });
      return;
    }
    if (from?.kind === 'tracker') {
      navigate('/');
      return;
    }
    navigate('/file-tagging');
  };

  const startCurrencyEdit = () => {
    setCurrencyDraft((resolvedCurrency || '').toUpperCase());
    setCurrencyEditMode(true);
  };

  const requestCurrencySave = () => {
    const next = currencyDraft.trim().toUpperCase();
    if (!/^[A-Z]{3}$/.test(next)) {
      toast.error('Currency must be a 3-letter code (e.g. INR)');
      return;
    }
    setPendingCurrency(next);
    setCurrencyConfirmOpen(true);
  };

  const saveCurrencyEdit = async () => {
    const idNum = Number(fileId);
    if (!Number.isFinite(idNum)) return;
    const next = pendingCurrency;
    if (!next) {
      setCurrencyConfirmOpen(false);
      return;
    }
    setCurrencyBusy(true);
    try {
      await setFileExtractionCurrency(idNum, next);
      await refreshExtractStatus();
      setCurrencyEditMode(false);
      setCurrencyConfirmOpen(false);
      setPendingCurrency(null);
      toast.success(`Currency set to ${next}`);
    } catch (e) {
      toast.error(getApiErrorMessage(e, 'Could not update currency'));
    } finally {
      setCurrencyBusy(false);
    }
  };

  const runPersistFxConversion = async () => {
    const idNum = Number(fileId);
    const tgt = fxPreviewTarget.trim().toUpperCase();
    if (!Number.isFinite(idNum) || !/^[A-Z]{3}$/.test(tgt)) {
      toast.error('Choose a valid target currency.');
      return;
    }
    setPersistConvertBusy(true);
    try {
      await applyFileExtractionConvertCurrency(idNum, tgt);
      setFxPreviewTarget('');
      setFxPreviewRates(null);
      setPersistConvertConfirmOpen(false);
      await refreshExtractStatus();
      toast.success(`Converted stored amounts and set reporting currency to ${tgt}`);
    } catch (e) {
      toast.error(getApiErrorMessage(e, 'Could not apply FX conversion'));
    } finally {
      setPersistConvertBusy(false);
    }
  };

  const startFieldEdit = (path: string, current: unknown) => {
    setPendingFieldPath(path);
    setPendingFieldCurrent(current);
    setFieldConfirmOpen(true);
  };

  const confirmStartFieldEdit = () => {
    if (!pendingFieldPath) {
      setFieldConfirmOpen(false);
      setPendingFieldCurrent(null);
      return;
    }
    setEditingPath(pendingFieldPath);
    setEditingDraft(pendingFieldCurrent == null ? '' : String(pendingFieldCurrent));
    setFieldConfirmOpen(false);
    setPendingFieldPath(null);
    setPendingFieldCurrent(null);
  };

  // Step 1: validate the new value, then open the justification pop-up (a manual edit must carry
  // an optional reason so it can be shown in the "edited manually" disclaimer).
  const saveFieldEdit = () => {
    if (!editingPath) return;
    const raw = editingDraft.trim();
    let value: number | null = null;
    if (raw !== '') {
      const parsed = Number(raw.replace(/,/g, ''));
      if (!Number.isFinite(parsed)) {
        toast.error('Enter a valid number');
        return;
      }
      value = parsed;
    }
    setPendingEditValue(value);
    setJustifyReason('');
    setJustifyOpen(true);
  };

  // Step 2: persist the edit with its justification.
  const commitFieldEdit = async () => {
    if (!editingPath) return;
    const idNum = Number(fileId);
    if (!Number.isFinite(idNum)) return;
    setFieldSaveBusy(true);
    try {
      await patchAuditFinancialExtractedValue(idNum, {
        path: editingPath,
        value: pendingEditValue,
        reason: justifyReason.trim() || null,
      });
      await refreshExtractStatus();
      setJustifyOpen(false);
      setEditingPath(null);
      setEditingDraft('');
      setJustifyReason('');
      setPendingEditValue(null);
      toast.success('Field updated');
    } catch (e) {
      toast.error(getApiErrorMessage(e, 'Could not update field'));
    } finally {
      setFieldSaveBusy(false);
    }
  };

  /** Patch only the unmatched list in local state without touching extracted (e.g. dismiss/restore). */
  const applyUnmatchedOnlyToState = (unmatched: Array<Record<string, unknown>>) => {
    setExtractStatus((prev) =>
      prev
        ? {
            ...prev,
            meta: prev.meta ? { ...prev.meta, audit_financials_unmatched: unmatched } : prev.meta,
          }
        : prev,
    );
  };

  /** Apply a map / components mutation response to local state (avoids a stale round-trip). */
  const applyMapResultToState = (result: {
    extracted?: Record<string, unknown>;
    audit_financials_unmatched?: Array<Record<string, unknown>>;
    audit_financials_field_components?: Record<string, FieldComponent[]>;
    source_refs?: Record<string, SourceRef>;
  } | null | undefined) => {
    if (result && typeof result === 'object' && 'extracted' in result) {
      // The attach carried the line's page ref onto the new canonical path — refresh the map so
      // the now-mapped field shows its "view source" link without a full reload.
      if (result.source_refs && typeof result.source_refs === 'object') {
        setSourceRefs(result.source_refs);
      }
      setExtractStatus((prev) =>
        prev
          ? {
              ...prev,
              meta: prev.meta
                ? {
                    ...prev.meta,
                    extracted: result.extracted,
                    ...(result.audit_financials_unmatched !== undefined
                      ? { audit_financials_unmatched: result.audit_financials_unmatched }
                      : {}),
                    ...(result.audit_financials_field_components !== undefined
                      ? { audit_financials_field_components: result.audit_financials_field_components }
                      : {}),
                  }
                : prev.meta,
            }
          : prev,
      );
    } else {
      void refreshExtractStatus();
    }
  };

  /** Delete a field from the tree → move it to the unmatched panel (instant, recoverable). The
   *  manual fallback for a duplicate / mis-mapped line the automatic dedupe didn't catch. */
  const handleDetachField = async (path: string) => {
    const idNum = Number(fileId);
    if (!Number.isFinite(idNum) || !path) return;
    try {
      const result = await detachAuditFinancialField(idNum, { path });
      applyMapResultToState(result);
      const label = path.includes('.') ? path.slice(path.lastIndexOf('.') + 1).replace(/_/g, ' ') : path;
      toast.success(`Removed "${label}" — moved to unmatched (recoverable)`);
    } catch (e: unknown) {
      toast.error(getApiErrorMessage(e, 'Could not remove field'));
    }
  };

  /** A 409 from map-unmatched carries the breakdown; open the resolution dialog. Returns true if handled. */
  const openConflictFromError = (e: unknown): boolean => {
    const ae = e as { response?: { status?: number; data?: { detail?: unknown } } };
    const detail = ae.response?.data?.detail;
    if (ae.response?.status !== 409 || !detail || typeof detail !== 'object') return false;
    const d = detail as Record<string, unknown>;
    if (!Array.isArray(d.existing_components) || !d.incoming) return false;
    setConflict({
      unmatched_id: String(d.unmatched_id ?? ''),
      target_parent_path: String(d.target_parent_path ?? ''),
      target_key: String(d.target_key ?? ''),
      full_path: String(d.full_path ?? ''),
      existing_components: d.existing_components as FieldComponent[],
      incoming: d.incoming as ConflictPayload['incoming'],
      double_count_warning:
        typeof d.double_count_warning === 'string' ? d.double_count_warning : null,
    });
    return true;
  };

  /** Re-run the map with an explicit conflict resolution (sum / total / replace). */
  const resolveConflict = async (mode: 'sum' | 'total' | 'replace') => {
    const idNum = Number(fileId);
    if (!Number.isFinite(idNum) || !conflict) return;
    setConflictBusy(true);
    try {
      const result = await mapAuditFinancialUnmatched(idNum, {
        unmatched_id: conflict.unmatched_id,
        target_parent_path: conflict.target_parent_path,
        target_key: conflict.target_key,
        conflict_mode: mode,
      });
      applyMapResultToState(result);
      setConflict(null);
      setUnmatchedAttachOpen(false);
      setAttachRow(null);
      setRetagPath(null);
      setRetagComponents(null);
      toast.success(
        mode === 'sum'
          ? 'Added to the field total (breakdown kept)'
          : mode === 'total'
            ? 'Set as the field total'
            : 'Field replaced',
      );
    } catch (e) {
      toast.error(getApiErrorMessage(e, 'Could not resolve the field'));
    } finally {
      setConflictBusy(false);
    }
  };

  const openComponentsEditor = (path: string, components: FieldComponent[]) => {
    setEditComponentsPath(path);
    setEditComponentsDraft(components.map((c) => ({ ...c })));
  };

  const saveComponentsEdit = async () => {
    const idNum = Number(fileId);
    if (!Number.isFinite(idNum) || !editComponentsPath) return;
    setEditComponentsBusy(true);
    try {
      const result = await setAuditFinancialFieldComponents(idNum, {
        path: editComponentsPath,
        components: editComponentsDraft,
      });
      applyMapResultToState(result);
      setEditComponentsPath(null);
      toast.success('Breakdown updated');
    } catch (e) {
      toast.error(getApiErrorMessage(e, 'Could not update breakdown'));
    } finally {
      setEditComponentsBusy(false);
    }
  };

  const openAttachDialog = (row: Record<string, unknown>) => {
    setAttachRow(row);
    // Pre-fill from the server suggestion when present, so "Change…" lands on the
    // proposed target ready to confirm or tweak rather than on an arbitrary first path.
    const sug = readSuggestion(row);
    const defaultPath = sug?.parent_path || (leafPaths.length > 0 ? leafPaths[0].slice(0, leafPaths[0].lastIndexOf('.')) : parentPaths[0] || '');
    const defaultKey = sug?.key || (leafPaths.length > 0 && !sug?.parent_path ? leafPaths[0].slice(leafPaths[0].lastIndexOf('.') + 1) : '');
    setAttachTargetParentPath(defaultPath);
    setAttachTargetKey(defaultKey);
    setAttachConfirmOverwrite(false);
    setAttachMode('existing');
    setAttachNewPath('');
    setRetagPath(null);
    setRetagComponents(null);
    setAttachSaveError(null);
    setUnmatchedAttachOpen(true);
  };

  const openRetagDialog = (path: string) => {
    // Derive a synthetic "row" so the dialog shows the current field label.
    const key = path.includes('.') ? path.slice(path.lastIndexOf('.') + 1) : path;
    const parentPath = path.includes('.') ? path.slice(0, path.lastIndexOf('.')) : '';
    setAttachRow({ id: `retag-${path}`, document_label: key.replace(/_/g, ' '), key });
    setAttachTargetParentPath(parentPath);
    setAttachTargetKey(key);
    setAttachConfirmOverwrite(false);
    setAttachMode('existing');
    setAttachNewPath('');
    setRetagPath(path);
    setRetagComponents(fieldComponents[path]?.length ? fieldComponents[path] : null);
    setAttachSaveError(null);
    setUnmatchedAttachOpen(true);
  };

  const openAddFieldDialog = (parentPath: string) => {
    // Check if parentPath points to an existing scalar leaf in the extracted tree.
    // If so, we're in "convert leaf to composite" mode: the leaf becomes the container,
    // and the user names a new child field + value to place inside it.
    const tree = financialTreeBase ?? extractedObj;
    const currentVal = tree ? dottedGet(tree, parentPath) : undefined;
    const isLeafConvert = typeof currentVal === 'number';
    if (isLeafConvert) {
      const lastDot = parentPath.lastIndexOf('.');
      const leafParent = lastDot >= 0 ? parentPath.slice(0, lastDot) : '';
      const leafKey = lastDot >= 0 ? parentPath.slice(lastDot + 1) : parentPath;
      setAddFieldParent(leafParent);
      setAddFieldKey(leafKey);
      setAddFieldConvertingLeafPath(parentPath);
      setAddFieldConvertingLeafCurrentValue(currentVal);
      setAddFieldIsComposite(true);
    } else {
      setAddFieldParent(parentPath);
      setAddFieldKey('');
      setAddFieldConvertingLeafPath(null);
      setAddFieldConvertingLeafCurrentValue(null);
      setAddFieldIsComposite(false);
    }
    setAddFieldValue('');
    setAddFieldLeafKey('');
    setAddFieldLeafValue('');
    setAddFieldKeepExistingKey('');
    setAddFieldError(null);
    setAddFieldOpen(true);
  };

  const saveAddField = async () => {
    const idNum = Number(fileId);
    if (!Number.isFinite(idNum)) return;
    const parent = addFieldParent.trim();
    const key = toSnakeCase(addFieldKey);
    if (!key) {
      setAddFieldError('Field key is required');
      return;
    }
    const fullPath = parent ? `${parent}.${key}` : key;
    if (addFieldIsComposite) {
      const leafKey = toSnakeCase(addFieldLeafKey);
      const rawLeaf = addFieldLeafValue.trim();
      if (!leafKey) {
        setAddFieldError('Leaf field name is required');
        return;
      }
      if (!rawLeaf) {
        setAddFieldError('Leaf value is required');
        return;
      }
      const parsedLeaf = Number(rawLeaf.replace(/,/g, ''));
      if (!Number.isFinite(parsedLeaf)) {
        setAddFieldError('Enter a valid number for the leaf value');
        return;
      }
      const leafPath = `${fullPath}.${leafKey}`;
      setAddFieldBusy(true);
      setAddFieldError(null);
      try {
        if (addFieldConvertingLeafPath) {
          await patchAuditFinancialExtractedValue(idNum, { path: addFieldConvertingLeafPath, value: null });
        }
        await addAuditFinancialCompositeField(idNum, { path: fullPath });
        const keepKey = toSnakeCase(addFieldKeepExistingKey);
        if (keepKey && addFieldConvertingLeafCurrentValue != null) {
          await patchAuditFinancialExtractedValue(idNum, { path: `${fullPath}.${keepKey}`, value: addFieldConvertingLeafCurrentValue });
        }
        await patchAuditFinancialExtractedValue(idNum, { path: leafPath, value: parsedLeaf });
        await refreshExtractStatus();
        await refreshParentPaths();
        setAddFieldOpen(false);
        toast.success(`"${key}" converted to composite with child "${leafKey}"`);
      } catch (e) {
        setAddFieldError(getApiErrorMessage(e, 'Could not create composite field'));
      } finally {
        setAddFieldBusy(false);
      }
      return;
    }
    const raw = addFieldValue.trim();
    if (!raw) {
      setAddFieldError('Value is required');
      return;
    }
    const parsed = Number(raw.replace(/,/g, ''));
    if (!Number.isFinite(parsed)) {
      setAddFieldError('Enter a valid number');
      return;
    }
    setAddFieldBusy(true);
    setAddFieldError(null);
    try {
      await patchAuditFinancialExtractedValue(idNum, { path: fullPath, value: parsed });
      await refreshExtractStatus();
      setAddFieldOpen(false);
      toast.success(`Field "${key}" added`);
    } catch (e) {
      setAddFieldError(getApiErrorMessage(e, 'Could not add field'));
    } finally {
      setAddFieldBusy(false);
    }
  };

  const saveAttachUnmatched = async () => {
    const idNum = Number(fileId);
    if (!Number.isFinite(idNum) || !attachRow) return;
    let resolvedParentPath: string;
    let resolvedKey: string | null;
    if (attachMode === 'new') {
      const newPathTrimmed = attachNewPath.trim();
      if (!newPathTrimmed || !newPathTrimmed.includes('.')) {
        toast.error('New field path must be a dotted path, e.g. profit_and_loss.my_custom_field');
        return;
      }
      if (attachNewIsComposite) {
        // Composite field: create the empty {} bucket first, then the attach dialog will close.
        setUnmatchedAttachBusy(true);
        try {
          const result = await addAuditFinancialCompositeField(idNum, { path: newPathTrimmed });
          applyMapResultToState(result as Parameters<typeof applyMapResultToState>[0]);
          await refreshParentPaths();
          setUnmatchedAttachOpen(false);
          setAttachRow(null);
          const leafKey = newPathTrimmed.slice(newPathTrimmed.lastIndexOf('.') + 1);
          toast.success(`Composite field "${leafKey}" created`);
        } catch (e) {
          setAttachSaveError(getApiErrorMessage(e, 'Could not create composite field'));
        } finally {
          setUnmatchedAttachBusy(false);
        }
        return;
      }
      const lastDot = newPathTrimmed.lastIndexOf('.');
      resolvedParentPath = newPathTrimmed.slice(0, lastDot);
      resolvedKey = newPathTrimmed.slice(lastDot + 1) || null;
    } else {
      if (!attachTargetParentPath) {
        toast.error('Select a canonical parent path');
        return;
      }
      resolvedParentPath = attachTargetParentPath;
      resolvedKey = attachTargetKey.trim() || null;
    }
    setUnmatchedAttachBusy(true);
    try {
      let unmatchedId: string;
      if (retagPath) {
        // Retag: detach the existing canonical field first, then re-map the resulting unmatched row.
        const detachResult = await detachAuditFinancialField(idNum, { path: retagPath });
        applyMapResultToState(detachResult);
        const expectedDetachedId = `detached-${retagPath.replace(/\./g, '-')}`;
        const detachedRow = Array.isArray(detachResult.audit_financials_unmatched)
          ? detachResult.audit_financials_unmatched.find(
              (r) => isPlainObject(r) && String(r.id ?? '') === expectedDetachedId,
            )
          : null;
        if (!detachedRow) {
          toast.error('Could not find the detached row — please try again');
          setUnmatchedAttachBusy(false);
          return;
        }
        unmatchedId = expectedDetachedId;
      } else {
        unmatchedId = String(attachRow.id ?? '').trim();
        if (!unmatchedId) {
          toast.error('Unmatched row id is missing');
          setUnmatchedAttachBusy(false);
          return;
        }
      }
      const result = await mapAuditFinancialUnmatched(idNum, {
        unmatched_id: unmatchedId,
        target_parent_path: resolvedParentPath,
        target_key: resolvedKey,
        // confirm_overwrite kept only for an explicit "Overwrite" tick; otherwise an occupied
        // slot returns a 409 with the breakdown so the user picks sum / total / replace.
        confirm_overwrite: attachConfirmOverwrite,
      });
      applyMapResultToState(result);
      // If the retagged field had breakdown components, re-attach them to the new path.
      if (retagPath && retagComponents?.length) {
        const newPath = resolvedKey
          ? `${resolvedParentPath}.${resolvedKey}`
          : resolvedParentPath;
        try {
          const compResult = await setAuditFinancialFieldComponents(idNum, {
            path: newPath,
            components: retagComponents,
          });
          applyMapResultToState(compResult);
        } catch {
          // Non-fatal — the value moved correctly; the breakdown just won't show on the new field.
          toast.error('Field retagged but breakdown components could not be moved — re-add them manually if needed.');
        }
      }
      setUnmatchedAttachOpen(false);
      setAttachRow(null);
      setRetagPath(null);
      setRetagComponents(null);
      const intendedPath = result.intended_path;
      const effectivePath = result.effective_path;
      const wasRemapped = intendedPath && effectivePath && intendedPath !== effectivePath;
      if (wasRemapped) {
        const effectiveKey = effectivePath.includes('.') ? effectivePath.slice(effectivePath.lastIndexOf('.') + 1).replace(/_/g, ' ') : effectivePath;
        toast.success(
          retagPath ? 'Field retagged' : 'Unmatched field attached',
          { description: `Saved as "${effectiveKey}" (canonical equivalent of what you selected). Look for it there in the tree.`, duration: 7000 },
        );
      } else {
        toast.success(retagPath ? 'Field retagged' : 'Unmatched field attached');
      }
    } catch (e: unknown) {
      if (openConflictFromError(e)) {
        // Resolution dialog opened; keep the attach dialog underneath in case of cancel.
      } else {
        const msg = getApiErrorMessage(e, retagPath ? 'Could not retag field' : 'Could not attach unmatched field');
        if (msg.includes('points to a container')) {
          setAttachSaveError(
            `"${attachTargetKey || '(auto)'}" is a group, not a leaf field. ` +
            `Select a more specific parent path from the dropdown (e.g. include "${attachTargetKey}" in the parent), ` +
            `then enter a child key.`,
          );
        } else {
          toast.error(msg);
        }
      }
    } finally {
      setUnmatchedAttachBusy(false);
    }
  };

  /** One-click accept of the server's suggested mapping for an unmatched row. */
  const acceptSuggestion = async (row: Record<string, unknown>) => {
    const idNum = Number(fileId);
    const sug = readSuggestion(row);
    const unmatchedId = String(row.id ?? '').trim();
    if (!Number.isFinite(idNum) || !sug || !unmatchedId) return;
    setAcceptingId(unmatchedId);
    try {
      const result = await mapAuditFinancialUnmatched(idNum, {
        unmatched_id: unmatchedId,
        target_parent_path: sug.parent_path,
        target_key: sug.key,
        confirm_overwrite: false,
      });
      applyMapResultToState(result);
      toast.success(`Mapped to ${String(sug.key).replace(/_/g, ' ')}`);
    } catch (e: unknown) {
      // Occupied slot → open the conflict resolver (sum / total / replace) instead of losing data.
      if (!openConflictFromError(e)) {
        toast.error(getApiErrorMessage(e, 'Could not map field'));
      }
    } finally {
      setAcceptingId(null);
    }
  };

  const dismissUnmatched = async (row: Record<string, unknown>) => {
    const idNum = Number(fileId);
    const uid = String(row.id ?? '').trim();
    if (!uid || !Number.isFinite(idNum)) return;
    setDismissBusyId(uid);
    try {
      const result = await dismissAuditFinancialUnmatched(idNum, uid);
      if (Array.isArray(result.audit_financials_unmatched)) {
        applyUnmatchedOnlyToState(result.audit_financials_unmatched);
      }
    } catch (e) {
      toast.error(getApiErrorMessage(e, 'Could not dismiss field'));
    } finally {
      setDismissBusyId(null);
    }
  };

  const restoreUnmatched = async (row: Record<string, unknown>) => {
    const idNum = Number(fileId);
    const uid = String(row.id ?? '').trim();
    if (!uid || !Number.isFinite(idNum)) return;
    setRestoreBusyId(uid);
    try {
      const result = await restoreAuditFinancialUnmatched(idNum, uid);
      if (Array.isArray(result.audit_financials_unmatched)) {
        applyUnmatchedOnlyToState(result.audit_financials_unmatched);
      }
    } catch (e) {
      toast.error(getApiErrorMessage(e, 'Could not restore field'));
    } finally {
      setRestoreBusyId(null);
    }
  };

  return (
    <div className="h-full overflow-auto">
      <div className="sticky top-0 z-10 bg-white border-b border-gray-200 px-6 py-4">
        <div className="flex items-center gap-3">
          <button
            onClick={backAction}
            className="p-2 rounded-lg border border-gray-300 bg-white text-gray-700 hover:bg-gray-50 transition-all shrink-0"
          >
            <ArrowLeft className="h-4 w-4" />
          </button>
          <div className="flex items-center gap-3 min-w-0">
            <FileText className="h-5 w-5 text-red-400 shrink-0" />
            <div className="min-w-0">
              <h1 className="text-lg font-bold text-gray-900 truncate">{file.fileName}</h1>
              <div className="flex items-center gap-3 text-xs text-gray-500">
                <span>{file.size}</span>
                <span>•</span>
                <span>Uploaded {new Date(file.uploadedAt).toLocaleDateString()}</span>
                <span>•</span>
                <span className={`inline-flex items-center gap-1 px-2.5 py-0.5 rounded-full text-xs font-medium ${status?.badge}`}>
                  <StatusIcon className="h-3 w-3" />
                  {file.status}
                </span>
              </div>
            </div>
          </div>
          <div className="ml-auto shrink-0 flex items-center gap-2">
            <button
              onClick={handleDownload}
              disabled={downloadLoading}
              className="inline-flex items-center gap-1.5 px-3 py-1.5 rounded-lg border border-gray-300 bg-white text-sm font-medium text-gray-700 hover:bg-gray-50 transition-all disabled:opacity-60 disabled:cursor-not-allowed"
              title={
                downloadLoading
                  ? 'Preparing download link…'
                  : downloadUrl
                    ? 'Download the original file'
                    : downloadError || 'File not available for download'
              }
            >
              {downloadLoading ? (
                <Loader2 className="h-4 w-4 animate-spin" />
              ) : (
                <Download className="h-4 w-4" />
              )}
              Download
            </button>
            {file.portfolioCompanyId && effectiveCompanyName ? (
              <button
                onClick={() =>
                  navigate(`/company/${file.portfolioCompanyId}`, {
                    state: { from: { kind: 'file', fileId: String(fileId || file.id) } },
                  })
                }
                className="inline-flex items-center gap-1 px-2.5 py-0.5 rounded-full text-xs font-medium bg-blue-50 text-blue-700 hover:bg-blue-100 transition-colors"
                title="Open portfolio company"
              >
                <Link2 className="h-3 w-3" />
                {effectiveCompanyName}
              </button>
            ) : (
              <button
                onClick={() => void openCompanyAttachDialog()}
                className="inline-flex items-center gap-1 px-2.5 py-0.5 rounded-full text-xs font-medium bg-amber-50 text-amber-700 hover:bg-amber-100 border border-amber-200 transition-colors"
                title="Attach this file to a company and entity"
              >
                <Paperclip className="h-3 w-3" />
                Attach to company
              </button>
            )}
            {file.taggedEntityName && (
              <span className="inline-flex items-center gap-1 px-2.5 py-0.5 rounded-full text-xs font-medium bg-gray-100 text-gray-800">
                {file.taggedEntityName}
              </span>
            )}
            {/* Compliance badge moved to the Compliance report card header (QualitativeReportContent). */}
          </div>
        </div>
      </div>

      <Tabs defaultValue="qualitative" className="p-6">
        <TabsList className="mb-6 bg-gray-100 rounded-lg p-1 flex flex-wrap gap-1">
          <TabsTrigger value="qualitative" className="rounded-md data-[state=active]:bg-white data-[state=active]:shadow-sm text-sm">
            <span className="inline-flex items-center gap-1.5">
              <ScrollText className="h-3.5 w-3.5 opacity-70" />
              Compliance Section
            </span>
          </TabsTrigger>
          <TabsTrigger value="info" className="rounded-md data-[state=active]:bg-white data-[state=active]:shadow-sm text-sm">
            Financial numbers
          </TabsTrigger>
          <TabsTrigger value="render" className="rounded-md data-[state=active]:bg-white data-[state=active]:shadow-sm text-sm">
            Document Preview
          </TabsTrigger>
        </TabsList>

        <TabsContent value="qualitative" className="space-y-4">
          <QualitativeReportContent {...qualitativeVm} showTechnicalHint />
        </TabsContent>

        <TabsContent value="info" className="space-y-6">
          {isExtractError ? (
            <div className="bg-white rounded-lg border border-red-200 shadow-sm p-6">
              <div className="text-sm font-medium text-red-800">Extraction failed</div>
              {extractStatus?.error_message ? (
                <div className="text-xs text-red-700 mt-2 whitespace-pre-wrap">{extractStatus.error_message}</div>
              ) : null}
            </div>
          ) : null}

          {isExtractRunning ? (
            <div className="bg-white rounded-lg border border-gray-200 shadow-sm p-6">
              <div className="flex items-center gap-3">
                <Clock className="h-5 w-5 text-gray-400" />
                <div>
                  <div className="text-sm font-medium text-gray-900">Processing</div>
                  <div className="text-xs text-gray-500">Extraction is running in the background. This view refreshes every few seconds.</div>
                </div>
              </div>
            </div>
          ) : null}

          {isExtractDone && extractedObj ? (
            <div className="bg-white rounded-xl border border-gray-200 shadow-sm p-5 sm:p-6">
              <div className="flex flex-col gap-4 sm:flex-row sm:items-start sm:justify-between sm:gap-6 mb-4">
                <h3 className="text-sm font-semibold text-gray-900 flex items-center gap-2 shrink-0">
                  <TrendingUp className="h-4 w-4 text-gray-400" />
                  {infoTitle}
                </h3>
                {isAuditFinancials ? (
                  <Button
                    type="button"
                    size="sm"
                    variant={isMinimized ? 'secondary' : 'outline'}
                    className="h-8 text-xs gap-1.5 shrink-0 self-start sm:self-auto"
                    onClick={() => setIsMinimized((prev) => !prev)}
                  >
                    <EyeOff className="h-3.5 w-3.5" />
                    {isMinimized ? 'Summary view on' : 'Summary view'}
                  </Button>
                ) : null}
              </div>
              {isAuditFinancials ? (
                <>
                <div className="mb-5 rounded-xl border border-gray-100 bg-gradient-to-b from-gray-50/90 to-white px-4 py-4 sm:px-5">
                  <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
                    <div>
                      <p className="text-[11px] font-medium uppercase tracking-wide text-gray-500">Reporting currency</p>
                      <p className="mt-0.5 text-xs text-gray-600">Used as the prefix for numeric line items in this file.</p>
                    </div>
                    <div className="flex flex-col gap-2 sm:items-end min-w-0 w-full sm:w-auto sm:max-w-md">
                      {!resolvedCurrency && !currencyEditMode ? (
                        <div
                          className="flex flex-wrap items-center gap-2 rounded-lg border border-amber-200/80 bg-amber-50/60 px-3 py-2.5 text-xs text-amber-900"
                          title="We could not detect a currency in this document."
                        >
                          <AlertTriangle className="h-4 w-4 shrink-0 text-amber-600" />
                          <span className="font-medium">Not detected</span>
                          <span className="text-amber-800/90 hidden sm:inline">— set a code to prefix amounts.</span>
                          <Button
                            type="button"
                            variant="outline"
                            size="sm"
                            className="h-7 text-xs border-amber-300 bg-white hover:bg-amber-50 ml-auto sm:ml-0"
                            onClick={startCurrencyEdit}
                          >
                            <Pencil className="h-3 w-3 mr-1" />
                            Set currency
                          </Button>
                        </div>
                      ) : null}
                      {resolvedCurrency && !currencyEditMode ? (
                        <div className="flex flex-wrap items-center gap-2 sm:justify-end">
                          <div
                            className="inline-flex items-center gap-2 rounded-lg border border-gray-200 bg-white px-3 py-2 shadow-sm"
                            title="Currency from extraction metadata."
                          >
                            <span className="text-lg font-semibold font-mono tracking-tight text-gray-900">{resolvedCurrency}</span>
                            <span
                              className={`text-[10px] font-semibold uppercase tracking-wider px-2 py-0.5 rounded-full ${
                                currencySource === 'manual'
                                  ? 'bg-violet-100 text-violet-800'
                                  : currencySource === 'converted'
                                    ? 'bg-sky-100 text-sky-800'
                                    : 'bg-slate-100 text-slate-600'
                              }`}
                            >
                              {currencySource === 'manual'
                                ? 'Manual'
                                : currencySource === 'converted'
                                  ? 'FX converted'
                                  : 'Detected'}
                            </span>
                          </div>
                          <Button type="button" variant="outline" size="sm" className="h-8 text-xs" onClick={startCurrencyEdit}>
                            <Pencil className="h-3.5 w-3.5 mr-1.5" />
                            Change
                          </Button>
                        </div>
                      ) : null}
                      {currencyEditMode ? (
                        <div className="flex flex-col gap-3 w-full sm:flex-row sm:flex-wrap sm:items-center sm:justify-end">
                          <Select value={currencyDraft} onValueChange={(v) => setCurrencyDraft(v)} disabled={currencyBusy}>
                            <SelectTrigger className="h-9 w-full sm:w-[min(100%,280px)] text-sm">
                              <SelectValue placeholder="Select currency" />
                            </SelectTrigger>
                            <SelectContent className="max-h-72">
                              {TOP_TRADED_CURRENCIES.map((c) => (
                                <SelectItem key={c.code} value={c.code} className="text-sm">
                                  <span className="font-mono font-medium">{c.code}</span>
                                  <span className="text-muted-foreground ml-2">{c.name}</span>
                                </SelectItem>
                              ))}
                            </SelectContent>
                          </Select>
                          <div className="flex gap-2 shrink-0">
                            <Button type="button" size="sm" className="h-9 text-xs px-4" disabled={currencyBusy} onClick={requestCurrencySave}>
                              Save
                            </Button>
                            <Button
                              type="button"
                              size="sm"
                              variant="outline"
                              className="h-9 text-xs px-4"
                              disabled={currencyBusy}
                              onClick={() => setCurrencyEditMode(false)}
                            >
                              Cancel
                            </Button>
                          </div>
                        </div>
                      ) : null}
                    </div>
                  </div>
                </div>
                <div className="mb-5 rounded-xl border border-blue-100 bg-blue-50/40 px-4 py-4 sm:px-5">
                  <div className="flex flex-col gap-3 sm:flex-row sm:items-start sm:justify-between">
                    <div className="min-w-0">
                      <p className="text-[11px] font-medium uppercase tracking-wide text-gray-600">
                        Preview vs apply FX conversion
                      </p>
                      <p className="mt-1 text-xs text-gray-700 leading-relaxed max-w-xl">
                        Select a currency to{' '}
                        <span className="font-semibold text-gray-900">preview only</span> converted line items below (P&amp;L, balance sheet, cash
                        flow). This does not change the database until you confirm{' '}
                        <span className="font-semibold text-gray-900">Apply FX to stored data</span>. Applying overwrites extracted numbers,
                        updates reporting currency, and syncs entity financial data linked to this file.
                      </p>
                    </div>
                    <div className="flex flex-col gap-2 w-full sm:w-auto sm:max-w-xs shrink-0">
                      <Select
                        value={fxPreviewTarget}
                        onValueChange={(v) => setFxPreviewTarget(v)}
                        disabled={!resolvedCurrency || currencyEditMode}
                      >
                        <SelectTrigger className="h-9 w-full text-sm bg-white">
                          <SelectValue placeholder={resolvedCurrency ? 'Preview currency…' : 'Set reporting currency first'} />
                        </SelectTrigger>
                        <SelectContent className="max-h-72">
                          {TOP_TRADED_CURRENCIES.filter((c) => c.code !== resolvedCurrency?.toUpperCase()).map((c) => (
                            <SelectItem key={c.code} value={c.code}>
                              <span className="font-mono">{c.code}</span>
                              <span className="text-muted-foreground ml-2 text-xs">{c.name}</span>
                            </SelectItem>
                          ))}
                        </SelectContent>
                      </Select>
                      <div className="flex gap-2 flex-wrap">
                        <Button
                          type="button"
                          variant="outline"
                          size="sm"
                          className="h-8 text-xs"
                          disabled={!fxPreviewTarget}
                          onClick={() => {
                            setFxPreviewTarget('');
                            setFxPreviewRates(null);
                          }}
                        >
                          Clear preview
                        </Button>
                        <Button
                          type="button"
                          size="sm"
                          className="h-8 text-xs"
                          disabled={!fxPreviewActive || persistConvertBusy}
                          onClick={() => setPersistConvertConfirmOpen(true)}
                        >
                          Apply FX to stored data
                        </Button>
                      </div>
                    </div>
                  </div>
                  {resolvedCurrency ? (
                    <div className="mt-3 pt-3 border-t border-blue-100/80 text-[11px] text-gray-700 space-y-1">
                      <div className="flex flex-wrap items-center gap-2">
                        {fxPreviewFetchBusy ? <Loader2 className="h-3.5 w-3.5 animate-spin text-blue-700" aria-hidden /> : null}
                        <span>
                          Stored reporting currency:{' '}
                          <span className="font-mono font-semibold text-gray-900">{resolvedCurrency}</span>
                          {fxPreviewTarget.trim() &&
                          fxPreviewTarget.trim().toUpperCase() !== resolvedCurrency.toUpperCase() &&
                          fxPreviewConversionRate != null ? (
                            <>
                              {' '}
                              → preview as{' '}
                              <span className="font-mono font-semibold text-gray-900">
                                {fxPreviewTarget.trim().toUpperCase()}
                              </span>
                              {' '}
                              <span className="text-gray-600">
                                (× {fxPreviewConversionRate.toLocaleString(undefined, { maximumFractionDigits: 8 })})
                              </span>
                              {fxPreviewRates?.fy_end ? (
                                <span className="text-gray-500"> · rate as of {fxPreviewRates.fy_end} ({fxPreviewRates.rate_date})</span>
                              ) : fxPreviewRates?.timestamp ? (
                                <span className="text-gray-500"> · rate time {fxPreviewRates.timestamp}</span>
                              ) : null}
                            </>
                          ) : null}
                        </span>
                      </div>
                      {fxPreviewActive ? (
                        <p className="text-amber-900 font-medium">
                          Showing converted amounts below for viewing only — not saved until you apply.
                        </p>
                      ) : fxPreviewTarget.trim() &&
                        fxPreviewTarget.trim().toUpperCase() !== resolvedCurrency.toUpperCase() &&
                        !fxPreviewFetchBusy &&
                        fxPreviewConversionRate == null ? (
                        <p className="text-amber-800">No FX quote for that pair.</p>
                      ) : null}
                    </div>
                  ) : null}
                </div>
                </>
              ) : null}
              {isAuditFinancials ? (
                <p className="text-xs text-gray-500 mb-4 border-l-2 border-gray-200 pl-3 leading-relaxed">
                  Line items that do not appear in the JSON tree were not found in the source statements for this run.
                  A numeric <span className="font-mono">0</span> means the document reported zero for that line—do not
                  infer absence from zero.
                </p>
              ) : null}
              {isAuditFinancials && figuresCaption ? (
                <p className="text-xs text-gray-600 mb-4 text-right">
                  <span className="text-red-500">*</span> {figuresCaption}
                </p>
              ) : null}
              {isAuditFinancials &&
              auditDisplay.parseErrorShort &&
              !auditDisplay.hasNumericLineItems ? (
                <div className="mb-4 rounded-lg border border-amber-200 bg-amber-50/90 px-4 py-3 text-xs text-amber-950">
                  <p className="font-medium text-amber-900">Could not parse model output as structured JSON</p>
                  <p className="mt-1 font-mono text-[11px] text-amber-800/95 break-words">
                    {auditDisplay.parseErrorShort}
                  </p>
                  {auditDisplay.rawTechnicalSnippet ? (
                    <details className="mt-2">
                      <summary className="cursor-pointer text-amber-800 underline underline-offset-2">
                        Technical details
                      </summary>
                      <pre className="mt-2 max-h-56 overflow-auto rounded border border-amber-100/80 bg-white/90 p-2 text-[10px] leading-relaxed text-gray-800 whitespace-pre-wrap break-words">
                        {auditDisplay.rawTechnicalSnippet}
                      </pre>
                    </details>
                  ) : null}
                </div>
              ) : null}
              {isAuditFinancials && isMinimized ? (
                <div className="space-y-3">
                  {minimizedEntries.map(([k, v]) => {
                    const total = sumNumericLeaves(v);
                    return (
                      <div
                        key={k}
                        className="flex items-center justify-between gap-4 rounded-lg border border-gray-100 bg-gray-50/80 px-4 py-3"
                      >
                        <span className="text-sm font-normal text-gray-900">{formatKey(k)}</span>
                        <span className="text-sm font-normal font-mono text-gray-900">
                          {typeof total === 'number'
                            ? formatAmountBare(
                                fxPreviewActive && fxPreviewConversionRate != null ? total * fxPreviewConversionRate : total,
                                amountDisplaySystem,
                              )
                            : '—'}
                        </span>
                      </div>
                    );
                  })}
                </div>
              ) : (
                <ReconcileContext.Provider value={handleReconcileJump}>
                <EbitdaBreakdownContext.Provider value={ebitdaBreakdownForTree}>
                <ManualEditsContext.Provider value={manualEditsForTree}>
                <FieldComponentsContext.Provider value={fieldComponents}>
                <StructuredJson
                  value={sourcePositionView.tree ?? displayedExtractedObj ?? financialTreeBase ?? extractedObj}
                  currency={treeCurrency}
                  amountDisplaySystem={amountDisplaySystem}
                  editable={isAuditFinancials}
                  editingPath={editingPath}
                  editingDraft={editingDraft}
                  onStartEdit={startFieldEdit}
                  onDraftChange={setEditingDraft}
                  onSaveEdit={() => void saveFieldEdit()}
                  onCancelEdit={() => {
                    setEditingPath(null);
                    setEditingDraft('');
                  }}
                  editBusy={fieldSaveBusy}
                  sourceRefs={sourceRefs}
                  onShowSource={openSourceViewer}
                  onDetach={isAuditFinancials ? handleDetachField : undefined}
                  onRetag={isAuditFinancials ? openRetagDialog : undefined}
                  fxPreviewEnabled={Boolean(fxPreviewActive)}
                  fxPreviewRate={fxPreviewConversionRate ?? 1}
                  fxPreviewCurrency={
                    fxPreviewActive && fxPreviewTarget.trim()
                      ? fxPreviewTarget.trim().toUpperCase()
                      : null
                  }
                  onAcceptUnmatched={isAuditFinancials ? acceptSuggestion : undefined}
                  onAttachUnmatched={isAuditFinancials ? openAttachDialog : undefined}
                  onDismissUnmatched={isAuditFinancials ? dismissUnmatched : undefined}
                  onShowSourceRefRow={openSourceRef}
                  unmatchedBusyId={acceptingId}
                  onAddChild={isAuditFinancials ? openAddFieldDialog : undefined}
                />
                </FieldComponentsContext.Provider>
                </ManualEditsContext.Provider>
                </EbitdaBreakdownContext.Provider>
                </ReconcileContext.Provider>
              )}
              {isAuditFinancials && !isMinimized && (unmatchedRows.length > 0 || otherBucketRows.length > 0 || rolledUpPaths.length > 0) ? (
                <div ref={unmatchedSectionRef} className="mt-6 pt-4 border-t border-gray-100 space-y-5">
                  {rolledUpPaths.length > 0 && (
                    <div>
                      <h4 className="text-sm font-semibold text-gray-700 mb-1">Combined fields</h4>
                      <p className="text-xs text-gray-500 mb-3">
                        Canonical fields built by rolling up more than one document line. The field value is the
                        signed sum of its components — edit the breakdown to drop a line or flip a sign.
                      </p>
                      <div className="space-y-2">
                        {rolledUpPaths.map((p) => {
                          const comps = fieldComponents[p] || [];
                          const total = comps.reduce(
                            (acc, c) => acc + (typeof c.value === 'number' ? c.value : 0) * (c.sign === '-' ? -1 : 1),
                            0,
                          );
                          const leafKey = p.split('.').pop() || p;
                          return (
                            <div key={p} className="rounded-md border border-sky-100 bg-sky-50/40 px-3 py-2.5">
                              <div className="flex items-start justify-between gap-3">
                                <div className="min-w-0">
                                  <div className="text-sm text-gray-900">{leafKey.replace(/_/g, ' ')}</div>
                                  <div className="text-[11px] font-mono text-gray-400 break-all">{p}</div>
                                </div>
                                <div className="flex items-center gap-3 shrink-0">
                                  <span className="font-mono text-sm text-gray-900">
                                    {formatAmountDisplay(total, treeCurrency, amountDisplaySystem)}
                                  </span>
                                  <Button
                                    type="button"
                                    size="sm"
                                    variant="outline"
                                    className="h-7 text-xs"
                                    onClick={() => openComponentsEditor(p, comps)}
                                  >
                                    Edit breakdown
                                  </Button>
                                </div>
                              </div>
                              <div className="mt-1.5 flex flex-wrap gap-x-3 gap-y-1 text-[11px] text-gray-600">
                                {comps.map((c, i) => (
                                  <span key={c.id ?? i} className="inline-flex items-center gap-1">
                                    <span className={c.sign === '-' ? 'text-rose-600' : 'text-emerald-600'}>
                                      {c.sign === '-' ? '−' : '+'}
                                    </span>
                                    <span className="text-gray-500">{c.label}</span>
                                    <span className="font-mono text-gray-400">
                                      {formatAmountDisplay(typeof c.value === 'number' ? c.value : 0, treeCurrency, amountDisplaySystem)}
                                    </span>
                                  </span>
                                ))}
                              </div>
                            </div>
                          );
                        })}
                      </div>
                    </div>
                  )}
                  {unmatchedRows.length > 0 && (
                    <div>
                      <h4 className="text-sm font-semibold text-gray-700 mb-1">Unidentified fields</h4>
                      <p className="text-xs text-gray-500 mb-3">
                        Extracted lines that couldn&apos;t be placed in the statement above (no usable source
                        position). Attach them to a canonical field or create a new one.
                      </p>
                      <UnmatchedTable
                        rows={unmatchedLeftover}
                        treeCurrency={treeCurrency}
                        amountDisplaySystem={amountDisplaySystem}
                        parentPaths={parentPaths}
                        onAttach={openAttachDialog}
                        onAccept={acceptSuggestion}
                        onDismiss={dismissUnmatched}
                        onRestore={restoreUnmatched}
                        acceptBusyId={acceptingId}
                        dismissBusyId={dismissBusyId}
                        restoreBusyId={restoreBusyId}
                        highlightIds={highlightedUnmatchedIds}
                        onShowSourceRef={openSourceRef}
                      />
                    </div>
                  )}
                  {otherBucketRows.length > 0 && (
                    <div>
                      <h4 className="text-sm font-semibold text-gray-700 mb-1">Additional extracted fields</h4>
                      <p className="text-xs text-gray-500 mb-3">
                        Fields placed in overflow buckets during extraction. Attach them to a canonical field or create a new one.
                      </p>
                      <UnmatchedTable
                        rows={otherBucketRows}
                        treeCurrency={treeCurrency}
                        amountDisplaySystem={amountDisplaySystem}
                        parentPaths={parentPaths}
                        onAttach={openAttachDialog}
                        onAccept={acceptSuggestion}
                        onDismiss={dismissUnmatched}
                        onRestore={restoreUnmatched}
                        acceptBusyId={acceptingId}
                        dismissBusyId={dismissBusyId}
                        restoreBusyId={restoreBusyId}
                        highlightIds={highlightedUnmatchedIds}
                        onShowSourceRef={openSourceRef}
                      />
                    </div>
                  )}
                </div>
              ) : null}
            </div>
          ) : null}

          {!isExtractError && !isExtractRunning && isExtractDone && !extractedObj ? (
            <div className="bg-white rounded-lg border border-gray-200 shadow-sm p-6 text-sm text-gray-600">
              Extraction finished, but no structured JSON was returned. Check logs or re-run extraction.
            </div>
          ) : null}

          <AlertDialog
            open={currencyConfirmOpen}
            onOpenChange={(open) => {
              setCurrencyConfirmOpen(open);
              if (!open) setPendingCurrency(null);
            }}
          >
            <AlertDialogContent className="w-[min(100vw-1.5rem,42rem)] max-w-none sm:max-w-2xl p-8 gap-6 max-h-[min(90vh,720px)] overflow-y-auto shadow-xl">
              <AlertDialogHeader className="space-y-3 text-left">
                <AlertDialogTitle className="text-xl pr-8">Confirm currency change</AlertDialogTitle>
                <AlertDialogDescription asChild>
                  <div className="space-y-4 text-sm text-muted-foreground text-left">
                    <p>This updates reporting currency in extraction metadata only. Amounts are not converted.</p>
                    <div className="rounded-lg border bg-muted/40 px-4 py-3">
                      <p className="text-[11px] font-medium uppercase tracking-wide text-muted-foreground mb-1">New code</p>
                      <p className="font-mono text-lg font-semibold text-foreground tracking-tight">{pendingCurrency ?? '—'}</p>
                    </div>
                  </div>
                </AlertDialogDescription>
              </AlertDialogHeader>
              <AlertDialogFooter className="gap-2 sm:space-x-0">
                <AlertDialogCancel disabled={currencyBusy} className="sm:mt-0">
                  Cancel
                </AlertDialogCancel>
                <Button type="button" disabled={currencyBusy || !pendingCurrency} onClick={() => void saveCurrencyEdit()}>
                  {currencyBusy ? 'Saving…' : 'Confirm'}
                </Button>
              </AlertDialogFooter>
            </AlertDialogContent>
          </AlertDialog>

          <AlertDialog open={persistConvertConfirmOpen} onOpenChange={setPersistConvertConfirmOpen}>
            <AlertDialogContent className="w-[min(100vw-1.5rem,42rem)] max-w-none sm:max-w-2xl p-8 gap-6 max-h-[min(90vh,720px)] overflow-y-auto shadow-xl">
              <AlertDialogHeader className="space-y-3 text-left">
                <AlertDialogTitle className="text-xl pr-8">Apply FX conversion to stored data?</AlertDialogTitle>
                <AlertDialogDescription asChild>
                  <div className="space-y-4 text-sm text-muted-foreground text-left">
                    <p>
                      This permanently multiplies numeric line items under profit &amp; loss, balance sheet, and cash flow by
                      the current exchange rate, sets reporting currency to{' '}
                      <span className="font-mono font-semibold text-foreground">{fxPreviewTarget.trim().toUpperCase()}</span>,
                      and updates linked extracted financial totals (FinancialData) for this file&apos;s entity.
                    </p>
                    <p>You can repeat this later from whatever currency is saved then (rates are fetched again at apply time).</p>
                    {resolvedCurrency &&
                    fxPreviewTarget.trim().toUpperCase() !== resolvedCurrency.toUpperCase() &&
                    fxPreviewConversionRate != null ? (
                      <div className="rounded-lg border bg-muted/40 px-4 py-3 text-foreground space-y-1">
                        <p className="text-[11px] font-medium uppercase tracking-wide text-muted-foreground">Rate preview</p>
                        <p className="font-mono text-sm">
                          1 {resolvedCurrency} ≈{' '}
                          {fxPreviewConversionRate.toLocaleString(undefined, { maximumFractionDigits: 8 })}{' '}
                          {fxPreviewTarget.trim().toUpperCase()}
                        </p>
                      </div>
                    ) : null}
                  </div>
                </AlertDialogDescription>
              </AlertDialogHeader>
              <AlertDialogFooter className="gap-2 sm:space-x-0">
                <AlertDialogCancel disabled={persistConvertBusy} className="sm:mt-0">
                  Cancel
                </AlertDialogCancel>
                <Button
                  type="button"
                  disabled={persistConvertBusy || !fxPreviewActive}
                  onClick={() => void runPersistFxConversion()}
                >
                  {persistConvertBusy ? 'Applying…' : 'Apply permanently'}
                </Button>
              </AlertDialogFooter>
            </AlertDialogContent>
          </AlertDialog>

          <AlertDialog
            open={unmatchedAttachOpen}
            onOpenChange={(open) => {
              setUnmatchedAttachOpen(open);
              if (!open) {
                setAttachRow(null);
                setAttachTargetParentPath('');
                setAttachTargetKey('');
                setAttachConfirmOverwrite(false);
                setAttachMode('existing');
                setAttachNewPath('');
                setAttachNewIsComposite(false);
                setRetagPath(null);
                setRetagComponents(null);
                setAttachSaveError(null);
              }
            }}
          >
            <AlertDialogContent className="w-[min(100vw-1.5rem,42rem)] max-w-none sm:max-w-2xl p-8 gap-6 max-h-[min(90vh,720px)] overflow-y-auto shadow-xl">
              <AlertDialogHeader className="space-y-3 text-left">
                <AlertDialogTitle className="text-xl pr-8">
                  {retagPath ? 'Retag canonical field' : 'Attach unmatched field'}
                </AlertDialogTitle>
                <AlertDialogDescription asChild>
                  <div className="space-y-4 text-sm text-muted-foreground text-left">
                    <div className="rounded-lg border bg-muted/40 px-4 py-3">
                      <p className="text-[11px] font-medium uppercase tracking-wide text-muted-foreground mb-1">
                        {retagPath ? 'Current canonical path' : 'Field'}
                      </p>
                      <p className="font-mono text-sm text-foreground break-all">
                        {retagPath ?? String(attachRow?.document_label ?? attachRow?.key ?? attachRow?.id ?? '—')}
                      </p>
                    </div>
                    {retagPath && (
                      <p className="rounded-md border border-sky-100 bg-sky-50/60 px-3 py-2 text-[11px] leading-relaxed text-sky-900">
                        This will detach the field from its current canonical slot and re-map it to the target you choose below.
                        Roll-up breakdowns will be dropped (only the summed value is preserved).
                      </p>
                    )}

                    {/* Mode toggle — hidden for retag (always "existing" to pick a canonical slot) */}
                    {!retagPath && (
                      <div className="flex rounded-md border border-input overflow-hidden text-xs font-medium">
                        <button
                          type="button"
                          className={`flex-1 px-3 py-2 transition-colors ${attachMode === 'existing' ? 'bg-gray-900 text-white' : 'bg-background text-muted-foreground hover:bg-muted'}`}
                          onClick={() => setAttachMode('existing')}
                          disabled={unmatchedAttachBusy}
                        >
                          Attach to existing field
                        </button>
                        <button
                          type="button"
                          className={`flex-1 px-3 py-2 border-l border-input transition-colors ${attachMode === 'new' ? 'bg-gray-900 text-white' : 'bg-background text-muted-foreground hover:bg-muted'}`}
                          onClick={() => setAttachMode('new')}
                          disabled={unmatchedAttachBusy}
                        >
                          Create new field
                        </button>
                      </div>
                    )}

                    {(retagPath || attachMode === 'existing') ? (
                      <>
                        {(() => {
                          // Combine leaf paths (canonical scalar slots) first, then container paths
                          // (for overflow buckets like .other). Leaf paths are shown with their last
                          // segment bolded so users can scan quickly.
                          const fullPath = attachTargetParentPath
                            ? attachTargetKey
                              ? `${attachTargetParentPath}.${attachTargetKey}`
                              : attachTargetParentPath
                            : '';
                          const allPaths = [
                            ...leafPaths,
                            ...parentPaths.filter((p) => !leafPaths.includes(p)),
                          ];
                          const isLeaf = (p: string) => leafPaths.includes(p);
                          return (
                            <div className="space-y-2">
                              <label className="text-xs font-medium text-foreground">Canonical field</label>
                              <Select
                                value={fullPath || ''}
                                onValueChange={(val) => {
                                  setAttachSaveError(null);
                                  const lastDot = val.lastIndexOf('.');
                                  if (isLeaf(val) && lastDot !== -1) {
                                    setAttachTargetParentPath(val.slice(0, lastDot));
                                    setAttachTargetKey(val.slice(lastDot + 1));
                                  } else {
                                    setAttachTargetParentPath(val);
                                    // Auto-populate key from label when target ends in ".other"
                                    if (val.endsWith('.other') || val === 'other') {
                                      const rawLabel = String(attachRow?.document_label ?? attachRow?.key ?? '');
                                      if (rawLabel) setAttachTargetKey(toSnakeCase(rawLabel));
                                      else setAttachTargetKey('');
                                    } else {
                                      setAttachTargetKey('');
                                    }
                                  }
                                }}
                                disabled={unmatchedAttachBusy || allPaths.length === 0}
                              >
                                <SelectTrigger className="h-9 w-full text-sm">
                                  <SelectValue placeholder="Select canonical field" />
                                </SelectTrigger>
                                <SelectContent className="max-h-72">
                                  {allPaths.map((p) => {
                                    const lastDot = p.lastIndexOf('.');
                                    const prefix = lastDot !== -1 ? p.slice(0, lastDot + 1) : '';
                                    const leaf = lastDot !== -1 ? p.slice(lastDot + 1) : p;
                                    return (
                                      <SelectItem key={p} value={p} className="text-xs font-mono">
                                        <span className="text-muted-foreground">{prefix}</span>
                                        <span className={isLeaf(p) ? 'font-semibold text-foreground' : ''}>{leaf}</span>
                                      </SelectItem>
                                    );
                                  })}
                                </SelectContent>
                              </Select>
                              {!isLeaf(fullPath) && fullPath && (
                                <div className="space-y-1 pt-1">
                                  <label className="text-xs font-medium text-foreground">Target key (optional)</label>
                                  <input
                                    type="text"
                                    className="h-9 w-full rounded-md border border-input bg-background px-3 py-1 text-sm font-mono"
                                    placeholder="leave blank to auto-generate from field label"
                                    value={attachTargetKey}
                                    onChange={(e) => { setAttachTargetKey(toSnakeCase(e.target.value)); setAttachSaveError(null); }}
                                    disabled={unmatchedAttachBusy}
                                  />
                                  <p className="text-[11px] text-muted-foreground">Key is auto-converted to snake_case.</p>
                                </div>
                              )}
                            </div>
                          );
                        })()}
                      </>
                    ) : (
                      <>
                        <div className="space-y-2">
                          <label className="text-xs font-medium text-foreground">Parent path</label>
                          <Select
                            value={parentPaths.includes(attachNewPath.includes('.') ? attachNewPath.slice(0, attachNewPath.lastIndexOf('.')) : attachNewPath) ? (attachNewPath.includes('.') ? attachNewPath.slice(0, attachNewPath.lastIndexOf('.')) : attachNewPath) : '__custom__'}
                            onValueChange={(val) => {
                              if (val === '__custom__') return;
                              const currentKey = attachNewPath.includes('.') ? attachNewPath.slice(attachNewPath.lastIndexOf('.') + 1) : '';
                              setAttachNewPath(currentKey ? `${val}.${currentKey}` : val);
                            }}
                            disabled={unmatchedAttachBusy || parentPaths.length === 0}
                          >
                            <SelectTrigger className="h-9 w-full text-sm">
                              <SelectValue placeholder="Select canonical parent path" />
                            </SelectTrigger>
                            <SelectContent className="max-h-72">
                              {parentPaths.map((p) => (
                                <SelectItem key={p} value={p} className="text-xs font-mono">
                                  {p}
                                </SelectItem>
                              ))}
                            </SelectContent>
                          </Select>
                        </div>
                        <div className="space-y-2">
                          <label className="text-xs font-medium text-foreground">Field key</label>
                          <input
                            type="text"
                            className="h-9 w-full rounded-md border border-input bg-background px-3 py-1 text-sm font-mono"
                            placeholder="e.g. borrowings (snake_case)"
                            value={attachNewPath.includes('.') ? attachNewPath.slice(attachNewPath.lastIndexOf('.') + 1) : ''}
                            onChange={(e) => {
                              const parent = attachNewPath.includes('.') ? attachNewPath.slice(0, attachNewPath.lastIndexOf('.')) : '';
                              const key = toSnakeCase(e.target.value);
                              setAttachNewPath(parent ? `${parent}.${key}` : key);
                              setAttachSaveError(null);
                            }}
                            disabled={unmatchedAttachBusy}
                          />
                          <p className="text-[11px] text-muted-foreground">Auto-converted to snake_case.</p>
                        </div>
                        <div className="flex items-center gap-2 pt-1">
                          <input
                            id="attach-new-composite"
                            type="checkbox"
                            className="h-4 w-4 rounded border-input accent-gray-900"
                            checked={attachNewIsComposite}
                            onChange={(e) => { setAttachNewIsComposite(e.target.checked); setAttachSaveError(null); }}
                            disabled={unmatchedAttachBusy}
                          />
                          <label htmlFor="attach-new-composite" className="text-xs font-medium text-foreground cursor-pointer select-none">
                            Composite field (group container — child fields can be added later)
                          </label>
                        </div>
                        {attachNewIsComposite && (
                          <p className="rounded-md border border-sky-100 bg-sky-50/60 px-3 py-2 text-[11px] leading-relaxed text-sky-900">
                            An empty group (<code>{'{}'}</code>) will be created at the specified path. The unmatched line will <strong>not</strong> be attached — add it as a child after the group is created.
                          </p>
                        )}
                        {(() => {
                          const parent = attachNewPath.includes('.') ? attachNewPath.slice(0, attachNewPath.lastIndexOf('.')) : '';
                          const isNonCanon = parent && !parentPaths.includes(parent);
                          return isNonCanon ? (
                            <p className="rounded-md border border-amber-200 bg-amber-50/70 px-3 py-2 text-[11px] leading-relaxed text-amber-800">
                              <AlertTriangle className="inline h-3 w-3 mr-1" />
                              This parent is not in the canonical schema. The field will be marked non-canonical. Use the dropdown above to pick a canonical path.
                            </p>
                          ) : null;
                        })()}
                        {!attachNewIsComposite && (
                          <p className="text-[11px] text-muted-foreground">
                            The field will be created under the specified path and marked as non-canonical if outside the canonical schema.
                          </p>
                        )}
                      </>
                    )}

                    {!attachNewIsComposite && (
                    <p className="rounded-md border border-gray-100 bg-gray-50/70 px-3 py-2 text-[11px] leading-relaxed text-muted-foreground">
                      If the target field already has a value, you&apos;ll be asked how to combine
                      them — <span className="font-medium text-foreground">add to the total (sum)</span>,
                      treat the incoming line as the stated total, or replace. Nothing is overwritten
                      silently.
                    </p>
                    )}

                    {attachSaveError && (
                      <p className="rounded-md border border-rose-200 bg-rose-50 px-3 py-2 text-[11px] leading-relaxed text-rose-800">
                        {attachSaveError}
                      </p>
                    )}
                  </div>
                </AlertDialogDescription>
              </AlertDialogHeader>
              <AlertDialogFooter className="gap-2 sm:space-x-0">
                <AlertDialogCancel disabled={unmatchedAttachBusy} className="sm:mt-0">
                  Cancel
                </AlertDialogCancel>
                <Button
                  type="button"
                  disabled={
                    unmatchedAttachBusy ||
                    !attachRow ||
                    ((retagPath || attachMode === 'existing') ? !attachTargetParentPath : !attachNewPath.trim())
                  }
                  onClick={() => void saveAttachUnmatched()}
                >
                  {unmatchedAttachBusy
                    ? retagPath ? 'Retagging…' : (attachMode === 'new' && attachNewIsComposite ? 'Creating…' : 'Attaching…')
                    : retagPath
                      ? 'Retag field'
                      : attachMode === 'new'
                        ? (attachNewIsComposite ? 'Create composite' : 'Create & attach')
                        : 'Attach'}
                </Button>
              </AlertDialogFooter>
            </AlertDialogContent>
          </AlertDialog>

          {/* Conflict resolution: several document lines → one canonical leaf. Never lose data. */}
          <AlertDialog
            open={!!conflict}
            onOpenChange={(open) => {
              if (!open && !conflictBusy) setConflict(null);
            }}
          >
            <AlertDialogContent className="w-[min(100vw-1.5rem,42rem)] max-w-none sm:max-w-2xl p-8 gap-6 max-h-[min(90vh,720px)] overflow-y-auto shadow-xl">
              <AlertDialogHeader className="space-y-3 text-left">
                <AlertDialogTitle className="text-xl pr-8">This field already has a value</AlertDialogTitle>
                <AlertDialogDescription asChild>
                  <div className="space-y-4 text-sm text-muted-foreground text-left">
                    {conflict && (
                      <>
                        <div className="rounded-lg border bg-muted/40 px-4 py-3">
                          <p className="text-[11px] font-medium uppercase tracking-wide text-muted-foreground mb-1">Canonical field</p>
                          <p className="font-mono text-sm text-foreground break-all">{conflict.full_path}</p>
                        </div>
                        {conflict.double_count_warning && (
                          <div className="flex gap-2 rounded-lg border border-amber-200 bg-amber-50/70 px-3 py-2.5 text-xs text-amber-900">
                            <AlertTriangle className="h-4 w-4 shrink-0 text-amber-600" />
                            <span>{conflict.double_count_warning}</span>
                          </div>
                        )}
                        <div>
                          <p className="text-[11px] font-medium uppercase tracking-wide text-muted-foreground mb-1">
                            Already mapped here ({conflict.existing_components.length})
                          </p>
                          <div className="space-y-1">
                            {conflict.existing_components.map((c, i) => (
                              <div key={c.id ?? i} className="flex items-center justify-between gap-3 text-xs">
                                <span className="inline-flex items-center gap-1 min-w-0">
                                  <span className={c.sign === '-' ? 'text-rose-600' : 'text-emerald-600'}>{c.sign === '-' ? '−' : '+'}</span>
                                  <span className="truncate text-foreground">{c.label}</span>
                                </span>
                                <span className="font-mono text-muted-foreground shrink-0">
                                  {formatAmountDisplay(typeof c.value === 'number' ? c.value : 0, treeCurrency, amountDisplaySystem)}
                                </span>
                              </div>
                            ))}
                          </div>
                        </div>
                        <div className="rounded-lg border bg-muted/40 px-4 py-3">
                          <p className="text-[11px] font-medium uppercase tracking-wide text-muted-foreground mb-1">Incoming line</p>
                          <div className="flex items-center justify-between gap-3">
                            <span className="inline-flex items-center gap-1 min-w-0">
                              <span className={conflict.incoming.inferred_sign === '-' ? 'text-rose-600' : 'text-emerald-600'}>
                                {conflict.incoming.inferred_sign === '-' ? '−' : '+'}
                              </span>
                              <span className="truncate text-foreground">{conflict.incoming.document_label}</span>
                            </span>
                            <span className="font-mono text-foreground shrink-0">
                              {formatAmountDisplay(conflict.incoming.value, treeCurrency, amountDisplaySystem)}
                            </span>
                          </div>
                        </div>
                      </>
                    )}
                  </div>
                </AlertDialogDescription>
              </AlertDialogHeader>
              <AlertDialogFooter className="flex-col gap-2 sm:flex-col sm:space-x-0">
                <Button type="button" disabled={conflictBusy} onClick={() => void resolveConflict('sum')}>
                  {conflictBusy ? 'Working…' : 'Add to total (sum) — keep both lines'}
                </Button>
                <Button type="button" variant="outline" disabled={conflictBusy} onClick={() => void resolveConflict('total')}>
                  Use the incoming line as the total
                </Button>
                <Button type="button" variant="outline" disabled={conflictBusy} onClick={() => void resolveConflict('replace')}>
                  Replace existing value
                </Button>
                <AlertDialogCancel disabled={conflictBusy} className="sm:mt-0">
                  Cancel
                </AlertDialogCancel>
              </AlertDialogFooter>
            </AlertDialogContent>
          </AlertDialog>

          {/* Reversible roll-up editor: drop a contributor or flip a sign; leaf recomputes. */}
          <AlertDialog
            open={!!editComponentsPath}
            onOpenChange={(open) => {
              if (!open && !editComponentsBusy) setEditComponentsPath(null);
            }}
          >
            <AlertDialogContent className="w-[min(100vw-1.5rem,42rem)] max-w-none sm:max-w-2xl p-8 gap-6 max-h-[min(90vh,720px)] overflow-y-auto shadow-xl">
              <AlertDialogHeader className="space-y-3 text-left">
                <AlertDialogTitle className="text-xl pr-8">Edit breakdown</AlertDialogTitle>
                <AlertDialogDescription asChild>
                  <div className="space-y-3 text-left">
                    <p className="font-mono text-xs text-muted-foreground break-all">{editComponentsPath}</p>
                    <div className="space-y-2">
                      {editComponentsDraft.map((c, i) => (
                        <div key={c.id ?? i} className="flex items-center gap-2 rounded-md border bg-muted/30 px-3 py-2">
                          <button
                            type="button"
                            className={`h-6 w-6 shrink-0 rounded border text-sm font-bold ${c.sign === '-' ? 'border-rose-200 bg-rose-50 text-rose-600' : 'border-emerald-200 bg-emerald-50 text-emerald-600'}`}
                            title="Toggle add / subtract"
                            disabled={editComponentsBusy}
                            onClick={() =>
                              setEditComponentsDraft((prev) =>
                                prev.map((x, idx) => (idx === i ? { ...x, sign: x.sign === '-' ? '+' : '-' } : x)),
                              )
                            }
                          >
                            {c.sign === '-' ? '−' : '+'}
                          </button>
                          <span className="flex-1 min-w-0 truncate text-sm text-foreground">{c.label}</span>
                          <span className="font-mono text-sm text-muted-foreground shrink-0">
                            {formatAmountDisplay(typeof c.value === 'number' ? c.value : 0, treeCurrency, amountDisplaySystem)}
                          </span>
                          <button
                            type="button"
                            className="shrink-0 text-xs text-rose-600 hover:underline disabled:opacity-50"
                            disabled={editComponentsBusy}
                            onClick={() => setEditComponentsDraft((prev) => prev.filter((_, idx) => idx !== i))}
                          >
                            Remove
                          </button>
                        </div>
                      ))}
                      {editComponentsDraft.length === 0 && (
                        <p className="text-xs text-muted-foreground">No components — the field value will be set to 0.</p>
                      )}
                    </div>
                    <div className="flex items-center justify-between border-t pt-2 text-sm">
                      <span className="text-muted-foreground">New field value (Σ signed)</span>
                      <span className="font-mono text-foreground">
                        {formatAmountDisplay(
                          editComponentsDraft.reduce(
                            (acc, c) => acc + (typeof c.value === 'number' ? c.value : 0) * (c.sign === '-' ? -1 : 1),
                            0,
                          ),
                          treeCurrency,
                          amountDisplaySystem,
                        )}
                      </span>
                    </div>
                  </div>
                </AlertDialogDescription>
              </AlertDialogHeader>
              <AlertDialogFooter className="gap-2 sm:space-x-0">
                <AlertDialogCancel disabled={editComponentsBusy} className="sm:mt-0">
                  Cancel
                </AlertDialogCancel>
                <Button type="button" disabled={editComponentsBusy} onClick={() => void saveComponentsEdit()}>
                  {editComponentsBusy ? 'Saving…' : 'Save'}
                </Button>
              </AlertDialogFooter>
            </AlertDialogContent>
          </AlertDialog>

          <AlertDialog
            open={fieldConfirmOpen}
            onOpenChange={(open) => {
              setFieldConfirmOpen(open);
              if (!open) {
                setPendingFieldPath(null);
                setPendingFieldCurrent(null);
              }
            }}
          >
            <AlertDialogContent className="w-[min(100vw-1.5rem,42rem)] max-w-none sm:max-w-2xl p-8 gap-6 max-h-[min(90vh,720px)] overflow-y-auto shadow-xl">
              <AlertDialogHeader className="space-y-3 text-left">
                <AlertDialogTitle className="text-xl pr-8">Confirm field edit</AlertDialogTitle>
                <AlertDialogDescription asChild>
                  <div className="space-y-4 text-sm text-muted-foreground text-left">
                    <p>Editing will replace the stored value for this path. Use Save after you finish editing the field.</p>
                    {pendingFieldPath ? (
                      <div className="rounded-lg border bg-muted/40 px-4 py-3">
                        <p className="text-[11px] font-medium uppercase tracking-wide text-muted-foreground mb-2">Field path</p>
                        <p className="font-mono text-xs sm:text-sm text-foreground break-all leading-relaxed">{pendingFieldPath}</p>
                      </div>
                    ) : null}
                    <div className="rounded-lg border bg-muted/40 px-4 py-3">
                      <p className="text-[11px] font-medium uppercase tracking-wide text-muted-foreground mb-1">Current value</p>
                      <p className="font-mono text-sm text-foreground break-all">
                        {pendingFieldCurrent == null || pendingFieldCurrent === ''
                          ? '— (empty / null)'
                          : typeof pendingFieldCurrent === 'number'
                            ? formatAmountDisplay(pendingFieldCurrent, treeCurrency, amountDisplaySystem)
                            : String(pendingFieldCurrent)}
                      </p>
                    </div>
                  </div>
                </AlertDialogDescription>
              </AlertDialogHeader>
              <AlertDialogFooter className="gap-2 sm:space-x-0">
                <AlertDialogCancel className="sm:mt-0">Cancel</AlertDialogCancel>
                <Button type="button" onClick={confirmStartFieldEdit} disabled={!pendingFieldPath}>
                  Continue to edit
                </Button>
              </AlertDialogFooter>
            </AlertDialogContent>
          </AlertDialog>

          {/* Justification capture for a manual value edit — recorded and shown in the "edited
              manually" disclaimer. Optional, so it never blocks the save. */}
          <AlertDialog
            open={justifyOpen}
            onOpenChange={(open) => {
              setJustifyOpen(open);
              if (!open) setPendingEditValue(null);
            }}
          >
            <AlertDialogContent className="w-[min(100vw-1.5rem,34rem)] max-w-none sm:max-w-lg p-7 gap-5 shadow-xl">
              <AlertDialogHeader className="space-y-2 text-left">
                <AlertDialogTitle className="text-lg pr-8">Why are you changing this value?</AlertDialogTitle>
                <AlertDialogDescription asChild>
                  <div className="space-y-3 text-sm text-muted-foreground text-left">
                    <p>
                      This will mark the field as <span className="font-medium text-amber-700">manually edited</span>.
                      Add an optional justification — it’s shown to reviewers in the edit disclaimer.
                    </p>
                    {editingPath ? (
                      <div className="rounded-lg border bg-muted/40 px-3 py-2">
                        <p className="text-[11px] font-medium uppercase tracking-wide text-muted-foreground mb-1">
                          {editingPath}
                        </p>
                        <p className="font-mono text-sm text-foreground">
                          New value:{' '}
                          {pendingEditValue == null
                            ? '— (cleared)'
                            : formatAmountDisplay(pendingEditValue, treeCurrency, amountDisplaySystem)}
                        </p>
                      </div>
                    ) : null}
                  </div>
                </AlertDialogDescription>
              </AlertDialogHeader>
              <textarea
                autoFocus
                rows={3}
                className="w-full rounded-md border border-input bg-background px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-amber-300"
                placeholder="e.g. Corrected per revised audited statement, page 12 (optional)"
                value={justifyReason}
                onChange={(e) => setJustifyReason(e.target.value)}
                disabled={fieldSaveBusy}
              />
              <AlertDialogFooter className="gap-2 sm:space-x-0">
                <AlertDialogCancel disabled={fieldSaveBusy} className="sm:mt-0">
                  Cancel
                </AlertDialogCancel>
                <Button type="button" onClick={() => void commitFieldEdit()} disabled={fieldSaveBusy}>
                  {fieldSaveBusy ? 'Saving…' : 'Save change'}
                </Button>
              </AlertDialogFooter>
            </AlertDialogContent>
          </AlertDialog>

          {/* Add field dialog — create a new numeric field at any level in the extraction tree */}
          <AlertDialog
            open={addFieldOpen}
            onOpenChange={(open) => {
              if (!open && !addFieldBusy) {
                setAddFieldOpen(false);
                setAddFieldError(null);
              }
            }}
          >
            <AlertDialogContent className="w-[min(100vw-1.5rem,42rem)] max-w-none sm:max-w-2xl p-8 gap-6 max-h-[min(90vh,720px)] overflow-y-auto shadow-xl">
              <AlertDialogHeader className="space-y-3 text-left">
                <AlertDialogTitle className="text-xl pr-8">
                  {addFieldConvertingLeafPath ? 'Convert to composite field' : 'Add field'}
                </AlertDialogTitle>
                <AlertDialogDescription asChild>
                  <div className="space-y-4 text-sm text-muted-foreground text-left">
                    {addFieldConvertingLeafPath ? (
                      <div className="space-y-3">
                        <div className="rounded-md border border-amber-300 bg-amber-50 px-3 py-2.5 text-[11px] leading-relaxed text-amber-900">
                          <div className="flex items-start gap-1.5">
                            <AlertTriangle className="h-3.5 w-3.5 shrink-0 mt-0.5 text-amber-600" />
                            <div>
                              <span className="font-semibold">This field currently holds a scalar value.</span>
                              {' '}Converting it to a composite will remove that value.
                              Current value: <span className="font-mono font-semibold">{addFieldConvertingLeafCurrentValue != null ? formatAmountBare(addFieldConvertingLeafCurrentValue, amountDisplaySystem) : '—'}</span>
                            </div>
                          </div>
                        </div>
                        <div className="space-y-2">
                          <label className="text-xs font-medium text-foreground">
                            Keep existing value as <span className="text-muted-foreground font-normal">(optional — leave blank to discard)</span>
                          </label>
                          <input
                            type="text"
                            className="h-9 w-full rounded-md border border-input bg-background px-3 py-1 text-sm font-mono"
                            placeholder={`e.g. total_${addFieldKey}`}
                            value={addFieldKeepExistingKey}
                            onChange={(e) => { setAddFieldKeepExistingKey(e.target.value); setAddFieldError(null); }}
                            disabled={addFieldBusy}
                          />
                          <p className="text-[11px] text-muted-foreground">
                            If provided, the current value will be saved under this child name inside the new group.
                          </p>
                        </div>
                      </div>
                    ) : (
                    <div className="space-y-2">
                      <label className="text-xs font-medium text-foreground">Parent path</label>
                      <div className="flex gap-2 items-start">
                        <div className="flex-1 space-y-1">
                          <Select
                            value={parentPaths.includes(addFieldParent) ? addFieldParent : '__custom__'}
                            onValueChange={(val) => {
                              if (val !== '__custom__') setAddFieldParent(val);
                            }}
                            disabled={addFieldBusy || parentPaths.length === 0}
                          >
                            <SelectTrigger className="h-9 w-full text-sm">
                              <SelectValue placeholder="Select parent path" />
                            </SelectTrigger>
                            <SelectContent className="max-h-72">
                              {parentPaths.map((p) => (
                                <SelectItem key={p} value={p} className="text-xs font-mono">
                                  {p}
                                </SelectItem>
                              ))}
                              {!parentPaths.includes(addFieldParent) && addFieldParent && (
                                <SelectItem value="__custom__" className="text-xs font-mono text-amber-700">
                                  {addFieldParent} (custom)
                                </SelectItem>
                              )}
                            </SelectContent>
                          </Select>
                        </div>
                      </div>
                      <p className="text-[11px] text-muted-foreground">Or type a custom parent path below:</p>
                      <input
                        type="text"
                        className="h-9 w-full rounded-md border border-input bg-background px-3 py-1 text-sm font-mono"
                        placeholder="e.g. balance_sheet.liabilities.current_liabilities.financial_liabilities"
                        value={addFieldParent}
                        onChange={(e) => setAddFieldParent(e.target.value.trim())}
                        disabled={addFieldBusy}
                      />
                      {addFieldParent && !parentPaths.includes(addFieldParent) && (
                        <p className="rounded-md border border-amber-200 bg-amber-50/70 px-3 py-2 text-[11px] leading-relaxed text-amber-800">
                          <AlertTriangle className="inline h-3 w-3 mr-1" />
                          This parent is not in the canonical schema. The field will be marked non-canonical. Tip: use the dropdown above to pick a canonical path.
                        </p>
                      )}
                    </div>
                    )}
                    {!addFieldConvertingLeafPath && (
                      <div className="space-y-2">
                        <label className="text-xs font-medium text-foreground">Field key</label>
                        <input
                          type="text"
                          className="h-9 w-full rounded-md border border-input bg-background px-3 py-1 text-sm font-mono"
                          placeholder="e.g. borrowings"
                          value={addFieldKey}
                          onChange={(e) => { setAddFieldKey(e.target.value); setAddFieldError(null); }}
                          disabled={addFieldBusy}
                        />
                        <p className="text-[11px] text-muted-foreground">Auto-converted to snake_case on save.</p>
                      </div>
                    )}
                    {!addFieldConvertingLeafPath && (
                      <div className="flex items-center gap-2">
                        <input
                          id="add-field-composite"
                          type="checkbox"
                          className="h-4 w-4 rounded border-input accent-gray-900"
                          checked={addFieldIsComposite}
                          onChange={(e) => { setAddFieldIsComposite(e.target.checked); setAddFieldLeafKey(''); setAddFieldLeafValue(''); setAddFieldError(null); }}
                          disabled={addFieldBusy}
                        />
                        <label htmlFor="add-field-composite" className="text-xs font-medium text-foreground cursor-pointer select-none">
                          Composite field (group container — child fields can be added later)
                        </label>
                      </div>
                    )}
                    {addFieldIsComposite ? (
                      <div className="space-y-3">
                        <div className="space-y-2">
                          <label className="text-xs font-medium text-foreground">Child field name</label>
                          <input
                            type="text"
                            className="h-9 w-full rounded-md border border-input bg-background px-3 py-1 text-sm font-mono"
                            placeholder="e.g. total_new_assets"
                            value={addFieldLeafKey}
                            onChange={(e) => { setAddFieldLeafKey(e.target.value); setAddFieldError(null); }}
                            disabled={addFieldBusy}
                            autoFocus={!!addFieldConvertingLeafPath}
                          />
                          <p className="text-[11px] text-muted-foreground">Auto-converted to snake_case on save.</p>
                        </div>
                        <div className="space-y-2">
                          <label className="text-xs font-medium text-foreground">Value (stored in base units)</label>
                          <input
                            type="text"
                            inputMode="decimal"
                            className="h-9 w-full rounded-md border border-input bg-background px-3 py-1 text-sm font-mono"
                            placeholder="e.g. 4730500000"
                            value={addFieldLeafValue}
                            onChange={(e) => { setAddFieldLeafValue(e.target.value); setAddFieldError(null); }}
                            disabled={addFieldBusy}
                          />
                        </div>
                      </div>
                    ) : (
                      <div className="space-y-2">
                        <label className="text-xs font-medium text-foreground">Value (stored in base units)</label>
                        <input
                          type="text"
                          inputMode="decimal"
                          className="h-9 w-full rounded-md border border-input bg-background px-3 py-1 text-sm font-mono"
                          placeholder="e.g. 4730500000"
                          value={addFieldValue}
                          onChange={(e) => { setAddFieldValue(e.target.value); setAddFieldError(null); }}
                          disabled={addFieldBusy}
                        />
                      </div>
                    )}
                    {(addFieldConvertingLeafPath || (addFieldParent && addFieldKey)) && (
                      <div className="rounded-lg border bg-muted/40 px-4 py-3">
                        <p className="text-[11px] font-medium uppercase tracking-wide text-muted-foreground mb-1">Full path</p>
                        {addFieldIsComposite && addFieldLeafKey ? (
                          <>
                            <p className="font-mono text-xs text-muted-foreground break-all">
                              {addFieldConvertingLeafPath || `${addFieldParent}.${toSnakeCase(addFieldKey)}`}
                              {' '}<span className="text-muted-foreground/60">(group)</span>
                            </p>
                            {addFieldConvertingLeafPath && toSnakeCase(addFieldKeepExistingKey) && (
                              <p className="font-mono text-xs text-amber-700 break-all mt-0.5">
                                {addFieldConvertingLeafPath}.{toSnakeCase(addFieldKeepExistingKey)}
                                {' '}<span className="text-amber-500/70">(existing value kept here)</span>
                              </p>
                            )}
                            <p className="font-mono text-xs text-foreground break-all mt-0.5">
                              {addFieldConvertingLeafPath || `${addFieldParent}.${toSnakeCase(addFieldKey)}`}.{toSnakeCase(addFieldLeafKey)}
                              {' '}<span className="text-muted-foreground/60">(new leaf)</span>
                            </p>
                          </>
                        ) : (
                          <p className="font-mono text-xs text-foreground break-all">{addFieldParent}.{toSnakeCase(addFieldKey)}</p>
                        )}
                      </div>
                    )}
                    {addFieldError && (
                      <p className="rounded-md border border-rose-200 bg-rose-50 px-3 py-2 text-[11px] leading-relaxed text-rose-800">
                        {addFieldError}
                      </p>
                    )}
                  </div>
                </AlertDialogDescription>
              </AlertDialogHeader>
              <AlertDialogFooter className="gap-2 sm:space-x-0">
                <AlertDialogCancel disabled={addFieldBusy} className="sm:mt-0">
                  Cancel
                </AlertDialogCancel>
                <Button
                  type="button"
                  disabled={addFieldBusy || (!addFieldConvertingLeafPath && !addFieldKey.trim()) || (addFieldIsComposite ? (!addFieldLeafKey.trim() || !addFieldLeafValue.trim()) : !addFieldValue.trim())}
                  onClick={() => void saveAddField()}
                >
                  {addFieldBusy
                    ? (addFieldConvertingLeafPath ? 'Converting…' : addFieldIsComposite ? 'Creating…' : 'Adding…')
                    : (addFieldConvertingLeafPath ? 'Convert & add child' : addFieldIsComposite ? 'Create composite' : 'Add field')}
                </Button>
              </AlertDialogFooter>
            </AlertDialogContent>
          </AlertDialog>

          {extractStatus !== null &&
          !isExtractError &&
          !isExtractRunning &&
          !isExtractDone &&
          !extractedObj ? (
            <div className="bg-white rounded-lg border border-gray-200 shadow-sm p-6">
              <div className="flex items-center gap-3">
                <Clock className="h-5 w-5 text-gray-400" />
                <div>
                  <div className="text-sm font-medium text-gray-900">No extraction yet</div>
                  <div className="text-xs text-gray-500">Run extraction from File Tagging, or open this page after a run has been queued.</div>
                </div>
              </div>
            </div>
          ) : null}

          {/* PDF source viewer dialog */}
          <PdfSourceViewer
            open={sourceViewerOpen}
            onClose={() => setSourceViewerOpen(false)}
            pdfUrl={viewerPdfUrl}
            fieldLabel={sourceViewerPath}
            sourceRef={sourceViewerRef ?? sourceRefs?.[sourceViewerPath] ?? null}
          />
        </TabsContent>



        <TabsContent value="render">
          <div className="bg-white rounded-lg border border-gray-200 shadow-sm overflow-hidden">
            {isXlsxFile ? (
              <SpreadsheetPreview fileId={Number(fileId)} />
            ) : downloadLoading ? (
              /* --- PDF / generic: loading --- */
              <div className="bg-gray-50 rounded-lg min-h-[500px] flex flex-col items-center justify-center gap-3 p-8">
                <FileText className="h-16 w-16 text-gray-300" />
                <p className="text-sm text-gray-500">Loading preview…</p>
              </div>
            ) : downloadUrl ? (
              /* --- PDF / generic: iframe --- */
              <iframe title="file-preview" src={downloadUrl} className="w-full h-[75vh] rounded-lg" />
            ) : (
              /* --- PDF / generic: error / unavailable --- */
              <div className="bg-gray-50 rounded-lg min-h-[500px] flex flex-col items-center justify-center gap-4 p-8">
                <FileText className="h-16 w-16 text-gray-300" />
                <div className="text-center space-y-1 max-w-md">
                  <p className="text-sm font-medium text-gray-900">{file.fileName}</p>
                  {downloadError ? (
                    <p className="text-xs text-red-600 leading-relaxed">{downloadError}</p>
                  ) : (
                    <p className="text-xs text-gray-400">Preview unavailable (missing download URL)</p>
                  )}
                </div>
              </div>
            )}
          </div>
        </TabsContent>





      </Tabs>

      {/* Attach to company dialog — outside tabs so it works from any tab */}
      {attachCompanyOpen && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40">
          <div className="bg-white rounded-xl shadow-2xl p-6 w-full max-w-md mx-4">
            <h2 className="text-base font-semibold text-gray-900 mb-4">Attach file to company</h2>
            <div className="space-y-4">
              <div>
                <label className="block text-xs text-gray-500 mb-1">FY End</label>
                <FyEndMonthYearPicker value={attachFyEnd} onChange={setAttachFyEnd} />
                {attachCycleResolving && (
                  <p className="text-xs text-gray-400 mt-1">Resolving review cycle…</p>
                )}
                {attachCycleError && (
                  <p className="text-xs text-red-500 mt-1">No review cycle found for this FY end.</p>
                )}
              </div>
              <div>
                <label className="block text-xs text-gray-500 mb-1">Company</label>
                <select
                  value={attachCompanyId ?? ''}
                  onChange={(e) => setAttachCompanyId(e.target.value ? Number(e.target.value) : null)}
                  disabled={!attachCycleId}
                  className="w-full px-3 py-2 border border-gray-300 rounded-lg text-sm bg-white focus:outline-none focus:ring-2 focus:ring-blue-500 disabled:opacity-50"
                >
                  <option value="">Select company…</option>
                  {attachCompanies.map((c) => (
                    <option key={c.id} value={c.id}>{c.name}</option>
                  ))}
                </select>
              </div>
              <div>
                <label className="block text-xs text-gray-500 mb-1">Entity</label>
                <select
                  value={attachEntityId ?? ''}
                  onChange={(e) => setAttachEntityId(e.target.value ? Number(e.target.value) : null)}
                  disabled={!attachCompanyId}
                  className="w-full px-3 py-2 border border-gray-300 rounded-lg text-sm bg-white focus:outline-none focus:ring-2 focus:ring-blue-500 disabled:opacity-50"
                >
                  <option value="">Select entity…</option>
                  {attachEntities.map((e) => (
                    <option key={e.id} value={e.id}>{e.name}</option>
                  ))}
                </select>
              </div>
            </div>
            <DuplicateFileWarning
              entityId={attachEntityId}
              reviewCycleId={attachCycleId || null}
              excludeFileId={fileId ? Number(fileId) : null}
              className="mt-4"
            />
            <div className="flex justify-end gap-2 mt-6">
              <button
                type="button"
                onClick={() => setAttachCompanyOpen(false)}
                className="px-4 py-2 text-sm rounded-lg border border-gray-300 bg-white text-gray-700 hover:bg-gray-50"
              >
                Cancel
              </button>
              <button
                type="button"
                onClick={() => void handleAttachToCompany()}
                disabled={!attachCompanyId || !attachEntityId || !attachFyEnd || !attachCycleId || attachCycleResolving || attachSaving}
                className="px-4 py-2 text-sm rounded-lg bg-blue-600 text-white hover:bg-blue-700 disabled:opacity-50 disabled:pointer-events-none"
              >
                {attachSaving ? 'Attaching…' : 'Attach'}
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
