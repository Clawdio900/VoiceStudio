import { cleanup, render, screen } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { afterEach, expect, it, vi } from 'vitest';

const mock = vi.hoisted(() => ({ api: vi.fn() }));

vi.mock('@/lib/api/client', async (load) => {
  const actual = await load<typeof import('@/lib/api/client')>();
  return { ...actual, apiJson: mock.api };
});
vi.mock('react-i18next', () => ({
  useTranslation: () => ({ t: (key: string) => key }),
}));

import { DataDirectorySettings } from './data-directory-settings';

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

it('shows the server data directory without offering a local relocation', async () => {
  mock.api.mockResolvedValue({ data_dir: '/data/voicestudio' });
  render(
    <QueryClientProvider
      client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}
    >
      <DataDirectorySettings />
    </QueryClientProvider>,
  );
  expect(await screen.findByText('/data/voicestudio')).toBeInTheDocument();
  expect(mock.api).toHaveBeenCalledWith('/system/info', expect.anything());
  expect(screen.queryByRole('button')).not.toBeInTheDocument();
});
