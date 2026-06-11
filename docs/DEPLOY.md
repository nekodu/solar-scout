# Deploying the demo to demo.th3cyberworld.* via Cloudflare

## Why not Cloudflare Workers/Pages directly

The backend runs Python with torch (YOLO), OpenCV and analyses that take
10-60 s and cache files. Workers are V8 isolates with millisecond CPU budgets
and no torch - they cannot host this. The proven setup is a small VPS running
the Docker image, fronted by a **Cloudflare Tunnel**: you get Cloudflare TLS,
caching and WAF on your domain with **zero open ports** on the server.

## What you need

1. A VPS with 4 GB RAM (torch + a 74 MB LoD2 tile parse peak around 2-3 GB).
   Hetzner CX22 (Falkenstein/Nuremberg, ~4 EUR/month) fits and keeps the data
   in Germany, which is a nice line when showing this to a German solar company.
2. Your domain (th3cyberworld.*) added to a free Cloudflare account
   (Cloudflare must run its DNS: set the two Cloudflare nameservers at your
   registrar; propagation takes minutes to hours).

## Steps

### 1. Server

```bash
ssh root@<vps-ip>
# install docker (Ubuntu/Debian)
curl -fsSL https://get.docker.com | sh
# get the code there (rsync from your machine, or git clone if you push a repo)
rsync -a --exclude .venv --exclude private --exclude market.db \
      /home/jahack/dev/pansun/ root@<vps-ip>:/opt/solarscout/
```

### 2. Cloudflare Tunnel

In the Cloudflare dashboard: **Zero Trust -> Networks -> Tunnels -> Create a
tunnel** (Cloudflared type). Name it `solarscout`. Copy the token from the
"install connector" command - everything after `--token`.

Add a **Public Hostname** to the tunnel:
- Subdomain: `1komma5` (personal touch) or `demo`, Domain: `th3cyberworld.*`
- Service: `HTTP` -> `solarscout:8080`   (the compose service name)

### 3. Launch

```bash
cd /opt/solarscout
echo 'CF_TUNNEL_TOKEN=<paste token>' > .env
docker compose up -d --build         # first build downloads ~2 GB (torch)
docker compose exec solarscout python -m solar_scout.demo_seed --db /data/market.db
```

https://demo.th3cyberworld.* is live as soon as the tunnel connects.

### 4. Restrict access to the 1KOMMA5 reviewers (recommended)

Cloudflare **Zero Trust -> Access -> Applications -> Add an application**
(Self-hosted): application domain `demo.th3cyberworld.*`, then a policy
*Allow* with *Emails* listing the reviewers (their own addresses) plus yours.
Visitors get a one-time-PIN e-mail check; nobody else can even load the page.
Free for up to 50 users. Skip this only if you want the link fully public.

### 5. Hardening that is already built in

- `/api/analyze` is capped at 2 concurrent analyses and 8 per client per
  10 minutes (Cloudflare's `cf-connecting-ip` header is honoured).
- Per-address results are private, token-scoped and expire after 6 h.
- Add a Cloudflare WAF rate-limiting rule on `/api/*` (free plan includes one)
  as a second layer if you make it public.

## Warm-up before the demo call

Click all four example chips once after deploying: that caches the Berlin and
Munich imagery and LoD2 tiles in the `solarscout-cache` volume, so examples
respond in seconds instead of downloading Bavaria's 157 MB tile live.

## Updating

```bash
cd /opt/solarscout && git pull   # or rsync again
docker compose up -d --build
```

## Outbound traffic note

The app calls Nominatim, Overpass, Photon, PVGIS, the states' WMS/LoD2
servers and Hugging Face (first build only). All are fine at demo volumes;
Nominatim and Overpass have fair-use policies, so do not load-test the
analyze endpoint.


## Free hosting options (instead of a paid VPS)

Most "free" platforms cannot run this app (Vercel and GitHub Pages are
serverless/static and cannot hold a 2.6 GB torch image; AWS's free EC2 has
1 GB RAM and we peak at 2-3 GB). Two options genuinely work:

### Option A: Oracle Cloud Always Free (recommended, $0 forever)

An ARM VM with up to 4 cores / 24 GB RAM, free permanently. Same architecture
as the paid-VPS path, just free. Detailed walk-through:

1. **Account**: sign up at cloud.oracle.com. A card is required for identity
   verification but Always Free resources are never charged. Pick your home
   region carefully: it cannot be changed later, and ARM capacity varies
   (eu-frankfurt-1 works but is popular; eu-marseille-1 or eu-stockholm-1
   often have more headroom).
2. **Instance**: Compute -> Instances -> Create.
   - Image: **Ubuntu 24.04** (aarch64). Not Oracle Linux: the Docker
     convenience script targets Ubuntu/Debian.
   - Shape: **VM.Standard.A1.Flex**, e.g. **2 OCPU / 12 GB RAM** (the free
     budget is 4 OCPU / 24 GB total, leaving headroom for a second VM later).
   - Add your SSH public key. Boot volume default (47 GB) is fine.
   - "Out of capacity" error: try fewer OCPUs, another availability domain,
     or simply retry later. Upgrading the account to Pay-As-You-Go keeps
     Always Free resources free and usually unlocks capacity instantly.
3. **No firewall work needed**: Oracle's default iptables rules block almost
   all inbound traffic, which normally bites everyone. The Cloudflare Tunnel
   only makes OUTBOUND connections, so you can leave the security lists and
   iptables exactly as they are. Do not open any ports.
4. **Software**: copy `scripts/setup-vm.sh` to the VM and run it; it installs
   Docker and tells you the rsync command for the code. Or by hand:

   ```bash
   # from your machine
   rsync -a --exclude .venv --exclude private --exclude market.db \
         --exclude __pycache__ ~/dev/pansun/ ubuntu@<VM-IP>:/opt/solarscout/
   scp scripts/setup-vm.sh ubuntu@<VM-IP>:
   ssh ubuntu@<VM-IP> bash setup-vm.sh
   ```

   The script asks for the Cloudflare Tunnel token (dashboard: Zero Trust ->
   Networks -> Tunnels -> Create, public hostname `demo.th3cyberworld.*` ->
   HTTP -> `solarscout:8080`) and starts everything. First ARM build takes
   about 10 minutes; all dependencies ship aarch64 wheels.
5. **Afterwards**: set up Cloudflare Access (step 4 of the main guide) and
   click the four example chips once to warm the imagery and LoD2 caches.

### Option B: Google Cloud Run (easiest deploy, free tier covers a demo)

Scale-to-zero containers; the always-free tier (180k vCPU-seconds/month) is
plenty for a review week. Card on file required, but a demo stays at $0.

```bash
gcloud init                       # one-time, pick/create a project
gcloud run deploy solarscout --source . --region europe-west3   --memory 4Gi --cpu 2 --timeout 600 --allow-unauthenticated   --max-instances 1
```

You get an https://solarscout-....run.app URL immediately; map
demo.th3cyberworld.* to it under Cloud Run "Domain mappings" (or keep the
run.app URL for the review). Caveats:
- Storage is ephemeral: the demo data re-seeds itself on boot (built into the
  image), but real leads and the LoD2 tile cache reset on cold starts. Fine
  for a demo, wrong for production.
- First request after idle cold-starts the 2.6 GB image (~15-30 s).
- Access control: either keep the unlisted URL, or front it with Cloudflare
  Access via your domain mapping.

### Honourable mention: Hugging Face Spaces

Free Docker hosting with 16 GB RAM and no card - push this repo with the
Dockerfile to a Space and it just runs. Downsides: public hf.space URL only
(no custom domain on free) and the Space sleeps when idle.


## Per-recipient branding

The demo personalizes itself from environment variables (set in
docker-compose.yml, already filled in for 1KOMMA5):

- PARTNER_NAME: greeting pill on the landing page and partner-portal persona
- PARTNER_CITY / PARTNER_SUBURB: the portal's default home region
- PARTNER_CHIP_ADDR / _DE / _EN: an extra example chip, e.g. the recipient's
  own building (the Astraturm comes back "obstructed": an honesty easter egg)

Re-brand for another company by changing the variables and
`docker compose up -d`.
