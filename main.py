from flask import Flask, request, jsonify
import yfinance as yf
import pandas as pd
import numpy as np
import os

app = Flask(__name__)

def extract_latest(series, fallback=None):
    try:
        return int(series.dropna().iloc[0])
    except:
        return fallback

def safe_extract(df, labels):
    for label in labels:
        if label in df.index:
            return extract_latest(df.loc[label])
    return None

def calculate_trends(df, line_item):
    if line_item not in df.index:
        return None
    values = df.loc[line_item].dropna().astype(float)
    if len(values) < 2:
        return None
    cagr = ((values[0] / values[-1]) ** (1 / (len(values) - 1)) - 1) * 100
    return round(cagr, 2)

def analyze_company(ticker):
    stock = yf.Ticker(ticker)
    info = stock.info
    name = info.get("longName", ticker)
    fin = stock.financials
    bal = stock.balance_sheet
    cf = stock.cashflow
    qbal = stock.quarterly_balance_sheet
    qcf = stock.quarterly_cashflow

    summary = {
        "Company": name,
        "Ticker": ticker.upper(),
        "Revenue": extract_latest(fin.loc["Total Revenue"]) if "Total Revenue" in fin.index else None,
        "Gross Profit": extract_latest(fin.loc["Gross Profit"]) if "Gross Profit" in fin.index else None,
        "SG&A": safe_extract(fin, ["Selling General Administrative", "Operating Expenses"]),
        "Net Income": extract_latest(fin.loc["Net Income"]) if "Net Income" in fin.index else None
    }

    rev = summary["Revenue"]
    if rev:
        summary["Gross Margin (%)"] = round(summary["Gross Profit"] / rev * 100, 2) if summary["Gross Profit"] else None
        summary["Net Income Margin (%)"] = round(summary["Net Income"] / rev * 100, 2) if summary["Net Income"] else None
        summary["SG&A as % of Revenue"] = round(summary["SG&A"] / rev * 100, 2) if summary["SG&A"] else None

    summary["Cash"] = safe_extract(bal, ["Cash", "Cash And Cash Equivalents"]) or safe_extract(qbal, ["Cash", "Cash And Cash Equivalents"])
    summary["Total Debt"] = safe_extract(bal, ["Long Term Debt", "Total Debt"]) or safe_extract(qbal, ["Long Term Debt", "Total Debt"])
    equity = safe_extract(bal, ["Total Stockholder Equity"]) or safe_extract(qbal, ["Total Stockholder Equity"])

    if summary["Cash"] is not None and summary["Total Debt"] is not None:
        summary["Net Debt"] = summary["Total Debt"] - summary["Cash"]
    else:
        summary["Net Debt"] = "Data not available"

    if summary["Total Debt"] and equity:
        summary["Debt-to-Equity Ratio"] = round(summary["Total Debt"] / equity, 2)
    else:
        summary["Debt-to-Equity Ratio"] = "Data not available"

    ocf = safe_extract(cf, ["Total Cash From Operating Activities"]) or safe_extract(qcf, ["Total Cash From Operating Activities"])
    capex = safe_extract(cf, ["Capital Expenditures"]) or safe_extract(qcf, ["Capital Expenditures"])
    buybacks = safe_extract(cf, ["Repurchase Of Stock"]) or safe_extract(qcf, ["Repurchase Of Stock"])

    summary["Operating Cash Flow"] = ocf or "Data not available"
    summary["CapEx"] = capex or "Data not available"
    summary["Share Buybacks"] = buybacks or "Data not available"

    if ocf and capex:
        fcf = ocf + capex
        summary["Free Cash Flow"] = fcf
        summary["FCF Margin (%)"] = round(fcf / rev * 100, 2) if rev else "Data not available"
    else:
        summary["Free Cash Flow"] = "Data not available"
        summary["FCF Margin (%)"] = "Data not available"

    summary["Revenue CAGR (%)"] = calculate_trends(fin, "Total Revenue")
    summary["Net Income CAGR (%)"] = calculate_trends(fin, "Net Income")
    summary["SG&A CAGR (%)"] = calculate_trends(fin, "Selling General Administrative")

    for k, v in summary.items():
        if v is None:
            summary[k] = "Data not available"

    return summary

def compare_to_peers(main_summary, peer_summaries):
    comparison = []
    insights = []
    main_rev = main_summary.get("Revenue", 1)

    for peer in peer_summaries:
        if not peer: continue
        comparison.append({
            "Ticker": peer["Ticker"],
            "Revenue": peer.get("Revenue"),
            "Gross Margin (%)": peer.get("Gross Margin (%)"),
            "SG&A as % of Revenue": peer.get("SG&A as % of Revenue"),
            "Net Income Margin (%)": peer.get("Net Income Margin (%)"),
            "FCF Margin (%)": peer.get("FCF Margin (%)"),
            "Debt-to-Equity Ratio": peer.get("Debt-to-Equity Ratio")
        })

        try:
            if (gm := peer.get("Gross Margin (%)")) != "Data not available" and \
               (mgm := main_summary.get("Gross Margin (%)")) != "Data not available" and \
               gm > mgm + 2:
                insights.append(f"{main_summary['Ticker']}'s gross margin ({mgm}%) is trailing {peer['Ticker']}'s {gm}%.")

            if (psga := peer.get("SG&A as % of Revenue")) != "Data not available" and \
               (msga := main_summary.get("SG&A as % of Revenue")) != "Data not available" and \
               psga < msga - 2:
                insights.append(f"{main_summary['Ticker']}'s SG&A is {msga}% of revenue — higher than {peer['Ticker']} at {psga}%.")

            if (pfcf := peer.get("FCF Margin (%)")) != "Data not available" and \
               (mfcf := main_summary.get("FCF Margin (%)")) != "Data not available" and \
               pfcf > mfcf + 3:
                insights.append(f"{main_summary['Ticker']}'s FCF margin ({mfcf}%) lags behind {peer['Ticker']} at {pfcf}%.")

        except:
            continue

    if main_summary.get("Cash") != "Data not available" and main_summary.get("Total Debt") != "Data not available":
        if main_summary["Cash"] > main_summary["Total Debt"]:
            insights.append(f"{main_summary['Ticker']} has excess cash (${main_summary['Cash']:,}) relative to debt (${main_summary['Total Debt']:,}) — consider returning capital.")
        elif isinstance(main_summary["Net Debt"], (int, float)) and main_summary["Net Debt"] / main_rev > 0.3:
            ratio = round(main_summary["Net Debt"]/main_rev*100,2)
            insights.append(f"{main_summary['Ticker']}'s net debt is {ratio}% of revenue — potential leverage concern.")

    if main_summary.get("Share Buybacks") != "Data not available" and \
       isinstance(main_summary.get("Revenue CAGR (%)"), (int, float)) and \
       main_summary["Revenue CAGR (%)"] < 2:
        insights.append(f"{main_summary['Ticker']} is buying back shares (${main_summary['Share Buybacks']:,}) despite slow revenue growth ({main_summary['Revenue CAGR (%)']}%).")

    return comparison, insights

@app.route("/analyze-activist", methods=["GET"])
def analyze_activist():
    ticker = request.args.get("ticker")
    peers = request.args.get("peers", "")
    peer_list = [p.strip().upper() for p in peers.split(",") if p.strip()]

    if not ticker:
        return jsonify({"error": "Missing 'ticker' parameter"}), 400

    main_summary = analyze_company(ticker.upper())
    peer_summaries = [analyze_company(p) for p in peer_list]
    comparison, insights = compare_to_peers(main_summary, peer_summaries)

    result = {
        "Target Summary": main_summary,
        "Peer Comparison": comparison,
        "Strategic Insights": insights
    }
    return jsonify(result)

@app.route("/generate-brief", methods=["GET"])
def generate_brief():
    ticker = request.args.get("ticker")
    peers = request.args.get("peers", "")
    peer_list = [p.strip().upper() for p in peers.split(",") if p.strip()]

    if not ticker:
        return jsonify({"error": "Missing 'ticker' parameter"}), 400

    main_summary = analyze_company(ticker.upper())
    peer_summaries = [analyze_company(p) for p in peer_list]
    comparison, insights = compare_to_peers(main_summary, peer_summaries)

    exec_summary = (
        f"As an activist investor evaluating {main_summary['Company']} ({ticker.upper()}), "
        f"your goal is to identify underperformance, strategic misalignment, governance risk, and capital inefficiency. "
        f"You are comparing against: {', '.join(peer_list)}.\n\n"
        f"Start with a narrative-style Executive Summary highlighting:\n"
        "- Core performance issues\n"
        "- Peer benchmarking gaps\n"
        "- Leverage or balance sheet flags\n"
        "- Capital allocation behavior (e.g., buybacks vs growth)\n"
        "- Strategic or governance vulnerabilities\n\n"
        f"Use the data below as inputs:"
    )

    prompt = f"""
## EXECUTIVE SUMMARY
{exec_summary}

## FINANCIAL HIGHLIGHTS
Target Company:
{main_summary}

Peer Comparison:
{comparison}

Strategic Flags:
{insights}

## FORMAT REQUIREMENTS
- Investor-grade tone (not conversational)
- Double-spaced text, structured for Word export
- Bold or italicize key inflection points
- Use citations when referencing uploaded 10-K or DEF 14A data (if available)
- Label assumptions or inferred data clearly
- Structure output using these sections:
  1. Executive Summary
  2. Financial Forensics
  3. Capital Allocation Review
  4. Strategic Positioning
  5. Operational Execution
  6. Governance & Board Review
  7. Brand & Customer Health
  8. Risk Heatmap
  9. Activist Playbook (5 actions)
  10. Appendix (optional peer stack, comps, models)
    """.strip()

    return jsonify({
        "prompt": prompt,
        "context_data": {
            "Target Summary": main_summary,
            "Peer Comparison": comparison
        },
        "insight_summary": insights,
        "metadata": {
            "ticker": ticker.upper(),
            "peers": peer_list
        }
    })

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8080))
    app.run(host="0.0.0.0", port=port)
