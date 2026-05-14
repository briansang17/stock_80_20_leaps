# Run the LEAPS Signal Scanner For Free in the Cloud

Three options ranked by ease, all genuinely free, all run 24/7 without your laptop being on.

| Option | Free Forever? | Setup Time | All 10 Strategies? | Best For |
|---|---|---|---|---|
| **🥇 GitHub Actions** | ✅ Yes (2,000 min/mo) | 15 min | ✅ Yes | Most people — recommended |
| **🥈 PythonAnywhere** | ✅ Yes (1 task/day) | 10 min | ✅ Yes | No GitHub needed |
| **🥉 Oracle Cloud Free Tier** | ✅ Yes (forever) | 60 min | ✅ Yes | Power users |

You'll need ~60 min/month of compute total. All three options give you way more than that.

---

## 🥇 OPTION 1 — GitHub Actions (RECOMMENDED)

GitHub Actions runs your Python on GitHub's servers, on a schedule, for free.

### Step 1. Create a Gmail App Password

1. Go to https://myaccount.google.com/apppasswords
   - Must have 2FA enabled on your Google account
2. App name: "LEAPS Signal"
3. Copy the **16-character password** (no spaces)

### Step 2. Push your project to GitHub

```bash
cd /Users/briansang/Desktop/stock_80_20_leaps

# Initialize git (if not already)
git init -b main

# Stage everything, commit
git add .
git commit -m "Initial commit: LEAPS top-10 signal scanner"

# Create a new GitHub repo at https://github.com/new
#   • Name: stock_80_20_leaps
#   • Visibility: PRIVATE  (free, no one else sees your code/data)
#   • Don't add README/license — you already have files

# Push (replace YOUR_USERNAME):
git remote add origin https://github.com/YOUR_USERNAME/stock_80_20_leaps.git
git push -u origin main
```

### Step 3. Add SMTP secrets to GitHub

In your repo on github.com:

**Settings → Secrets and variables → Actions → New repository secret**

Add these 5 secrets (each one separately):

| Secret name | Value |
|---|---|
| `SMTP_USER` | `your.email@gmail.com` |
| `SMTP_PASS` | the 16-character app password from Step 1 |
| `SMTP_TO` | `your.email@gmail.com` (where to send the alert) |
| `SMTP_HOST` | `smtp.gmail.com` |
| `SMTP_PORT` | `465` |

### Step 4. Test it manually

Go to repo on github.com:

**Actions → "Daily LEAPS Signal Check" → "Run workflow" → check "Force email" → Run**

It will run within a minute. You'll see a green checkmark when done. Open your email — you should have a test email.

### Step 5. That's it

The workflow auto-runs **Mon-Fri 4:30pm ET** going forward. You'll get an email **only when at least 1 of the 10 strategies fires**. Free forever.

### What you'll see in your GitHub repo

- **Actions tab** — shows every run (one per weekday)
- **Artifacts** — each run uploads `daily_top10_log.csv` so you have a historical record

### How to update the strategy

Just edit the code locally, then:
```bash
git add .
git commit -m "tweak signal logic"
git push
```
The next scheduled run will use the new code automatically.

---

## 🥈 OPTION 2 — PythonAnywhere (no Git required)

PythonAnywhere is a free Python-in-the-cloud service. Their free tier allows **1 scheduled task per day**, which is exactly what we need.

### Step 1. Create account

1. Sign up at https://www.pythonanywhere.com — free tier ("Beginner")

### Step 2. Upload your project

In the PythonAnywhere dashboard:
- **Files tab** → create folder `stock_80_20_leaps`
- Upload all `.py` files, `requirements.txt`, and the `data_cache/` folder
   - Easiest: ZIP your project locally, upload the zip, then `unzip` in their Bash console

### Step 3. Install dependencies

Open a **Bash console** in PythonAnywhere:
```bash
cd stock_80_20_leaps
pip3.11 install --user -r requirements.txt
```

### Step 4. Add SMTP config

Still in Bash console:
```bash
cat > ~/.leaps_signal_config.json <<EOF
{
  "smtp_user": "your.email@gmail.com",
  "smtp_pass": "YOUR_GMAIL_APP_PASSWORD",
  "smtp_to":   "your.email@gmail.com",
  "smtp_host": "smtp.gmail.com",
  "smtp_port": 465
}
EOF
chmod 600 ~/.leaps_signal_config.json
```

### Step 5. Test it

```bash
cd ~/stock_80_20_leaps
python3.11 daily_signal_top10.py --force
```

### Step 6. Schedule it daily

**Tasks tab** → schedule a daily task:
- Hour (UTC): **21**
- Minute: **30**
- Command:
```
cd /home/YOUR_USERNAME/stock_80_20_leaps && python3.11 daily_signal_top10.py
```

The free tier supports exactly 1 daily task, which matches our needs perfectly.

---

## 🥉 OPTION 3 — Oracle Cloud Free Tier (most power, most setup)

Oracle gives you **2 free ARM-based VMs forever** with 24GB RAM total. Overkill but it's a real always-on Linux server.

### Step 1. Create account at https://www.oracle.com/cloud/free/

(Requires credit card for verification, never charged — they have a real free tier.)

### Step 2. Launch a free ARM instance

- Image: Ubuntu 22.04
- Shape: VM.Standard.A1.Flex (free ARM)
- 1 CPU, 6 GB RAM (within free limits)
- Enable SSH with your public key

### Step 3. SSH in and set up

```bash
ssh ubuntu@YOUR_VM_IP

# Install Python and git
sudo apt update && sudo apt install -y python3.11 python3-pip python3-venv git

# Clone your repo (after pushing to GitHub) OR scp the folder over
git clone https://github.com/YOUR_USERNAME/stock_80_20_leaps.git
cd stock_80_20_leaps

# Set up venv
python3.11 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

# SMTP config
cat > ~/.leaps_signal_config.json <<EOF
{
  "smtp_user": "your.email@gmail.com",
  "smtp_pass": "YOUR_GMAIL_APP_PASSWORD",
  "smtp_to":   "your.email@gmail.com",
  "smtp_host": "smtp.gmail.com",
  "smtp_port": 465
}
EOF

# Test
python daily_signal_top10.py --force
```

### Step 4. Schedule via crontab

```bash
crontab -e

# Add this line:
30 21 * * 1-5 cd /home/ubuntu/stock_80_20_leaps && /home/ubuntu/stock_80_20_leaps/.venv/bin/python daily_signal_top10.py >> /home/ubuntu/leaps.log 2>&1
```

Cron uses UTC. `30 21` = 21:30 UTC = 4:30pm ET (DST) / 5:30pm ET (standard).

---

## Honest Comparison

| Aspect | GitHub Actions | PythonAnywhere | Oracle Cloud |
|---|---|---|---|
| Truly free forever? | ✅ | ✅ | ✅ |
| Easiest setup | 🟡 Need git | 🟢 No git | 🔴 Most setup |
| Always-on VM you can SSH into | ❌ | 🟡 Limited | ✅ Full |
| Strategy code is private | ✅ Private repo | ✅ Your account | ✅ Your VM |
| What happens if free tier changes? | Workflow continues until policy change | Same | Same |
| Can run other side projects | ✅ Different workflows | ❌ 1 task limit | ✅ Anything |

## My Recommendation: GitHub Actions

For your situation (LEAPS signal scanner, ~1-min runtime per day):

1. **GitHub Actions is the cleanest free solution**
2. You already have the code organized nicely
3. Git is good for version control of your strategy anyway
4. You get free history/audit trail of every signal in GitHub Actions logs
5. Trivial to update the code (just `git push`)

**If you don't want to learn git → PythonAnywhere** is the closest alternative.

---

## After You Set It Up

You can still run locally too:
```bash
python daily_signal_top10.py --force
```

Multiple monitoring layers (local + GitHub + Sheets) won't hurt — they all check the same conditions, just from different angles. **You'll never miss a signal**, no matter where your computer is.

## Troubleshooting

**GitHub Actions: "secret not found"**
- Make sure each secret name is EXACTLY `SMTP_USER`, `SMTP_PASS`, etc. (capital letters)
- Secrets are case-sensitive

**GitHub Actions: Yahoo Finance fails**
- Yahoo sometimes rate-limits cloud IPs. If you see download errors, add a retry or switch to an alternative data source like `stooq` or `pandas-datareader`
- Workaround: schedule the run during off-peak hours (early morning UTC)

**PythonAnywhere: cron task didn't run**
- Free tier only allows 1 task — make sure no other tasks are configured
- Check the task log in the Tasks tab

**Oracle Cloud: VM kicked off / region full**
- Try a different region during signup
- Oracle's free ARM instances are popular; sometimes you need to try several times

---

## Cost Summary

```
GitHub Actions:    $0/month, ~60 min/year (well under 24,000 min/year free)
PythonAnywhere:    $0/month (free Beginner tier)
Oracle Cloud:      $0/month (always-free VM, included forever)
Email (Gmail):     $0/month (using Google's SMTP)

Total infrastructure cost: $0/year ✅
```

The only "cost" is your one-time setup effort, which I'd estimate at 15-60 minutes depending on which option you pick.
