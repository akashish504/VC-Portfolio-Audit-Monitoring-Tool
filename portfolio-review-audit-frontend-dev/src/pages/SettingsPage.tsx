import { useState } from 'react';
import { Settings } from 'lucide-react';

import ParameterThresholdPage from './ParameterThresholdPage';
import CycleAdjustmentsTab from '@/components/settings/CycleAdjustmentsTab';
import DataSyncConfigTab from '@/components/settings/DataSyncConfigTab';
import FinancialMetricMappingTab from '@/components/settings/FinancialMetricMappingTab';
import SnowflakePRFinancialMappingTab from '@/components/settings/SnowflakePRFinancialMappingTab';
import EmailTemplateAssignmentTab from '@/components/settings/EmailTemplateAssignmentTab';
import OrgChartUploadTab from '@/components/settings/OrgChartUploadTab';

const tabs = [
  { key: 'thresholds' as const, label: 'Metric Thresholds' },
  { key: 'cycles' as const, label: 'Cycle Adjustments' },
  { key: 'financial-mapping' as const, label: 'Financial extraction mapping' },
  { key: 'snowflake-pr-mapping' as const, label: 'Snowflake PR formulas' },
  { key: 'email-templates' as const, label: 'Tag Email Templates' },
  { key: 'org-chart-upload' as const, label: 'Org Chart Upload' },
  { key: 'data-sync-config' as const, label: 'Data Sync Config' },
];

export default function SettingsPage() {
  const [activeTab, setActiveTab] = useState<
    'thresholds' | 'cycles' | 'financial-mapping' | 'snowflake-pr-mapping' | 'email-templates' | 'org-chart-upload' | 'data-sync-config'
  >('thresholds');

  return (
    <div className="h-full overflow-auto bg-gray-50 p-6">
      <div className="mb-6">
        <h1 className="text-2xl font-bold text-gray-900">Settings</h1>
      </div>

      <div className="flex flex-wrap gap-1 mb-6 bg-gray-100 rounded-lg p-1">
        {tabs.map((t) => (
          <button
            key={t.key}
            onClick={() => setActiveTab(t.key)}
            className={`rounded-md px-3 py-1.5 text-sm font-medium transition-colors ${
              activeTab === t.key ? 'bg-white shadow-sm text-gray-900' : 'text-gray-500 hover:text-gray-700'
            }`}
          >
            {t.label}
          </button>
        ))}
      </div>

      {activeTab === 'thresholds' && <ParameterThresholdPage />}
      {activeTab === 'cycles' && <CycleAdjustmentsTab />}
      {activeTab === 'financial-mapping' && <FinancialMetricMappingTab />}
      {activeTab === 'snowflake-pr-mapping' && <SnowflakePRFinancialMappingTab />}
      {activeTab === 'email-templates' && <EmailTemplateAssignmentTab />}
      {activeTab === 'org-chart-upload' && <OrgChartUploadTab />}
      {activeTab === 'data-sync-config' && <DataSyncConfigTab />}
    </div>
  );
}

