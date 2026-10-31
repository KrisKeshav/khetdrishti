#!/usr/bin/env bash
# One-time server preparation on a fresh Ubuntu VM (run as the default 'ubuntu' user with sudo).
# Installs Docker, opens ports 80/443 in the OS firewall (Oracle's Ubuntu images block them by default,
# in addition to the cloud Security List), and adds swap so builds don't run out of memory on small VMs.
set -euo pipefail

if ! command -v docker >/dev/null; then
  curl -fsSL https://get.docker.com | sudo sh
  sudo usermod -aG docker "$USER"
fi

# Oracle Ubuntu images ship iptables rules that REJECT everything except SSH.
for port in 80 443; do
  if ! sudo iptables -C INPUT -p tcp --dport "$port" -j ACCEPT 2>/dev/null; then
    sudo iptables -I INPUT 6 -m state --state NEW -p tcp --dport "$port" -j ACCEPT
  fi
done
sudo apt-get install -y -qq netfilter-persistent iptables-persistent >/dev/null 2>&1 || true
sudo netfilter-persistent save >/dev/null 2>&1 || true

# 2 GB swap: pip installing numpy/rasterio wheels on a 1 GB micro VM can otherwise be OOM-killed.
if ! swapon --show | grep -q /swapfile; then
  sudo fallocate -l 2G /swapfile && sudo chmod 600 /swapfile && sudo mkswap /swapfile >/dev/null && sudo swapon /swapfile
  echo '/swapfile none swap sw 0 0' | sudo tee -a /etc/fstab >/dev/null
fi

sudo mkdir -p /opt/khetdrishti && sudo chown "$USER" /opt/khetdrishti
echo "bootstrap done"
