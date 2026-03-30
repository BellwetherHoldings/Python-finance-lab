#!/usr/bin/env python3
"""
MGC Trading Signal Bot
======================
Micro Gold Futures (MGC) — On-Demand Signal Generator

Run this any time you want a signal:
    python mgc_signal.py

The bot fetches live market data, runs multi-timeframe technical analysis,
and prints EXACTLY what you need to do — entry, stop, and targets with
dollar amounts per contract.
"""

import sys
import warnings
warnings.filterwarnings("ignore")

try:
    import yfinance as yf
    import pandas as pd
    import numpy as np
except ImportError as e:
    print(f"\n  [ERROR] Missing dependency: {e}")
    print("  Fix: pip install -r requirements.txt\n")
    sys.exit(1)

from datetime import datetime, timezone

# ─── CONTRACT SPECS ────────────────────────────────────────────────────────────
TICKER       = "MGC=F"   # Micro Gold Futures (CME)
FALLBACK     = "GC=F"    # Full-size Gold Futures (same price, used as fallback)
CONTRACT_OZ  = 10        # MGC = 10 troy oz per contract
TICK_SIZE    = 0.10      # Minimum price increment ($/oz)
TICK_VALUE   = 1.00      # Dollar value per tick per contract

# ─── DISPLAY ───────────────────────────────────────────────────────────────────
W = 58   # inner box width (chars between ║ and ║)


def _top():  print(f"╔{'═' * W}╗")
def _sep():  print(f"╠{'═' * W}╣")
def _bot():  print(f"╚{'═' * W}╝")
def _blank(): print(f"║{' ' * W}║")


def _line(s=""):
    """Left-aligned line with 1-char margin each side."""
    inner = W - 2
    text = str(s)
    if len(text) > inner:
        text = text[:inner]
    print(f"║ {text:<{inner}} ║")


def _center(s=""):
    """Centered line."""
    print(f"║{str(s).center(W)}║")


def _cols(label, value, note=""):
    """Three-column row: label(22) value(16) note(18)."""
    L, V, N = 22, 16, W - 2 - 22 - 16
    row = f" {label:<{L}}{value:<{V}}{note:<{N}}"
    if len(row) > W:
        row = row[:W]
    print(f"║{row}║")


# ─── TECHNICAL INDICATORS ──────────────────────────────────────────────────────

def _ema(s: pd.Series, n: int) -> pd.Series:
    return s.ewm(span=n, adjust=False).mean()


def _rsi(s: pd.Series, n: int = 14) -> pd.Series:
    d = s.diff()
    gain = d.clip(lower=0).ewm(com=n - 1, adjust=False).mean()
    loss = (-d.clip(upper=0)).ewm(com=n - 1, adjust=False).mean()
    rs = gain / loss.replace(0, np.nan)
    return 100 - (100 / (1 + rs))


def _macd(s: pd.Series, fast=12, slow=26, sig=9):
    line   = _ema(s, fast) - _ema(s, slow)
    signal = _ema(line, sig)
    hist   = line - signal
    return line, signal, hist


def _bb(s: pd.Series, n: int = 20, k: float = 2.0):
    mid   = s.rolling(n).mean()
    sigma = s.rolling(n).std()
    return mid + k * sigma, mid, mid - k * sigma


def _atr(h: pd.Series, l: pd.Series, c: pd.Series, n: int = 14) -> pd.Series:
    tr = pd.concat(
        [h - l, (h - c.shift()).abs(), (l - c.shift()).abs()], axis=1
    ).max(axis=1)
    return tr.ewm(com=n - 1, adjust=False).mean()


def _pivot_points(df: pd.DataFrame) -> dict:
    """Classic pivot points from the last completed daily candle."""
    h = float(df["High"].iloc[-2])
    l = float(df["Low"].iloc[-2])
    c = float(df["Close"].iloc[-2])
    p = (h + l + c) / 3.0
    return {
        "R2": round(p + (h - l), 1),
        "R1": round(2 * p - l,   1),
        "P":  round(p,            1),
        "S1": round(2 * p - h,   1),
        "S2": round(p - (h - l), 1),
    }


# ─── DATA FETCHING ─────────────────────────────────────────────────────────────

def _fetch(ticker: str, period: str, interval: str) -> pd.DataFrame | None:
    try:
        df = yf.download(
            ticker, period=period, interval=interval,
            progress=False, auto_adjust=True
        )
        if df is None or df.empty:
            return None
        # Flatten MultiIndex columns (yfinance sometimes returns these)
        if isinstance(df.columns, pd.MultiIndex):
            df.columns = df.columns.get_level_values(0)
        return df
    except Exception:
        return None


def get_data() -> tuple:
    """Return (daily_df, hourly_df, ticker_used) or raise on failure."""
    for ticker in [TICKER, FALLBACK]:
        daily  = _fetch(ticker, "1y",  "1d")
        hourly = _fetch(ticker, "60d", "1h")
        if (
            daily  is not None and len(daily)  >= 52 and
            hourly is not None and len(hourly) >= 52
        ):
            return daily, hourly, ticker
    return None, None, None


# ─── ANALYSIS ENGINE ───────────────────────────────────────────────────────────

def analyze(daily: pd.DataFrame, hourly: pd.DataFrame) -> dict:
    """
    Score the market from -10 (strongly bearish) to +10 (strongly bullish).
    Returns a rich dict used for display and level calculation.
    """
    score   = 0
    reasons = []   # supporting evidence
    alerts  = []   # caution flags (don't block signal, just note)

    cd = daily["Close"]
    ch = hourly["Close"]
    price = float(ch.iloc[-1])

    # ── 1. DAILY TREND (EMA 50 / 200) ─────────────────────────────────────────
    d_e50  = float(_ema(cd, 50).iloc[-1])
    d_e200 = float(_ema(cd, 200).iloc[-1])
    last_d = float(cd.iloc[-1])

    if last_d > d_e50 > d_e200:
        score += 2
        reasons.append("Daily trend: BULLISH  (price > EMA50 > EMA200)")
    elif last_d < d_e50 < d_e200:
        score -= 2
        reasons.append("Daily trend: BEARISH  (price < EMA50 < EMA200)")
    elif last_d > d_e50:
        score += 1
        reasons.append("Daily trend: Mild bullish  (price > EMA50)")
    else:
        score -= 1
        reasons.append("Daily trend: Mild bearish  (price < EMA50)")

    # ── 2. 1-HOUR EMA STACK (9 / 21 / 50) ────────────────────────────────────
    e9  = float(_ema(ch,  9).iloc[-1])
    e21 = float(_ema(ch, 21).iloc[-1])
    e50 = float(_ema(ch, 50).iloc[-1])

    if e9 > e21 > e50:
        score += 2
        reasons.append("1H EMAs: Bullish stack  (9 > 21 > 50)")
    elif e9 < e21 < e50:
        score -= 2
        reasons.append("1H EMAs: Bearish stack  (9 < 21 < 50)")
    elif e9 > e21:
        score += 1
        reasons.append("1H EMAs: Short-term bullish  (9 > 21)")
    else:
        score -= 1
        reasons.append("1H EMAs: Short-term bearish  (9 < 21)")

    # ── 3. RSI (14) on 1H ─────────────────────────────────────────────────────
    rsi_val = float(_rsi(ch, 14).iloc[-1])

    if 50 < rsi_val < 70:
        score += 1
        reasons.append(f"RSI(14): {rsi_val:.1f}  — Bullish momentum zone")
    elif rsi_val >= 70:
        score -= 1
        alerts.append(f"RSI(14): {rsi_val:.1f}  — OVERBOUGHT, longs are risky")
    elif 30 < rsi_val <= 50:
        score -= 1
        reasons.append(f"RSI(14): {rsi_val:.1f}  — Bearish momentum zone")
    else:  # <= 30
        score += 1
        alerts.append(f"RSI(14): {rsi_val:.1f}  — OVERSOLD, shorts are risky")

    # ── 4. MACD (12/26/9) on 1H ───────────────────────────────────────────────
    ml, sl, hl = _macd(ch)
    macd_now  = float(ml.iloc[-1])
    sig_now   = float(sl.iloc[-1])
    hist_now  = float(hl.iloc[-1])
    hist_prev = float(hl.iloc[-2])

    if macd_now > sig_now:
        if hist_now > hist_prev:
            score += 2
            reasons.append("MACD: Bullish AND accelerating  ▲▲")
        else:
            score += 1
            reasons.append("MACD: Bullish  (above signal line)")
    else:
        if hist_now < hist_prev:
            score -= 2
            reasons.append("MACD: Bearish AND accelerating  ▼▼")
        else:
            score -= 1
            reasons.append("MACD: Bearish  (below signal line)")

    # ── 5. BOLLINGER BANDS (20, 2) on 1H ─────────────────────────────────────
    bu, bm, bl_band = _bb(ch)
    bb_u = float(bu.iloc[-1])
    bb_l = float(bl_band.iloc[-1])
    bb_range = bb_u - bb_l
    bb_pos = (price - bb_l) / bb_range if bb_range > 0 else 0.5

    if bb_pos > 0.85:
        score -= 1
        alerts.append(f"BB: Near upper band ({bb_pos:.0%})  — stretched, fade risk")
    elif bb_pos < 0.15:
        score += 1
        alerts.append(f"BB: Near lower band ({bb_pos:.0%})  — potential bounce zone")
    elif bb_pos >= 0.5:
        score += 1
        reasons.append(f"BB: Upper half ({bb_pos:.0%})  — bullish positioning")
    else:
        score -= 1
        reasons.append(f"BB: Lower half ({bb_pos:.0%})  — bearish positioning")

    # ── ATR & PIVOT POINTS (not scored, used for levels) ─────────────────────
    atr_1h  = float(_atr(hourly["High"], hourly["Low"], hourly["Close"], 14).iloc[-1])
    atr_day = float(_atr(daily["High"],  daily["Low"],  daily["Close"],  14).iloc[-1])
    pivots  = _pivot_points(daily)

    score = max(-10, min(10, score))

    return {
        "score":    score,
        "price":    price,
        "reasons":  reasons,
        "alerts":   alerts,
        "atr_1h":   atr_1h,
        "atr_day":  atr_day,
        "pivots":   pivots,
        "rsi":      rsi_val,
        "macd":     macd_now,
        "macd_sig": sig_now,
        "e9":       e9,
        "e21":      e21,
        "e50":      e50,
        "bb_pos":   bb_pos,
        "bb_upper": bb_u,
        "bb_lower": bb_l,
    }


# ─── TRADE LEVEL CALCULATOR ────────────────────────────────────────────────────

def compute_levels(a: dict) -> dict:
    """
    Build exact entry / stop / target levels from the analysis.
    - Stop is 1.5 × ATR(1H) away, adjusted to nearest pivot support/resistance.
    - TP1 is 1.5 × ATR(1H), capped at nearest pivot.
    - TP2 is 3.0 × ATR(1H) — the runner target.
    """
    price  = a["price"]
    atr    = a["atr_1h"]
    score  = a["score"]
    pvt    = a["pivots"]

    if score < 4 and score > -4:
        return {"dir": "NEUTRAL"}

    direction = "LONG" if score >= 4 else "SHORT"

    if direction == "LONG":
        entry = price
        stop  = price - 1.5 * atr
        tp1   = price + 1.5 * atr
        tp2   = price + 3.0 * atr

        supports     = sorted([v for v in pvt.values() if v < price], reverse=True)
        resistances  = sorted([v for v in pvt.values() if v > price])

        # Tighten stop just below nearest support (if it's tighter than ATR stop)
        if supports and supports[0] > stop:
            stop = supports[0] - 0.3

        # Cap TP1 just below nearest resistance (if it's closer than ATR target)
        if resistances and resistances[0] < tp1:
            tp1 = resistances[0] - 0.1

    else:  # SHORT
        entry = price
        stop  = price + 1.5 * atr
        tp1   = price - 1.5 * atr
        tp2   = price - 3.0 * atr

        resistances  = sorted([v for v in pvt.values() if v > price])
        supports     = sorted([v for v in pvt.values() if v < price], reverse=True)

        # Tighten stop just above nearest resistance
        if resistances and resistances[0] < stop:
            stop = resistances[0] + 0.3

        # Cap TP1 just above nearest support
        if supports and supports[0] > tp1:
            tp1 = supports[0] + 0.1

    entry = round(entry, 1)
    stop  = round(stop,  1)
    tp1   = round(tp1,   1)
    tp2   = round(tp2,   1)

    risk    = abs(entry - stop)
    reward1 = abs(tp1   - entry)
    reward2 = abs(tp2   - entry)

    return {
        "dir":       direction,
        "entry":     entry,
        "stop":      stop,
        "tp1":       tp1,
        "tp2":       tp2,
        "risk_$":    round(risk    * CONTRACT_OZ, 2),
        "reward1_$": round(reward1 * CONTRACT_OZ, 2),
        "reward2_$": round(reward2 * CONTRACT_OZ, 2),
        "rr1":       round(reward1 / risk, 2) if risk else 0,
        "rr2":       round(reward2 / risk, 2) if risk else 0,
    }


# ─── CONFIDENCE LABEL ──────────────────────────────────────────────────────────

def _conf(score: int) -> str:
    s = abs(score)
    if s >= 8: return "VERY HIGH"
    if s >= 6: return "HIGH"
    if s >= 4: return "MEDIUM"
    return "LOW"


# ─── SIGNAL DISPLAY ────────────────────────────────────────────────────────────

def print_signal(a: dict, lvl: dict, ticker: str) -> None:
    now  = datetime.now(timezone.utc).strftime("%Y-%m-%d  %H:%M UTC")
    d    = lvl["dir"]
    conf = _conf(a["score"])

    print()
    _top()
    _center("MGC — MICRO GOLD FUTURES")
    _center(f"Trading Signal  ·  {now}")
    _sep()

    # ── VERDICT ───────────────────────────────────────────────────────────────
    if d == "LONG":
        _center(f"▲   SIGNAL:  LONG   [{conf} CONFIDENCE]")
    elif d == "SHORT":
        _center(f"▼   SIGNAL:  SHORT  [{conf} CONFIDENCE]")
    else:
        _center("◆   SIGNAL:  NEUTRAL — STAND ASIDE")

    _center(f"Composite score: {a['score']:+d} / 10   (threshold ±4)")
    _sep()
    _cols(" Current Price", f"${a['price']:>10,.1f}")
    _blank()

    # ── TRADE SETUP ───────────────────────────────────────────────────────────
    if d != "NEUTRAL":
        _sep()
        _center("— TRADE SETUP —")
        _sep()

        action = f"{'BUY' if d == 'LONG' else 'SELL SHORT'} MGC at market"
        _cols(" ACTION", action)
        _blank()
        _cols(" Entry",    f"${lvl['entry']:>10,.1f}",  "(market order)")
        _cols(" Stop Loss", f"${lvl['stop']:>10,.1f}",
              f"-${lvl['risk_$']:.0f} / contract")
        _cols(" Target 1", f"${lvl['tp1']:>10,.1f}",
              f"+${lvl['reward1_$']:.0f}  R:R {lvl['rr1']:.1f}:1")
        _cols(" Target 2", f"${lvl['tp2']:>10,.1f}",
              f"+${lvl['reward2_$']:.0f}  R:R {lvl['rr2']:.1f}:1")
        _blank()
        _line(f"  Plan: Exit 50% at Target 1, let 50% run to Target 2.")
        _line(f"  Move stop to breakeven once Target 1 is hit.")

    # ── KEY PIVOT LEVELS ─────────────────────────────────────────────────────
    _sep()
    _center("— DAILY PIVOT LEVELS —")
    _sep()

    pvt   = a["pivots"]
    price = a["price"]
    all_vals = list(pvt.values())
    nearest = min(all_vals, key=lambda v: abs(v - price))

    for label, val in [
        ("Resistance R2", pvt["R2"]),
        ("Resistance R1", pvt["R1"]),
        ("Pivot Point",   pvt["P"]),
        ("Support S1",    pvt["S1"]),
        ("Support S2",    pvt["S2"]),
    ]:
        tag  = "  ◄ NEAR PRICE" if val == nearest else ""
        side = "above" if val > price else "below"
        dist = abs(val - price)
        note = f"{tag}" if tag else f"  ({side} by ${dist:.1f})"
        _cols(f"  {label}", f"${val:>10,.1f}", note)

    # ── INDICATOR DASHBOARD ───────────────────────────────────────────────────
    _sep()
    _center("— INDICATOR DASHBOARD  (1-Hour Chart) —")
    _sep()

    rsi_tag  = ("OVERBOUGHT" if a["rsi"] > 70
                else "OVERSOLD"   if a["rsi"] < 30
                else "BULLISH"    if a["rsi"] > 50
                else "BEARISH")
    macd_tag = "BULLISH" if a["macd"] > a["macd_sig"] else "BEARISH"
    ema_tag  = ("BULLISH" if a["e9"] > a["e21"] > a["e50"]
                else "BEARISH" if a["e9"] < a["e21"] < a["e50"]
                else "MIXED")

    _cols("  RSI(14)",     f"{a['rsi']:.1f}",       f"[{rsi_tag}]")
    _cols("  MACD",        f"{a['macd']:.2f}",       f"[{macd_tag}]")
    _cols("  EMA 9/21/50", f"{a['e9']:.1f}",         f"[{ema_tag}]")
    _cols("  ATR(14) 1H",  f"${a['atr_1h']:.1f}",   "(avg hourly range)")
    _cols("  ATR(14) Day", f"${a['atr_day']:.1f}",  "(avg daily range)")
    _cols("  BB Position", f"{a['bb_pos']:.0%}",     "(0%=low  100%=high)")

    # ── ANALYSIS BREAKDOWN ────────────────────────────────────────────────────
    _sep()
    _center("— ANALYSIS BREAKDOWN —")
    _sep()

    for r in a["reasons"]:
        _line(f"  + {r}")

    if a["alerts"]:
        _blank()
        for alert in a["alerts"]:
            _line(f"  ! {alert}")

    # ── NEUTRAL GUIDANCE ──────────────────────────────────────────────────────
    if d == "NEUTRAL":
        _sep()
        _center("DO NOT TRADE  —  No clear edge right now.")
        _center("Re-run this script when conditions change.")

    # ── FOOTER ────────────────────────────────────────────────────────────────
    _sep()
    _line(f"  Ticker: {ticker}   |   MGC = {CONTRACT_OZ} oz   "
          f"|   Tick: ${TICK_SIZE} = ${TICK_VALUE:.2f}/contract")
    _line("  This is technical analysis only — not financial advice.")
    _bot()
    print()


# ─── MAIN ──────────────────────────────────────────────────────────────────────

def main() -> None:
    print(f"\n  Fetching live market data...", end="", flush=True)

    daily, hourly, ticker = get_data()

    if daily is None:
        print("\n\n  [ERROR] Could not fetch market data.")
        print("  Check your internet connection and try again.\n")
        sys.exit(1)

    print(f" OK  ({ticker})\n")

    analysis = analyze(daily, hourly)
    levels   = compute_levels(analysis)

    print_signal(analysis, levels, ticker)


if __name__ == "__main__":
    main()
