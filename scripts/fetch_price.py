"""scripts/fetch_price.py — 銘柄コードから現在値を取得して標準出力に出力"""
import sys, math
import yfinance as yf

def main():
    if len(sys.argv) < 2:
        sys.exit(1)
    ticker = sys.argv[1].strip()
    sym = (ticker if ticker.endswith(".T") else ticker + ".T")
    try:
        hist = yf.Ticker(sym).history(period="5d")
        if hist.empty:
            sys.exit(1)
        close = hist["Close"].dropna()
        if close.empty:
            sys.exit(1)
        p = float(close.iloc[-1])
        if math.isnan(p) or p <= 0:
            sys.exit(1)
        print(f"{p:.0f}")
    except Exception:
        sys.exit(1)

if __name__ == "__main__":
    main()
