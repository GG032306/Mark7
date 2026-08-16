#!/bin/bash
# --- MARK 7 DAILY FORWARD TESTING LAUNCHER ---
PROJECT_DIR="/home/gauravgupta2303/nifty_upstox_bot"
DATE_STR=$(date +"%Y-%m-%d")
LOG_FILE="$PROJECT_DIR/logs/mark7_$DATE_STR.log"

cd $PROJECT_DIR

echo "[$(date)] ==========================================" >> $LOG_FILE
echo "[$(date)] 🚀 INITIATING DAILY MARKET SEQUENCE" >> $LOG_FILE

# 1. Kill any existing zombie processes
pkill -f mark7_live.py

# 2. Generate a fresh daily Upstox token
echo "[$(date)] 🔑 Fetching fresh daily authentication token..." >> $LOG_FILE
python3 auto_login.py >> $LOG_FILE 2>&1

if [ ! -f "access_token.txt" ]; then
    echo "[$(date)] ❌ CRITICAL: access_token.txt not found. Aborting sequence." >> $LOG_FILE
    exit 1
fi

# 3. Launch Mark 7 Live Engine
echo "[$(date)] 📡 Booting Mark 7 Live Engine..." >> $LOG_FILE
python3 -u mark7_live.py >> $LOG_FILE 2>&1 &

echo "[$(date)] ✅ Mark 7 running autonomously in background with PID $!" >> $LOG_FILE
