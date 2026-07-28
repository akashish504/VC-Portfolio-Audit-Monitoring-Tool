import { useCallback, useEffect, useLayoutEffect, useRef, useState } from 'react';
import { createPortal } from 'react-dom';

export interface DealOption {
  id: number;
  name: string;
}

interface DealSearchSelectProps {
  label?: string;
  value: number | null;
  onChange: (option: DealOption | null) => void;
  /** Called with the current query string; should return matching options. */
  onSearch: (q: string) => Promise<DealOption[]>;
  disabled?: boolean;
  /** When false, the field stays disabled until prerequisites (e.g. FY end) are set. */
  ready?: boolean;
  placeholder?: string;
  notReadyPlaceholder?: string;
}

const DEBOUNCE_MS = 300;
const MIN_CHARS = 1;

export function DealSearchSelect({
  label = 'Deal',
  value,
  onChange,
  onSearch,
  disabled = false,
  ready = true,
  placeholder = 'Search deals…',
  notReadyPlaceholder = 'Select month and year first…',
}: DealSearchSelectProps) {
  const [open, setOpen] = useState(false);
  const [search, setSearch] = useState('');
  const [options, setOptions] = useState<DealOption[]>([]);
  const [loading, setLoading] = useState(false);
  const inputRef = useRef<HTMLInputElement>(null);
  const menuRef = useRef<HTMLUListElement>(null);
  const [pos, setPos] = useState({ top: 0, left: 0, width: 0, maxHeight: 192 });
  const debounceRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  const [selectedName, setSelectedName] = useState('');
  const displayValue = open ? search : selectedName;

  // Keep selectedName in sync when value is cleared externally
  useEffect(() => {
    if (value == null) setSelectedName('');
  }, [value]);

  const updatePos = useCallback(() => {
    const el = inputRef.current;
    if (!el) return;
    const r = el.getBoundingClientRect();
    const preferredHeight = 192;
    const spaceBelow = window.innerHeight - r.bottom - 8;
    const spaceAbove = r.top - 8;
    const openUp = spaceBelow < 120 && spaceAbove > spaceBelow;
    const maxHeight = Math.min(preferredHeight, openUp ? spaceAbove : spaceBelow);
    setPos({
      top: openUp ? r.top - maxHeight - 4 : r.bottom + 4,
      left: r.left,
      width: r.width,
      maxHeight: Math.max(96, maxHeight),
    });
  }, []);

  useLayoutEffect(() => {
    if (!open) return;
    updatePos();
    const onWin = () => updatePos();
    window.addEventListener('resize', onWin);
    window.addEventListener('scroll', onWin, true);
    return () => {
      window.removeEventListener('resize', onWin);
      window.removeEventListener('scroll', onWin, true);
    };
  }, [open, updatePos, options.length]);

  useEffect(() => {
    if (!open) {
      setSearch('');
      setOptions([]);
      return;
    }
    const handler = (e: MouseEvent) => {
      const t = e.target as Node;
      if (inputRef.current?.contains(t) || menuRef.current?.contains(t)) return;
      setOpen(false);
    };
    document.addEventListener('mousedown', handler);
    return () => document.removeEventListener('mousedown', handler);
  }, [open]);

  // Debounced search on keystroke
  useEffect(() => {
    if (!open) return;
    if (debounceRef.current) clearTimeout(debounceRef.current);

    const q = search.trim();
    if (q.length < MIN_CHARS) {
      setOptions([]);
      setLoading(false);
      return;
    }

    setLoading(true);
    debounceRef.current = setTimeout(async () => {
      try {
        const results = await onSearch(q);
        setOptions(results);
      } catch {
        setOptions([]);
      } finally {
        setLoading(false);
      }
    }, DEBOUNCE_MS);

    return () => {
      if (debounceRef.current) clearTimeout(debounceRef.current);
    };
  }, [search, open, onSearch]);

  const fieldDisabled = disabled || !ready;

  const showMenu = open && ready && !fieldDisabled;

  // Keep the menu's pointerdown from reaching Radix's outside-dismiss listener,
  // so selecting an option inside a modal Dialog doesn't close the dialog.
  useEffect(() => {
    const el = menuRef.current;
    if (!showMenu || !el) return;
    const stop = (e: Event) => e.stopPropagation();
    el.addEventListener('pointerdown', stop);
    return () => el.removeEventListener('pointerdown', stop);
  }, [showMenu]);

  const handleSelect = (option: DealOption) => {
    onChange(option);
    setSelectedName(option.name);
    setSearch('');
    setOpen(false);
  };

  const showEmpty = showMenu && search.trim().length >= MIN_CHARS && !loading && options.length === 0;
  const showHint = showMenu && search.trim().length < MIN_CHARS;

  const inputId = `deal-search-${label.toLowerCase().replace(/\s+/g, '-')}`;

  return (
    <div>
      <label htmlFor={inputId} className="block text-xs font-medium text-gray-500 uppercase tracking-wider mb-1.5">
        {label}
      </label>
      <div className="relative">
        <input
          ref={inputRef}
          id={inputId}
          name={inputId}
          type="text"
          value={displayValue}
          disabled={fieldDisabled}
          placeholder={ready ? placeholder : notReadyPlaceholder}
          onFocus={() => {
            if (fieldDisabled) return;
            setSearch('');
            setOpen(true);
          }}
          onChange={(e) => {
            setSearch(e.target.value);
            setOpen(true);
            if (!e.target.value) { onChange(null); setSelectedName(''); }
          }}
          className="w-full px-3 py-2 border border-gray-300 rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-blue-500 focus:border-blue-500 disabled:bg-gray-50"
        />
        {value != null && !fieldDisabled && (
          <button
            type="button"
            onClick={() => {
              onChange(null);
              setSelectedName('');
              setSearch('');
              setOpen(false);
            }}
            className="absolute right-2 top-1/2 -translate-y-1/2 text-xs text-gray-400 hover:text-red-500"
            aria-label="Clear deal"
          >
            ✕
          </button>
        )}
      </div>

      {showMenu &&
        createPortal(
          <ul
            ref={menuRef}
            // pointer-events-auto: a modal Dialog disables pointer events on <body>,
            // where this menu is portaled — without this the options aren't clickable.
            className="fixed z-[500] bg-white border border-gray-200 rounded-lg shadow-lg overflow-y-auto pointer-events-auto"
            style={{ top: pos.top, left: pos.left, width: pos.width, maxHeight: pos.maxHeight }}
            onMouseDown={(e) => e.preventDefault()}
          >
            {showHint && (
              <li className="px-3 py-2 text-sm text-gray-400 italic">Type to search deals…</li>
            )}
            {loading && (
              <li className="px-3 py-2 text-sm text-gray-400 italic">Searching…</li>
            )}
            {!loading && options.map((option) => (
              <li
                key={option.id}
                onMouseDown={() => handleSelect(option)}
                className={`px-3 py-2 text-sm cursor-pointer hover:bg-blue-50 ${
                  value === option.id ? 'bg-blue-50 text-blue-700 font-medium' : 'text-gray-700'
                }`}
              >
                {option.name}
              </li>
            ))}
            {showEmpty && (
              <li className="px-3 py-2 text-sm text-gray-400 italic">No deals match</li>
            )}
          </ul>,
          document.body,
        )}
    </div>
  );
}
