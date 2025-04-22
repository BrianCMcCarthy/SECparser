from flask import Flask, request, jsonify
import yfinance as yf
import pandas as pd
import numpy as np
import os

app = Flask(__name__)

def fetch_all_data(ticker):
    stock = yf.Ticker(ticker)
    info = stock.info
    fin = stock.financials
    bal = stock.balance_sheet
    cf = stock.cashflow
    return stock, info, fin, bal, cf

def extract_latest(series, fallback=None):
    try:
        return int(series.dropna().iloc[0])
    except:
        return fallback

def calculate_trends(df, line_item):
    if line_item not in df.index:
        return None
    values = df.loc[line_item].dropna().astype(float)
    if len(values) < 2:
        return None
    cagr = ((values[0] / values[-1]) ** (1 / (len(values) - 1)) - 1) * 100
    return round(cagr, 2)

def analyze_company(ticker):
    stock, info, fin, bal, cf = fetch_all_data(ticker)
    name = info.get("longName", ticker)
    summary = {"Company": name, "Ticker": ticker.upper()}

    # Income statement
    revenue = extract_latest(fin.loc["Total Revenue"]) if "Total Revenue" in fin.index else None
    gross = extract_latest(fin.loc["Gross Profit"]) if "Gross Profit" in fin.index else None
    sga = extract_latest(fin.loc["Selling General Administrative"]) if "Selling General Administrative" in fin.index else extract_latest(fin.loc["Operating Expenses"]) if "Operating Expenses" in fin.index else None
    net = extract_latest(fin.loc["Net Income"]) if "Net Income" in fin.index else None

    summary["Revenue"] = revenue
    summary["Gross Profit"] = gross
    summary["SG&A"] = sga
    summary["Net Income"] = net

    if revenue:
        summary["Gross Margin (%)"] = round(gross / revenue * 100, 2) if gross else None
        summary["Net Income Margin (%)"] = round(net / revenue * 100, 2) if net else None
        summary["SG&A as % of Revenue"] = round(sga / revenue * 100, 2) if sga else None

    # Balance Sheet
    cash = extract_latest(bal.loc["Cash"]) if "Cash" in bal.index else None
    debt = extract_latest(bal.loc["Long Term Debt"]) if "Long Term Debt" in bal.index else None
    equity = extract_latest(bal.loc["Total Stockholder Equity"]) if "Total Stockholder Equity" in bal.index else None

    summary["Cash"] = cash
    summary["Total Debt"] = debt
    summary["Net Debt"] = debt - cash if debt is not None and cash is not None else None
    summary["Debt-to-Equity Ratio"] = round(debt / equity, 2) if debt and equity else None

    # Cash Flow
    ocf = extract_latest(cf.loc["Total Cash From Operating Activities"]) if "Total Cash From Operating Activities" in cf.index else None
    capex = extract_latest(cf.loc["Capital Expenditures"]) if "Capital Expenditures" in cf.index else None
    buybacks = extract_latest(cf.loc["Repurchase Of Stock"]) if "Repurchase Of Stock" in cf.index else None

    fcf = ocf + capex if ocf and capex else None  # CapEx is negative

    summary["Operating Cash Flow"] = ocf
    summary["CapEx"] = capex
    summary["Free Cash Flow"] = fcf
    summary["FCF Margin (%)"] = round(fcf / revenue * 100, 2) if fcf and revenue else None
    summary["Share Buybacks"] = buybacks

    # Trends
    summary["Revenue CAGR (%)"] = calculate_trends(fin, "Total Revenue")
    summary["Net Income CAGR (%)"] = calculate_trends(fin, "Net Income")
    summary["SG&A CAGR (%)"] = calculate_trends(fin, "Selling General Administrative")

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

        # Benchmarks vs main
        if peer.get("Gross Margin (%)") and main_summary.get("Gross Margin (%)") and peer["Gross Margin (%)"] > main_summary["Gross Margin (%)"] + 2:
            insights.append(f"{main_summary['Ticker']}'s gross margin ({main_summary['Gross Margin (%)']}%) is trailing {peer['Ticker']}'s {peer['Gross Margin (%)']}%.")
        if peer.get("SG&A as % of Revenue") and main_summary.get("SG&A as % of Revenue") and peer["SG&A as % of Revenue"] < main_summary["SG&A as % of Revenue"] - 2:
            insights.append(f"{main_summary['Ticker']}'s SG&A is {main_summary['SG&A as % of Revenue']}% of revenue — higher than {peer['Ticker']} at {peer['SG&A as % of Revenue']}%.")
        if peer.get("FCF Margin (%)") and main_summary.get("FCF Margin (%)") and peer["FCF Margin (%)"] > main_summary["FCF Margin (%)"] + 3:
            insights.append(f"{main_summary['Ticker']}'s FCF margin ({main_summary['FCF Margin (%)']}%) lags behind {peer['Ticker']} at {peer['FCF Margin (%)']}%.")

    # Capital structure insights
    if main_summary.get("Cash") and main_summary.get("Total Debt"):
        if main_summary["Cash"] > main_summary["Total Debt"]:
            insights.append(f"{main_summary['Ticker']} is over-capitalized with ${main_summary['Cash']:,} in cash and less debt — suggesting potential for buybacks or dividends.")
        elif main_summary["Net Debt"] and main_summary["Net Debt"] / main_rev > 0.3:
            insights.append(f"{main_summary['Ticker']}'s net debt is {round(main_summary['Net Debt']/main_rev*100,2)}% of revenue — potential leverage concern.")

    if main_summary.get("Share Buybacks") and main_summary.get("Revenue CAGR (%)") is not None and main_summary["Revenue CAGR (%)"] < 2:
        insights.append(f"{main_summary['Ticker']} is returning capital to shareholders via buybacks (${main_summary['Share Buybacks']:,}) despite sluggish revenue growth ({main_summary['Revenue CAGR (%)']}%).")

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

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8080))
    app.run(host="0.0.0.0", port=port)
