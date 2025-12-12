# ESXi VM Launcher

Web-App zur Verwaltung von VMs auf einem ESXi Server.

## Features

- VMs starten/herunterfahren
- VMRC Console öffnen
- VM-Details (CPU, RAM, IP, Status)

## Docker

```bash
docker-compose up -d
```

Läuft auf: `http://localhost:5000`

## Portainer

1. Stacks → Add stack
2. `docker-compose.yml` einfügen
3. Deploy

## Manuelle Installation

```bash
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
python app.py
```

## Voraussetzungen

- ESXi 6.5+
- VMRC auf Client
- VMware Tools in VMs

## Lizenz

MIT
```