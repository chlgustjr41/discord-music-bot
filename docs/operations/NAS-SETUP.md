# Installing Jacky Music on the home NAS

Instructions for the agent working on the device (UGREEN DXP2800, UGOS Pro,
Docker on SSD). The **operator** is the human who owns the accounts and the
network. Work through the sections in order. Each ends with a check; do not go
on until it passes.

## Rules for the agent

- **Never display a secret.** Do not `cat` or `grep` values out of
  `deploy/.env` or the service-account file, and do not echo them into the
  conversation or a log. Check presence by key name only (see section 3).
- **Never publish a port and never touch the router.** This stack needs no
  inbound connection of any kind. If a step seems to need a port mapping, a
  port forward or a DNS record, stop and ask the operator.
- **One bot at a time.** Do not start the `bot` service until the operator
  confirms the old host is stopped (section 6).
- **Stop and ask** if a check fails twice, if Docker is not using the
  iptables firewall backend, or if anything here does not match what you find
  on the device. Do not improvise around a failed security check.
- Commands assume a shell in the repository root, with:
  ```sh
  C="docker compose -f deploy/docker-compose.yml --env-file deploy/.env"
  ```
  Prefix `docker` with `sudo` if your user is not allowed to run it.

## 1. What you are installing

Eight containers, described in `docs/architecture/ARCHITECTURE.md`. The bot
talks to Discord, the audio server (Lavalink) streams from YouTube, three
helpers keep YouTube access working, guardian watches and restarts, a small
proxy limits what guardian may ask Docker to do, and `cloudflared` carries the
Stream Deck control API out through a Cloudflare tunnel.

Every connection is **outbound**. State lives in Firestore, in the cloud, so
there is nothing to migrate onto the device except configuration.

## 2. Check the device

```sh
uname -m                       # expect x86_64
docker version --format '{{.Server.Version}}'
docker compose version
docker info --format '{{.DockerRootDir}}'   # must be on the SSD volume
free -m | sed -n 2p            # expect roughly 8000 total
timedatectl show -p NTPSynchronized --value   # expect yes
ip -4 route get 1.1.1.1 | head -1             # note the interface: must be wired
docker network ls --format '{{.Name}} {{.Driver}}' ; ip -4 route | grep 172.28 || echo "172.28.0.0/16 is free"
```

Ask the operator to confirm that the Docker data directory is on an SSD and
that the device is connected by cable. If `172.28.0.0/16` is already routed
or used by another Docker network, stop: the compose file and the firewall
script both assume that range.

The containers use public DNS resolvers, because the firewall in section 4
blocks them from the LAN, which is where the router's resolver lives. Check
that this network lets such lookups out:

```sh
for r in 1.1.1.1 8.8.8.8 9.9.9.9; do
  docker run --rm alpine nslookup github.com "$r" >/dev/null 2>&1     && echo "reachable  $r" || echo "BLOCKED    $r"
done
```

- `1.1.1.1` reachable: nothing to do.
- Only another one reachable: after creating `deploy/.env` in section 3, set
  `JACKY_DNS_1` and `JACKY_DNS_2` to it.
- None reachable: set both to the router's address **and** allow that address
  through the firewall by creating `/etc/default/jacky-egress-firewall`
  containing `JACKY_DNS_ALLOW="<router address>"` before section 4. Tell the
  operator; this opens port 53 to the router and nothing else.

## 3. Get the code and create the configuration

Clone into a directory that is **not** a shared folder (no SMB, NFS or cloud
sync) and that only the admin user can read. Ask the operator for the path.

```sh
git clone https://github.com/chlgustjr41/discord-music-bot.git jacky-music
cd jacky-music
test -f deploy/nas/egress-firewall.sh && grep -q 'socket-proxy' deploy/docker-compose.yml \
  && echo "hardened release present" || echo "WRONG RELEASE - stop"
chmod 700 .
cp deploy/.env.example deploy/.env && chmod 600 deploy/.env
```

Fill `deploy/.env`. Values you can set yourself, without printing them:

```sh
set_env() { sed -i "s|^$1=.*|$1=$2|" deploy/.env; }
set_env LAVALINK_PASSWORD "$(openssl rand -hex 16)"
set_env YOUTUBE_REMOTE_CIPHER_PASSWORD "$(openssl rand -hex 16)"
set_env DOCKER_GID "$(stat -c %g /var/run/docker.sock)"
```

Values the operator must provide. Ask them to edit the file themselves, or to
paste each value into an editor you open for them:

| Variable | Notes |
|----------|-------|
| `DISCORD_TOKEN` | Leave the placeholder until section 6. The operator resets the token at cutover. |
| `ALERT_WEBHOOK_URL` | Discord webhook for guardian's alerts. |
| `WEB_APP_URL` | Public address of the dashboard. |
| `DISCORD_CLIENT_ID`, `DISCORD_CLIENT_SECRET` | Discord OAuth app, for Stream Deck sign-in. |
| `CLOUDFLARE_TUNNEL_TOKEN` | Connector token of the existing tunnel. Also uncomment `COMPOSE_PROFILES=control`. |
| `YOUTUBE_OAUTH_REFRESH_TOKEN` | Optional. If left empty, run `scripts/reauth-v2.sh` after the stack is up. |
| `OPENAI_API_KEY` | Optional. Without it the voice-command key reports "unavailable". |
| Firebase key file | A JSON key for a service account that has **only** the `Cloud Datastore User` role. Save it as `deploy/firebase-service-account.json`. |

Leave `YOUTUBE_PLUGIN_VERSION`, `YOUTUBE_PLUGIN_SHA256`, `LAVALINK_HEAP`,
`POT_REFRESH_HOURS` and the two `LAVALINK_*_MS` audio settings at their
example values unless the operator says otherwise.

The bot runs as user id 10001 inside its container and must be able to read
the key file:

```sh
sudo chown 10001:10001 deploy/firebase-service-account.json
sudo chmod 400 deploy/firebase-service-account.json
```

**Check** (prints key names only, never values):

```sh
for k in DISCORD_TOKEN ALERT_WEBHOOK_URL WEB_APP_URL LAVALINK_PASSWORD \
         YOUTUBE_REMOTE_CIPHER_PASSWORD YOUTUBE_PLUGIN_VERSION DOCKER_GID; do
  v=$(sed -n "s/^$k=//p" deploy/.env)
  [ -n "$v" ] && [ "$v" != changeme ] && echo "ok       $k" || echo "MISSING  $k"
done
$C config -q && echo "compose file valid"
$C config | grep -E '^\s+(published|ports):' && echo "PORTS PUBLISHED - stop" || echo "no published ports"
```

`DISCORD_TOKEN` may still say MISSING at this point. Everything else must be ok.

## 4. Network configuration

**On the router (operator):** forward no ports, turn UPnP off, and give the
NAS a reserved address. Nothing else is required.

**On the NAS:** install the rule that keeps these containers away from the
home network. They may reach the internet and each other, and nothing on a
private address. Read the header of `deploy/nas/egress-firewall.sh` for the
reasoning.

```sh
REPO="$(pwd)"
sudo sh deploy/nas/egress-firewall.sh apply
sudo sed "s|__REPO_DIR__|$REPO|" deploy/nas/jacky-egress-firewall.service \
  | sudo tee /etc/systemd/system/jacky-egress-firewall.service >/dev/null
sudo cp deploy/nas/jacky-egress-firewall.timer /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now jacky-egress-firewall.service jacky-egress-firewall.timer
```

If this firmware has no `systemctl`, or does not keep files in
`/etc/systemd/system` across updates, use the NAS's own scheduled-task feature
to run `sh <repo>/deploy/nas/egress-firewall.sh apply` as root at boot and
every ten minutes, and tell the operator which mechanism you used.

**Check:**

```sh
sudo sh deploy/nas/egress-firewall.sh status      # "in place", exit code 0
systemctl is-enabled jacky-egress-firewall.timer   # enabled
```

The rule is tested against live containers in section 5.

## 5. Rehearsal without Discord

The audio half needs no Discord token, so it can be proven while the old host
is still serving.

```sh
$C up -d --build lavalink cipher pot-provider token-minter
$C ps        # wait until lavalink and pot-provider show (healthy)
```

**Checks.** Set `ROUTER` to the default gateway shown by `ip -4 route`.

```sh
ROUTER=192.168.1.1
# 1. Only the intended sources are enabled: expect youtube and soundcloud, and no "http".
$C exec -T lavalink sh -c 'curl -s -H "Authorization: $LAVALINK_SERVER_PASSWORD" http://localhost:2333/v4/info' \
  | grep -o '"sourceManagers":\[[^]]*\]'
# 2. YouTube answers from this address: expect "loadType":"search".
$C exec -T lavalink sh -c 'curl -s -H "Authorization: $LAVALINK_SERVER_PASSWORD" "http://localhost:2333/v4/loadtracks?identifier=ytsearch:lofi"' \
  | grep -o '"loadType":"[a-z]*"'
# 3. A fresh poToken was minted and pushed.
$C logs token-minter | grep -c "minted and pushed"      # 1 or more, within about a minute
# 4. The firewall holds: the router must be unreachable, the internet reachable.
$C exec -T lavalink sh -c "curl -s -o /dev/null -m 5 -w '%{http_code}\n' http://$ROUTER/"                     # expect 000
$C exec -T lavalink sh -c "curl -s -o /dev/null -m 10 -w '%{http_code}\n' https://www.youtube.com/generate_204" # expect 204
```

If check 4 shows anything other than `000` for the router, the containers can
reach the LAN: stop and fix section 4 before going further. If check 2 fails
while check 4's second line passes, YouTube is refusing this address or the
OAuth token; tell the operator and consult `docs/operations/RUNBOOK.md`
(playbooks F1 to F3).

## 6. Go live

Do this with the operator, at a moment when nobody is listening. Only one bot
may be connected with the bot token at a time.

1. **Operator:** stop the stack on the old host and confirm it is down.
2. **Operator:** reset the bot token in the Discord Developer Portal and put
   the new value in `DISCORD_TOKEN` in `deploy/.env`.
3. **Agent:** `$C up -d --build`

## 7. Verify

```sh
$C ps                                           # every service Up; lavalink and pot-provider (healthy)
docker ps --format '{{.Names}} {{.Ports}}' | grep -E '0\.0\.0\.0|\[::\]' || echo "no published ports"
$C logs --tail 50 bot | grep -ciE "error|traceback" # expect 0
$C exec -T bot python -c "import json,urllib.request as u; print(json.load(u.urlopen('http://localhost:8080/health'))['status'])"
# guardian may list and restart containers, and nothing else:
$C exec -T guardian python -c "
import urllib.request as u, urllib.error as e
def code(method, path):
    try: return u.urlopen(u.Request('http://socket-proxy:2375' + path, method=method), timeout=5).status
    except e.HTTPError as x: return x.code
    except Exception as x: return type(x).__name__
print('list   ', code('GET',  '/containers/json'))      # expect 200
print('create ', code('POST', '/containers/create'))    # expect 403
print('images ', code('GET',  '/images/json'))          # expect 403
"
sudo sh deploy/nas/egress-firewall.sh status
```

Then ask the operator to confirm, from Discord and from outside the home
network:

- the bot shows online, and `j!play <song>` plays a track to the end;
- the web dashboard shows the session;
- the Stream Deck key works (signing in again is expected);
- `https://control.<domain>/health` returns **404** and
  `https://control.<domain>/control/now-playing` returns **401**.

## 8. Keeping it healthy

- **Updates:** `git pull && $C up -d --build`. Re-run section 7 afterwards.
- **After any NAS firmware or Docker update:** run
  `sudo sh deploy/nas/egress-firewall.sh status`. If it reports missing,
  re-apply and re-check the systemd units (section 4).
- **Logs:** `$C logs -f --tail=100 <service>`. They are size-limited.
- **Audio quality:** the bot's `/health` and `j!status` report the audio
  server's frame statistics; the bot logs a warning when frames are lost.
- **Outages the device cannot report:** guardian cannot alert when the NAS or
  the internet is down. Ask the operator to set up an outside heartbeat
  monitor. Do not publish guardian's status port to satisfy one.
- **Backups:** `deploy/.env` and the service-account file belong in the
  operator's password manager, not in a shared or cloud-synced folder.

## 9. When something fails

| Symptom | Likely cause | Fix |
|---------|--------------|-----|
| bot exits with a permission error on the service-account file | File not readable by uid 10001 | Repeat the `chown`/`chmod` in section 3 |
| `socket-proxy` restarts, or guardian logs Docker errors | `DOCKER_GID` does not match the socket's group | `stat -c %g /var/run/docker.sock`, fix `.env`, `$C up -d socket-proxy guardian` |
| Containers cannot resolve names | Public resolvers are blocked on this network, or a LAN resolver is set without a firewall allowance | Repeat the DNS check in section 2 |
| token-minter cannot write `tokens.env` | A tokens volume created by an older, root-run release | `$C down`, `docker volume rm jacky-music_tokens`, `$C up -d` |
| lavalink never becomes healthy | Plugin download or checksum failure | `$C logs lavalink`; see RUNBOOK F4a |
| Tracks fail to load or stop midway | YouTube-side breakage | RUNBOOK F1 to F3 |
| Compose refuses to start with "set in .env" | A required variable is empty | Section 3 check |
