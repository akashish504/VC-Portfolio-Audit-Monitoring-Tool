import { NavLink, useLocation } from 'react-router-dom';
import { cn } from '@/lib/utils';

const TABS = [
  { label: 'Timeline', to: '/audit-dashboard', end: true },
  { label: 'Scoping', to: '/audit-dashboard/scoping', end: false },
  { label: 'Variance', to: '/audit-dashboard/variance', end: false },
];

/** Top-level switcher between the Scoping, Variance and Timeline dashboard views.
 *  Preserves the current query string (e.g. ?cycle=) across the views. */
export default function DashboardViewTabs() {
  const { search } = useLocation();
  return (
    <div className="inline-flex rounded-lg border border-gray-200 bg-gray-50 p-0.5">
      {TABS.map((t) => (
        <NavLink
          key={t.to}
          to={`${t.to}${search}`}
          end={t.end}
          className={({ isActive }) =>
            cn(
              'rounded-md px-4 py-1.5 text-sm font-medium transition-colors',
              isActive ? 'bg-white text-blue-700 shadow-sm' : 'text-gray-500 hover:text-gray-700',
            )
          }
        >
          {t.label}
        </NavLink>
      ))}
    </div>
  );
}
