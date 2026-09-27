import { useState } from 'react';
import { useTranslation } from 'react-i18next';
import { ChevronDownIcon, RefreshCwIcon, ShieldCheckIcon, SparklesIcon } from 'lucide-react';
import { useQuery } from '@tanstack/react-query';
import { appVersion } from '@/components/bridge';
import { apiJson } from '@/lib/api/client';
import { SettingsRow, SettingsSection } from './settings-layout';

interface ChangelogRelease {
  version: string;
  date?: string;
  intro?: string;
  sections?: { title?: string; bullets: string[] }[];
}

interface ChangelogResponse {
  available: boolean;
  releases: ChangelogRelease[];
}

interface BackupState {
  available: boolean;
  latest?: { path: string; created_at: number; size_bytes: number } | null;
}

/**
 * Version, database backup and changelog for the running server. The web app
 * is updated by redeploying the server (for example a new Docker image), so
 * there is no in-app download or restart flow.
 */
export function UpdateSettings() {
  const { t } = useTranslation();
  const [expandedRelease, setExpandedRelease] = useState<string | null | undefined>(undefined);
  const changelog = useQuery({
    queryKey: ['changelog', 5],
    queryFn: ({ signal }) =>
      apiJson<ChangelogResponse>('/api/settings/changelog?limit_versions=5', {
        signal,
      }),
    staleTime: 300_000,
  });
  const backup = useQuery({
    queryKey: ['db-backup'],
    queryFn: ({ signal }) => apiJson<BackupState>('/api/settings/db-backup', { signal }),
    staleTime: 300_000,
  });

  return (
    <>
      <SettingsSection icon={RefreshCwIcon} title={t('updates.tab')}>
        <SettingsRow id="app-version" title={t('about.version')}>
          <code className="rounded-md border border-border/60 bg-muted/35 px-2.5 py-1.5 text-xs text-muted-foreground">
            v{appVersion()}
          </code>
        </SettingsRow>
      </SettingsSection>

      <SettingsSection icon={ShieldCheckIcon} title={t('updates.backup_line')}>
        <div className="px-4 py-3 text-sm text-muted-foreground">
          {backup.isPending
            ? t('common.loading')
            : backup.data?.available && backup.data.latest
              ? t('updates.backup_latest', {
                  when: new Date(backup.data.latest.created_at * 1000).toLocaleString(),
                })
              : t('updates.backup_none')}
        </div>
      </SettingsSection>

      {changelog.data?.available && changelog.data.releases.length > 0 && (
        <SettingsSection icon={SparklesIcon} title={t('update.whats_new')}>
          <div className="divide-y divide-border/50">
            {changelog.data.releases.map((release, index) => {
              const open =
                expandedRelease === undefined ? index === 0 : expandedRelease === release.version;
              return (
                <details key={release.version} className="group px-4 py-3" open={open}>
                  <summary
                    className="flex cursor-pointer list-none items-center gap-2 text-sm font-medium"
                    onClick={(event) => {
                      event.preventDefault();
                      setExpandedRelease(open ? null : release.version);
                    }}
                  >
                    <ChevronDownIcon className="size-4 -rotate-90 text-muted-foreground transition-transform group-open:rotate-0" />
                    <span>v{release.version}</span>
                    {release.date && (
                      <time className="ml-auto text-xs font-normal text-muted-foreground">
                        {release.date}
                      </time>
                    )}
                  </summary>
                  <div className="mt-3 space-y-4 pl-6 text-xs leading-5 text-muted-foreground">
                    {release.intro && (
                      <p className="font-medium text-foreground">
                        {release.intro.replaceAll('**', '')}
                      </p>
                    )}
                    {release.sections?.map((section, sectionIndex) => (
                      <section key={`${section.title || 'notes'}-${sectionIndex}`}>
                        {section.title && (
                          <h3 className="mb-1.5 font-medium text-foreground">{section.title}</h3>
                        )}
                        <ul className="list-disc space-y-1.5 pl-4">
                          {section.bullets.map((bullet, bulletIndex) => (
                            <li key={bulletIndex}>{bullet}</li>
                          ))}
                        </ul>
                      </section>
                    ))}
                  </div>
                </details>
              );
            })}
          </div>
        </SettingsSection>
      )}

    </>
  );
}
