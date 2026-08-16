import requests
import json
import pytz
from datetime import datetime

class Mark7Executor:
    def __init__(self, paper_mode=True):
        self.paper_mode = paper_mode
        mode_str = "📝 PAPER TRADING (ZERO RISK)" if self.paper_mode else "💰 LIVE CAPITAL"
        print(f"⚙️ Booting Mark 7 Execution Module [{mode_str}]...")
        
        try:
            with open('access_token.txt', 'r') as f:
                self.token = f.read().strip()
        except FileNotFoundError:
            print("❌ access_token.txt missing.")
            self.token = None
            
        self.headers = {
            'Accept': 'application/json',
            'Authorization': f'Bearer {self.token}',
            'Content-Type': 'application/json'
        }
        self.base_url = 'https://api.upstox.com/v2'
        
        # Position tracking state
        self.position_open = False
        self.current_instrument = None
        self.qty = 0
        self.entry_price = 0.0
        self.highest_price = 0.0
        self.stop_loss = 0.0
        self.virtual_wallet = 15000.0  # Starting virtual capital

    def fire_order(self, instrument_key, side, quantity, simulated_price=0.0):
        """Routes order: simulated for paper mode, live REST API for live mode."""
        if self.paper_mode:
            print(f"\n📝 [PAPER TRADE] {side.upper()} {quantity}x {instrument_key} @ ₹{simulated_price:.2f}")
            return True
            
        # Live Order Routing
        url = f"{self.base_url}/order/place"
        payload = {
            "quantity": quantity,
            "product": "I",
            "validity": "DAY",
            "price": 0,
            "tag": "mark7_bot",
            "instrument_token": instrument_key,
            "order_type": "MARKET",
            "transaction_type": side.upper(),
            "disclosed_quantity": 0,
            "trigger_price": 0,
            "is_amo": False
        }
        
        print(f"\n🚀 TRANSMITTING LIVE {side.upper()} ORDER: {quantity}x {instrument_key}")
        response = requests.post(url, headers=self.headers, data=json.dumps(payload))
        
        if response.status_code == 200 and response.json().get('status') == 'success':
            print(f"✅ ORDER PLACED SUCCESSFULLY. Order ID: {response.json()['data']['order_id']}")
            return True
        else:
            print(f"⚠️ EXCHANGE REJECTION: {response.status_code} - {response.text}")
            return False

    def initialize_position(self, instrument, quantity, fill_price):
        """Locks in the target option instrument and sets initial risk boundaries."""
        self.position_open = True
        self.current_instrument = instrument
        self.qty = quantity
        self.entry_price = fill_price
        self.highest_price = fill_price
        self.stop_loss = fill_price * 0.50  # 50% catastrophic stop
        print(f"🎯 POSITION LOCKED: {quantity}x {instrument} @ ₹{fill_price:.2f} | Initial Stop: ₹{self.stop_loss:.2f}")

    def manage_risk(self, instrument_key, current_price, current_time):
        """Monitors real-time option ticks and executes trailing stop / kill switch."""
        if not self.position_open or instrument_key != self.current_instrument:
            return

        # 1. 15:15 IST Mandatory Portfolio Flattening
        if current_time.hour == 15 and current_time.minute >= 15:
            pnl = (current_price - self.entry_price) * self.qty
            self.virtual_wallet += pnl
            print(f"\n🛑 15:15 KILL SWITCH: Flattening position @ ₹{current_price:.2f}")
            print(f"📊 Trade P&L: ₹{pnl:+.2f} | Virtual Capital: ₹{self.virtual_wallet:.2f}")
            self.fire_order(self.current_instrument, "SELL", self.qty, simulated_price=current_price)
            self.position_open = False
            return

        # 2. Track High-Water Mark and Ratchet Trailing Stop
        if current_price > self.highest_price:
            self.highest_price = current_price
            
            # Arm trailing stop once price gains 10%
            breakeven_threshold = self.entry_price * 1.10
            if self.highest_price >= breakeven_threshold:
                new_stop = self.highest_price * 0.95  # 5% trailing ratchet
                if new_stop > self.stop_loss:
                    print(f"🛡️ Ratcheting Stop Loss UP: ₹{new_stop:.2f} (Peak: ₹{self.highest_price:.2f})")
                    self.stop_loss = new_stop

        # 3. Stop-Loss Trigger
        if current_price <= self.stop_loss:
            pnl = (current_price - self.entry_price) * self.qty
            self.virtual_wallet += pnl
            print(f"\n💥 STOP LOSS TRIGGERED at ₹{current_price:.2f} (Entry: ₹{self.entry_price:.2f})")
            print(f"📊 Trade P&L: ₹{pnl:+.2f} | Virtual Capital: ₹{self.virtual_wallet:.2f}")
            self.fire_order(self.current_instrument, "SELL", self.qty, simulated_price=current_price)
            self.position_open = False
