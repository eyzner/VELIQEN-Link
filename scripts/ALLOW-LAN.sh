#!/usr/bin/env bash
set -euo pipefail
if [[ "${1:-}" == --help || $# -ne 1 ]]; then
    printf '%s\n' 'Разрешить обмен только из своей локальной IPv4-сети:' 'bash scripts/ALLOW-LAN.sh 192.168.1.0/24' 'Используй фактическую подсеть роутера. Скрипт не включает и не отключает UFW.'
    exit 0
fi
SUBNET="$(python3 - "$1" <<'PY'
import ipaddress,sys
n=ipaddress.ip_network(sys.argv[1],strict=False)
private=[ipaddress.ip_network(x) for x in ('10.0.0.0/8','172.16.0.0/12','192.168.0.0/16')]
if n.version!=4 or not any(n.subnet_of(p) for p in private):
    raise SystemExit('Нужна локальная подсеть RFC1918, например 192.168.1.0/24.')
print(n)
PY
)"
command -v ufw >/dev/null || { printf '%s\n' 'UFW не установлен. Настрой действующий межсетевой экран вручную.'; exit 1; }
sudo ufw allow from "$SUBNET" to any port 1714:1764 proto tcp comment 'VELIQEN Link LAN'
sudo ufw allow from "$SUBNET" to any port 1714:1764 proto udp comment 'VELIQEN Link LAN'
sudo ufw allow from "$SUBNET" to any port 38475 proto tcp comment 'VELIQEN Link Screen'
