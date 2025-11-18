# Deployment on Ubuntu

## Quick Setup with systemd

1. **Install Python and dependencies:**
```bash
sudo apt update
sudo apt install python3 python3-pip python3-venv
```

2. **Clone and setup the service:**
```bash
cd /opt
sudo git clone <your-repo-url> sms-gateway
cd sms-gateway
sudo python3 -m venv venv
sudo venv/bin/pip install -r requirements.txt
```

3. **Configure environment:**
```bash
sudo cp .env.example .env
sudo nano .env  # Edit with your settings
```

4. **Create systemd service:**
```bash
sudo nano /etc/systemd/system/sms-gateway.service
```

Paste this configuration:
```ini
[Unit]
Description=TG100 SMS Gateway Service
After=network.target

[Service]
Type=simple
User=root
WorkingDirectory=/opt/sms-gateway
ExecStart=/opt/sms-gateway/venv/bin/python3 /opt/sms-gateway/main.py
Restart=always
RestartSec=10
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
```

5. **Enable and start the service:**
```bash
sudo systemctl daemon-reload
sudo systemctl enable sms-gateway
sudo systemctl start sms-gateway
```

## Service Management

Check status:
```bash
sudo systemctl status sms-gateway
```

View logs:
```bash
sudo journalctl -u sms-gateway -f
```

Restart service:
```bash
sudo systemctl restart sms-gateway
```

Stop service:
```bash
sudo systemctl stop sms-gateway
```

## Notes

- Service auto-starts on boot
- Auto-restarts on failure (10 second delay)
- Logs available via `journalctl` and `sms_gateway.log` file
- Update code: `git pull`, then `sudo systemctl restart sms-gateway`
