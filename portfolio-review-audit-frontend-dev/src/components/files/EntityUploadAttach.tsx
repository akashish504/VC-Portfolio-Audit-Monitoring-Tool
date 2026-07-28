import { useState } from 'react';
import { toast } from 'sonner';

import { createEntity } from '@/api/portfolio';
import { ENTITY_TYPE_OPTIONS, type EntityType } from '@/types/domain'; // EntityType used as type assertion in includes()

type EntityOption = { id: number; name: string; geolocation?: string | null };

type EntityUploadAttachProps = {
  portfolioCompanyId: number;
  entities: EntityOption[];
  entityId: number | null;
  onEntityIdChange: (id: number | null) => void;
  onEntitiesChange: (entities: EntityOption[]) => void;
  disabled?: boolean;
  required?: boolean;
};

export function EntityUploadAttach({
  portfolioCompanyId,
  entities,
  entityId,
  onEntityIdChange,
  onEntitiesChange,
  disabled,
  required,
}: EntityUploadAttachProps) {
  const [quickName, setQuickName] = useState('');
  const [quickGeo, setQuickGeo] = useState('');
  const [quickType, setQuickType] = useState('');
  const [quickSaving, setQuickSaving] = useState(false);

  const showQuickCreate = entities.length === 0;

  const handleQuickCreate = async () => {
    const name = quickName.trim();
    if (!name) {
      toast.error('Enter an entity name');
      return;
    }
    setQuickSaving(true);
    try {
      const created = await createEntity({
        portfolio_company_id: portfolioCompanyId,
        name,
        geolocation: quickGeo.trim() || null,
        entity_type: quickType || null,
        parent_entity_id: null,
        is_parent: false,
      });
      const next = [...entities, { id: created.id, name: created.name, geolocation: created.geolocation ?? null }];
      onEntitiesChange(next);
      onEntityIdChange(created.id);
      setQuickName('');
      setQuickGeo('');
      setQuickType('');
      toast.success('Entity created — selected for upload');
    } catch (e) {
      const msg = e instanceof Error ? e.message : 'Failed to create entity';
      toast.error(msg);
    } finally {
      setQuickSaving(false);
    }
  };

  if (showQuickCreate) {
    return (
      <div className="rounded-lg border border-dashed border-gray-300 bg-gray-50 p-3 space-y-3">
        <p className="text-xs text-gray-600">No entities yet. Create one to attach this upload.</p>
        <div>
          <label htmlFor="quick-entity-name" className="block text-xs text-gray-500 mb-1">Entity name (required)</label>
          <input
            id="quick-entity-name"
            name="quick-entity-name"
            value={quickName}
            disabled={disabled || quickSaving}
            onChange={(e) => setQuickName(e.target.value)}
            className="w-full px-3 py-2 border border-gray-300 rounded-lg text-sm"
            placeholder="Legal entity name"
          />
        </div>
        <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
          <div>
            <label htmlFor="quick-entity-geo" className="block text-xs text-gray-500 mb-1">Geolocation (optional)</label>
            <input
              id="quick-entity-geo"
              name="quick-entity-geo"
              value={quickGeo}
              disabled={disabled || quickSaving}
              onChange={(e) => setQuickGeo(e.target.value)}
              className="w-full px-3 py-2 border border-gray-300 rounded-lg text-sm"
              placeholder="Country / region"
            />
          </div>
          <div>
            <label htmlFor="quick-entity-type" className="block text-xs text-gray-500 mb-1">Entity type (optional)</label>
            <select
              id="quick-entity-type"
              name="quick-entity-type"
              value={ENTITY_TYPE_OPTIONS.includes(quickType as EntityType) || quickType === '' ? quickType : 'Other'}
              disabled={disabled || quickSaving}
              onChange={(e) => setQuickType(e.target.value)}
              className="w-full px-3 py-2 border border-gray-300 rounded-lg text-sm bg-white"
            >
              <option value="">Not set</option>
              {ENTITY_TYPE_OPTIONS.map((value) => (
                <option key={value} value={value}>
                  {value}
                </option>
              ))}
            </select>
            {(quickType === 'Other' || (quickType !== '' && !ENTITY_TYPE_OPTIONS.includes(quickType as EntityType))) && (
              <input
                type="text"
                value={quickType === 'Other' ? '' : quickType}
                disabled={disabled || quickSaving}
                onChange={(e) => setQuickType(e.target.value || 'Other')}
                className="mt-1 w-full px-3 py-2 border border-gray-300 rounded-lg text-sm"
                placeholder="Describe entity type (optional)"
              />
            )}
          </div>
        </div>
        <button
          type="button"
          disabled={disabled || quickSaving}
          onClick={() => void handleQuickCreate()}
          className="px-3 py-2 text-sm rounded-lg bg-white border border-gray-300 hover:bg-gray-100 disabled:opacity-50"
        >
          {quickSaving ? 'Creating…' : 'Create entity'}
        </button>
      </div>
    );
  }

  return (
    <div>
      <label htmlFor="entity-select" className="block text-xs text-gray-500 mb-1">
        Entity
        {required ? <span className="text-gray-700 font-medium"> (required)</span> : null}
      </label>
      <select
        id="entity-select"
        name="entity-select"
        value={entityId ?? ''}
        disabled={disabled}
        onChange={(e) => onEntityIdChange(e.target.value ? Number(e.target.value) : null)}
        className="w-full px-3 py-2 border border-gray-300 rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-blue-500 focus:border-blue-500"
      >
        <option value="">Select an entity…</option>
        {entities.map((e) => (
          <option key={e.id} value={e.id}>
            {e.name}{e.geolocation ? ` (${e.geolocation})` : ''}
          </option>
        ))}
      </select>
    </div>
  );
}
