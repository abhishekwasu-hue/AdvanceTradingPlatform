#!/usr/bin/env bash
# Block 1 of 3 - host bootstrap (Hostinger KVM 2, Ubuntu 24.04). Run ONCE as root right after the first SSH login:
#
#   ssh root@<server-ip>
#   bash -c "$(echo <base64 from docs/DEPLOY_HOSTINGER_MR.md> | base64 -d)"      # or: bash bootstrap.sh
#
# Idempotent: running it again changes nothing that is already in place. It never touches the Trade repo droplet,
# never writes a secret, and never starts the platform (that is block 2, as the new user).
#
#   ATP_USER   the non-root user to create (default: atp)
#   SWAP_GB    swap size (default: 2 - an 8 GB host: a safety margin, not working memory)
#
# What it does: user + sudo (root's SSH key copied to it), SSH key-only (passwords and root login off - kept OPEN
# until the new user's key works), ufw (22, 80, 443 only), fail2ban (sshd), unattended security upgrades, Docker
# Engine + compose plugin, swap, timezone Asia/Kolkata, chrony (exchange timestamps and TOTP need a true clock).
set -euo pipefail

ATP_USER="${ATP_USER:-atp}"
SWAP_GB="${SWAP_GB:-2}"
log() { printf '\n==> %s\n' "$*"; }
[ "$(id -u)" = "0" ] || { echo "run as root (ssh root@<ip>)" >&2; exit 1; }
. /etc/os-release
[ "${ID:-}" = "ubuntu" ] || echo "warning: written for Ubuntu 24.04, found ${PRETTY_NAME:-unknown}" >&2

log "packages"
export DEBIAN_FRONTEND=noninteractive
apt-get update -q
apt-get install -yq ca-certificates curl gnupg git ufw fail2ban unattended-upgrades chrony jq rclone

log "time: Asia/Kolkata + chrony"
timedatectl set-timezone Asia/Kolkata
systemctl enable --now chrony
chronyc makestep >/dev/null 2>&1 || true

log "user $ATP_USER (sudo, root's SSH key)"
if ! id "$ATP_USER" >/dev/null 2>&1; then
  adduser --disabled-password --gecos "" "$ATP_USER"
fi
usermod -aG sudo "$ATP_USER"
install -d -m 700 -o "$ATP_USER" -g "$ATP_USER" "/home/$ATP_USER/.ssh"
if [ -s /root/.ssh/authorized_keys ]; then
  touch "/home/$ATP_USER/.ssh/authorized_keys"
  # append root's keys that the user does not have yet
  grep -vxF -f "/home/$ATP_USER/.ssh/authorized_keys" /root/.ssh/authorized_keys >> "/home/$ATP_USER/.ssh/authorized_keys" || true
  chown "$ATP_USER:$ATP_USER" "/home/$ATP_USER/.ssh/authorized_keys"; chmod 600 "/home/$ATP_USER/.ssh/authorized_keys"
fi
# sudo without a password only for this user (key-only SSH means it has no usable password)
echo "$ATP_USER ALL=(ALL) NOPASSWD:ALL" > "/etc/sudoers.d/90-$ATP_USER"; chmod 440 "/etc/sudoers.d/90-$ATP_USER"
visudo -cq

log "SSH: keys only"
cat > /etc/ssh/sshd_config.d/10-atp-hardening.conf <<'SSHD'
PasswordAuthentication no
KbdInteractiveAuthentication no
ChallengeResponseAuthentication no
PubkeyAuthentication yes
PermitEmptyPasswords no
X11Forwarding no
MaxAuthTries 4
LoginGraceTime 30
SSHD
if [ -s "/home/$ATP_USER/.ssh/authorized_keys" ]; then
  echo "PermitRootLogin no" >> /etc/ssh/sshd_config.d/10-atp-hardening.conf
else
  echo "warning: $ATP_USER has no SSH key yet - root login stays on (keys only) so you are not locked out" >&2
  echo "PermitRootLogin prohibit-password" >> /etc/ssh/sshd_config.d/10-atp-hardening.conf
fi
sshd -t
systemctl reload ssh 2>/dev/null || systemctl reload sshd

log "firewall: 22, 80, 443 only"
ufw default deny incoming
ufw default allow outgoing
ufw allow 22/tcp
ufw allow 80/tcp
ufw allow 443/tcp
ufw allow 443/udp           # HTTP/3 on the same port
ufw --force enable
# Docker publishes ports past ufw; the prod overlays publish only Caddy's 80/443 on the public interface
# (Postgres, Redis and the API bind 127.0.0.1) - checked again by deploy/hostinger/status.sh.

log "fail2ban (sshd)"
cat > /etc/fail2ban/jail.d/atp-sshd.local <<'F2B'
[sshd]
enabled = true
backend = systemd
maxretry = 5
findtime = 10m
bantime = 1h
F2B
systemctl enable --now fail2ban
systemctl restart fail2ban

log "unattended security upgrades"
cat > /etc/apt/apt.conf.d/20auto-upgrades <<'UU'
APT::Periodic::Update-Package-Lists "1";
APT::Periodic::Unattended-Upgrade "1";
APT::Periodic::AutocleanInterval "7";
UU
# no automatic reboot: a reboot during market hours would stop the worker - reboot by hand after 15:30 IST
sed -i 's|^//\?\s*Unattended-Upgrade::Automatic-Reboot .*|Unattended-Upgrade::Automatic-Reboot "false";|' /etc/apt/apt.conf.d/50unattended-upgrades || true
systemctl enable --now unattended-upgrades

log "Docker Engine + compose plugin"
if ! command -v docker >/dev/null 2>&1; then
  install -m 0755 -d /etc/apt/keyrings
  curl -fsSL https://download.docker.com/linux/ubuntu/gpg -o /etc/apt/keyrings/docker.asc
  chmod a+r /etc/apt/keyrings/docker.asc
  echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.asc] https://download.docker.com/linux/ubuntu ${VERSION_CODENAME} stable" \
    > /etc/apt/sources.list.d/docker.list
  apt-get update -q
  apt-get install -yq docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
fi
usermod -aG docker "$ATP_USER"
mkdir -p /etc/docker
[ -f /etc/docker/daemon.json ] || cat > /etc/docker/daemon.json <<'DOCKER'
{ "log-driver": "json-file", "log-opts": { "max-size": "20m", "max-file": "5" }, "live-restore": true }
DOCKER
systemctl enable --now docker
systemctl restart docker

log "swap ${SWAP_GB} GB"
if ! swapon --show | grep -q /swapfile; then
  fallocate -l "${SWAP_GB}G" /swapfile
  chmod 600 /swapfile
  mkswap /swapfile >/dev/null
  swapon /swapfile
  grep -q '^/swapfile ' /etc/fstab || echo '/swapfile none swap sw 0 0' >> /etc/fstab
fi
sysctl -q -w vm.swappiness=10
echo 'vm.swappiness=10' > /etc/sysctl.d/90-atp.conf

log "directories"
install -d -m 750 -o "$ATP_USER" -g "$ATP_USER" /opt/atp /var/backups/atp-offsite /var/backups/atp-restore-test

log "done"
docker --version; docker compose version
echo "timezone: $(timedatectl show -p Timezone --value)  swap: $(swapon --show=SIZE --noheadings | head -1)"
ufw status | head -12
echo
echo "NEXT: open a NEW terminal and check 'ssh $ATP_USER@<server-ip>' works BEFORE closing this one."
echo "Then run block 2 (deploy) as $ATP_USER."
