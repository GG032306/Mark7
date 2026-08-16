from __future__ import annotations
import argparse
import itertools
import os
import glob
from dataclasses import dataclass, replace
from typing import Optional
import numpy as np
import pandas as pd
import warnings
warnings.filterwarnings("ignore")

@dataclass(frozen=True)
class Config:
    initial_equity: float = 15_000.0
    lot_size: int = 65
    brokerage_per_order: float = 0.0
    stt_sell_rate: float = 0.001
    exchange_rate: float = 0.0003503
    sebi_rate: float = 0.000001
    stamp_buy_rate: float = 0.00003
    gst_rate: float = 0.18
    slippage_bps: float = 2.0
    market_impact_bps: float = 1.0
    max_premium_allocation: float = 0.75
    max_risk_fraction: float = 0.15
    max_daily_loss_fraction: float = 0.15
    max_weekly_loss_fraction: float = 0.09
    equity_kill_drawdown: float = 0.20
    orb_minutes: int = 5
    ema_fast: int = 5
    ema_slow: int = 13
    spot_atr_period: int = 14
    volume_period: int = 20
    volume_multiplier: float = 1.35
    contraction_ratio: float = 0.75
    breakout_buffer_bps: float = 2.0
    minimum_signal_score: int = 3
    cooldown_minutes: int = 20
    max_trades_per_day: int = 2
    initial_stop_atr: float = 1.6
    minimum_stop_fraction: float = 0.16
    maximum_stop_fraction: float = 0.32
    breakeven_at_r: float = 0.8
    breakeven_buffer_r: float = 0.05
    chandelier_atr: float = 2.8
    tighten_after_r: float = 3.0
    tight_chandelier_atr: float = 2.1
    entry_start: str = "09:20"
    entry_end: str = "11:15"
    force_exit: str = "15:15"
    avoid_expiry_after: str = "13:30"
    minimum_premium: float = 20.0
    maximum_premium: float = 220.0

def load_data_for_dates(dates: list[str], data_dir: str) -> pd.DataFrame:
    """LAZY LOADER: Only loads the specific dates needed for the current window into RAM"""
    df_list = []
    for d in dates:
        files = glob.glob(os.path.join(data_dir, f"{d}_*.parquet"))
        for f in files:
            df_list.append(pd.read_parquet(f))
            
    if not df_list: return pd.DataFrame()
    
    raw_df = pd.concat(df_list, ignore_index=True)
    raw_df["expiry"] = pd.to_datetime(raw_df["expiry"]).dt.date
    raw_df["option_type"] = raw_df["option_type"].astype(str).str.upper()
    raw_df["date"] = raw_df["timestamp"].dt.date
    raw_df["week"] = raw_df["timestamp"].dt.to_period("W").astype(str)
    
    numeric = ["spot", "spot_volume", "strike", "open", "high", "low", "close"]
    raw_df[numeric] = raw_df[numeric].apply(pd.to_numeric, errors="coerce")
    return raw_df.dropna(subset=numeric).query("option_type in ['CE', 'PE']").sort_values(["timestamp", "expiry", "strike", "option_type"]).reset_index(drop=True)

def true_range(high: pd.Series, low: pd.Series, close: pd.Series) -> pd.Series:
    previous_close = close.shift(1)
    return pd.concat([high - low, (high - previous_close).abs(), (low - previous_close).abs()], axis=1).max(axis=1)

def add_spot_indicators(day: pd.DataFrame, cfg: Config) -> pd.DataFrame:
    spot = day.groupby("timestamp", as_index=False).agg(spot=("spot", "first"), volume=("spot_volume", "first")).sort_values("timestamp")
    spot["ema_fast"] = spot["spot"].ewm(span=cfg.ema_fast, adjust=False).mean()
    spot["ema_slow"] = spot["spot"].ewm(span=cfg.ema_slow, adjust=False).mean()
    cumulative_volume = spot["volume"].replace(0, np.nan).cumsum()
    spot["vwap"] = ((spot["spot"] * spot["volume"]).cumsum() / cumulative_volume).ffill()
    spot["range"] = spot["spot"].diff().abs()
    spot["atr"] = spot["range"].ewm(alpha=1 / cfg.spot_atr_period, adjust=False).mean()
    spot["volume_average"] = spot["volume"].rolling(cfg.volume_period, min_periods=5).mean().shift(1)
    spot["short_volatility"] = spot["range"].rolling(5).mean()
    spot["long_volatility"] = spot["range"].rolling(20).mean()
    spot["contraction"] = (spot["short_volatility"] < cfg.contraction_ratio * spot["long_volatility"]).shift(1).fillna(False)
    return spot

def transaction_cost(cfg: Config, buy_turnover: float, sell_turnover: float) -> float:
    turnover = buy_turnover + sell_turnover
    brokerage = 2 * cfg.brokerage_per_order
    exchange = turnover * cfg.exchange_rate
    sebi = turnover * cfg.sebi_rate
    stt = sell_turnover * cfg.stt_sell_rate
    stamp = buy_turnover * cfg.stamp_buy_rate
    gst = cfg.gst_rate * (brokerage + exchange + sebi)
    return brokerage + exchange + sebi + stt + stamp + gst

def fill_price(price: float, side: str, cfg: Config) -> float:
    friction = (cfg.slippage_bps + cfg.market_impact_bps) / 10_000
    if side == "BUY": return price * (1 + friction)
    return max(0.01, price * (1 - friction))

def signal_direction(current: pd.Series, previous: pd.Series, orb_high: float, orb_low: float, recent_contraction: bool, cfg: Config) -> tuple[Optional[str], int]:
    buffer = cfg.breakout_buffer_bps / 10_000
    volume_spike = pd.notna(current["volume_average"]) and current["volume"] >= current["volume_average"] * cfg.volume_multiplier
    bullish_cross = previous["ema_fast"] <= previous["ema_slow"] and current["ema_fast"] > current["ema_slow"]
    bearish_cross = previous["ema_fast"] >= previous["ema_slow"] and current["ema_fast"] < current["ema_slow"]
    bullish_breakout = previous["spot"] <= orb_high * (1 + buffer) and current["spot"] > orb_high * (1 + buffer)
    bearish_breakout = previous["spot"] >= orb_low * (1 - buffer) and current["spot"] < orb_low * (1 - buffer)
    bullish_impulse = current["range"] > current["atr"] * 0.8 and current["spot"] > previous["spot"]
    bearish_impulse = current["range"] > current["atr"] * 0.8 and current["spot"] < previous["spot"]
    
    long_score = sum([current["spot"] > current["vwap"], current["ema_fast"] > current["ema_slow"], bullish_breakout or bullish_cross, volume_spike, recent_contraction and bullish_impulse])
    short_score = sum([current["spot"] < current["vwap"], current["ema_fast"] < current["ema_slow"], bearish_breakout or bearish_cross, volume_spike, recent_contraction and bearish_impulse])
    
    if long_score >= cfg.minimum_signal_score and long_score > short_score: return "LONG", int(long_score)
    if short_score >= cfg.minimum_signal_score and short_score > long_score: return "SHORT", int(short_score)
    return None, max(int(long_score), int(short_score))

def choose_contract(chain: pd.DataFrame, direction: str, cfg: Config) -> Optional[pd.Series]:
    option_type = "CE" if direction == "LONG" else "PE"
    candidates = chain[chain["option_type"] == option_type].copy()
    if candidates.empty: return None
    candidates = candidates[(candidates["open"] >= cfg.minimum_premium) & (candidates["open"] <= cfg.maximum_premium)]
    if candidates.empty: return None
    nearest_expiry = candidates["expiry"].min()
    candidates = candidates[candidates["expiry"] == nearest_expiry].copy()
    candidates["distance"] = abs(candidates["strike"] - candidates["spot"])
    return candidates.sort_values(["distance", "strike"]).iloc[0]

def option_atr(bars: pd.DataFrame, period: int = 14) -> pd.Series:
    tr = true_range(bars["high"], bars["low"], bars["close"])
    return tr.ewm(alpha=1 / period, adjust=False).mean()

def simulate_trade(day: pd.DataFrame, contract: pd.Series, entry_time: pd.Timestamp, equity: float, cfg: Config) -> Optional[dict]:
    bars = day[(day["timestamp"] >= entry_time) & (day["expiry"] == contract["expiry"]) & (day["strike"] == contract["strike"]) & (day["option_type"] == contract["option_type"])].sort_values("timestamp").copy()
    if bars.empty: return None
    bars["option_atr"] = option_atr(bars)
    raw_entry = float(bars.iloc[0]["open"])
    entry = fill_price(raw_entry, "BUY", cfg)
    if not cfg.minimum_premium <= entry <= cfg.maximum_premium: return None
    
    initial_atr = float(bars.iloc[0]["option_atr"])
    if not np.isfinite(initial_atr) or initial_atr <= 0: initial_atr = entry * 0.12
    
    stop_distance = max(entry * cfg.minimum_stop_fraction, initial_atr * cfg.initial_stop_atr)
    stop_distance = min(stop_distance, entry * cfg.maximum_stop_fraction)
    
    quantity = cfg.lot_size
    if entry * quantity > equity * cfg.max_premium_allocation: return None
    if stop_distance * quantity > equity * cfg.max_risk_fraction: return None
    
    initial_stop = max(0.05, entry - stop_distance)
    active_stop = initial_stop
    highest = entry
    breakeven_armed = False
    exit_price, exit_time, exit_reason = None, None, None
    maximum_r = 0.0

    for bar in bars.iloc[1:].itertuples():
        bar_atr = float(bar.option_atr) if np.isfinite(bar.option_atr) else initial_atr
        previous_highest = highest
        profit_r = (previous_highest - entry) / stop_distance
        maximum_r = max(maximum_r, profit_r)

        if profit_r >= cfg.breakeven_at_r:
            breakeven_armed = True
            active_stop = max(active_stop, entry + cfg.breakeven_buffer_r * stop_distance)

        trail_multiplier = cfg.tight_chandelier_atr if profit_r >= cfg.tighten_after_r else cfg.chandelier_atr
        
        # RESTORED DELAYED RATCHET (The 38% Logic)
        if breakeven_armed:
            active_stop = max(active_stop, previous_highest - trail_multiplier * bar_atr)

        if float(bar.low) <= active_stop:
            exit_price = fill_price(active_stop, "SELL", cfg)
            exit_time = bar.timestamp
            exit_reason = "gamma_trail" if breakeven_armed else "initial_stop"
            break

        highest = max(highest, float(bar.high))
        maximum_r = max(maximum_r, (highest - entry) / stop_distance)

        if bar.timestamp.strftime("%H:%M") >= cfg.force_exit:
            exit_price = fill_price(float(bar.close), "SELL", cfg)
            exit_time = bar.timestamp
            exit_reason = "time_exit"
            break

    if exit_price is None:
        final_bar = bars.iloc[-1]
        exit_price = fill_price(float(final_bar["close"]), "SELL", cfg)
        exit_time = final_bar["timestamp"]
        exit_reason = "data_end"

    buy_turnover, sell_turnover = entry * quantity, exit_price * quantity
    fees = transaction_cost(cfg, buy_turnover, sell_turnover)
    pnl = sell_turnover - buy_turnover - fees

    return {"entry_time": entry_time, "exit_time": exit_time, "option_type": contract["option_type"], "expiry": contract["expiry"], "strike": float(contract["strike"]), "quantity": quantity, "entry": entry, "exit": exit_price, "initial_stop": initial_stop, "risk_rupees": stop_distance * quantity, "maximum_r": maximum_r, "fees": fees, "pnl": pnl, "exit_reason": exit_reason}

def run_period(data: pd.DataFrame, cfg: Config, starting_equity: float) -> tuple[float, pd.DataFrame]:
    if data.empty: return starting_equity, pd.DataFrame()
    equity, equity_peak, weekly_start_equity = starting_equity, starting_equity, starting_equity
    current_week = None
    trades: list[dict] = []

    for session_date, day in data.groupby("date", sort=True):
        day = day.sort_values("timestamp")
        session_week = day.iloc[0]["week"]
        if session_week != current_week: current_week, weekly_start_equity = session_week, equity
        if max(0.0, weekly_start_equity - equity) >= weekly_start_equity * cfg.max_weekly_loss_fraction: continue
        
        indicators = add_spot_indicators(day, cfg)
        if len(indicators) < max(25, cfg.orb_minutes + 2): continue
        
        session_open = indicators.iloc[0]["timestamp"]
        orb_end = session_open + pd.Timedelta(minutes=cfg.orb_minutes)
        orb = indicators[indicators["timestamp"] <= orb_end]
        if orb.empty: continue
        
        orb_high, orb_low = float(orb["spot"].max()), float(orb["spot"].min())
        day_start_equity, trades_today, last_exit_time = equity, 0, None
        
        candidates = indicators[(indicators["timestamp"] > orb_end) & (indicators["timestamp"].dt.strftime("%H:%M") >= cfg.entry_start) & (indicators["timestamp"].dt.strftime("%H:%M") <= cfg.entry_end)]
        for index in candidates.index:
            if trades_today >= cfg.max_trades_per_day: break
            current = indicators.loc[index]
            previous_position = indicators.index.get_loc(index) - 1
            if previous_position < 0: continue
            previous = indicators.iloc[previous_position]
            
            if last_exit_time is not None:
                if (current["timestamp"] - last_exit_time).total_seconds() / 60 < cfg.cooldown_minutes: continue
            
            recent = indicators.loc[max(indicators.index.min(), index - 5):index - 1, "contraction"]
            direction, signal_score = signal_direction(current, previous, orb_high, orb_low, bool(recent.any()), cfg)
            if direction is None: continue
            
            # [AUDIT FIX] Select contract on the signal candle, execute on the NEXT available tick
            decision_time = current["timestamp"]
            chain = day[day["timestamp"] == decision_time]
            contract = choose_contract(chain, direction, cfg)
            if contract is None: continue
            
            available_times = day.loc[day["timestamp"] > decision_time, "timestamp"].drop_duplicates()
            if available_times.empty: continue
            entry_time = available_times.iloc[0]
            if contract["expiry"] == session_date and entry_time.strftime("%H:%M") > cfg.avoid_expiry_after: continue
            
            trade = simulate_trade(day, contract, entry_time, equity, cfg)
            if trade is None: continue
            
            trade["date"], trade["direction"], trade["signal_score"], trade["equity_before"] = session_date, direction, signal_score, equity
            equity += trade["pnl"]
            equity_peak = max(equity_peak, equity)
            trade["equity_after"] = equity
            trade["drawdown"] = 1 - equity / equity_peak if equity_peak > 0 else 1
            trades.append(trade)
            trades_today += 1
            last_exit_time = trade["exit_time"]
            
            if max(0.0, day_start_equity - equity) >= day_start_equity * cfg.max_daily_loss_fraction: break
            if trade["drawdown"] >= cfg.equity_kill_drawdown or equity <= 0: return equity, pd.DataFrame(trades)
    return equity, pd.DataFrame(trades)

def objective(data: pd.DataFrame, cfg: Config) -> float:
    final_equity, trades = run_period(data, cfg, cfg.initial_equity)
    if len(trades) < 5 or final_equity <= 0: return -np.inf
    returns = trades["pnl"] / trades["equity_before"]
    mean_return = returns.mean()
    volatility = returns.std(ddof=1)
    sharpe_like = mean_return / volatility * np.sqrt(len(returns)) if volatility > 0 else 0
    max_drawdown = trades["drawdown"].max()
    profit_factor = min((trades.loc[trades["pnl"] > 0, "pnl"].sum() / abs(trades.loc[trades["pnl"] < 0, "pnl"].sum()) if (trades["pnl"] < 0).any() else 3.0), 3.0)
    growth = np.log(final_equity / cfg.initial_equity)
    trade_frequency = len(trades) / max(1, data["date"].nunique() / 5)
    return growth + 0.30 * sharpe_like + 0.10 * profit_factor - 2.0 * max_drawdown - (abs(trade_frequency - 2.0) * 0.08)

def parameter_grid(base: Config) -> list[Config]:
    dimensions = {
        "orb_minutes": [5, 10],
        "minimum_signal_score": [3, 4],
        "initial_stop_atr": [1.6, 2.0],
        "chandelier_atr": [2.5, 3.0]
    }
    keys = list(dimensions)
    return [
        replace(base, **dict(zip(keys, values)))
        for values in itertools.product(*(dimensions[k] for k in keys))
    ]

def walk_forward_bigdata(data_dir: str, base: Config, train_days: int = 10, test_days: int = 4) -> tuple[float, pd.DataFrame]:
    # Extract unique dates from the filenames
    files = [f for f in os.listdir(data_dir) if f.endswith(".parquet")]
    dates = sorted(list(set([f.split('_')[0] for f in files])))
    
    if len(dates) <= train_days: raise ValueError(f"Need more than {train_days} trading days.")
    equity, results, grid = base.initial_equity, [], parameter_grid(base)
    
    print(f"🔄 Commencing Big Data Walk-Forward across {len(dates)} days...")
    
    for test_start in range(train_days, len(dates), test_days):
        train_dates = dates[test_start - train_days:test_start]
        test_dates = dates[test_start:test_start + test_days]
        
        # ⚠️ LAZY LOAD: ONLY LOAD WHAT WE NEED INTO RAM
        training = load_data_for_dates(train_dates, data_dir)
        testing = load_data_for_dates(test_dates, data_dir)
        
        if testing.empty: break
        
        valid = [item for item in [(objective(training, cfg), cfg) for cfg in grid] if np.isfinite(item[0])]
        selected = max(valid, key=lambda item: item[0])[1] if valid else base
        equity, trades = run_period(testing, selected, equity)
        
        if not trades.empty:
            trades["wf_orb_minutes"], trades["wf_volume_multiplier"], trades["wf_signal_score"] = selected.orb_minutes, selected.volume_multiplier, selected.minimum_signal_score
            trades["wf_initial_stop_atr"], trades["wf_chandelier_atr"] = selected.initial_stop_atr, selected.chandelier_atr
            results.append(trades)
            
        print(f"   ✓ Processed Window ending {test_dates[-1]} | Current Equity: ₹{equity:,.2f}")
        if equity <= 0: break
        
    return equity, (pd.concat(results, ignore_index=True) if results else pd.DataFrame())

def print_report(initial_equity: float, final_equity: float, trades: pd.DataFrame) -> None:
    print("\n" + "="*50)
    print("🚀 NIFTY BIG DATA OPTIONS ENGINE (5-Year Ready)")
    print("="*50)
    print(f"Initial equity: ₹{initial_equity:,.2f}")
    print(f"Final equity:   ₹{final_equity:,.2f}")
    print(f"Net return:     {(final_equity / initial_equity - 1) * 100:,.2f}%")
    if trades.empty:
        print("No out-of-sample trades executed.")
        return
    
    winners, losers = trades[trades["pnl"] > 0], trades[trades["pnl"] < 0]
    gross_profit, gross_loss = winners["pnl"].sum(), abs(losers["pnl"].sum())
    returns = trades["pnl"] / trades["equity_before"]
    sharpe = returns.mean() / returns.std(ddof=1) * np.sqrt(len(returns)) if len(returns) > 1 and returns.std(ddof=1) > 0 else 0.0

    print(f"Trades:         {len(trades)}")
    print(f"Win rate:       {(trades.pnl > 0).mean() * 100:.2f}%")
    print(f"Median P&L:     ₹{trades.pnl.median():,.2f}")
    print(f"Max drawdown:   {trades.drawdown.max() * 100:.2f}%")
    print(f"Profit factor:  {gross_profit / gross_loss:.2f}" if gross_loss > 0 else "Profit factor:  inf")
    
    print("-" * 50)
    print("⏳ RECENT TRADE TIMELINE (Last 10 trades):")
    for _, row in trades.tail(10).iterrows():
        print(f"[{row['entry_time']}] BOUGHT @ ₹{row['entry']:.2f} | SOLD @ ₹{row['exit']:.2f} | PNL: ₹{row['pnl']:.2f} | Exit: {row['exit_reason']}")
    print("="*50 + "\n")
    trades.to_csv("bigdata_walk_forward_trades.csv", index=False)

def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("data_dir", help="Directory containing the daily .parquet files")
    parser.add_argument("--brokerage", type=float, default=0.0)
    parser.add_argument("--lot-size", type=int, default=65)
    args = parser.parse_args()
    
    config = replace(Config(), brokerage_per_order=args.brokerage, lot_size=args.lot_size)
    final_equity, trades = walk_forward_bigdata(args.data_dir, config)
    print_report(config.initial_equity, final_equity, trades)

if __name__ == "__main__":
    main()

