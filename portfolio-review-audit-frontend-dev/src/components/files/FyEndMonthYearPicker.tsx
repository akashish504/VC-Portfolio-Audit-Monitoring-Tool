import { useEffect, useState } from 'react';

import { MONTH_ABBR, formatFyEnd, fyEndYearOptions, parseFyEndParts } from '@/utils/fyEnd';

type FyEndMonthYearPickerProps = {
  value: string;
  onChange: (value: string) => void;
  disabled?: boolean;
  label?: string;
  required?: boolean;
  id?: string;
};

export function FyEndMonthYearPicker({
  value,
  onChange,
  disabled,
  label = 'FY end',
  required,
  id = 'fy-end-picker',
}: FyEndMonthYearPickerProps) {
  const years = fyEndYearOptions();
  const [month, setMonth] = useState<number | ''>('');
  const [year, setYear] = useState<number | ''>('');

  useEffect(() => {
    const parts = parseFyEndParts(value);
    if (parts) {
      setMonth(parts.month);
      setYear(parts.year);
    } else if (!value) {
      setMonth('');
      setYear('');
    }
  }, [value]);

  const emitChange = (nextMonth: number | '', nextYear: number | '') => {
    if (nextMonth && nextYear) {
      onChange(formatFyEnd(nextMonth, nextYear));
    } else {
      onChange('');
    }
  };

  return (
    <div>
      <label htmlFor={`${id}-month`} className="block text-xs text-gray-500 mb-1">
        {label}
        {required ? <span className="text-gray-700 font-medium"> (required)</span> : null}
      </label>
      <div className="grid grid-cols-2 gap-2">
        <select
          id={`${id}-month`}
          value={month}
          disabled={disabled}
          onChange={(e) => {
            const nextMonth = e.target.value ? Number(e.target.value) : '';
            setMonth(nextMonth);
            emitChange(nextMonth, year);
          }}
          className="w-full px-3 py-2 border border-gray-300 rounded-lg text-sm bg-white focus:outline-none focus:ring-2 focus:ring-blue-500 focus:border-blue-500"
        >
          <option value="">Month…</option>
          {MONTH_ABBR.map((name, index) => (
            <option key={name} value={index + 1}>
              {name}
            </option>
          ))}
        </select>
        <select
          id={`${id}-year`}
          value={year}
          disabled={disabled}
          onChange={(e) => {
            const nextYear = e.target.value ? Number(e.target.value) : '';
            setYear(nextYear);
            emitChange(month, nextYear);
          }}
          className="w-full px-3 py-2 border border-gray-300 rounded-lg text-sm bg-white focus:outline-none focus:ring-2 focus:ring-blue-500 focus:border-blue-500"
        >
          <option value="">Year…</option>
          {years.map((y) => (
            <option key={y} value={y}>
              {y}
            </option>
          ))}
        </select>
      </div>
    </div>
  );
}
