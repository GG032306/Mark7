import asyncio
import websockets
import requests
import json
import MarketDataFeed_pb2
from datetime import datetime
import pytz

from mark7_brain import Mark7Brain
from mark7_execution import Mark7Executor

class Mark7LiveEngine:
    def __init__(self):
        print("="*50)
        print("🚀 INITIATING MARK 7 LIVE TRADING ENGINE")
        print("="*50)
        
        # Initialize isolated components
        self.brain = Mark7Brain()
        self.executor = Mark7Executor()
        self.token = self.executor.token
        
        self.headers = {
            'Accept': 'application/json',
            'Authorization': f'Bearer {self.token}'
        }
        self.base_url = 'https://api.upstox.com/v2'

    def get_target_option(self, direction):
        """Scans the Option Chain to find the nearest expiry contract between ₹20 and ₹220."""
        print(f"🔍 SCANNING OPTION CHAIN FOR {direction.upper()}...")
        
        # 1. Fetch Option Contracts to get the nearest expiry date
        contract_url = f"{self.base_url}/option/contract?instrument_key=NSE_INDEX|Nifty%2050"
        res = requests.get(contract_url, headers=self.headers)
        if res.status_code != 200:
            print("❌ Failed to fetch option contracts.")
            return None
            
        contracts = res.json().get('data', [])
        if not contracts:
            return None
            
        # Get the closest expiry
        nearest_expiry = contracts[0]['expiry_date'] 
        print(f"📅 Nearest Expiry Selected: {nearest_expiry}")
        
        # 2. Fetch the actual Options Chain for that expiry
        chain_url = f"{self.base_url}/option/chain?instrument_key=NSE_INDEX|Nifty%2050&expiry_date={nearest_expiry}"
        chain_res = requests.get(chain_url, headers=self.headers)
        if chain_res.status_code != 200:
            return None
            
        chain_data = chain_res.json().get('data', [])
        
        best_instrument = None
        best_price = 0
        
        # 3. Filter for premiums between ₹20 and ₹220 (Prioritize closer to ₹220 for higher delta)
        for strike in chain_data:
            target_data = strike.get('call_options') if direction == "CE" else strike.get('put_options')
            if target_data and target_data.get('market_data'):
                price = target_data['market_data'].get('ltp', 0)
                if 20 <= price <= 220:
                    if price > best_price: # Grabbing the most expensive valid one for higher delta
                        best_price = price
                        best_instrument = target_data['instrument_key']
                        
        if best_instrument:
            print(f"🎯 TARGET LOCKED: {best_instrument} @ ₹{best_price}")
        return best_instrument

    async def run_market_loop(self):
        auth_url = 'https://api.upstox.com/v3/feed/market-data-feed/authorize'
        res = requests.get(auth_url, headers=self.headers)
        ws_url = res.json()['data']['authorized_redirect_uri']
        
        print("🔗 Establishing Exchange WebSocket...")
        async with websockets.connect(ws_url) as websocket:
            print("✅ Streamer Online. Mark 7 is hunting.")
            req = {
                "guid": "nifty_stream_01",
                "method": "sub",
                "data": {"mode": "full", "instrumentKeys": ["NSE_INDEX|Nifty 50"]}
            }
            await websocket.send(json.dumps(req).encode('utf-8'))
            
            # Temporary storage to build 1-minute candles from milliseconds ticks
            current_minute = None
            open_p = high = low = close = volume = 0
            
            try:
                while True:
                    message = await websocket.recv()
                    feed_resp = MarketDataFeed_pb2.FeedResponse()
                    feed_resp.ParseFromString(message)
                    
                    if feed_resp.type == 1: # Live Feed
                        for key, feed in feed_resp.feeds.items():
                            if feed.HasField('fullFeed'):
                                ltp = feed.fullFeed.ltpc.ltp
                                vol = feed.fullFeed.eFeedDetails.vol
                                timestamp = datetime.now(pytz.timezone('Asia/Kolkata'))
                                
                                # 1. Manage Live Risk (Kill Switch & Trailing Stops)
                                if self.executor.position_open:
                                    self.executor.manage_risk(instrument_key=key, current_price=ltp, current_time=timestamp)
                                
                                # 2. Build 1-Minute Candles
                                if current_minute is None:
                                    current_minute = timestamp.minute
                                    open_p = high = low = close = ltp
                                    volume = vol
                                elif timestamp.minute == current_minute:
                                    high = max(high, ltp)
                                    low = min(low, ltp)
                                    close = ltp
                                    volume = vol
                                else:
                                    # Minute closed! Send to Brain.
                                    self.brain.ingest_candle(timestamp, open_p, high, low, close, volume)
                                    
                                    # Reset for the new minute
                                    current_minute = timestamp.minute
                                    open_p = high = low = close = ltp
                                    volume = vol
                                    
                                    # 3. Evaluate Strategy
                                    if not self.executor.position_open:
                                        score, metrics = self.brain.evaluate_signals()
                                        
                                        if score >= 3:
                                            print(f"\n🚨 SIGNAL TRIGGERED! Score: {score}/5")
                                            # Determine Direction (EMA 5 > EMA 13 = CE, else PE)
                                            direction = "CE" if metrics["ema_5"] > metrics["ema_13"] else "PE"
                                            
                                            target_key = self.get_target_option(direction)
                                            if target_key:
                                                # Execute Order (Assuming a base lot size of 25 for Nifty)
                                                success = self.executor.fire_order(target_key, "BUY", 25)
                                                if success:
                                                    # [AUDIT FIX] Initialize position and subscribe to the Option Ticker
                                                    self.executor.initialize_position(target_key, 25, best_price)
                                                    sub_req = {
                                                        "guid": "option_sub",
                                                        "method": "sub",
                                                        "data": {"mode": "full", "instrumentKeys": [target_key]}
                                                    }
                                                    await websocket.send(json.dumps(sub_req).encode('utf-8'))
                                                    print(f"📡 SUBSCRIBED TO OPTION TICKER: {target_key}")
                                                    
            except websockets.ConnectionClosed:
                print("⚠️ Exchange connection lost.")

if __name__ == "__main__":
    engine = Mark7LiveEngine()
    try:
        asyncio.run(engine.run_market_loop())
    except KeyboardInterrupt:
        print("\n⏹️ Mark 7 safely deactivated.")
