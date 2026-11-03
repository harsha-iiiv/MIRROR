# Deploying the MIRROR studio

Two parts:

* **Static site (Netlify).** The recorded session and the pre-registered results, built by
  `scripts/export_static.py`. It always works, with or without the live server.
* **Live server (a Debian 13 VM).** The studio API on `127.0.0.1:8000`, reached through the
  VM's HTTPS port tunnel. Visitors switch to it with **Live simulation** on the site (the
  choice is kept in the URL as `?live=1`). If the server is asleep the page says
  "Live server waking up…" and retries for up to 90 seconds; if it stays unreachable or busy
  the page keeps showing the recordings and says so.

Nothing secret reaches the browser: the model key lives only in `/etc/mirror/mirror.env`.

## Before deploying (checks)

1. **Client addresses.** Verify whether the tunnel adds `X-Forwarded-For`: from two different networks,
   call `GET /api/health` and compare `admission.tracked`, or log the header once. If it does, set
   `MIRROR_TRUST_PROXY=1`. If it does not, leave it at 0: every visitor then arrives from 127.0.0.1,
   per-address limits are off for them, and only the global limits apply.
2. **Session state.** Set `MIRROR_STUDIO_STATE` (for example `/var/lib/mirror/state`, owned by the
   service account) so sessions, their tokens and shared challenge records survive a restart.
   Without it a restart ends every visitor session and shared records are lost.
3. **Study recording** stays off unless `MIRROR_TRIAL_STORE` is set, and only for sessions a facilitator
   starts with consent.

## 1. Live server

Assumes a fresh Debian 13 machine with 4 cores and 7 GB of memory, and a tunnel that
forwards a public HTTPS URL to `127.0.0.1:8000` (websockets included).

```bash
# packages
sudo apt-get update
sudo apt-get install -y git python3 python3-venv python3-dev build-essential ffmpeg

# service account and code
sudo useradd --system --home /opt/mirror --shell /usr/sbin/nologin mirror
sudo git clone https://github.com/harsha-iiiv/MIRROR.git /opt/mirror
sudo python3 -m venv /opt/mirror/.venv
sudo /opt/mirror/.venv/bin/pip install --upgrade pip
sudo /opt/mirror/.venv/bin/pip install -e "/opt/mirror[meta,video]" "uvicorn[standard]"
sudo chown -R root:root /opt/mirror            # code is read-only for the service

# settings (edit the REPLACE values: the Netlify origin and the model key)
sudo mkdir -p /etc/mirror
sudo install -m 600 -o root -g root /opt/mirror/deploy/mirror.env.example /etc/mirror/mirror.env
sudo nano /etc/mirror/mirror.env

# service
sudo cp /opt/mirror/deploy/mirror-studio.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now mirror-studio
systemctl status mirror-studio --no-pager
curl -s http://127.0.0.1:8000/api/health
```

Point the tunnel at `127.0.0.1:8000` and check from outside:

```bash
curl -s https://YOUR-TUNNEL-HOST/api/health
```

Logs: `journalctl -u mirror-studio -f`. After changing the env file:
`sudo systemctl restart mirror-studio`. To update the code:
`cd /opt/mirror && sudo git pull && sudo /opt/mirror/.venv/bin/pip install -e ".[meta,video]" && sudo systemctl restart mirror-studio`.

### Settings (`/etc/mirror/mirror.env`)

| Variable | Default | Meaning |
|---|---|---|
| `MIRROR_CORS_ORIGINS` | *(none: same origin only)* | Comma-separated origins allowed to use the API and websocket |
| `MIRROR_MAX_SESSIONS` | 6 | Visitor sessions held at once |
| `MIRROR_SESSION_IDLE_S` | 1800 | Idle seconds before a session is closed |
| `MIRROR_MAX_JOBS` | 2 | Simulation jobs at once; the rest queue, and each visitor sees their place |
| `MIRROR_MAX_EVAL_SEEDS` | 6 | Showcase layouts per paired run (dev seeds 9000-9099 only) |
| `MIRROR_STUDIO_EVAL_WORKERS` | 2 | Processes per paired run (use 1 on 4 cores with 2 jobs) |
| `MIRROR_STUDIO_RUNTIME` | system temp | Scratch folder for session files |
| `MIRROR_STUDIO_BACKEND` | auto | `live` for the real simulator |
| `MIRROR_LLM_MODE` | offline | `live`, `replay` or `offline`; a key alone never enables live calls |
| `MIRROR_LLM_BASE_URL` | https://api.meta.ai/v1 | Model endpoint |
| `MIRROR_LLM_MODEL` | muse-spark-1.3 | Model id |
| `MODEL_API_KEY` | *(none)* | Model key, server-side only |
| `MIRROR_LLM_TIMEOUT` | 90 | Seconds per model request |
| `MIRROR_MODEL_CALLS_PER_HOUR` | 80 | Muse Spark requests per sliding hour, whole server (interpretation, corrections, video grounding) |
| `MIRROR_MODEL_CALLS_PER_SESSION` | 16 | Muse Spark requests per visitor session; a video demonstration may need 11 |
| `MIRROR_SAM_CALLS_PER_HOUR` | 24 | Video tracking requests (SAM) per hour, whole server |
| `MIRROR_SAM_CALLS_PER_SESSION` | 8 | Video tracking requests per session; one video demonstration needs up to 8 |
| `MIRROR_VIDEO_CALLS_PER_HOUR` | 12 | Video understanding requests per hour |
| `MIRROR_VIDEO_CALLS_PER_SESSION` | 2 | Video understanding requests per session |
| `MIRROR_VOICE_CALLS_PER_HOUR` | 60 | Speech-to-text requests per hour |
| `MIRROR_VOICE_CALLS_PER_SESSION` | 10 | Speech-to-text requests per session |

#### Tokens, admission, persistence and uploads

The Origin check stops other web sites from using a visitor's browser; it does not authenticate
HTTP clients. Sessions are bearer capabilities (id + secret token), and what any client can make the
server do is bounded per client address and globally (docs/CHALLENGE_API.md sections 1, 7, 8).

| Variable | Default | Meaning |
|---|---|---|
| `MIRROR_REQUIRE_TOKEN` | 1 | Every session request needs `X-Mirror-Token` (0: legacy id-only) |
| `MIRROR_STUDIO_STATE` | *(none)* | Persist sessions here; they survive a restart with the same id and token |
| `MIRROR_RECORDS_MAX` | 200 | Shared challenge records kept (oldest evicted, evicted ids answer 410) |
| `MIRROR_RECORDS_MAX_MB` | 500 | Bytes of shared records kept |
| `MIRROR_EVICT_MIN_IDLE_S` | 300 | A newcomer may only displace a session idle at least this long |
| `MIRROR_SESSIONS_PER_ADDR_HOUR` | 12 | New sessions per client address per hour |
| `MIRROR_LIVE_SESSIONS_PER_ADDR` | 2 | Sessions one address holds at once |
| `MIRROR_UPLOADS_PER_ADDR_HOUR` | 12 | Video uploads per address per hour |
| `MIRROR_UPLOAD_MB_PER_ADDR_HOUR` | 200 | Uploaded megabytes per address per hour |
| `MIRROR_JOBS_PER_ADDR_HOUR` | 120 | Simulation and interpretation jobs per address per hour |
| `MIRROR_MODEL_CALLS_PER_ADDR_HOUR` | 24 | Model requests per address per hour |
| `MIRROR_REQUESTS_PER_ADDR_MIN` | 300 | API requests per address per minute |
| `MIRROR_TRUST_PROXY` | 0 | 1: client address = right-most `X-Forwarded-For` entry |
| `MIRROR_JOB_TIMEOUT_S` | 900 | Longer jobs are reported failed and their late result discarded |
| `MIRROR_UPLOAD_MAX_MB` | 40 | One uploaded video |
| `MIRROR_UPLOAD_SESSION_MB` | 120 | All media one session holds |
| `MIRROR_UPLOAD_GLOBAL_MB` | 1024 | All uploaded media on the server |
| `MIRROR_UPLOAD_SESSION_FILES` | 6 | Files one session holds |
| `MIRROR_UPLOAD_MAX_S` | 90 | Longest video |
| `MIRROR_UPLOAD_MAX_SIDE` | 1920 | Largest width or height |
| `MIRROR_UPLOAD_MAX_PIXELS` | 2073600 | Largest frame (1080p) |
| `MIRROR_UPLOAD_MAX_FPS` | 60 | Highest frame rate |
| `MIRROR_UPLOAD_MAX_FRAMES` | 5400 | Most frames |
| `MIRROR_UPLOAD_PROBE_S` | 15 | Wall time for ffprobe |
| `MIRROR_UPLOAD_DECODE_S` | 60 | Wall time for the full decode check |
| `MIRROR_UPLOAD_MEM_MB` | 1536 | Address-space limit of ffprobe/ffmpeg |
| `MIRROR_UPLOAD_TTL_S` | 1800 | An upload not used by a demonstration expires |
| `MIRROR_CAPTURE_UPLOAD_MAX_MB` | 300 | One phone-capture clip (original phone files) |
| `MIRROR_CAPTURE_UPLOAD_SESSION_MB` | 900 | All capture media one session holds (the global limit still applies) |
| `MIRROR_CAPTURE_UPLOAD_MAX_S` | 120 | Longest capture clip |
| `MIRROR_CAPTURE_UPLOAD_MAX_SIDE` | 3840 | Largest capture clip width or height |
| `MIRROR_STUDY_AUDIO_RETENTION_DAYS` | 0 | Consented study audio is kept only when > 0 (then deleted after that many days) |

The tunnel forwards every visitor from `127.0.0.1`. Set `MIRROR_TRUST_PROXY=1` only if the tunnel
adds `X-Forwarded-For`; check with two networks that `GET /api/health` shows `admission.tracked`
growing per network. Without it, loopback requests cannot be told apart and are exempt from the
per-address limits (counting them together would let one visitor push the others out); only the
global session, job, upload and model limits apply. These are controlled-demo
settings, not measured public-load capacity.

A model request is one structured interpreter request (it may include one repair round).
When a budget is used up, or the model fails, that step uses the offline rule-based
interpreter and the page names the interpreter that produced the specification.

For `replay` (no model calls at all), also set `MIRROR_CASSETTE_DIR` to the folder of
recorded responses and `MIRROR_LLM_MODEL` to the model they were recorded with.

### Health check

`GET /api/health` (no session needed, no secrets):

```json
{
  "ok": true, "service": "mirror-studio", "version": "0.2", "backend": "live",
  "llm": {"mode": "live", "model": "muse-spark-1.3", "endpoint_host": "api.meta.ai",
          "calls_per_hour": 40, "calls_remaining_hour": 37, "calls_per_session": 8},
  "sessions": {"active": 1, "max": 6, "idle_expiry_s": 1800, "busy": 0},
  "jobs": {"running": 0, "queued": 0, "max": 2},
  "eval": {"max_seeds": 6, "showcase_seeds": [9000, 9099]},
  "uptime_s": 812.4
}
```

`llm` holds only `mode` when the mode is `offline`, and no budget fields for `replay`.

### API errors the page handles

| Status | `error` | When |
|---|---|---|
| 400 | `no_session` | Request without the `X-Mirror-Session` header |
| 404 | `session_expired` | Session closed (idle, evicted or server restarted); the page starts a new one |
| 409 | `session_busy` | This session already has a job running |
| 503 | `server_busy` | Every session slot has work in progress (`Retry-After: 30`) |
| 403 | `origin_not_allowed` / `evaluation_fenced` | Origin not listed / non-showcase or frozen seeds |
| 401 | `token_required` / `token_invalid` | Missing or wrong `X-Mirror-Token` |
| 429 | `rate_limited` | Per-address limit (`Retry-After`) |
| 409 | `stale_generation` | The request named a replaced demonstration generation |
| 422 | `too_many_seeds` | More than `MIRROR_MAX_EVAL_SEEDS` layouts |

## 2. Static site on Netlify

Build the site on a machine with the repository installed:

```bash
MIRROR_LLM_MODE=replay python scripts/export_static.py --out site --evaluation results/eval_muse
```

The deployed site loads the second pre-registered evaluation (`results/eval_muse`, Muse Spark
specifications, seeds 50000+/60000+). `--evaluation results/eval` builds the first registration
(seeds 30000+/40000+) instead; the page names whichever folder it loaded.

Point it at the live server (rewrites only `site/config.js`, `site/netlify.toml` and
`site/_headers`; the content security policy then allows exactly that server):

```bash
python scripts/export_static.py --write-config site --api-base https://YOUR-TUNNEL-HOST
```

Deploy the folder as it is:

```bash
cd site && npx netlify-cli deploy --prod --dir .
```

(or drag the `site` folder onto the Netlify dashboard; `_headers` carries the same rules).
Then add the Netlify origin to `MIRROR_CORS_ORIGINS` on the server and restart it.

`deploy/netlify.toml` is the same configuration with a policy that allows any HTTPS API,
for a repository-linked Netlify site whose publish folder is the exported `site/`.

Caching: `index.html`, `config.js` and `data/session.json` are always revalidated; rollout
files are requested with the export id (`?v=…`) and cached for a year.

## 3. Check

1. Open the Netlify URL: the recorded session plays, the switch reads "Live simulation off".
2. Turn it on: the URL gains `?live=1`, the badge reads "live simulation" and the story's
   first action runs on the server.
3. Stop the service (`sudo systemctl stop mirror-studio`) and reload: after the wake-up
   wait the page says "Live server unavailable, showing recordings". Start it again.
