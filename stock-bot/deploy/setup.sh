#!/bin/bash
##############################################################################
# Stock Bot VPS Setup Script
# Run this as root on a fresh DigitalOcean Ubuntu 24.04 LTS droplet
# Usage: sudo bash setup.sh
##############################################################################

set -e

echo "========================================"
echo "Stock Bot VPS Setup"
echo "========================================"

# 1. Set timezone to America/New_York (market timezone)
echo "[1/6] Setting timezone to America/New_York..."
timedatectl set-timezone America/New_York
timedatectl

# 2. Update system packages
echo "[2/6] Updating system packages..."
apt update
apt upgrade -y

# 3. Install Python and dependencies
echo "[3/6] Installing Python and system dependencies..."
apt install -y git python3 python3-venv python3-pip

# 4. Create non-root bot user
echo "[4/6] Creating botuser account..."
if id botuser &>/dev/null; then
    echo "    User 'botuser' already exists, skipping creation."
else
    adduser --disabled-password --gecos "Stock Bot Service" botuser
    usermod -aG sudo botuser
    echo "    Created botuser"
fi

# 5. Clone repo and install dependencies
echo "[5/6] Cloning repo and installing Python dependencies..."
if [ -d /home/botuser/stock-bot ]; then
    echo "    Directory /home/botuser/stock-bot already exists, pulling latest..."
    su - botuser -c "cd stock-bot && git pull"
else
    su - botuser -c "git clone https://github.com/jrrp3333/stock-bot.git"
    echo "    Cloned repo"
fi

su - botuser -c "cd stock-bot && python3 -m venv .venv && . .venv/bin/activate && pip install --upgrade pip wheel && pip install -r requirements.txt"
echo "    Installed Python dependencies"

# 6. Install systemd service files
echo "[6/6] Installing systemd services..."
cp /home/botuser/stock-bot/deploy/stock-bot.service /etc/systemd/system/
cp /home/botuser/stock-bot/deploy/stock-dashboard.service /etc/systemd/system/
systemctl daemon-reload
systemctl enable stock-bot
systemctl enable stock-dashboard
echo "    Services enabled (will start on reboot)"

echo ""
echo "========================================"
echo "Setup complete!"
echo "========================================"
echo ""
echo "Next steps:"
echo "1. Add your API keys to /home/botuser/stock-bot/.env"
echo "   Command: sudo nano /home/botuser/stock-bot/.env"
echo ""
echo "2. Verify .env contents:"
echo "   ALPACA_API_KEY=your_key"
echo "   ALPACA_SECRET_KEY=your_secret"
echo "   FINNHUB_API_KEY=your_finnhub_key"
echo "   PAPER_TRADING=true"
echo "   AUTO_START_MODE=autonomous"
echo "   AUTONOMOUS_INTERVAL_MINUTES=30"
echo "   TICKER_UNIVERSE=AAPL,MSFT,GOOGL,AMZN,NVDA,TSLA,JPM,WMT,UNH,XOM"
echo ""
echo "3. Start the services:"
echo "   sudo systemctl start stock-bot"
echo "   sudo systemctl start stock-dashboard"
echo ""
echo "4. Check status:"
echo "   sudo systemctl status stock-bot"
echo "   sudo systemctl status stock-dashboard"
echo ""
echo "5. View live logs:"
echo "   sudo journalctl -u stock-bot -f"
echo "   sudo journalctl -u stock-dashboard -f"
echo ""
echo "========================================"
