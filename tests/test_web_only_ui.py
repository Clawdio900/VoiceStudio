import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_tauri_shell_and_legacy_ui_entrypoints_are_removed():
    assert not (ROOT / "frontend/src-tauri").exists()
    assert not (ROOT / "frontend/src").exists()
    assert not (ROOT / ".github/workflows/release.yml").exists()
    for path in (
        "frontend/index.html",
        "electron/src/shared/main.jsx",
        "electron/src/shared/main-app.jsx",
        "electron/src/shared/App.jsx",
        "scripts/desktop-dev.mjs",
        "scripts/desktop-dev-launch.mjs",
        "scripts/desktop-prod.sh",
        "scripts/desktop-prod.mjs",
        "scripts/desktop-fresh.mjs",
    ):
        assert not (ROOT / path).exists(), f"retired Tauri entrypoint remains: {path}"


def test_electron_desktop_shell_is_removed():
    for path in (
        "electron/src/main",
        "electron/src/preload",
        "electron/electron-builder.config.mjs",
        "electron/electron.vite.config.ts",
        "electron/native-helper-build.mjs",
        "electron/native-linux-libraries.mjs",
        "electron/build",
        "electron/scripts/dev.mjs",
        "native/desktop-bridge",
        ".github/workflows/electron-build.yml",
        ".github/workflows/electron-release.yml",
    ):
        assert not (ROOT / path).exists(), f"retired desktop shell file remains: {path}"


def test_supported_ui_commands_target_the_web_workspace():
    root_package = json.loads((ROOT / "package.json").read_text(encoding="utf-8"))
    assert root_package["workspaces"] == ["electron"]
    for command in ("build", "build:web", "dev:frontend", "test", "typecheck"):
        assert "--cwd electron" in root_package["scripts"][command]
    for retired in ("desktop", "start", "dist", "dist:dir", "desktop-prod"):
        assert retired not in root_package["scripts"]

    assert not (ROOT / "frontend/package.json").exists()
    manifests = [ROOT / "package.json", ROOT / "electron/package.json"]
    assert all("@tauri-apps/" not in path.read_text(encoding="utf-8") for path in manifests)

    electron_package = json.loads((ROOT / "electron/package.json").read_text(encoding="utf-8"))
    assert "vite.shared.config.ts" in electron_package["scripts"]["test"]
    deps = {**electron_package.get("dependencies", {}), **electron_package.get("devDependencies", {})}
    for desktop_dep in ("electron", "electron-builder", "electron-vite", "electron-updater"):
        assert desktop_dep not in deps
    assert "vite.web.config.ts" in electron_package["scripts"]["build:web"]


def test_remote_worker_acceptance_uses_the_electron_workspace():
    script = (ROOT / "scripts/verify-remote-worker.sh").read_text(encoding="utf-8")
    assert "--cwd frontend" not in script
    assert script.count("bun run --cwd electron test -- src/shared/") == 2


def test_docker_builds_the_electron_renderer():
    dockerfile = (ROOT / "deploy/Dockerfile").read_text(encoding="utf-8")
    assert "--cwd electron build:web" in dockerfile
    assert "frontend/index.html" not in dockerfile


def test_home_is_a_work_launcher_not_a_repeated_brand_banner():
    home = (ROOT / "electron/src/renderer/src/features/home/home-page.tsx").read_text(
        encoding="utf-8"
    )
    assert "t('nav.home')" in home
    assert "t('projects.create')" in home
    assert "t('app.name')" not in home
    assert "voicestudio.sh" not in home
    assert "PanelLeftOpenIcon" not in home
