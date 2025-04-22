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

@app.route("/parse", methods=["GET"])
def parse_sec_filing():
    sec_url = request.args.get("url")
    if not sec_url:
        return jsonify({"error": "Missing SEC filing URL"}), 400

    try:
        headers = {
            "User-Agent": "Mozilla/5.0 (compatible; SECParserBot/1.0; brian.c.mccarthy@gmail.com)",
            "Accept-Encoding": "gzip, deflate",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Connection": "keep-alive"
        }
        resp = requests.get(sec_url, headers=headers, timeout=20)
        resp.raise_for_status()
        tree = etree.HTML(resp.content)

        namespaces = {'ix': 'http://www.xbrl.org/2013/inlineXBRL'}

        extracted = {
            "Filing URL": sec_url,
            "Revenue": "Not found",
            "Gross Profit": "Not found",
            "SG&A": "Not found",
            "Net Income": "Not found"
        }

        tags = tree.xpath("//ix:nonFraction | //ix:nonNumeric", namespaces=namespaces)
        found_tags = {}

        for tag in tags:
            name = tag.attrib.get("name", "").lower()
            context_ref = tag.attrib.get("contextRef", "")
            if name in TARGET_TAGS:
                value_str = tag.text.strip() if tag.text else ""
                value_str = value_str.replace(',', '')
                if value_str.startswith('(') and value_str.endswith(')'):
                    value_str = '-' + value_str[1:-1]
                try:
                    float(value_str)
                    tag_key = (name, context_ref)
                    if tag_key not in found_tags:
                        found_tags[tag_key] = value_str
                except ValueError:
                    pass

        for (tag_name, context), value in found_tags.items():
            target_field = TARGET_TAGS.get(tag_name)
            if target_field == "Revenue" and extracted["Revenue"] == "Not found":
                extracted["Revenue"] = value
            elif target_field == "Gross Profit" and extracted["Gross Profit"] == "Not found":
                extracted["Gross Profit"] = value
            elif target_field == "SG&A" and extracted["SG&A"] == "Not found":
                extracted["SG&A"] = value
            elif target_field == "Net Income" and extracted["Net Income"] == "Not found":
                extracted["Net Income"] = value

        return jsonify(extracted)

    except requests.exceptions.RequestException as e:
        return jsonify({"error": f"Request failed: {e}"}), 500
    except etree.XMLSyntaxError as e:
        return jsonify({"error": f"HTML parsing failed: {e}"}), 500
    except Exception as e:
        print(f"Unexpected error: {e}")
        return jsonify({"error": str(e)}), 500

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8080))
    app.run(host="0.0.0.0", port=port)
