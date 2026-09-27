# Removing VoiceStudio from a server

VoiceStudio keeps everything in its data directory: voices, projects,
settings, the database and downloaded model weights. Nothing is registered
anywhere else.

## Docker (server compose)

```bash
# stop and remove the containers, keep the data
docker compose -f deploy/docker-compose.server.yml down

# also delete all data, models and Caddy certificates
docker compose -f deploy/docker-compose.server.yml down -v
```

With the plain Studio compose, use `deploy/docker-compose.yml` and the same
commands. `docker image prune` reclaims the image layers afterwards.

## Source install

Stop the service, then delete:

| What | Where |
|---|---|
| App data (voices, projects, DB, settings) | `$OMNIVOICE_DATA_DIR`, default `omnivoice_data/` in the checkout |
| Model cache (several GB) | `$HF_HOME`, default `~/.cache/huggingface` |
| Python environment | `.venv/` in the checkout |
| The checkout itself | the cloned `VoiceStudio/` folder |
