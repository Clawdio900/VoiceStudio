# Web UI — internal contracts (read before editing)

## Same-origin API rule

Renderer HTTP requests never call `http://127.0.0.1:3900` directly (CORS). They use
app-relative `/api/...` paths:

- dev: the Vite dev server (`vite.web.config.ts`, port 3901) proxies `/api` → backend
  (see `src/shared/web-api-routing.ts`).
- prod: the FastAPI backend serves the built UI (`frontend/dist`) and the API from
  the same origin. So `API_BASE = '/api'` in the renderer, and `/api/audio/<file>` is
  a valid `<audio src>`.

`apiJson()` arguments are backend paths. Most are unprefixed (`/engines`), while the
shared settings and MCP routers intentionally retain their backend `/api/...` prefix.
Those calls therefore appear as `/api/api/settings/...` in renderer network tools: the
first `/api` is the transport prefix removed by the proxy; the second belongs to the
backend route. Do not collapse the pair in the API client.

Live dictation uses a WebSocket at `/api/ws/transcribe`; the dev proxy removes only its
own exact localhost origin on WebSocket upgrades. Other origins remain subject to
backend validation.

## Host bridge

There is no desktop shell. `getBridge()` (`@/components/bridge`) returns null in the
web app; the optional `window.voicestudio` type lives in `@/lib/bridge-types` so unit
tests can stub a host. Do not add new features that require it.

## Renderer module API (lib + hooks) — implemented by the data-layer task,

## consumed by the UI task. Names/paths are fixed.

- `@/lib/api/client.ts`: `API_BASE='/api'`; `class ApiError extends Error { status:number; detail:string; payload:ApiErrorPayload|null }`;
  `apiFetch(path, init?)` (throws ApiError on !ok; `path` is relative to API_BASE);
  `apiJson<T>(path, init?)`; `audioUrl(filename)` → `/api/audio/<filename>`;
  `profileAudioUrl(id)` → `/api/profiles/<id>/audio`.
- `@/lib/api/generate.ts`: `generateClone(input: CloneGenerateInput, opts?: { signal?: AbortSignal; onProgress?: (pct: number|null) => void }): Promise<GenerateResult>`;
  `sanitizeInstruct(free: string): { instruct: string; unsupported: string[]; duplicates: string[]; conflicts: string[] }` (port of electron/src/shared/utils/voiceInstruct.js buildDesignInstruct with empty vdStates);
  `CLONE_MAX_SECONDS = 15`, `REF_HARD_MAX_SECONDS = 75`.
- `@/lib/api/profiles.ts`: `listProfiles()`, `createCloneProfile({ name, refAudio, refAudioName, refText, instruct, language })`, `deleteProfile(id)`.
- `@/lib/api/history.ts`: `listHistory()`, `clearHistory()`, `deleteHistoryItem(id)`, `setHistoryStarred(id, starred)`.
- `@/lib/api/engines.ts`: `getEngines()`, `getSystemInfo()`.
- `@/lib/api/audio.ts`: `cleanAudio(blob, filename): Promise<File>` (POST /clean-audio, field `audio`, honours X-Clean-Filename).
- `@/lib/query.ts`: `queryClient`, `queryKeys = { profiles:['profiles'], history:['history'], engines:['engines'], systemInfo:['system','info'] }`.
- `@/lib/languages.ts`: `LANGUAGES: string[]` (bundled list, index 0 = 'Auto'), `POPULAR_LANGUAGES: string[]`, `TAGS: string[]` (expression tokens).
- `@/lib/store/clone-settings.ts` (TanStack Store, persisted to localStorage `voicestudio.clone.settings.v1`):
  `interface CloneSettings { text; language; refText; instruct; steps; cfg; speed; tShift; posTemp; classTemp; layerPenalty; denoise; postprocess; duration; showOverrides; selectedProfileId: string|null; autoPlay: boolean }`,
  `DEFAULT_CLONE_SETTINGS`, `cloneSettingsStore`, `useCloneSetting(key)`, `useCloneSettings()`, `setCloneSetting(key, value)`, `patchCloneSettings(partial)`, `resetOverrides()`.
- `@/lib/store/reference.ts` (not persisted): `interface ReferenceState { file: File|null; durationSeconds: number|null; objectUrl: string|null }`, `useReference()`, `setReferenceFile(file: File|null): Promise<{ ok: boolean; durationSeconds: number|null; tooLong: boolean }>` (probes duration; clears selectedProfileId when a file is set).
- `@/lib/store/output.ts`: `interface OutputState { result: GenerateResult|null; objectUrl: string|null; text: string }`, `useLatestOutput()`, `setLatestOutput(result, text)`.
- `@/lib/audio/playback.ts`: `claimPlayback(stop: () => void, source: string): () => void`, `stopActivePlayback()`, `usePlaybackSource(): string|null`, `playBlob(blob, source): Promise<void>` (plays via a hidden <audio>, claims the slot).
- `@/hooks/use-backend-status.ts`: `useBackendStatus(): BackendStatus` (useSyncExternalStore; in the web app it reports the backend as ready and relies on API errors/online state).
- `@/hooks/use-generate.ts`: `useGenerateClone(): { generate(): Promise<void>; cancel(): void; isGenerating: boolean; elapsedSeconds: number; progress: number|null }` (validation toasts via sonner + i18next; routing/dropped toasts; sets output store; invalidates history; autoplay via playBlob when settings.autoPlay).
- `@/hooks/use-recording.ts`: `useRecording(onRecorded: (file: File) => void): { isRecording; isStarting; isCleaning; seconds: number; inputs: MediaDeviceInfo[]; selectedInputId: string; setSelectedInputId; channelMode: 'auto'|'mono'|'stereo'; setChannelMode; level: number; start(): Promise<void>; stop(): void }`.
- `@/hooks/use-profiles.ts`: `useProfiles()` (react-query, `Profile[]`), `useCreateCloneProfile()`, `useDeleteProfile()` (mutations; invalidate profiles).
- `@/hooks/use-history.ts`: `useHistory()`, `useDeleteHistoryItem()`, `useClearHistory()`, `useToggleStarred()`.
- `@/hooks/use-engines.ts`: `useEngines(): { data?: EnginesResponse; anyTtsReady: boolean; activeTts: EngineBackend|null; isLoading: boolean }`.
- Toasts: `import { toast } from 'sonner'`. Strings: `i18next.t('...')` / `useTranslation()`; keys live in `src/renderer/src/i18n/locales/en.json` — add keys there, never hardcode UI text.

## Tooling

- `bun run typecheck` (tsgo, TypeScript 7), `bun run lint` (vp lint / oxlint), `bun run format` (vp fmt / oxfmt), `bun run test` (vp test / vitest, jsdom), `bun run build:web` (Vite → `../frontend/dist`), `bun run dev:web`.
- Do NOT add dependencies. If one is truly needed, report it instead.
