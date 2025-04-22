from flask import Flask, request, jsonify
import requests
from lxml import etree
import re
import os

app = Flask(__name__)

TARGET_TAGS = {
    "us-gaap:revenues": "Revenue",
    "us-gaap:salesrevenuenet": "Revenue",
    "us-gaap:grossprofit": "Gross Profit",
    "us-gaap:sellinggeneralandadministrativeexpense": "SG&A",
    "us-gaap:sellingandmarketingexpense": "SG&A_Component",
    "us-gaap:generalandadministrativeexpense": "SG&A_Component",
    "us-gaap:netincomeloss": "Net Income",
    "us-gaap:profitloss": "Net Income",
    "us-gaap:incomelossfromcontinuingoperationsbeforeincometaxesextraordinaryitemsnoncontrollinginterest": "Pretax Income"
}

def get_latest_filing_url(ticker, form_type="10-K"):
    cik_url = f"https://www.sec.gov/files/company_tickers.json"
    headers = {
        "User-Agent": "BrianSECParser/1.0 (your@email.com)"
    }
    cik_data = requests.get(cik_url, headers=headers).json()

    ticker_upper = ticker.upper()
    matched = [entry for entry in cik_data.values() if entry["ticker"] == ticker_upper]
    if not matched:
        return None

    cik_str = str(matched[0]["cik_str"]).zfill(10)

    filings_url = f"https://data.sec.gov/submissions/CIK{cik_str}.json"
    filings_resp = requests.get(filings_url, headers=headers).json()

    recent_filings = filings_resp.get("filings", {}).get("recent", {})
    accession_numbers = recent_filings.get("accessionNumber", [])
    forms = recent_filings.get("form", [])
    doc_names = recent_filings.get("primaryDocument", [])

    for form, acc_num, doc_name in zip(forms, accession_numbers, doc_names):
        if form == form_type:
            acc_num_clean = acc_num.replace("-", "")
            doc_url = f"https://www.sec.gov/Archives/edgar/data/{int(cik_str)}/{acc_num_clean}/{doc_name}"
            return doc_url

    return None

@app.route("/parse", methods=["GET"])
def parse_sec_filing():
    sec_url = request.args.get("url")
    if not sec_url:
        return jsonify({"error": "Missing SEC filing URL"}), 400

    try:
        headers = {
            "User-Agent": "BrianSECParser/1.0 (youremail@example.com)",
            "Accept-Encoding": "gzip, deflate",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Connection": "keep-alive"
        }

        resp = requests.get(sec_url, headers=headers, timeout=20)
        resp.raise_for_status()
        tree = etree.HTML(resp.content)

        namespaces = {'ix': 'http://www.xbrl.org/2013/inlineXBRL'}
        tags = tree.xpath("//ix:nonFraction", namespaces=namespaces)

        values_by_tag = {}
        for tag in tags:
            name = tag.attrib.get("name", "").lower()
            context = tag.attrib.get("contextRef", "").lower()
            value = tag.text.strip() if tag.text else ""
            value = value.replace(",", "")

            if value.startswith("(") and value.endswith(")"):
                value = "-" + value[1:-1]

            try:
                float_val = float(value)
                # Prioritize contexts that are likely consolidated and recent
                if any(kw in context for kw in ["current", "year", "q4", "duration", "consolidated"]):
                    key = (name, context)
                    if name in TARGET_TAGS:
                        values_by_tag[key] = float_val
            except:
                continue

        # Build result dictionary
        extracted = {
            "Filing URL": sec_url,
            "Revenue": "Not found",
            "Gross Profit": "Not found",
            "SG&A": "Not found",
            "Net Income": "Not found"
        }

        # Pick the highest value for each field (simple fallback)
        for (tag_name, _), val in values_by_tag.items():
            field = TARGET_TAGS.get(tag_name)
            if field and extracted[field] == "Not found":
                extracted[field] = str(val)

        return jsonify(extracted)

    except requests.exceptions.RequestException as e:
        return jsonify({"error": f"Request failed: {e}"}), 500
    except etree.XMLSyntaxError as e:
        return jsonify({"error": f"HTML parsing failed: {e}"}), 500
    except Exception as e:
        print(f"Unexpected error: {e}")
        return jsonify({"error": str(e)}), 500

@app.route("/analyze", methods=["GET"])
def analyze():
    ticker = request.args.get("ticker")
    if not ticker:
        return jsonify({"error": "Missing ticker"}), 400

    filing_url = get_latest_filing_url(ticker)
    if not filing_url:
        return jsonify({"error": f"No 10-K filing found for {ticker}"}), 404

    try:
        # Trick to reuse the /parse logic without rewriting it
        request.args = request.args.copy()
        request.args["url"] = filing_url
        return parse_sec_filing()
    except Exception as e:
        return jsonify({"error": str(e)}), 500

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8080))
    app.run(host="0.0.0.0", port=port)
