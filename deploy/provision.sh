#!/usr/bin/env bash
# One-time server preparation for Amora.
#
# Run ON THE SERVER as a user with sudo, on a fresh Debian or Ubuntu host:
#
#   curl -fsSL https://raw.githubusercontent.com/Prakhar924/amora/main/deploy/provision.sh | bash
#   # or, having cloned the repo:  sudo ./deploy/provision.sh
#
# Installs Docker, creates the application directory, and configures a
# firewall that exposes only SSH and HTTPS. It does NOT start the application
# and does NOT create any secrets — deploy/deploy.sh does the first, and you
# do the second.
set -euo pipefail

APP_DIR="${APP_DIR:-/opt/amora}"
APP_USER="${SUDO_USER:-${USER}}"

log() { printf '\n\033[1;35m==>\033[0m %s\n' "$*"; }

if [ "$(id -u)" -ne 0 ]; then
	echo "This script needs root. Re-run with: sudo $0" >&2
	exit 1
fi

log "Updating packages"
export DEBIAN_FRONTEND=noninteractive
apt-get update -qq
apt-get install -y -qq ca-certificates curl gnupg rsync ufw unattended-upgrades

# --- Docker ----------------------------------------------------------------
if ! command -v docker >/dev/null 2>&1; then
	log "Installing Docker Engine"
	install -m 0755 -d /etc/apt/keyrings
	curl -fsSL https://download.docker.com/linux/debian/gpg \
		| gpg --dearmor -o /etc/apt/keyrings/docker.gpg
	chmod a+r /etc/apt/keyrings/docker.gpg
	# shellcheck disable=SC1091
	. /etc/os-release
	echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.gpg] \
https://download.docker.com/linux/${ID} ${VERSION_CODENAME} stable" \
		> /etc/apt/sources.list.d/docker.list
	apt-get update -qq
	apt-get install -y -qq docker-ce docker-ce-cli containerd.io \
		docker-buildx-plugin docker-compose-plugin
else
	log "Docker already installed: $(docker --version)"
fi

systemctl enable --now docker

# --- Application directory -------------------------------------------------
log "Preparing $APP_DIR"
mkdir -p "$APP_DIR"
chown -R "$APP_USER":"$APP_USER" "$APP_DIR"
usermod -aG docker "$APP_USER" || true

# --- Firewall --------------------------------------------------------------
# Postgres and Redis publish no host ports, but a firewall is defence in
# depth: a future compose edit that accidentally exposes 5432 should not
# instantly put the database on the public internet.
log "Configuring the firewall (SSH, HTTP, HTTPS only)"
ufw --force reset >/dev/null
ufw default deny incoming >/dev/null
ufw default allow outgoing >/dev/null
ufw allow OpenSSH >/dev/null
ufw allow 80/tcp  >/dev/null   # required for Let's Encrypt HTTP-01
ufw allow 443/tcp >/dev/null
ufw allow 443/udp >/dev/null   # HTTP/3
ufw --force enable >/dev/null
ufw status verbose

# --- Automatic security updates -------------------------------------------
log "Enabling unattended security upgrades"
dpkg-reconfigure -f noninteractive unattended-upgrades || true

# --- Swap ------------------------------------------------------------------
# A 2GB VPS running Postgres, Redis and two uvicorn workers benefits from a
# little swap; without it the OOM killer tends to pick Postgres.
if [ ! -f /swapfile ] && [ "$(free -m | awk '/^Mem:/{print $2}')" -lt 4096 ]; then
	log "Creating a 2GB swapfile"
	fallocate -l 2G /swapfile
	chmod 600 /swapfile
	mkswap /swapfile >/dev/null
	swapon /swapfile
	grep -q '^/swapfile' /etc/fstab || echo '/swapfile none swap sw 0 0' >> /etc/fstab
fi

log "Server ready."
cat <<EOF

Next steps, from your workstation:

  1. Point your API domain's DNS A record at this server's IP address.
  2. Generate and fill in the configuration:
       python deploy/generate-secrets.py -o .env.production
       \$EDITOR .env.production
       python deploy/check-config.py .env.production
  3. Copy it to the server, once:
       scp .env.production $APP_USER@<this-host>:$APP_DIR/
  4. Deploy:
       ./deploy/deploy.sh $APP_USER@<this-host>

Note: you may need to log out and back in for docker group membership
to take effect for $APP_USER.
EOF
