import { monthsForReviewCycleId, normalizeFyEnd } from '@/utils/fyEnd';

type FyEndSelectProps = {
  reviewCycleId: string;
  value: string;
  onChange: (value: string) => void;
  disabled?: boolean;
  label?: string;
  required?: boolean;
  id?: string;
};

export function FyEndSelect({
  reviewCycleId,
  value,
  onChange,
  disabled,
  label = 'FY end',
  required,
  id = 'fy-end-select',
}: FyEndSelectProps) {
  const options = monthsForReviewCycleId(reviewCycleId);
  const normalizedValue = normalizeFyEnd(value) ?? '';

  return (
    <div>
      <label htmlFor={id} className="block text-xs text-gray-500 mb-1">
        {label}
        {required ? <span className="text-gray-700 font-medium"> (required)</span> : null}
      </label>
      <select
        id={id}
        value={normalizedValue}
        disabled={disabled || !reviewCycleId || options.length === 0}
        onChange={(e) => onChange(e.target.value)}
        className="w-full px-3 py-2 border border-gray-300 rounded-lg text-sm bg-white focus:outline-none focus:ring-2 focus:ring-blue-500 focus:border-blue-500"
      >
        <option value="">{reviewCycleId ? 'Select FY end…' : 'Select a review cycle first'}</option>
        {options.map((opt) => (
          <option key={opt} value={opt}>
            {opt}
          </option>
        ))}
      </select>
      {reviewCycleId && options.length === 0 ? (
        <p className="text-xs text-amber-600 mt-1">No FY end months for this review cycle.</p>
      ) : null}
    </div>
  );
}
