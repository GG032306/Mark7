import pandas as pd
import numpy as np
from collections import deque
from datetime import datetime

class Mark7Brain:
    def __init__(self, max_length=50):
        # The rolling memory bank: Strictly holds the last 50 candles
        self.max_length = max_length
        self.candles = deque(maxlen=max_length)
        
        # Opening Range Breakout (ORB) tracking variables
        self.orb_high = -np.inf
        self.orb_low = np.inf
        self.orb_formed = False

    def ingest_candle(self, timestamp, open_p, high, low, close, volume):
        """Ingests a completed 1-minute candle and updates the ORB logic."""
        
        # 1. Handle the Opening Range (09:15 to 09:20 IST)
        if timestamp.hour == 9 and 15 <= timestamp.minute < 20:
            self.orb_high = max(self.orb_high, high)
            self.orb_low = min(self.orb_low, low)
        elif timestamp.hour == 9 and timestamp.minute >= 20:
            self.orb_formed = True

        # Reset ORB completely if it's a brand new day
        if timestamp.hour == 9 and timestamp.minute == 15:
            self.orb_high = high
            self.orb_low = low
            self.orb_formed = False

        # 2. Append the new candle to the rolling memory
        self.candles.append({
            'timestamp': timestamp,
            'open': open_p,
            'high': high,
            'low': low,
            'close': close,
            'volume': volume,
            'typical_price': (high + low + close) / 3
        })

    def evaluate_signals(self):
        """Crunches the memory bank to generate a Signal Score (0 to 5)."""
        # Need at least 20 minutes of data to calculate the 20-period indicators
        if len(self.candles) < 20:
            return 0, "Gathering Data..."

        # Convert to Pandas for hyper-fast vector math
        df = pd.DataFrame(self.candles)
        current_close = df['close'].iloc[-1]
        
        score = 0
        
        # 1. Trend: 5 EMA vs 13 EMA
        df['ema_5'] = df['close'].ewm(span=5, adjust=False).mean()
        df['ema_13'] = df['close'].ewm(span=13, adjust=False).mean()
        if df['ema_5'].iloc[-1] > df['ema_13'].iloc[-1]:
            score += 1

        # 2. Momentum: Rolling VWAP
        vwap = (df['typical_price'] * df['volume']).sum() / df['volume'].sum()
        if current_close > vwap:
            score += 1

        # 3. Volatility Contraction: 5-period StdDev vs 20-period StdDev
        df['std_5'] = df['close'].rolling(5).std()
        df['std_20'] = df['close'].rolling(20).std()
        if df['std_5'].iloc[-1] < df['std_20'].iloc[-1]:
            score += 1

        # 4. Volume Spike: Current Volume > 1.35x of 20-min Average Volume
        df['vol_20_sma'] = df['volume'].rolling(20).mean()
        if df['volume'].iloc[-1] > (1.35 * df['vol_20_sma'].iloc[-1]):
            score += 1

        # 5. ORB Breakout: Price is soaring above the initial morning chaos
        if self.orb_formed and current_close > self.orb_high:
            score += 1

        return score, {
            "close": current_close,
            "ema_5": df['ema_5'].iloc[-1],
            "ema_13": df['ema_13'].iloc[-1],
            "vwap": vwap,
            "orb_high": self.orb_high
        }

# --- QUICK SANITY TEST ---
if __name__ == "__main__":
    from datetime import timedelta
    print("🧠 Booting Mark 7 State Machine Test...")
    
    brain = Mark7Brain()
    base_time = datetime(2026, 8, 17, 9, 15) # Simulated Monday Morning
    
    # Simulate feeding 25 minutes of live market ticks into the brain
    for i in range(25):
        current_time = base_time + timedelta(minutes=i)
        # Simulating an uptrending breakout market
        close_price = 24300 + (i * 10) 
        volume = 1000 + (i * 50)
        
        brain.ingest_candle(
            timestamp=current_time,
            open_p=close_price - 5,
            high=close_price + 5,
            low=close_price - 10,
            close=close_price,
            volume=volume
        )
        
    score, metrics = brain.evaluate_signals()
    
    print(f"\n✅ Mark 7 Brain Processed 25 Candles.")
    print(f"🎯 Final Signal Score: {score}/5")
    print(f"📊 Current State: {metrics}")
