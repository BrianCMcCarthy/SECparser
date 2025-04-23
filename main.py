from flask import Flask, request, jsonify, send_from_directory
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
from docx.oxml import OxmlElement
from docx.oxml.ns import qn

app = Flask(__name__)
UPLOAD_FOLDER = "uploads"
os.makedirs(UPLOAD_FOLDER, exist_ok=True)

# === Analysis & Helper Functions ===
def extract_latest(series, fallback=None):
    try:
        value = series.dropna().iloc[0]
        return int(value) if float(value).is_integer() else round(float(value), 2)
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

def parse_uploaded_content():
    parsed_data = {
        "board_insights": [],
        "strategy_flags": [],
        "board_comp_table": []
    }
    try:
        for filename in os.listdir(UPLOAD_FOLDER):
            if not filename.lower().endswith(".pdf"):
                continue
            filepath = os.path.join(UPLOAD_FOLDER, filename)
            try:
                doc = fitz.open(filepath)
                text = "\n".join([page.get_text() for page in doc])
                doc.close()
            except Exception as pdf_error:
                parsed_data["board_insights"].append(f"Failed to parse {filename}: {pdf_error}")
                continue

            for line in text.split("\n"):
                if re.search(r"(?i)director compensation|total compensation|meeting fees", line):
                    parsed_data["board_insights"].append(line.strip())
                    amt = re.search(r"\$\s?\d{1,3}(?:,\d{3})*(?:\.\d{2})?", line)
                    year = re.search(r"\b(20\d{2})\b", line)
                    parsed_data["board_comp_table"].append({
                        "Name": "Unknown",
                        "Title": "Director",
                        "Amount": amt.group(0) if amt else "-",
                        "Type": "Unknown",
                        "Line": line.strip(),
                        "Year": year.group(0) if year else "N/A"
                    })
                if re.search(r"(?i)FLX Rewards|loyalty program|strategic initiative|omnichannel", line):
                    parsed_data["strategy_flags"].append(line.strip())
    except Exception as e:
        parsed_data["error"] = f"Parse error: {str(e)}"

    return parsed_data

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
        series = series.dropna().astype(float)
        if series.empty or len(series) < 2:
            return None

        fig, ax = plt.subplots()
        series[::-1].plot(kind="bar", ax=ax, color="steelblue")
        ax.set_title(title, fontsize=14, fontweight='bold')
        ax.set_ylabel("USD", fontsize=12)
        ax.set_xlabel("Date", fontsize=12)
        ax.grid(True, which='major', axis='y', linestyle='--', alpha=0.7)
        ax.legend([title], loc='upper left', fontsize=10)
        for i, v in enumerate(series[::-1]):
            ax.text(i, v, f"{v:,.0f}", ha='center', va='bottom', fontsize=8, rotation=0)
        plt.xticks(rotation=45, ha='right')
        plt.tight_layout()

        buf = io.BytesIO()
        plt.savefig(buf, format="png")
        plt.close(fig)
        buf.seek(0)
        return base64.b64encode(buf.read()).decode("utf-8")

    for label, options in chart_targets.items():
        for key in options:
            for df in [fin, cf, bal]:
                if key in df.index:
                    encoded = plot_and_encode(df.loc[key], label)
                    if encoded:
                        charts[label] = encoded
                        break
            if label in charts:
                break

    return jsonify(charts)

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
        series.plot(kind="bar", ax=ax, color="steelblue")
        ax.set_title(title, fontsize=14, fontweight='bold')
        ax.set_ylabel("USD", fontsize=12)
        ax.set_xlabel("Date", fontsize=12)
        ax.grid(True, which='major', axis='y', linestyle='--', alpha=0.7)
        ax.legend([title], loc='upper left', fontsize=10)
        for i, v in enumerate(series):
            if not pd.isna(v):
                ax.text(i, v, f"{v:,.0f}", ha='center', va='bottom', fontsize=8, rotation=0)
        plt.xticks(rotation=45, ha='right')
        plt.tight_layout()
        buf = io.BytesIO()
        plt.savefig(buf, format="png")
        plt.close(fig)
        buf.seek(0)
        return buf

    document = Document()
    document.add_heading(f"Activist Report: {ticker.upper()}", 0)

    summary = analyze_company(ticker)
    parsed = parse_uploaded_content()

    document.add_heading("Financial Highlights", level=1)
    for key in summary:
        value_entry = summary[key]
        if isinstance(value_entry, dict):
            document.add_paragraph(f"{key}: {value_entry['value']} [{value_entry['source']}]")
        else:
            document.add_paragraph(f"{key}: {value_entry}")

    document.add_heading("Charts", level=1)
    document.add_paragraph(
        "The following charts provide a visual summary of key financial trends, helping stakeholders quickly grasp financial strengths, risks, and strategic signals."
    )

    chart_targets = {
        "SG&A": ["Selling General Administrative", "Operating Expenses"],
        "Net Income": ["Net Income"],
        "Long Term Debt": ["Long Term Debt"],
        "Share Buybacks": ["Repurchase Of Stock"]
    }
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

    document.add_heading("Governance & Board Review", level=1)
    board_table = parsed.get("board_comp_table", [])
    if board_table:
        table = document.add_table(rows=1, cols=6)
        hdr_cells = table.rows[0].cells
        for idx, title in enumerate(["Name", "Title", "Amount", "Type", "Line", "Year"]):
            hdr_cells[idx].text = title
        for entry in board_table:
            row = table.add_row().cells
            row[0].text = entry.get("Name", "")
            row[1].text = entry.get("Title", "")
            row[2].text = entry.get("Amount", "")
            row[3].text = entry.get("Type", "")
            row[4].text = entry.get("Line", "")
            row[5].text = entry.get("Year", "")
    else:
        document.add_paragraph("No relevant board compensation disclosures found in uploaded materials.")

    document.add_heading("Strategic Positioning Flags", level=1)
    if parsed.get("strategy_flags"):
        for item in parsed["strategy_flags"]:
            document.add_paragraph(item)
    else:
        document.add_paragraph("No strategic initiative references found.")

    file_path = os.path.join(UPLOAD_FOLDER, f"{ticker}_activist_report.docx")
    document.save(file_path)
    return jsonify({"doc_path": file_path})

@app.route("/generate-brief", methods=["GET"])
def generate_brief():
    ticker = request.args.get("ticker")
    peers = request.args.get("peers", "")
    peer_list = [p.strip().upper() for p in peers.split(",") if p.strip()]

    if not ticker:
        return jsonify({"error": "Missing 'ticker' parameter"}), 400

    from analyze_company import analyze_company  # ensure this is imported
    main_summary = analyze_company(ticker.upper())
    peer_summaries = [analyze_company(p) for p in peer_list]
    parsed = parse_uploaded_content()

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

    if parsed.get("strategy_flags"):
        insights.append("\nStrategic initiatives referenced in uploaded materials:")
        for line in parsed["strategy_flags"]:
            insights.append(f"- {line}")

    if parsed.get("board_insights"):
        insights.append("\nBoard governance references in uploaded materials:")
        for line in parsed["board_insights"]:
            insights.append(f"- {line}")

    return jsonify({
        "ticker": ticker,
        "executive_summary": insights,
        "parsed_files": parsed,
        "main_summary": main_summary,
        "peer_summaries": peer_summaries
    })

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

    from analyze_company import analyze_company  # Ensure consistent import reference
    data = analyze_company(ticker.upper())

    if "error" in data:
        return jsonify(data), 500

    return jsonify({"ticker": ticker, "irr_table": data.get("IRR Table", [])})

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

@app.route("/uploads/<path:filename>", methods=["GET"])
def download_file(filename):
    return send_from_directory(UPLOAD_FOLDER, filename, as_attachment=True)


def generate_longform_prompt(summary, peers, insights, parsed):
    prompt = f"""
### ACTIVIST REPORT: {summary['Company']} ({summary['Ticker']}) ###

1. Executive Summary
Write a 1000-word overview summarizing:
- Underperformance
- Strategic gaps
- Governance risk
- Capital efficiency
- Peer deltas
- Opportunities for shareholder value creation

2. Financial Forensics
Include full summary and peer benchmarks.

3. Capital Allocation Review
Comment on cash, debt, buybacks, CapEx, and IRR table.

4. Strategic Positioning
Pull language from parsed['strategy_flags'].

5. Operational Execution
Comment on trends in SG&A, margins, cost structure.

6. Governance & Board Review
Use parsed['board_insights'] and parsed['board_comp_table'].

7. Brand & Customer Health
Look for loyalty programs, customer metrics, channel mix.

8. Risk Heatmap
Create a list of known risks or omissions in disclosures.

9. Activist Playbook (5 Actions)
Recommend 5 bold but credible actions for shareholder value.

10. Appendix
Include IRR table, raw financials, and peer stack.

### CONTEXT DATA ###
MAIN SUMMARY:
{summary}

PEER SUMMARIES:
{peers}

INSIGHTS:
{insights}

PARSED FILE EXCERPTS:
{parsed}
"""
    return prompt

@app.route("/generate-prompt", methods=["GET"])
def generate_prompt():
    ticker = request.args.get("ticker")
    peers = request.args.get("peers", "")
    peer_list = [p.strip().upper() for p in peers.split(",") if p.strip()]

    if not ticker:
        return jsonify({"error": "Missing 'ticker' parameter"}), 400

    main_summary = analyze_company(ticker.upper())
    peer_summaries = [analyze_company(p) for p in peer_list]
    parsed = parse_uploaded_content()

    insights = []
    main_rev = main_summary["Revenue"]["value"] or 1
    for peer in peer_summaries:
        if peer["Gross Margin (%)"]["value"] and main_summary["Gross Margin (%)"]["value"] and \
           peer["Gross Margin (%)"]["value"] > main_summary["Gross Margin (%)"]["value"] + 2:
            insights.append(f"{main_summary['Ticker']} gross margin ({main_summary['Gross Margin (%)']['value']}%) is below {peer['Ticker']} at {peer['Gross Margin (%)']['value']}%.")

        if peer["SG&A as % of Revenue"]["value"] and main_summary["SG&A as % of Revenue"]["value"] and \
           peer["SG&A as % of Revenue"]["value"] < main_summary["SG&A as % of Revenue"]["value"] - 2:
            insights.append(f"{main_summary['Ticker']} SG&A % of revenue ({main_summary['SG&A as % of Revenue']['value']}%) is higher than {peer['Ticker']} at {peer['SG&A as % of Revenue']['value']}%.")

        if peer["FCF Margin (%)"]["value"] and main_summary["FCF Margin (%)"]["value"] and \
           peer["FCF Margin (%)"]["value"] > main_summary["FCF Margin (%)"]["value"] + 2:
            insights.append(f"{main_summary['Ticker']} FCF margin ({main_summary['FCF Margin (%)']['value']}%) lags {peer['Ticker']} at {peer['FCF Margin (%)']['value']}%.")

    full_prompt = generate_longform_prompt(main_summary, peer_summaries, insights, parsed)
    return jsonify({"prompt": full_prompt})

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8080))
    app.run(host="0.0.0.0", port=port)
