from flask import Flask, request, jsonify
import yfinance as yf
import pandas as pd
import numpy as np
import os
import fitz  # PyMuPDF
import re
from typing import List, Dict
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import io
import base64
from docx import Document
from docx.shared import Inches

app = Flask(__name__)
UPLOAD_FOLDER = "uploads"
os.makedirs(UPLOAD_FOLDER, exist_ok=True)

# === Analysis & Helper Functions ===
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
    try:
        stock = yf.Ticker(ticker)
        info = stock.info if stock.info else {}
        fin = stock.financials if not stock.financials.empty else pd.DataFrame()
        bal = stock.balance_sheet if not stock.balance_sheet.empty else pd.DataFrame()
        cf = stock.cashflow if not stock.cashflow.empty else pd.DataFrame()
        qbal = stock.quarterly_balance_sheet if not stock.quarterly_balance_sheet.empty else pd.DataFrame()
        qcf = stock.quarterly_cashflow if not stock.quarterly_cashflow.empty else pd.DataFrame()
    except Exception as e:
        return {"error": f"Yahoo Finance failed for {ticker}: {str(e)}"}

    name = info.get("longName", ticker)

    def get_val(df, label, fallback_label=None):
        try:
            if label in df.index:
                return extract_latest(df.loc[label])
            if fallback_label and fallback_label in df.index:
                return extract_latest(df.loc[fallback_label])
        except Exception:
            return None
        return None

    summary = {
        "Company": name,
        "Ticker": ticker.upper(),
        "Revenue": label_source(get_val(fin, "Total Revenue"), "Yahoo Finance"),
        "Gross Profit": label_source(get_val(fin, "Gross Profit"), "Yahoo Finance"),
        "SG&A": label_source(get_val(fin, "Selling General Administrative", "Operating Expenses"), "Estimated"),
        "Net Income": label_source(get_val(fin, "Net Income"), "Yahoo Finance")
    }

    rev = summary["Revenue"]["value"]
    gp = summary["Gross Profit"]["value"]
    ni = summary["Net Income"]["value"]
    sga = summary["SG&A"]["value"]

    summary["Gross Margin (%)"] = label_source(round(gp / rev * 100, 2) if gp and rev else None, "Calculated")
    summary["Net Income Margin (%)"] = label_source(round(ni / rev * 100, 2) if ni and rev else None, "Calculated")
    summary["SG&A as % of Revenue"] = label_source(round(sga / rev * 100, 2) if sga and rev else None, "Calculated")

    cash = get_val(bal, "Cash") or get_val(qbal, "Cash")
    debt = get_val(bal, "Long Term Debt", "Total Debt") or get_val(qbal, "Long Term Debt", "Total Debt")
    equity = get_val(bal, "Total Stockholder Equity") or get_val(qbal, "Total Stockholder Equity")

    summary["Cash"] = label_source(cash, "Yahoo Finance")
    summary["Total Debt"] = label_source(debt, "Yahoo Finance")
    summary["Net Debt"] = label_source(debt - cash if debt and cash else None, "Calculated")
    summary["Debt-to-Equity Ratio"] = label_source(round(debt / equity, 2) if debt and equity else None, "Calculated")

    ocf = get_val(cf, "Total Cash From Operating Activities") or get_val(qcf, "Total Cash From Operating Activities")
    capex = get_val(cf, "Capital Expenditures") or get_val(qcf, "Capital Expenditures")
    buybacks = get_val(cf, "Repurchase Of Stock") or get_val(qcf, "Repurchase Of Stock")

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

    excerpts = []
    for line in full_text.split("\n"):
        for kw in keywords:
            if kw.lower() in line.lower():
                excerpts.append({"keyword": kw, "excerpt": line.strip()})

    board_comp_table = extract_board_comp_table(full_text)

    return jsonify({
        "filename": file.filename,
        "excerpts": excerpts,
        "board_comp_table": board_comp_table,
        "keywords_matched": list(set([e["keyword"] for e in excerpts])),
        "num_findings": len(excerpts),
        "num_comp_entries": len(board_comp_table)
    })

def extract_board_comp_table(text: str) -> List[Dict[str, str]]:
    comp_entries = []
    pattern = r"(?i)(?:[\$€¥£]\s?[\d{1,3},]*\d{1,3}(?:\.\d{1,2})?)"
    lines = text.split("\n")
    for line in lines:
        matches = re.findall(pattern, line)
        for match in matches:
            normalized = match.replace(",", "").replace(" ", "")
            try:
                value = float(re.sub(r"[^\d.]", "", normalized))
                if value >= 1 and value <= 20000000:
                    comp_entries.append({
                        "Line": line.strip(),
                        "Reported Comp": f"${int(value) if value.is_integer() else round(value, 2)}"
                    })
            except:
                continue
    return comp_entries

@app.route("/generate-charts", methods=["GET"])
def generate_charts():
    ticker = request.args.get("ticker")
    if not ticker:
        return jsonify({"error": "Missing 'ticker' parameter"}), 400

    stock = yf.Ticker(ticker)
    fin = stock.financials
    cf = stock.cashflow
    bal = stock.balance_sheet

    charts = {}

    chart_targets = {
        "SG&A": ["Selling General Administrative", "Operating Expenses"],
        "Net Income": ["Net Income"],
        "Long Term Debt": ["Long Term Debt"],
        "Share Buybacks": ["Repurchase Of Stock"],
        "CapEx": ["Capital Expenditures"],
        "Operating Cash Flow": ["Total Cash From Operating Activities"],
        "Revenue": ["Total Revenue"]
    }

    def plot_and_encode(series, title):
        fig, ax = plt.subplots()
        series = series.dropna().astype(float)
        if series.empty:
            return None
        series[::-1].plot(kind="bar", ax=ax)
        ax.set_title(title)
        ax.set_ylabel("USD")
        ax.set_xlabel("Date")
        plt.tight_layout()
        buf = io.BytesIO()
        plt.savefig(buf, format="png")
        plt.close(fig)
        buf.seek(0)
        return base64.b64encode(buf.read()).decode("utf-8")

    for label, options in chart_targets.items():
        found = False
        for key in options:
            for df_name, df in [("financials", fin), ("cashflow", cf), ("balance_sheet", bal)]:
                if key in df.index:
                    encoded = plot_and_encode(df.loc[key], f"{label} ({df_name})")
                    if encoded:
                        charts[label] = encoded
                        found = True
                        break
            if found:
                break

    return jsonify(charts)

try:
        market_cap = info.get("marketCap", None)
        if market_cap and fcf and fcf != 0:
            irr_table = []
            hold_period = 3
            for multiple in range(8, 13):
                exit_ev = multiple * fcf
                entry_ev = market_cap + (debt or 0) - (cash or 0)
                irr = ((exit_ev / entry_ev) ** (1 / hold_period) - 1) * 100 if entry_ev > 0 else None
                irr_table.append({"Exit EV/FCF": multiple, "IRR (%)": round(irr, 2) if irr else None})
            summary["IRR Table"] = irr_table
    except Exception as e:
        summary["IRR Table"] = f"Error calculating IRR: {str(e)}"

    return summary

@app.route("/generate-irr", methods=["GET"])
def generate_irr():
    ticker = request.args.get("ticker")
    if not ticker:
        return jsonify({"error": "Missing 'ticker' parameter"}), 400
    data = analyze_company(ticker)
    if "error" in data:
        return jsonify(data), 500
    return jsonify({"ticker": ticker, "irr_table": data.get("IRR Table", [])})

@app.route("/uploads/<path:filename>", methods=["GET"])
def download_file(filename):
    return send_from_directory(UPLOAD_FOLDER, filename, as_attachment=True)

# === DOCX GENERATION ===
@app.route("/generate-docx", methods=["GET"])
def generate_docx():
    ticker = request.args.get("ticker")
    if not ticker:
        return jsonify({"error": "Missing 'ticker' parameter"}), 400

    stock = yf.Ticker(ticker)
    fin = stock.financials
    cf = stock.cashflow
    bal = stock.balance_sheet

    def plot_series_to_img(series, title):
        fig, ax = plt.subplots()
        series = series.dropna().astype(float)
        if series.empty:
            return None
        series.plot(kind="bar", ax=ax)
        ax.set_title(title)
        plt.tight_layout()
        buf = io.BytesIO()
        plt.savefig(buf, format="png")
        plt.close(fig)
        buf.seek(0)
        return buf

    document = Document()
    document.add_heading(f"Activist Report: {ticker.upper()}", 0)

    # Section: Financial Summary
    summary = analyze_company(ticker)
    document.add_heading("Financial Highlights", level=1)
    for key in summary:
        value_entry = summary[key]
        if isinstance(value_entry, dict):
            document.add_paragraph(f"{key}: {value_entry['value']} [{value_entry['source']}]")
        else:
            document.add_paragraph(f"{key}: {value_entry}")

    # Section: Charts
    chart_targets = {
        "SG&A": ["Selling General Administrative", "Operating Expenses"],
        "Net Income": ["Net Income"],
        "Long Term Debt": ["Long Term Debt"],
        "Share Buybacks": ["Repurchase Of Stock"]
    }

    document.add_heading("Charts", level=1)
    for label, fields in chart_targets.items():
        for key in fields:
            if key in fin.index:
                buf = plot_series_to_img(fin.loc[key], label)
            elif key in cf.index:
                buf = plot_series_to_img(cf.loc[key], label)
            elif key in bal.index:
                buf = plot_series_to_img(bal.loc[key], label)
            else:
                continue

            if buf:
                document.add_heading(label, level=2)
                document.add_picture(buf, width=Inches(6))
                break

    # Save file
    file_path = os.path.join(UPLOAD_FOLDER, f"{ticker}_activist_report.docx")
    document.save(file_path)

    return jsonify({"doc_path": file_path})

from flask import send_from_directory

@app.route("/uploads/<path:filename>", methods=["GET"])
def download_file(filename):
    return send_from_directory(UPLOAD_FOLDER, filename, as_attachment=True)

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8080))
    app.run(host="0.0.0.0", port=port)
