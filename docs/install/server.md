# VoiceStudio on your own server (web app)

VoiceStudio runs as a web app: the FastAPI backend serves the browser UI and
the API from one port. Put it behind HTTPS and open it from any browser.

## Local network only (no domain)

For a server that only devices on your home or office network should reach.

**One-command install (NVIDIA GPU, Ubuntu/Debian or RHEL/Fedora):** installs
Docker and the NVIDIA Container Toolkit if needed, then builds and starts
VoiceStudio on `https://<LAN IP>:9999`. Safe to re-run to update.

```bash
curl -fsSL https://raw.githubusercontent.com/Clawdio900/VoiceStudio/webapp-server/scripts/install-docker-gpu.sh | sudo bash
```

Manual steps:

```bash
git clone -b webapp-server https://github.com/<you>/VoiceStudio.git
cd VoiceStudio
hostname -I          # note the LAN IP, e.g. 192.168.1.50

printf 'VOICESTUDIO_HOST=192.168.1.50\nOMNIVOICE_API_KEY=%s\n' "$(openssl rand -base64 32)" > deploy/.env
chmod 600 deploy/.env

# CPU
docker compose -f deploy/docker-compose.lan.yml up -d --build
# NVIDIA GPU
docker compose -f deploy/docker-compose.lan.yml \
  -f deploy/docker-compose.server.gpu.yml up -d --build
```

Open `https://192.168.1.50:9999` from any device on the network and sign in
with the key (`cat deploy/.env`). Give the server a fixed IP in your router so
the address does not change, and do not forward port 9999 to the internet.

**Certificate warning.** The certificate comes from a private CA that Caddy
creates on first start, so browsers warn once per device; choose "Advanced →
Proceed". To remove the warning, copy the CA and install it as a trusted root
on each device:

```bash
docker compose -f deploy/docker-compose.lan.yml cp \
  caddy:/data/caddy/pki/authorities/local/root.crt ./voicestudio-ca.crt
```

HTTPS is used even on a LAN because browsers only allow the microphone
(recording, dictation) on secure origins.

## Public internet (with a domain)

### Quick start (Docker + automatic HTTPS)

Requirements: a Linux x86-64 server with Docker and Docker Compose, a domain
name pointing at the server, and ports 9999 (the app) and 80 (certificate
challenge only) open. For an NVIDIA GPU, also
install the [NVIDIA Container Toolkit](https://docs.nvidia.com/datacenter/cloud-native/container-toolkit/latest/install-guide.html).

```bash
git clone https://github.com/<you>/VoiceStudio.git
cd VoiceStudio

cat > deploy/.env <<EOF
VOICESTUDIO_DOMAIN=voice.example.com
OMNIVOICE_API_KEY=$(openssl rand -base64 32)
HF_TOKEN=
EOF
chmod 600 deploy/.env

# CPU
docker compose -f deploy/docker-compose.server.yml up -d --build
# NVIDIA GPU
docker compose -f deploy/docker-compose.server.yml \
  -f deploy/docker-compose.server.gpu.yml up -d --build
```

The app is served on **port 9999**. To use another port, add
`VOICESTUDIO_PORT=<port>` to `deploy/.env`.

Open `https://voice.example.com:9999` and sign in with the `OMNIVOICE_API_KEY`
value from `deploy/.env`. The browser exchanges it for a short-lived session
cookie; the key itself is never stored in the browser.

The first start downloads several GB of model weights into the
`voicestudio-data` volume. Follow progress with
`docker compose -f deploy/docker-compose.server.yml logs -f voicestudio`.

### Update

```bash
git pull
docker compose -f deploy/docker-compose.server.yml up -d --build
```

Your voices, projects, settings and models live in the `voicestudio-data`
volume and survive rebuilds.

## Your own reverse proxy (nginx, Traefik, ...)

Run the plain Studio compose (`deploy/docker-compose.yml --profile cpu` or
`--profile gpu`), which publishes the app on `127.0.0.1:3900`, and proxy to it.
Required settings:

- Set `OMNIVOICE_API_KEY` (mandatory) and
  `OMNIVOICE_ALLOWED_ORIGINS=https://voice.example.com:9999`.
- Forward `X-Forwarded-For` and `X-Forwarded-Proto`.
- Allow WebSocket upgrades and disable response buffering (progress is
  streamed with SSE).
- Raise the upload limit (videos for dubbing can be large).

nginx example:

```nginx
server {
    listen 9999 ssl http2;
    server_name voice.example.com;
    # ssl_certificate / ssl_certificate_key ...

    client_max_body_size 2g;

    location / {
        proxy_pass http://127.0.0.1:3900;
        proxy_http_version 1.1;
        proxy_set_header Host $host;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection "upgrade";
        proxy_buffering off;
        proxy_read_timeout 1h;
    }
}
```

## Security model

- **Every browser must authenticate** with the API key. Requests arriving
  through a reverse proxy are never treated as local, even when the proxy
  runs on the same machine: the backend ignores loopback trust for any
  request that carries `X-Forwarded-For`, `Forwarded` or `X-Real-IP`, or
  whose `Host` is not a loopback name (this also blocks DNS rebinding).
- Only `localhost` / `127.0.0.1` requests made directly on the server (for
  example `curl http://127.0.0.1:3900/health` from a shell) are trusted.
- Video URLs given to the dubbing and gallery downloaders must resolve to
  public internet addresses. Set `OMNIVOICE_ALLOW_PRIVATE_MEDIA_URLS=1` to
  allow media on your LAN.
- The optional LAN-share PIN is throttled (10 wrong guesses per client and
  100 overall per 5 minutes) and its cookie is `HttpOnly`.
- Always use HTTPS on the public internet. See [api-auth.md](../api-auth.md)
  for API clients and key rotation.

## Without Docker

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh     # uv
curl -fsSL https://bun.sh/install | bash            # bun
sudo apt install ffmpeg libsndfile1

bun install && bun run build:web                    # builds the browser UI
uv sync                                             # Python backend

export OMNIVOICE_API_KEY="$(openssl rand -base64 32)"
export OMNIVOICE_SERVER_MODE=1
export OMNIVOICE_ALLOWED_ORIGINS=https://voice.example.com:9999
uv run uvicorn main:app --app-dir backend --host 127.0.0.1 --port 3900
```

Then put nginx or Caddy in front as shown above, and run the command under
systemd so it restarts on boot.
