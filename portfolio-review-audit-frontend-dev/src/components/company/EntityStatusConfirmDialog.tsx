import type { EntityStatusConfirmState } from '@/components/company/entityStatusChange';
import { entityStatusDisplayLabel } from '@/constants/auditStatus';
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from '@/components/ui/alert-dialog';

type Props = {
  open: boolean;
  state: EntityStatusConfirmState | null;
  loading?: boolean;
  onOpenChange: (open: boolean) => void;
  onConfirm: () => void | Promise<void>;
};

export function EntityStatusConfirmDialog({ open, state, loading, onOpenChange, onConfirm }: Props) {
  return (
    <AlertDialog open={open} onOpenChange={onOpenChange}>
      <AlertDialogContent className="bg-white sm:max-w-md">
        <AlertDialogHeader>
          <AlertDialogTitle className="text-gray-900">Confirm status change</AlertDialogTitle>
          <AlertDialogDescription asChild>
            <div className="space-y-3 text-sm text-gray-500">
              <p>
                Change <span className="font-medium text-gray-700">{state?.entityLabel ?? 'entity'}</span> from{' '}
                <span className="font-medium text-gray-700">
                  {entityStatusDisplayLabel(state?.previousStatus)}
                </span>{' '}
                to{' '}
                <span className="font-medium text-gray-700">{entityStatusDisplayLabel(state?.newStatus)}</span>?
              </p>
            </div>
          </AlertDialogDescription>
        </AlertDialogHeader>
        <AlertDialogFooter>
          <AlertDialogCancel className="border-gray-300 text-gray-700 hover:bg-gray-50" disabled={loading}>
            Cancel
          </AlertDialogCancel>
          <AlertDialogAction
            disabled={loading}
            className="bg-blue-500 text-white hover:bg-blue-600"
            onClick={(e) => {
              e.preventDefault();
              void onConfirm();
            }}
          >
            {loading ? 'Updating…' : 'Confirm'}
          </AlertDialogAction>
        </AlertDialogFooter>
      </AlertDialogContent>
    </AlertDialog>
  );
}
