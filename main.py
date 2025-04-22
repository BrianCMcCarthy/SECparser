from flask import Flask, request, jsonify
import yfinance as yf
import pandas as pd
import numpy as np
import os
import fitz  # PyMuPDF
import re

app = Flask(__name__)
UPLOAD_FOLDER = "uploads"
os.makedirs(UPLOAD_FOLDER, exist_ok=True)

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

def label_source(value, source):
    return {"value": value, "source": source if value is not None else "Missing"}

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
        "Revenue": label_source(extract_latest(fin.loc["Total Revenue"]) if "Total Revenue" in fin.index else None, "Yahoo Finance"),
        "Gross Profit": label_source(extract_latest(fin.loc["Gross Profit"]) if "Gross Profit" in fin.index else None, "Yahoo Finance"),
        "SG&A": label_source(safe_extract(fin, ["Selling General Administrative", "Operating Expenses"]), "Estimated"),
        "Net Income": label_source(extract_latest(fin.loc["Net Income"]) if "Net Income" in fin.index else None, "Yahoo Finance")
    }

    rev = summary["Revenue"]["value"]
    gp = summary["Gross Profit"]["value"]
    ni = summary["Net Income"]["value"]
    sga = summary["SG&A"]["value"]

    summary["Gross Margin (%)"] = label_source(round(gp / rev * 100, 2) if gp and rev else None, "Calculated")
    summary["Net Income Margin (%)"] = label_source(round(ni / rev * 100, 2) if ni and rev else None, "Calculated")
    summary["SG&A as % of Revenue"] = label_source(round(sga / rev * 100, 2) if sga and rev else None, "Calculated")

    summary["Cash"] = label_source(safe_extract(bal, ["Cash", "Cash And Cash Equivalents"]) or safe_extract(qbal, ["Cash", "Cash And Cash Equivalents"]), "Yahoo Finance")
    summary["Total Debt"] = label_source(safe_extract(bal, ["Long Term Debt", "Total Debt"]) or safe_extract(qbal, ["Long Term Debt", "Total Debt"]), "Yahoo Finance")
    equity = safe_extract(bal, ["Total Stockholder Equity"]) or safe_extract(qbal, ["Total Stockholder Equity"])

    cash = summary["Cash"]["value"]
    debt = summary["Total Debt"]["value"]

    summary["Net Debt"] = label_source((debt - cash) if cash is not None and debt is not None else None, "Calculated")
    summary["Debt-to-Equity Ratio"] = label_source(round(debt / equity, 2) if debt and equity else None, "Calculated")

    ocf = safe_extract(cf, ["Total Cash From Operating Activities"]) or safe_extract(qcf, ["Total Cash From Operating Activities"])
    capex = safe_extract(cf, ["Capital Expenditures"]) or safe_extract(qcf, ["Capital Expenditures"])
    buybacks = safe_extract(cf, ["Repurchase Of Stock"]) or safe_extract(qcf, ["Repurchase Of Stock"])

    summary["Operating Cash Flow"] = label_source(ocf, "Estimated")
    summary["CapEx"] = label_source(capex, "Estimated")
    summary["Share Buybacks"] = label_source(buybacks, "Estimated")

    fcf = ocf + capex if ocf and capex else None
    summary["Free Cash Flow"] = label_source(fcf, "Calculated")
    summary["FCF Margin (%)"] = label_source(round(fcf / rev * 100, 2) if fcf and rev else None, "Calculated")

    summary["Revenue CAGR (%)"] = label_source(calculate_trends(fin, "Total Revenue"), "Calculated")
    summary["Net Income CAGR (%)"] = label_source(calculate_trends(fin, "Net Income"), "Calculated")
    summary["SG&A CAGR (%)"] = label_source(calculate_trends(fin, "Selling General Administrative"), "Calculated")

    return summary

@app.route("/analyze-activist", methods=["GET"])
def analyze_activist():
    ticker = request.args.get("ticker")
    peers = request.args.get("peers", "")
    peer_list = [p.strip().upper() for p in peers.split(",") if p.strip()]

    if not ticker:
        return jsonify({"error": "Missing 'ticker' parameter"}), 400

    main_summary = analyze_company(ticker.upper())
    peer_summaries = [analyze_company(p) for p in peer_list]

    result = {
        "Target Summary": main_summary,
        "Peer Comparison": peer_summaries
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

    insights = []
    main_rev = main_summary["Revenue"]["value"] or 1
    for peer in peer_summaries:
        if peer["Gross Margin (%)"]["value"] and main_summary["Gross Margin (%)"]["value"] and \
           peer["Gross Margin (%)"]["value"] > main_summary["Gross Margin (%)"]["value"] + 2:
            insights.append(f"{main_summary['Ticker']} gross margin ({main_summary['Gross Margin (%)']['value']}%) is below {peer['Ticker']} at {peer['Gross Margin (%)']['value']}%. [[SOURCE: {main_summary['Gross Margin (%)']['source']}]]")

        if peer["SG&A as % of Revenue"]["value"] and main_summary["SG&A as % of Revenue"]["value"] and \
           peer["SG&A as % of Revenue"]["value"] < main_summary["SG&A as % of Revenue"]["value"] - 2:
            insights.append(f"{main_summary['Ticker']} SG&A % of revenue ({main_summary['SG&A as % of Revenue']['value']}%) is higher than {peer['Ticker']} at {peer['SG&A as % of Revenue']['value']}%. [[SOURCE: {main_summary['SG&A as % of Revenue']['source']}]]")

        if peer["FCF Margin (%)"]["value"] and main_summary["FCF Margin (%)"]["value"] and \
           peer["FCF Margin (%)"]["value"] > main_summary["FCF Margin (%)"]["value"] + 2:
            insights.append(f"{main_summary['Ticker']} FCF margin ({main_summary['FCF Margin (%)']['value']}%) lags {peer['Ticker']} at {peer['FCF Margin (%)']['value']}%. [[SOURCE: {main_summary['FCF Margin (%)']['source']}]]")

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
{peer_summaries}

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
            "Peer Comparison": peer_summaries
        },
        "insight_summary": insights,
        "metadata": {
            "ticker": ticker.upper(),
            "peers": peer_list
        }
    })

@app.route("/upload-file", methods=["POST"])
def upload_file():
    if "file" not in request.files:
        return jsonify({"error": "No file part in request"}), 400

    file = request.files["file"]
    if file.filename == "":
        return jsonify({"error": "No selected file"}), 400

    filepath = os.path.join(UPLOAD_FOLDER, file.filename)
    file.save(filepath)

    try:
        doc = fitz.open(filepath)
        full_text = "\n".join([page.get_text() for page in doc])
        doc.close()
    except Exception as e:
        return jsonify({"error": f"Failed to process file: {str(e)}"}), 500

    keywords = [
        "Board of Directors", "Compensation Committee", "Shareholder", "Dividend",
        "BOPIS", "Loyalty", "FLX Rewards", "Private Label", "Digital", "App", "Buyback",
        "Ometria", "CDP", "Return Policy", "Omnichannel", "E-commerce"
    ]

    findings = []
    for line in full_text.split("\n"):
        for kw in keywords:
            if kw.lower() in line.lower():
                findings.append({"keyword": kw, "excerpt": line.strip()})

    board_comp = []
    known_names_pattern = re.compile(
        r"\b(Mary Dillon|Sonia Syngal|Darlene Nicosia|Tristan Walker|John Venhuizen|Ulice Payne|Virginia Drosos|Kimberly Underhill|Dona Young)\b",
        re.IGNORECASE
    )
    money_pattern = re.compile(r"\$\d{1,3}(?:,\d{3})*(?:\.\d{2})?")

    lines = full_text.split("\n")
    for i, line in enumerate(lines):
        if known_names_pattern.search(line) and money_pattern.search(line):
            name = known_names_pattern.search(line).group(0)
            comp = money_pattern.search(line).group(0)
            board_comp.append({
                "Name": name,
                "Reported Comp": comp,
                "Line": line.strip()
            })

    return jsonify({
        "filename": file.filename,
        "num_findings": len(findings),
        "keywords_matched": list(set([f["keyword"] for f in findings])),
        "excerpts": findings,
        "board_comp_table": board_comp,
        "num_comp_entries": len(board_comp)
    })

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8080))
    app.run(host="0.0.0.0", port=port)
