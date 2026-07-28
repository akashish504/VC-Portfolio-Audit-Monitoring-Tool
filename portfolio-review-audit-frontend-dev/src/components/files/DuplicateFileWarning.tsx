import { useEffect, useState } from 'react';
import { AlertTriangle } from 'lucide-react';

import { listFiles } from '@/api/portfolio';

type ExistingFile = { id: number; filename: string };

interface DuplicateFileWarningProps {
  /** Entity the file is being tagged/attached/uploaded to. */
  entityId: number | null;
  /** Review cycle derived from the selected FY end. */
  reviewCycleId: string | null;
  /** File currently being re-tagged — excluded from the check (it's the one moving). */
  excludeFileId?: number | null;
  className?: string;
}

/**
 * Non-blocking inline banner: warns when a non-deleted audit file already exists
 * for the selected entity + review cycle. Adding another is a valid scenario, so
 * this only informs — it never blocks the action. Renders nothing when there's no
 * existing file (or when entity/cycle aren't both selected yet).
 */
export function DuplicateFileWarning({
  entityId,
  reviewCycleId,
  excludeFileId,
  className,
}: DuplicateFileWarningProps) {
  const [existing, setExisting] = useState<ExistingFile[]>([]);

  useEffect(() => {
    let cancelled = false;
    if (entityId == null || !reviewCycleId) {
      setExisting([]);
      return;
    }
    listFiles({ entity_id: entityId, limit: 100, offset: 0 })
      .then((res) => {
        if (cancelled) return;
        const matches = (res.items ?? []).filter(
          (f) =>
            f.review_cycle_id === reviewCycleId &&
            (f.status ?? '').toLowerCase() !== 'deleted' &&
            f.id !== excludeFileId,
        );
        setExisting(matches.map((f) => ({ id: f.id, filename: f.filename })));
      })
      .catch(() => {
        if (!cancelled) setExisting([]);
      });
    return () => {
      cancelled = true;
    };
  }, [entityId, reviewCycleId, excludeFileId]);

  if (existing.length === 0) return null;

  return (
    <div
      className={`flex items-start gap-2 px-3 py-2.5 bg-amber-50 border border-amber-200 rounded-lg text-xs text-amber-800 ${className ?? ''}`}
    >
      <AlertTriangle className="h-4 w-4 shrink-0 mt-0.5 text-amber-500" />
      <span>
        <span className="font-medium">
          {existing.length === 1
            ? 'An audit file already exists for this entity and review cycle.'
            : `${existing.length} audit files already exist for this entity and review cycle.`}
        </span>{' '}
        Adding another is allowed — it will be kept as an additional file.
        <span className="block mt-1 text-amber-700">
          Existing: {existing.map((f) => f.filename).join(', ')}
        </span>
      </span>
    </div>
  );
}
