"""
Pure-Python technical indicator calculations.
All functions take a list of closing prices (float) and return computed values.
"""
from typing import Optional


def compute_sma(prices: list[float], period: int = 20) -> Optional[float]:
    """Simple Moving Average."""
    if len(prices) < period:
        return None
    return sum(prices[-period:]) / period


def compute_ema(prices: list[float], period: int = 12) -> Optional[float]:
    """Exponential Moving Average."""
    if len(prices) < period:
        return None
    k = 2.0 / (period + 1)
    ema = sum(prices[:period]) / period  # seed with SMA
    for price in prices[period:]:
        ema = price * k + ema * (1 - k)
    return ema


def compute_rsi(prices: list[float], period: int = 14) -> Optional[float]:
    """Relative Strength Index (0–100). Returns None if insufficient data."""
    if len(prices) < period + 1:
        return None
    gains, losses = [], []
    for i in range(1, len(prices)):
        delta = prices[i] - prices[i - 1]
        gains.append(max(delta, 0.0))
        losses.append(max(-delta, 0.0))

    avg_gain = sum(gains[:period]) / period
    avg_loss = sum(losses[:period]) / period

    for i in range(period, len(gains)):
        avg_gain = (avg_gain * (period - 1) + gains[i]) / period
        avg_loss = (avg_loss * (period - 1) + losses[i]) / period

    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    return 100.0 - (100.0 / (1 + rs))


def compute_macd(
    prices: list[float],
    fast: int = 12,
    slow: int = 26,
    signal: int = 9,
) -> Optional[dict]:
    """
    MACD indicator.
    Returns {"macd": float, "signal": float, "histogram": float} or None.
    """
    if len(prices) < slow + signal:
        return None
    ema_fast = _ema_series(prices, fast)
    ema_slow = _ema_series(prices, slow)
    if ema_fast is None or ema_slow is None:
        return None

    # Align series (slow is shorter)
    min_len = min(len(ema_fast), len(ema_slow))
    macd_line = [ema_fast[-(min_len - i)] - ema_slow[-(min_len - i)] for i in range(min_len)]

    if len(macd_line) < signal:
        return None

    # Signal line = EMA of MACD line
    k = 2.0 / (signal + 1)
    sig = sum(macd_line[:signal]) / signal
    for v in macd_line[signal:]:
        sig = v * k + sig * (1 - k)

    macd_val = macd_line[-1]
    hist = macd_val - sig
    return {"macd": round(macd_val, 4), "signal": round(sig, 4), "histogram": round(hist, 4)}


def compute_bollinger(
    prices: list[float],
    period: int = 20,
    std_dev: float = 2.0,
) -> Optional[dict]:
    """
    Bollinger Bands.
    Returns {"upper": float, "middle": float, "lower": float, "bandwidth": float} or None.
    """
    if len(prices) < period:
        return None
    window = prices[-period:]
    middle = sum(window) / period
    variance = sum((p - middle) ** 2 for p in window) / period
    std = variance ** 0.5
    upper = middle + std_dev * std
    lower = middle - std_dev * std
    bandwidth = (upper - lower) / middle if middle != 0 else 0.0
    return {
        "upper": round(upper, 4),
        "middle": round(middle, 4),
        "lower": round(lower, 4),
        "bandwidth": round(bandwidth, 4),
    }


def rsi_signal(rsi: float) -> str:
    if rsi >= 70:
        return "overbought"
    if rsi <= 30:
        return "oversold"
    return "neutral"


def macd_signal(histogram: float) -> str:
    if histogram > 0:
        return "bullish"
    if histogram < 0:
        return "bearish"
    return "neutral"


# ── Internal helpers ──────────────────────────────────────────────────────────

def _ema_series(prices: list[float], period: int) -> Optional[list[float]]:
    """Return the full EMA series for the given prices and period."""
    if len(prices) < period:
        return None
    k = 2.0 / (period + 1)
    result = [sum(prices[:period]) / period]
    for price in prices[period:]:
        result.append(price * k + result[-1] * (1 - k))
    return result
