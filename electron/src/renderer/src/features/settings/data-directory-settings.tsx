import { useQuery } from '@tanstack/react-query';
import { useTranslation } from 'react-i18next';
import { DatabaseIcon } from 'lucide-react';
import { apiJson } from '@/lib/api/client';
import { SettingsRow, SettingsSection } from './settings-layout';

/** Shows where the server keeps its data. The location is configured on the server. */
export function DataDirectorySettings() {
  const { t } = useTranslation();
  return (
    <SettingsSection icon={DatabaseIcon} title={t('settings.data_directory')}>
      <SettingsRow
        id="data-directory"
        title={t('settings.data_directory')}
        description={t('settings.data_directory_desc')}
      >
        <DataDirectoryPath />
      </SettingsRow>
    </SettingsSection>
  );
}

function DataDirectoryPath() {
  const { t } = useTranslation();
  const query = useQuery({
    queryKey: ['system-info'],
    queryFn: ({ signal }) => apiJson<{ data_dir?: string }>('/system/info', { signal }),
  });
  const path = query.data?.data_dir || '';
  return (
    <code
      title={path}
      className="min-w-0 max-w-full flex-1 truncate rounded-md border border-border/60 bg-muted/35 px-2.5 py-1.5 text-xs text-muted-foreground @2xl:max-w-[32rem]"
    >
      {path || t('common.loading')}
    </code>
  );
}
