import { useEffect, useState } from 'react';
import { Building2, FileBarChart2, Mail, FileText, Tag, Settings, LayoutDashboard, Network, Bell } from 'lucide-react';
import { NavLink } from '@/components/NavLink';
import peakLogo from '@/assets/peak-logo.svg';
import peakIcon from '@/assets/peak-icon.svg';
import { listFiles } from '@/api/portfolio';
import { getSyncAlertsUnreadCount } from '@/api/syncAlerts';
import {
  Sidebar,
  SidebarContent,
  SidebarGroup,
  SidebarGroupContent,
  SidebarGroupLabel,
  SidebarMenu,
  SidebarMenuButton,
  SidebarMenuItem,
  useSidebar,
} from '@/components/ui/sidebar';

const navItems = [
  { title: 'In Review Tracker', url: '/', icon: Building2 },
  { title: 'Audit Tracker', url: '/review-cycle-adjustments', icon: Building2 },
  { title: 'Master Scoping', url: '/master-scoping', icon: Network },
  { title: 'Audit Dashboard', url: '/audit-dashboard', icon: LayoutDashboard },
  { title: 'Email Templates', url: '/email-templates', icon: Mail },
  { title: 'File Tagging', url: '/file-tagging', icon: FileText },
  { title: 'Email Tagging', url: '/email-tagging', icon: Tag },
  { title: 'Audited Financials Emails', url: '/audited-financials-emails', icon: FileBarChart2 },
  { title: 'Sync Alerts', url: '/sync-alerts', icon: Bell },
  { title: 'Settings', url: '/settings', icon: Settings },
];

export function AppSidebar() {
  const { state } = useSidebar();
  const collapsed = state === 'collapsed';
  const [hasUnattached, setHasUnattached] = useState(false);
  const [syncAlertsUnread, setSyncAlertsUnread] = useState(0);

  useEffect(() => {
    const check = async () => {
      try {
        const res = await listFiles({ unattached_only: true, limit: 1, offset: 0 });
        setHasUnattached((res.total ?? 0) > 0);
      } catch {
        // silently ignore — sidebar alert is non-critical
      }
    };
    void check();

    const onUpdated = () => { void check(); };
    window.addEventListener('files:updated', onUpdated);
    return () => window.removeEventListener('files:updated', onUpdated);
  }, []);

  useEffect(() => {
    const checkSyncAlerts = async () => {
      try {
        const res = await getSyncAlertsUnreadCount();
        setSyncAlertsUnread(res.count);
      } catch {
        // silently ignore
      }
    };
    void checkSyncAlerts();

    const onUpdated = () => { void checkSyncAlerts(); };
    window.addEventListener('sync-alerts:updated', onUpdated);
    return () => window.removeEventListener('sync-alerts:updated', onUpdated);
  }, []);

  return (
    <Sidebar collapsible="icon" className="bg-white border-r border-gray-200">
      <SidebarContent className="bg-white">
        <div className={`px-4 py-5 border-b border-gray-200 ${collapsed ? 'px-2' : ''}`}>
          {!collapsed ? (
            <div className="flex items-center gap-2">
              <img src={peakIcon} alt="" className="h-8 w-8" />
              <img src={peakLogo} alt="" className="h-6" />
            </div>
          ) : (
            <img src={peakIcon} alt="" className="h-8 w-8 mx-auto" />
          )}
        </div>

        <SidebarGroup>
          <SidebarGroupLabel className="text-xs font-medium text-gray-500 uppercase tracking-wider px-4">Navigation</SidebarGroupLabel>
          <SidebarGroupContent>
            <SidebarMenu>
              {navItems.map((item) => {
                const showDot =
                  (item.url === '/file-tagging' && hasUnattached) ||
                  (item.url === '/sync-alerts' && syncAlertsUnread > 0);
                const badgeCount = item.url === '/sync-alerts' ? syncAlertsUnread : 0;
                return (
                  <SidebarMenuItem key={item.title}>
                    <SidebarMenuButton asChild>
                      <NavLink
                        to={item.url}
                        end={item.url === '/'}
                        className="relative flex items-center gap-3 px-3 py-2 rounded-md text-sm font-medium text-gray-600 hover:bg-gray-100 transition-colors"
                        activeClassName="bg-blue-100 text-blue-700"
                      >
                        <span className="relative shrink-0">
                          <item.icon className="h-4 w-4" />
                          {showDot && collapsed && (
                            <span className="absolute -top-1 -right-1 h-2 w-2 rounded-full bg-red-500 ring-1 ring-white" />
                          )}
                        </span>
                        {!collapsed && (
                          <>
                            <span>{item.title}</span>
                            {showDot && badgeCount > 0 ? (
                              <span className="ml-auto inline-flex items-center justify-center rounded-full bg-red-500 text-white text-xs min-w-[1rem] h-4 px-1 shrink-0">
                                {badgeCount > 99 ? '99+' : badgeCount}
                              </span>
                            ) : showDot ? (
                              <span className="ml-auto h-2 w-2 rounded-full bg-red-500 ring-1 ring-white shrink-0" />
                            ) : null}
                          </>
                        )}
                      </NavLink>
                    </SidebarMenuButton>
                  </SidebarMenuItem>
                );
              })}
            </SidebarMenu>
          </SidebarGroupContent>
        </SidebarGroup>
      </SidebarContent>
    </Sidebar>
  );
}
