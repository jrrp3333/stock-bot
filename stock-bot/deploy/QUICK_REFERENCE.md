# Quick Reference — Common VPS Commands

Copy-paste these commands into your DigitalOcean SSH session.

## Initial Setup (One-Time)

```bash
# Add API keys to .env
sudo nano /home/botuser/stock-bot/.env

# Start the services
sudo systemctl start stock-bot
sudo systemctl start stock-dashboard

# Verify they're running
sudo systemctl status stock-bot
sudo systemctl status stock-dashboard
```

## Daily Operations

```bash
# View bot logs (live, Ctrl+C to exit)
sudo journalctl -u stock-bot -f

# View dashboard logs
sudo journalctl -u stock-dashboard -f

# View last 50 lines of bot logs
sudo journalctl -u stock-bot -n 50 --no-pager

# Check if services are healthy
sudo systemctl status stock-bot
sudo systemctl status stock-dashboard
```

## Maintenance

```bash
# Restart both services (if you changed .env or code)
sudo systemctl restart stock-bot
sudo systemctl restart stock-dashboard

# Stop the bot (to prevent trades)
sudo systemctl stop stock-bot

# Stop the dashboard
sudo systemctl stop stock-dashboard

# Disable auto-start on reboot (requires manual systemctl start each time)
sudo systemctl disable stock-bot

# Re-enable auto-start
sudo systemctl enable stock-bot
```

## Updates

```bash
# Pull latest code from GitHub
cd /home/botuser/stock-bot
sudo -u botuser git pull

# Reinstall dependencies (if requirements.txt changed)
sudo -u botuser bash -c '. .venv/bin/activate && pip install -r requirements.txt'

# Restart to apply changes
sudo systemctl restart stock-bot stock-dashboard
```

## Debugging

```bash
# Check if bot has connectivity to Alpaca
sudo -u botuser bash -c '. .venv/bin/activate && python -c "from alpaca.trading.client import TradingClient; import os; from dotenv import load_dotenv; load_dotenv(); TradingClient(os.getenv(\"ALPACA_API_KEY\"), os.getenv(\"ALPACA_SECRET_KEY\"), paper=True).get_account(); print(\"✅ Alpaca connected\")"'

# Check timezone (should be America/New_York)
timedatectl

# Check disk space
df -h

# Check memory usage
free -h

# Check running Python processes
ps aux | grep python
```

## Dashboard Access (from laptop)

```bash
# Open SSH tunnel in a terminal, leave it open
ssh -L 5000:127.0.0.1:5000 root@YOUR_DROPLET_IP

# Then open in browser
http://127.0.0.1:5000
```

---

**Tip:** Save these commands in a text file on your laptop for quick copy-paste.
