import { cleanup, render, screen } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { afterEach, describe, expect, test, vi } from 'vitest';

const mocks = vi.hoisted(() => ({ api: vi.fn() }));

vi.mock('@/components/bridge', () => ({ appVersion: () => '0.5.2' }));
vi.mock('@/lib/api/client', () => ({ apiJson: mocks.api, describeError: String }));
vi.mock('react-i18next', () => ({
  useTranslation: () => ({
    t: (key: string, values?: Record<string, string>) =>
      values ? `${key} ${Object.values(values).join(' ')}` : key,
  }),
}));

import { UpdateSettings } from './update-settings';

function renderSettings() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <UpdateSettings />
    </QueryClientProvider>,
  );
}

describe('UpdateSettings', () => {
  afterEach(() => {
    cleanup();
    vi.clearAllMocks();
  });

  test('shows the server version, latest backup and changelog without updater controls', async () => {
    mocks.api.mockImplementation(async (path: string) => {
      if (path.includes('/changelog'))
        return {
          available: true,
          releases: [{ version: '0.5.2', intro: '**Faster** dubbing', sections: [] }],
        };
      if (path.includes('/db-backup'))
        return { available: true, latest: { path: '/data/b.db', created_at: 1, size_bytes: 1 } };
      throw new Error(`unexpected ${path}`);
    });
    renderSettings();

    expect(screen.getByText('v0.5.2')).toBeInTheDocument();
    expect(await screen.findByText('Faster dubbing')).toBeInTheDocument();
    expect(await screen.findByText(/updates\.backup_latest/)).toBeInTheDocument();
    expect(screen.queryByText('updates.check_now')).not.toBeInTheDocument();
    expect(screen.queryByText('update.restart')).not.toBeInTheDocument();
  });

  test('explains when no backup exists yet', async () => {
    mocks.api.mockImplementation(async (path: string) =>
      path.includes('/changelog')
        ? { available: false, releases: [] }
        : { available: false, latest: null },
    );
    renderSettings();
    expect(await screen.findByText('updates.backup_none')).toBeInTheDocument();
  });
});
