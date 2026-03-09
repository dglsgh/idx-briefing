"""
IDX Daily Market Briefing Generator
------------------------------------
Fetches live market data, calls Claude AI, and saves the briefing to a GitHub Gist.
Run manually to test, or let launchd run it automatically at 8:50AM.
"""

import yfinance as yf
import requests
import anthropic
import json
import os
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
import concurrent.futures

# ─────────────────────────────────────────────
#  CONFIG — set via environment variables OR edit directly here
#  Recommended: add these three lines to ~/.zprofile (or ~/.bash_profile):
#    export ANTHROPIC_API_KEY="sk-ant-..."
#    export GITHUB_TOKEN="ghp_..."
#    export IDX_GIST_ID=""          ← left blank; auto-created on first run
# ─────────────────────────────────────────────

ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "your-anthropic-api-key-here")
GITHUB_TOKEN      = os.environ.get("GITHUB_TOKEN",      "your-github-token-here")
GIST_ID           = os.environ.get("IDX_GIST_ID",       "")  # auto-created if empty

# Path where the Gist ID is persisted between runs (same folder as this script)
_SCRIPT_DIR  = os.path.dirname(os.path.abspath(__file__))
_GIST_ID_FILE = os.path.join(_SCRIPT_DIR, ".idx_gist_id")

# ─────────────────────────────────────────────
#  STOCK & COMMODITY SYMBOLS
# ─────────────────────────────────────────────

IDX_STOCKS = [
    "^JKSE",
    "BBCA.JK","BBRI.JK","BMRI.JK","TLKM.JK","ASII.JK",
    "BREN.JK","GOTO.JK","HMSP.JK","UNVR.JK","ICBP.JK",
    "INDF.JK","KLBF.JK","ADRO.JK","PTBA.JK","MDKA.JK",
    "SMGR.JK","GGRM.JK","EXCL.JK","ISAT.JK","PGAS.JK",
    "AKRA.JK","ANTM.JK","INCO.JK","TINS.JK","MEDC.JK",
    "ESSA.JK","HRUM.JK","BUMI.JK","ITMG.JK","CPIN.JK",
    "JPFA.JK","MNCN.JK","ACES.JK","MAPI.JK","LSIP.JK",
    "AALI.JK","SIMP.JK","TBIG.JK","TOWR.JK","MTEL.JK",
    "SIDO.JK","HEAL.JK","MIKA.JK","SILO.JK","BBNI.JK",
    "BNGA.JK","BJBR.JK","BTPS.JK","EMTK.JK","WIFI.JK",
]

COMMODITIES = {
    "GC=F":    "Gold (per troy oz)",
    "CL=F":    "Crude Oil WTI (per bbl)",
    "NG=F":    "Natural Gas (per MMBtu)",
    "FCPO.KL": "CPO Palm Oil (per tonne, MYR)",
    "NI=F":    "Nickel (per lb)",
    "SI=F":    "Silver (per troy oz)",
}

# ─────────────────────────────────────────────
#  STEP 1: FETCH EXCHANGE RATES
# ─────────────────────────────────────────────

def fetch_rates():
    print("  → Fetching exchange rates...")
    r = requests.get("https://open.er-api.com/v6/latest/USD", timeout=10)
    rates = r.json().get("rates", {})
    idr_per_usd = rates.get("IDR")
    idr_per_myr = (rates.get("IDR") / rates.get("MYR")) if rates.get("IDR") and rates.get("MYR") else None
    print(f"     USD/IDR: {idr_per_usd:,.0f}" if idr_per_usd else "     Exchange rate unavailable")
    return idr_per_usd, idr_per_myr


# ─────────────────────────────────────────────
#  STEP 2: FETCH STOCK & COMMODITY PRICES
# ─────────────────────────────────────────────

def fetch_prices(symbols):
    """Fetch latest price and % change for a list of symbols."""
    try:
        tickers = yf.Tickers(" ".join(symbols))
        result = {}
        for sym in symbols:
            try:
                info = tickers.tickers[sym].fast_info
                price  = getattr(info, "last_price", None)
                prev   = getattr(info, "previous_close", None)
                change = ((price - prev) / prev * 100) if price and prev else None
                currency = getattr(info, "currency", "USD")
                if price:
                    result[sym] = {"price": price, "change": change, "currency": currency}
            except Exception:
                pass
        return result
    except Exception as e:
        print(f"     Warning: {e}")
        return {}


# ─────────────────────────────────────────────
#  STEP 2b: FETCH FUNDAMENTALS (P/E, EPS, DIVIDENDS)
#  Uses yf.Ticker.info — richer but slower than fast_info.
#  Parallelised with ThreadPoolExecutor to keep runtime under ~10s.
# ─────────────────────────────────────────────

def _fetch_one(sym):
    try:
        info = yf.Ticker(sym).info
        ex_ts = info.get("exDividendDate")
        return sym, {
            "name":    info.get("longName") or info.get("shortName", ""),
            "pe":      info.get("trailingPE"),
            "eps":     info.get("trailingEps"),
            "yield":   info.get("dividendYield"),   # decimal e.g. 0.045
            "mcap":    info.get("marketCap"),
            "ex_ts":   ex_ts,                       # Unix timestamp or None
        }
    except Exception:
        return sym, {}

def fetch_fundamentals(symbols, max_workers=10):
    """Fetch P/E, EPS, dividend yield and ex-date for a list of symbols."""
    print(f"  → Fetching fundamentals for {len(symbols)} tickers (parallelised)...")
    results = {}
    with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as ex:
        for sym, data in ex.map(_fetch_one, symbols):
            if data:
                results[sym] = data
    print(f"     Got fundamentals for {len(results)} tickers")
    return results


def build_dividend_list(fundamentals, today_date, window_days=90):
    """Return upcoming ex-dividend dates within the next window_days, sorted by date."""
    cutoff = today_date + timedelta(days=window_days)
    divs = []
    for sym, d in fundamentals.items():
        ex_ts = d.get("ex_ts")
        if not ex_ts:
            continue
        try:
            ex_date = datetime.fromtimestamp(ex_ts).date()
        except Exception:
            continue
        if today_date <= ex_date <= cutoff:
            yld = d.get("yield")
            divs.append({
                "ticker":  sym.replace(".JK", ""),
                "name":    d.get("name", ""),
                "exDate":  ex_date.strftime("%-d %b"),   # e.g. "15 Apr"
                "exFull":  ex_date.isoformat(),           # for sorting
                "yield":   f"{yld*100:.1f}%" if yld else "—",
            })
    divs.sort(key=lambda x: x["exFull"])
    for d in divs:
        del d["exFull"]   # clean up sort key before writing to JSON
    return divs


def build_heatmap(stocks):
    """Build a list of {t, pct} for every stock that has a daily % change."""
    items = []
    for sym, data in stocks.items():
        if sym == "^JKSE" or data.get("change") is None:
            continue
        items.append({"t": sym.replace(".JK", ""), "pct": round(data["change"], 2)})
    return sorted(items, key=lambda x: x["pct"], reverse=True)


# ─────────────────────────────────────────────
#  STEP 3: FORMAT DATA BLOCK FOR CLAUDE
# ─────────────────────────────────────────────

def to_idr(price, currency, idr_per_usd, idr_per_myr):
    if price is None:
        return None
    if currency == "IDR":
        return price
    if currency == "USD" and idr_per_usd:
        return price * idr_per_usd
    if currency == "MYR" and idr_per_myr:
        return price * idr_per_myr
    return price * idr_per_usd if idr_per_usd else None


def fmt_idr(n):
    return f"IDR {round(n):,}" if n else "N/A"


def fmt_pct(n):
    if n is None:
        return ""
    return f" ↑{abs(n):.2f}%" if n > 0 else f" ↓{abs(n):.2f}%"


def build_data_block(stocks, commodities, idr_per_usd, idr_per_myr, fundamentals=None):
    lines = []

    if idr_per_usd:
        lines.append(f"USD/IDR: {round(idr_per_usd):,}")
    if idr_per_myr:
        lines.append(f"MYR/IDR: {round(idr_per_myr):,}")
    lines.append("")

    # IHSG
    ihsg = stocks.get("^JKSE")
    if ihsg:
        lines.append(f"IHSG: {round(ihsg['price']):,}{fmt_pct(ihsg['change'])}")

    lines.append("\nTop IDX Stocks (in IDR):")
    for sym in IDX_STOCKS[1:]:  # skip ^JKSE
        s = stocks.get(sym)
        if not s:
            continue
        ticker = sym.replace(".JK", "")
        price_idr = to_idr(s["price"], s["currency"], idr_per_usd, idr_per_myr)
        # Append P/E and yield if available
        extras = []
        if fundamentals:
            f = fundamentals.get(sym, {})
            if f.get("pe"):
                extras.append(f"P/E {f['pe']:.1f}x")
            if f.get("yield"):
                extras.append(f"yield {f['yield']*100:.1f}%")
        extra_str = f"  [{', '.join(extras)}]" if extras else ""
        lines.append(f"  {ticker}: {fmt_idr(price_idr)}{fmt_pct(s['change'])}{extra_str}")

    lines.append("\nCommodities (converted to IDR):")
    for sym, label in COMMODITIES.items():
        c = commodities.get(sym)
        if not c:
            continue
        curr = "MYR" if sym == "FCPO.KL" else "USD"
        price_idr = to_idr(c["price"], curr, idr_per_usd, idr_per_myr)
        orig = f"orig {c['currency']} {c['price']:.2f}"
        lines.append(f"  {label}: {fmt_idr(price_idr)}{fmt_pct(c['change'])} ({orig})")

    return "\n".join(lines)


# ─────────────────────────────────────────────
#  STEP 4: CALL CLAUDE
# ─────────────────────────────────────────────

def generate_with_claude(data_block, today_str):
    print("  → Calling Claude AI...")
    client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)

    prompt = f"""You are a senior financial analyst writing a daily IDX market briefing for Indonesian retail investors. Today is {today_str}.

LIVE MARKET DATA (all prices in IDR unless noted):
{data_block}

Write a comprehensive, insightful briefing using the exact figures above. ALL prices in your response must be in IDR.

Return ONLY a valid JSON object. No markdown. No preamble. No trailing text.

{{
  "headline": "One punchy headline max 10 words",
  "sentiment": "bullish",
  "macro": "• point 1\\n• point 2\\n• point 3\\n• point 4\\n• point 5",
  "sectors": "• point 1\\n• point 2\\n• point 3\\n• point 4\\n• point 5",
  "tickers": "• point 1\\n• point 2\\n• point 3\\n• point 4\\n• point 5",
  "discord": "emoji point 1\\nemoji point 2\\nemoji point 3\\nemoji point 4\\nemoji point 5",
  "trivia": "**TERM**\\nOne sentence defining it. One sentence why it matters for IDX investors today."
}}

Rules:
- sentiment: exactly "bullish", "bearish", or "neutral"
- macro: EXACTLY 5 bullets ≤18 words each, Fed/DXY/yields/China/geopolitics, use real numbers in IDR
- sectors: EXACTLY 5 bullets, "• SECTOR: outlook ↑↓→ + one reason", Banking/Consumer/Energy/Mining/Telco
- tickers: EXACTLY 5 bullets, most interesting movers, "• TICKER: IDR price change% — reason + implication"
- discord: EXACTLY 5 lines, one emoji each, plain English, 15-20 words each, covers macro/IDR/commodity/stock/outlook
- trivia: one term from today's briefing, ≤60 words total"""

    message = client.messages.create(
        model="claude-sonnet-4-20250514",
        max_tokens=2000,
        messages=[{"role": "user", "content": prompt}]
    )

    raw = message.content[0].text.strip()

    # Parse JSON
    try:
        return json.loads(raw)
    except Exception:
        import re
        m = re.search(r'\{[\s\S]*\}', raw)
        if m:
            return json.loads(m.group(0))
        raise ValueError("Could not parse Claude response as JSON")


# ─────────────────────────────────────────────
#  STEP 5: SAVE TO GITHUB GIST
#  • First run (no GIST_ID): creates a new private Gist and saves its ID
#    to .idx_gist_id so subsequent runs update the same Gist.
#  • Returns the raw URL — paste it into GIST_URL in idx-briefing-v11.jsx
#    (one-time step; after that the JSX always fetches the same URL).
# ─────────────────────────────────────────────

def _load_gist_id():
    """Return GIST_ID from env → local file → empty string, in that order."""
    if GIST_ID:
        return GIST_ID
    if os.path.exists(_GIST_ID_FILE):
        with open(_GIST_ID_FILE) as f:
            return f.read().strip()
    return ""

def _save_gist_id(gist_id):
    with open(_GIST_ID_FILE, "w") as f:
        f.write(gist_id)
    print(f"     Gist ID saved to {_GIST_ID_FILE}")

def fetch_existing_gist(gist_id):
    """Fetch the current Gist JSON so we can compare sections before overwriting."""
    if not gist_id:
        return None
    try:
        headers = {
            "Authorization": f"token {GITHUB_TOKEN}",
            "Accept": "application/vnd.github.v3+json",
        }
        r = requests.get(f"https://api.github.com/gists/{gist_id}", headers=headers, timeout=10)
        if r.status_code == 200:
            content = r.json()["files"]["idx_briefing.json"]["content"]
            return json.loads(content)
    except Exception:
        pass
    return None


def save_to_gist(briefing_data):
    print("  → Saving to GitHub Gist...")
    headers = {
        "Authorization": f"token {GITHUB_TOKEN}",
        "Accept": "application/vnd.github.v3+json",
    }
    content = json.dumps(briefing_data, ensure_ascii=False, indent=2)
    payload = {
        "description": "IDX Daily Briefing — auto-updated by generate_briefing.py",
        "public": False,
        "files": {"idx_briefing.json": {"content": content}},
    }

    gist_id = _load_gist_id()

    if gist_id:
        # ── Update existing Gist ──────────────────────────────────────────────
        r = requests.patch(
            f"https://api.github.com/gists/{gist_id}",
            headers=headers, json=payload, timeout=15,
        )
        if r.status_code != 200:
            raise Exception(f"Gist update failed: {r.status_code} — {r.text}")
    else:
        # ── Create Gist on first run ──────────────────────────────────────────
        print("     No Gist ID found — creating a new private Gist...")
        r = requests.post(
            "https://api.github.com/gists",
            headers=headers, json=payload, timeout=15,
        )
        if r.status_code != 201:
            raise Exception(f"Gist creation failed: {r.status_code} — {r.text}")
        gist_id = r.json()["id"]
        _save_gist_id(gist_id)

    raw_url = r.json()["files"]["idx_briefing.json"]["raw_url"]
    print(f"     ✓ Saved!  Gist ID : {gist_id}")
    print(f"     ✓         Raw URL : {raw_url}")
    print()
    print("  ┌─────────────────────────────────────────────────────────────────")
    print("  │  FIRST-TIME SETUP (one-time only):")
    print(f" │  Copy the Raw URL above and paste it into idx-briefing-v11.jsx:")
    print(f" │    const GIST_URL = \"{raw_url}\";")
    print("  └─────────────────────────────────────────────────────────────────")
    return raw_url


# ─────────────────────────────────────────────
#  MAIN
# ─────────────────────────────────────────────

def main():
    jakarta_tz = ZoneInfo("Asia/Jakarta")
    now = datetime.now(jakarta_tz)
    today_str = now.strftime("%A, %d %B %Y")
    print(f"\n{'='*50}")
    print(f"  IDX Briefing Generator — {today_str}")
    print(f"{'='*50}\n")

    # 1. Exchange rates
    idr_per_usd, idr_per_myr = fetch_rates()

    # 2. Stock prices
    print("  → Fetching IDX stock prices (this may take ~20s)...")
    stocks = fetch_prices(IDX_STOCKS)
    print(f"     Got {len(stocks)} stocks")

    # 3. Commodity prices
    print("  → Fetching commodity prices...")
    commodities = fetch_prices(list(COMMODITIES.keys()))
    print(f"     Got {len(commodities)} commodities")

    # 3b. Fundamentals — top 25 stocks only (P/E, EPS, div yield, ex-date)
    #     Runs in parallel so adds ~5-8s, not 25x sequential requests
    fundamentals = fetch_fundamentals(IDX_STOCKS[1:26])

    # 4. Build data block (fundamentals appended inline for Claude to use)
    data_block = build_data_block(stocks, commodities, idr_per_usd, idr_per_myr, fundamentals)

    # 5. Generate with Claude
    briefing = generate_with_claude(data_block, today_str)

    # 6. Add metadata
    briefing["date"]        = now.strftime("%Y-%m-%d")
    briefing["generatedAt"] = now.isoformat()

    # 6b. Attach dividend calendar and heatmap
    briefing["dividends"] = build_dividend_list(fundamentals, now.date())
    briefing["heatmap"]   = build_heatmap(stocks)
    print(f"     Dividends upcoming: {len(briefing['dividends'])}  |  Heatmap tickers: {len(briefing['heatmap'])}")

    # 6c. Carry forward macroDate if macro content is unchanged from last run
    existing = fetch_existing_gist(_load_gist_id())
    if existing and existing.get("macro", "").strip() == briefing.get("macro", "").strip():
        briefing["macroDate"] = existing.get("macroDate", existing.get("date", briefing["date"]))
        print(f"     ℹ  Macro unchanged → macroDate stays {briefing['macroDate']}")
    else:
        briefing["macroDate"] = briefing["date"]
        print(f"     ✓  Macro updated  → macroDate set to {briefing['macroDate']}")

    # 7. Save to Gist
    raw_url = save_to_gist(briefing)

    print(f"\n  ✓ Done! Briefing ready for {today_str}")
    print(f"  Gist raw URL: {raw_url}\n")

    return raw_url


if __name__ == "__main__":
    main()
