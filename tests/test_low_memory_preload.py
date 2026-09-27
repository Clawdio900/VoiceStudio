"""OMNIVOICE_PRELOAD_TTS=0 must skip the boot-time TTS warm-up (low-RAM servers)."""
import asyncio
import logging


def test_tts_preload_can_be_disabled(monkeypatch, caplog):
    from services import model_manager

    monkeypatch.setenv("OMNIVOICE_PRELOAD_TTS", "0")
    monkeypatch.setattr(model_manager, "model", None)

    def must_not_run(*a, **k):
        raise AssertionError("preload must not probe or load the model")

    monkeypatch.setattr(model_manager, "_is_model_installed_locally", must_not_run, raising=False)
    with caplog.at_level(logging.INFO):
        asyncio.run(model_manager.preload_model())
    assert model_manager.model is None
    assert "TTS preload disabled" in caplog.text


def test_tts_preload_flag_defaults_on(monkeypatch):
    from services import model_manager

    monkeypatch.delenv("OMNIVOICE_PRELOAD_TTS", raising=False)
    assert model_manager._env_flag("OMNIVOICE_PRELOAD_TTS", default=True) is True
