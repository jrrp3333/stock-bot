# DigitalOcean Deployment Guide — Stock Bot

This guide walks you through deploying the autonomous trading bot to a DigitalOcean Ubuntu VPS. The bot will run 24/7, scan trades every 30 minutes during market hours, and reweight weekly.

---

## Prerequisites

- GitHub account with the repo pushed (✓ you already did this)
- $200 DigitalOcean credit from GitHub Education Pack (or just $4/month to pay)
- SSH key added to your DigitalOcean account
- Real Alpaca API keys + Finnhub key

---

## Step 1: Create the DigitalOcean Droplet

1. **Log in to DigitalOcean** → Droplets → Create Droplet
2. **Choose Region**: Select nearest to you (US East is fine for EST market hours)
3. **Operating System**: Ubuntu 24.04 LTS x64
4. **Droplet Size**: **Basic** → **Regular Intel** → **1 GB RAM / 1 vCPU** ($4/month)
5. **Settings**:
   - Leave VPC Network at default
   - Add your SSH public key (you should have this from GitHub)
   - Hostname: `stock-bot` (or whatever you like)
6. **Firewall** (if shown):
   - Allow SSH (port 22) — required
   - Allow HTTP (port 80) — optional (we'll skip dashboard public access for now)
7. Click **Create Droplet**

Wait 30–60 seconds for it to boot. You'll see an IP address on the dashboard:

```
YOUR_DROPLET_IP = 123.45.67.89  (example)
```

Save this IP—you'll use it for SSH.

---

## Step 2: SSH into the Droplet

From your laptop, open a terminal (PowerShell, CMD, or Bash) and run:

```bash
ssh root@YOUR_DROPLET_IP
```

Example:
```bash
ssh root@123.45.67.89
```

You should be logged in as `root` with no password prompt (SSH key auth).

---

## Step 3: Run the Automated Setup Script

On the droplet, clone the repo and run the setup script:

```bash
git clone https://github.com/jrrp3333/stock-bot.git
cd stock-bot
sudo bash deploy/setup.sh
```

This script will automatically:
- Set the timezone to America/New_York 🕐
- Update system packages
- Install Python 3, venv, pip
- Create a `botuser` account for the bot to run under
- Clone the repo under `/home/botuser/stock-bot`
- Create a Python virtual environment and install all dependencies
- Copy systemd service files
- Enable both services (so they auto-start on reboot)

When it finishes, you'll see:

```
========================================
Setup complete!
========================================

Next steps:
1. Add your API keys to /home/botuser/stock-bot/.env
   Command: sudo nano /home/botuser/stock-bot/.env
...
```

---

## Step 4: Add Your API Keys

Still on the droplet, create the `.env` file with your real keys:

```bash
sudo nano /home/botuser/stock-bot/.env
```

Paste the following template and **replace with your real values**:

```
ALPACA_API_KEY=YOUR_REAL_ALPACA_KEY
ALPACA_SECRET_KEY=YOUR_REAL_ALPACA_SECRET
FINNHUB_API_KEY=YOUR_REAL_FINNHUB_KEY
PAPER_TRADING=true
AUTO_START_MODE=autonomous
AUTONOMOUS_INTERVAL_MINUTES=30
TICKER_UNIVERSE=AAPL,MSFT,GOOGL,AMZN,NVDA,TSLA,JPM,WMT,UNH,XOM
```

**Important:**
- Keep `PAPER_TRADING=true` until you've watched it successfully for a few days.
- `TICKER_UNIVERSE` must be 5–20 symbols. The example has 10, which is safe.
- Do **not** leave placeholder values like `PASTE_YOUR_...` or `changeme`.

Save with `Ctrl+O` → Enter → `Ctrl+X`.

---

## Step 5: Start the Services

Still on the droplet:

```bash
sudo systemctl start stock-bot
sudo systemctl start stock-dashboard
```

Verify both are running:

```bash
sudo systemctl status stock-bot
sudo systemctl status stock-dashboard
```

You should see `Active: active (running)` for both.

---

## Step 6: Check the Logs

Watch the bot's first run in real time:

```bash
sudo journalctl -u stock-bot -f
```

You should see output like:

```
🚀 TRADING BOT WITH OPTIMIZATION AGENTS
...
🤖 Autonomous mode enabled: scanning every 30 minutes
🔍 Scanning 10 tickers for politician activity...
```

Press `Ctrl+C` to exit the logs (the bot keeps running).

Similarly, check the dashboard logs:

```bash
sudo journalctl -u stock-dashboard -f
```

---

## Step 7: Access the Dashboard

The dashboard binds to `localhost:5000` only (secure by default). To access it from your laptop, create an SSH tunnel:

**From your laptop**, open a new terminal and run:

```bash
ssh -L 5000:127.0.0.1:5000 root@YOUR_DROPLET_IP
```

Example:
```bash
ssh -L 5000:127.0.0.1:5000 root@123.45.67.89
```

Then open this in your browser:

```
http://127.0.0.1:5000
```

Leave the SSH tunnel open in a terminal while you browse the dashboard. When done, close the terminal with `Ctrl+C`.

---

## Step 8: Reboot Test

The final proof that it's autonomous—reboot the droplet:

```bash
sudo reboot
```

Wait 30 seconds, then SSH back in:

```bash
ssh root@YOUR_DROPLET_IP
```

Check both services:

```bash
sudo systemctl status stock-bot
sudo systemctl status stock-dashboard
```

Both should be `active (running)`. If they are, your bot is now running 24/7 without any interaction.

---

## Ongoing Operations

### View Live Logs

To monitor the bot in real time:

```bash
sudo journalctl -u stock-bot -f
```

### Check Service Health

```bash
sudo systemctl status stock-bot
sudo systemctl status stock-dashboard
```

### Restart a Service

If you change `.env`, restart the bot:

```bash
sudo systemctl restart stock-bot
sudo systemctl restart stock-dashboard
```

### Stop the Bot (if needed)

```bash
sudo systemctl stop stock-bot
```

### Update the Code

Pull the latest code from GitHub and restart:

```bash
cd /home/botuser/stock-bot
sudo -u botuser git pull
sudo systemctl restart stock-bot stock-dashboard
```

---

## Troubleshooting

### "Permission denied" when editing `.env`

Use `sudo`:
```bash
sudo nano /home/botuser/stock-bot/.env
```

### Bot won't start / status shows "dead"

Check the logs:
```bash
sudo journalctl -u stock-bot -n 50 --no-pager
```

Common issues:
- Missing or placeholder API keys in `.env`
- `TICKER_UNIVERSE` has > 20 symbols (violates validation)
- Connectivity to Alpaca or Finnhub failed

### Dashboard not accessible

Make sure the SSH tunnel is open:
```bash
ssh -L 5000:127.0.0.1:5000 root@YOUR_DROPLET_IP
```

Then try `http://127.0.0.1:5000` in your browser.

### Services keep restarting

This is the `Restart=always` policy in the systemd file. It means if the bot crashes, it auto-restarts after 10 seconds. Check logs to see why it's crashing:

```bash
sudo journalctl -u stock-bot -f
```

---

## Architecture Summary

After deployment, here's what's running:

```
DigitalOcean Droplet (Ubuntu 24.04 LTS)
  ├─ stock-bot.service (systemd)
  │   └─ bot.py (autonomous mode, scans every 30 min)
  │
  └─ stock-dashboard.service (systemd)
      └─ dashboard.py (Flask, localhost:5000)
```

Both services:
- Auto-start on reboot
- Auto-restart if they crash
- Log to systemd journal (visible with `journalctl`)
- Run as `botuser` (non-root, safer)

---

## Next Steps

1. ✅ Deploy and verify both services are running
2. Watch logs for 24–48 hours to catch any configuration issues
3. Keep `PAPER_TRADING=true` for now to validate bot behavior
4. After a week of successful autonomous runs, consider switching to small real money trades if desired
5. Set a calendar reminder to review performance weekly

---

## Support

If the bot runs into issues:
1. Check systemd logs: `sudo journalctl -u stock-bot -n 100 --no-pager`
2. Verify `.env` has real values (no placeholders)
3. Verify TICKER_UNIVERSE is 5–20 symbols
4. Check Alpaca + Finnhub connectivity from the VPS (they can timeout or go down)

---

Good luck! Your bot is now fully autonomous on the cloud. 🚀
