import { useMemo } from 'react';
import { Pencil, Trash2 } from 'lucide-react';
import { Background, Controls, Edge, MiniMap, Node, Position, ReactFlow } from '@xyflow/react';
import dagre from 'dagre';

import '@xyflow/react/dist/style.css';

import { ENTITY_STATUS_OPTIONS, entityStatusBadgeClass, normalizeEntityStatus } from '@/constants/auditStatus';
import type { EntityReviewStatusValue } from '@/constants/statusEnums';
import { toast } from '@/components/ui/sonner';

export type OrgChartEntity = {
  id: number;
  name: string;
  geolocation?: string | null;
  entity_type?: import('@/types/domain').EntityType | null;
  parent_entity_id?: number | null;
  is_parent: boolean;
  /** Same values as portfolio company `in_review_status`. */
  status?: string | null;
};

const NODE_WIDTH = 260;
/** Room for title, status row, and edit/remove actions. */
const NODE_HEIGHT = 188;

function layout(nodes: Node[], edges: Edge[]) {
  const g = new dagre.graphlib.Graph();
  g.setDefaultEdgeLabel(() => ({}));
  g.setGraph({
    rankdir: 'TB',
    ranksep: 78,
    nodesep: 40,
    edgesep: 10,
    marginx: 20,
    marginy: 20,
  });

  nodes.forEach((n) => g.setNode(n.id, { width: NODE_WIDTH, height: NODE_HEIGHT }));
  edges.forEach((e) => g.setEdge(e.source, e.target));
  dagre.layout(g);

  const laidOut = nodes.map((n) => {
    const p = g.node(n.id);
    return {
      ...n,
      targetPosition: Position.Top,
      sourcePosition: Position.Bottom,
      position: { x: p.x - NODE_WIDTH / 2, y: p.y - NODE_HEIGHT / 2 },
    };
  });

  return { nodes: laidOut, edges };
}

function EntityNodeLabel({
  entity,
  onStatusChange,
  saving,
  onEdit,
  onDelete,
  deleting,
  deleteBlocked,
}: {
  entity: OrgChartEntity;
  onStatusChange?: (id: number, status: EntityReviewStatusValue) => void;
  saving: boolean;
  onEdit?: (id: number) => void;
  onDelete?: (id: number) => void;
  deleting?: boolean;
  /** True when this entity has children; delete must stay disabled until children are moved. */
  deleteBlocked?: boolean;
}) {
  const current = normalizeEntityStatus(entity.status);
  const archived = current === 'Archive Entity';
  return (
    <div className={`px-3 py-2 ${archived ? 'text-gray-500' : ''}`} onPointerDown={(e) => e.stopPropagation()}>
      <div
        className={`text-sm font-semibold whitespace-normal break-words leading-snug ${
          archived ? 'text-gray-500' : 'text-gray-900'
        }`}
      >
        {entity.geolocation ? `${entity.name} (${entity.geolocation})` : entity.name}
      </div>
      {entity.entity_type ? (
        <div className="mt-1">
          <span className="inline-flex items-center rounded-full bg-slate-100 px-2 py-0.5 text-[11px] font-medium text-slate-700">
            {entity.entity_type}
          </span>
        </div>
      ) : null}
      <div className="mt-2">
        <label className="sr-only">Audit status</label>
        <select
          value={current}
          disabled={!onStatusChange || saving}
          onChange={(ev) => {
            const v = ev.target.value as EntityReviewStatusValue;
            void onStatusChange?.(entity.id, v);
          }}
          className={`w-full text-[11px] font-medium rounded-md border px-2 py-1.5 cursor-pointer focus:outline-none focus:ring-2 focus:ring-blue-500 disabled:opacity-60 disabled:cursor-not-allowed ${
            entityStatusBadgeClass[current] || 'bg-gray-100 text-gray-800 border-gray-200'
          }`}
        >
          {ENTITY_STATUS_OPTIONS.map((s) => (
            <option key={s} value={s}>
              {s}
            </option>
          ))}
        </select>
      </div>
      {(onEdit || onDelete) && (
        <div className="mt-2 flex items-center gap-1.5 border-t border-gray-100 pt-2">
          {onEdit && (
            <button
              type="button"
              onClick={() => onEdit(entity.id)}
              disabled={saving || deleting}
              className="inline-flex items-center gap-1.5 px-2 py-1 text-[11px] font-medium rounded-md border border-gray-200 bg-white text-gray-700 hover:bg-gray-50 disabled:opacity-50"
            >
              <Pencil className="h-3 w-3" />
              Edit
            </button>
          )}
          {onDelete && (
            <button
              type="button"
              onClick={() => {
                if (deleteBlocked) {
                  toast.error(
                    'This entity has child entities attached. Move those child entities under another parent (or make them top-level) first, then you can delete this entity.',
                  );
                  return;
                }
                onDelete(entity.id);
              }}
              disabled={saving || deleting}
              title={
                deleteBlocked
                  ? 'Move child entities under another parent (or top-level) before deleting this entity.'
                  : undefined
              }
              className={`inline-flex items-center gap-1.5 px-2 py-1 text-[11px] font-medium rounded-md border ${
                deleteBlocked
                  ? 'border-amber-200 bg-amber-50 text-amber-800 hover:bg-amber-100'
                  : 'border-red-100 bg-red-50 text-red-700 hover:bg-red-100'
              } disabled:opacity-50`}
            >
              <Trash2 className="h-3 w-3" />
              {deleting ? '…' : 'Remove'}
            </button>
          )}
        </div>
      )}
    </div>
  );
}

export function OrgChartGraph({
  entities,
  onEntityStatusChange,
  savingEntityId,
  onEditEntity,
  onDeleteEntity,
  deletingEntityId,
}: {
  entities: OrgChartEntity[];
  /** Fired when the user picks a new status; parent should confirm before calling the API. */
  onEntityStatusChange?: (entityId: number, status: EntityReviewStatusValue) => void | Promise<void>;
  savingEntityId?: number | null;
  onEditEntity?: (entityId: number) => void;
  onDeleteEntity?: (entityId: number) => void;
  deletingEntityId?: number | null;
}) {
  const { nodes, edges } = useMemo(() => {
    const nodes: Node[] = entities.map((e) => {
      const archived = normalizeEntityStatus(e.status) === 'Archive Entity';
      return {
        id: String(e.id),
        data: {},
        position: { x: 0, y: 0 },
        style: {
          width: NODE_WIDTH,
          borderRadius: 10,
          border: archived ? '1px solid rgb(209, 213, 219)' : '1px solid rgb(229, 231, 235)',
          background: archived ? 'rgb(249, 250, 251)' : 'white',
          boxShadow: archived ? 'none' : '0 1px 2px rgba(0,0,0,0.06)',
          opacity: archived ? 0.82 : 1,
        },
      };
    });

    const edges: Edge[] = [];
    for (const e of entities) {
      if (e.parent_entity_id) {
        edges.push({
          id: `${e.parent_entity_id}->${e.id}`,
          source: String(e.parent_entity_id),
          target: String(e.id),
          type: 'smoothstep',
          animated: false,
          style: {
            stroke: '#6b7280',
            strokeWidth: 2,
            strokeLinecap: 'round',
            strokeLinejoin: 'round',
          },
        });
      }
    }

    const laidOut = layout(nodes, edges);
    const byId = new Map(entities.map((x) => [String(x.id), x]));
    const nodesWithLabels = laidOut.nodes.map((n) => {
      const ent = byId.get(n.id);
      if (!ent) return n;
      const hasChildren = entities.some((x) => x.parent_entity_id === ent.id);
      return {
        ...n,
        data: {
          label: (
            <EntityNodeLabel
              entity={ent}
              onStatusChange={onEntityStatusChange}
              saving={savingEntityId === ent.id}
              onEdit={onEditEntity}
              onDelete={onDeleteEntity}
              deleting={deletingEntityId === ent.id}
              deleteBlocked={hasChildren}
            />
          ),
        },
      };
    });

    return { nodes: nodesWithLabels, edges: laidOut.edges };
  }, [entities, onEntityStatusChange, savingEntityId, onEditEntity, onDeleteEntity, deletingEntityId]);

  if (entities.length === 0) return null;

  return (
    <div className="w-full h-[620px] rounded-lg border border-gray-200 bg-white overflow-hidden">
      <ReactFlow
        nodes={nodes}
        edges={edges}
        fitView
        nodesDraggable={false}
        nodesConnectable={false}
        elementsSelectable
        proOptions={{ hideAttribution: true }}
        defaultEdgeOptions={{
          type: 'smoothstep',
          animated: false,
          style: {
            stroke: '#6b7280',
            strokeWidth: 2,
            strokeLinecap: 'round',
            strokeLinejoin: 'round',
          },
        }}
      >
        <Background color="#e5e7eb" gap={18} />
        <MiniMap pannable zoomable nodeStrokeColor="#9ca3af" nodeColor="#ffffff" maskColor="rgba(0,0,0,0.05)" />
        <Controls />
      </ReactFlow>
    </div>
  );
}
