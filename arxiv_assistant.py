"""
Daily arXiv Research Assistant
==============================
Searches arXiv for papers matching your AMO/cavity-QED research interests,
generates an HTML summary report, emails it, and saves papers to Zotero.

Research fields (32 topics in 9 groups):
  Cavity QED, strong coupling, vacuum Rabi splitting, collective strong coupling,
  Fabry-Perot cavity, high finesse cavity, cavity-enhanced spectroscopy/detection,
  atom-cavity interaction, molecule-cavity interaction, light-matter interaction,
  Dicke model, Jaynes-Cummings, Tavis-Cummings, input-output theory, quantum optics,
  ultracold molecules, KRb, Rb2, K2, RbCs, ground-state molecules,
  photoassociation, STIRAP, Franck-Condon, molecular spectroscopy,
  laser cooling, magneto-optical trap, optical dipole trap, neutral atoms,
  Feshbach resonance

Usage:
  python arxiv_assistant.py          # Run the full pipeline
  python arxiv_assistant.py --test   # Dry run (no email/Zotero, saves report locally)
"""

import os
import sys
import time
import re
import smtplib
import ssl
import logging
import argparse
from datetime import datetime, timedelta, timezone
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from pathlib import Path

import requests
import feedparser
from pyzotero import zotero
from dotenv import load_dotenv

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
SCRIPT_DIR = Path(__file__).parent
load_dotenv(SCRIPT_DIR / "config.env")

# Email
EMAIL_SENDER    = os.getenv("EMAIL_SENDER", "")
EMAIL_PASSWORD  = os.getenv("EMAIL_PASSWORD", "")
EMAIL_RECIPIENT = os.getenv("EMAIL_RECIPIENT", "faraz@rrimail.rri.res.in")
SMTP_SERVER     = os.getenv("SMTP_SERVER", "smtp.gmail.com")
SMTP_PORT       = int(os.getenv("SMTP_PORT", "587"))

# Zotero
ZOTERO_API_KEY        = os.getenv("ZOTERO_API_KEY", "")
ZOTERO_USER_ID        = os.getenv("ZOTERO_USER_ID", "")
ZOTERO_LIBRARY_TYPE   = "user"
ZOTERO_COLLECTION_NAME = "new papers"

# Logging
LOG_FILE = SCRIPT_DIR / "arxiv_assistant.log"
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler(LOG_FILE, encoding="utf-8"),
        logging.StreamHandler(),
    ],
)
logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Research Topics  — keywords + arXiv categories
# ---------------------------------------------------------------------------
RESEARCH_TOPICS = {
    # --- Group 1: Cavity QED & Strong Coupling ---
    "Cavity QED & Strong Coupling": {
        "queries": [
            'all:"cavity QED"',
            'all:"cavity quantum electrodynamics"',
            'all:"strong coupling" AND all:"cavity"',
            'all:"vacuum Rabi splitting"',
            'all:"collective strong coupling"',
        ],
        "categories": ["quant-ph", "physics.atom-ph", "physics.optics"],
    },
    # --- Group 2: Cavity Technology ---
    "Cavity Technology": {
        "queries": [
            'all:"Fabry-Perot cavity" OR all:"Fabry Perot cavity"',
            'all:"high finesse cavity" OR all:"high-finesse cavity"',
            'all:"cavity-enhanced spectroscopy" OR all:"cavity enhanced spectroscopy"',
            'all:"cavity-enhanced detection" OR all:"cavity enhanced detection"',
        ],
        "categories": ["physics.optics", "physics.atom-ph", "quant-ph", "physics.ins-det"],
    },
    # --- Group 3: Atom/Molecule–Cavity Interaction ---
    "Atom-Molecule Cavity Interaction": {
        "queries": [
            'all:"atom-cavity interaction" OR all:"atom cavity interaction"',
            'all:"molecule-cavity interaction" OR all:"molecule cavity interaction"',
            'all:"light-matter interaction" AND all:"cavity"',
        ],
        "categories": ["quant-ph", "physics.atom-ph", "physics.optics"],
    },
    # --- Group 4: Quantum Optics Models ---
    "Quantum Optics Models": {
        "queries": [
            'all:"Jaynes-Cummings" OR all:"Jaynes Cummings"',
            'all:"Tavis-Cummings" OR all:"Tavis Cummings"',
            'all:"Dicke model"',
            'all:"input-output theory" OR all:"input output theory"',
            'ti:"quantum optics" AND (all:"cavity" OR all:"atom")',
        ],
        "categories": ["quant-ph", "physics.optics", "physics.atom-ph"],
    },
    # --- Group 5: Ultracold Molecules & Species ---
    "Ultracold Molecules": {
        "queries": [
            'all:"ultracold molecules"',
            'all:"ground-state molecules" OR all:"ground state molecules"',
            'all:"KRb" AND (cat:physics.atom-ph OR cat:cond-mat.quant-gas OR cat:physics.chem-ph)',
            'all:"RbCs" AND (all:"ultracold" OR all:"molecule" OR all:"trap")',
            '(all:"Rb2" OR all:"K2") AND (all:"ultracold" OR all:"molecule" OR all:"cold")',
        ],
        "categories": ["physics.atom-ph", "cond-mat.quant-gas", "physics.chem-ph", "quant-ph"],
    },
    # --- Group 6: Photoassociation & STIRAP ---
    "Photoassociation & STIRAP": {
        "queries": [
            'all:"photoassociation" OR all:"photo-association"',
            'all:"STIRAP"',
            'all:"Franck-Condon" OR all:"Franck Condon"',
        ],
        "categories": ["physics.atom-ph", "cond-mat.quant-gas", "physics.chem-ph", "quant-ph"],
    },
    # --- Group 7: Laser Cooling & Trapping ---
    "Laser Cooling & Trapping": {
        "queries": [
            'all:"laser cooling" AND all:"neutral atoms"',
            'all:"magneto-optical trap" OR all:"magneto optical trap"',
            'all:"optical dipole trap" AND (all:"atoms" OR all:"molecules")',
        ],
        "categories": ["physics.atom-ph", "cond-mat.quant-gas", "quant-ph"],
    },
    # --- Group 8: Feshbach Resonance ---
    "Feshbach Resonance": {
        "queries": [
            'all:"Feshbach resonance"',
            'all:"Feshbach" AND all:"scattering length"',
        ],
        "categories": ["physics.atom-ph", "cond-mat.quant-gas"],
    },
    # --- Group 9: Molecular Spectroscopy ---
    "Molecular Spectroscopy": {
        "queries": [
            'all:"molecular spectroscopy" AND (all:"cold" OR all:"ultracold" OR all:"alkali")',
            'all:"potential energy curve" AND (all:"KRb" OR all:"RbCs" OR all:"Rb2" OR all:"K2")',
        ],
        "categories": ["physics.atom-ph", "physics.chem-ph", "quant-ph"],
    },
}

# Keywords for highlighting in abstracts
HIGHLIGHT_KEYWORDS = [
    # Cavity QED & coupling
    "cavity QED", "cavity quantum electrodynamics", "strong coupling",
    "collective strong coupling", "vacuum Rabi", "Rabi splitting",
    "normal mode splitting",
    # Cavity technology
    "Fabry-Perot", "high finesse", "cavity-enhanced",
    "cavity enhanced", "finesse",
    # Atom/molecule-cavity
    "atom-cavity", "molecule-cavity", "light-matter interaction",
    # Quantum optics models
    "Jaynes-Cummings", "Tavis-Cummings", "Dicke model",
    "input-output theory", "quantum optics",
    # Ultracold molecules & species
    "ultracold molecule", "ground-state molecule", "ground state molecule",
    "KRb", "Rb2", "K2", "RbCs",
    "potassium rubidium", "rubidium cesium",
    # Photoassociation & STIRAP
    "photoassociation", "photo-association", "photoassociative",
    "STIRAP", "Franck-Condon",
    # Cooling & trapping
    "laser cooling", "magneto-optical trap", "MOT",
    "optical dipole trap", "dipole trap", "neutral atoms",
    # Feshbach
    "Feshbach", "scattering length",
    # Molecular spectroscopy
    "molecular spectroscopy", "potential energy curve", "PEC",
]


# ===================================================================
#  1.  arXiv Search
# ===================================================================

def search_arxiv(query: str, max_results: int = 40) -> list[dict]:
    """Query the arXiv API and return parsed paper entries."""
    base_url = "http://export.arxiv.org/api/query"
    params = {
        "search_query": query,
        "sortBy": "submittedDate",
        "sortOrder": "descending",
        "start": 0,
        "max_results": max_results,
    }

    try:
        resp = requests.get(base_url, params=params, timeout=30)
        resp.raise_for_status()
    except requests.RequestException as e:
        logger.error(f"arXiv API error: {e}")
        return []

    feed = feedparser.parse(resp.text)
    papers = []

    for entry in feed.entries:
        # Skip the feed-level "error" entry that arXiv returns for zero results
        if "arxiv.org" not in entry.get("id", ""):
            continue

        arxiv_id = entry.id.split("/abs/")[-1]

        authors = [a.get("name", "Unknown") for a in getattr(entry, "authors", [])]

        categories = [t.get("term", "") for t in getattr(entry, "tags", [])]

        pdf_url = ""
        for link in entry.links:
            if link.get("title") == "pdf":
                pdf_url = link.get("href", "")
                break
        if not pdf_url:
            pdf_url = f"https://arxiv.org/pdf/{arxiv_id}"

        papers.append({
            "arxiv_id": arxiv_id,
            "title": re.sub(r"\s+", " ", entry.title).strip(),
            "abstract": re.sub(r"\s+", " ", entry.summary).strip(),
            "authors": authors,
            "published": entry.published,
            "updated": entry.updated,
            "categories": categories,
            "abs_url": entry.id,
            "pdf_url": pdf_url,
            "matched_topics": [],
        })

    return papers


def filter_recent(papers: list[dict], hours: int = 48) -> list[dict]:
    """Keep only papers published/updated within the last *hours*."""
    cutoff = datetime.now(timezone.utc) - timedelta(hours=hours)
    recent = []
    for p in papers:
        try:
            pub = datetime.fromisoformat(p["published"].replace("Z", "+00:00"))
            if pub >= cutoff:
                recent.append(p)
        except (ValueError, AttributeError):
            continue
    return recent


def search_all_topics() -> list[dict]:
    """Run arXiv searches for every research topic, deduplicate by arxiv_id."""
    seen: dict[str, dict] = {}

    for topic_name, cfg in RESEARCH_TOPICS.items():
        logger.info(f"  🔍  Searching: {topic_name}")
        cat_filter = " OR ".join(f"cat:{c}" for c in cfg["categories"])

        for q in cfg["queries"]:
            full_q = f"({q}) AND ({cat_filter})"
            results = search_arxiv(full_q, max_results=30)

            for paper in results:
                pid = paper["arxiv_id"]
                if pid in seen:
                    if topic_name not in seen[pid]["matched_topics"]:
                        seen[pid]["matched_topics"].append(topic_name)
                else:
                    paper["matched_topics"] = [topic_name]
                    seen[pid] = paper

            time.sleep(3)  # respect arXiv rate limit

    return list(seen.values())


# ===================================================================
#  2.  Abstract-Based Summary
# ===================================================================

def generate_summary(paper: dict) -> tuple[str, list[str]]:
    """
    Return (summary_text, matched_keywords) extracted from the abstract.
    Takes the first 4 sentences and lists which highlight-keywords appear.
    """
    abstract = paper["abstract"]

    # Split on sentence-ending punctuation
    sentences = re.split(r"(?<=[.!?])\s+", abstract)
    summary = " ".join(sentences[: min(4, len(sentences))])

    # Find which keywords appear
    kw_found = []
    lower_abs = abstract.lower()
    for kw in HIGHLIGHT_KEYWORDS:
        if kw.lower() in lower_abs:
            kw_found.append(kw)

    return summary, kw_found


# ===================================================================
#  3.  HTML Email Report
# ===================================================================

TOPIC_COLORS = {
    "Cavity QED & Strong Coupling":      "#e94560",
    "Cavity Technology":                 "#0f3460",
    "Atom-Molecule Cavity Interaction":   "#2d6a4f",
    "Quantum Optics Models":             "#533483",
    "Ultracold Molecules":               "#d62828",
    "Photoassociation & STIRAP":         "#1d3557",
    "Laser Cooling & Trapping":          "#e76f51",
    "Feshbach Resonance":                "#457b9d",
    "Molecular Spectroscopy":            "#264653",
}


def _paper_card(i: int, paper: dict) -> str:
    """Render one paper as an HTML card."""
    summary, keywords = generate_summary(paper)

    # Topic tag pills
    tags = "".join(
        f'<span style="display:inline-block;background:{TOPIC_COLORS.get(t,"#666")}; '
        f'color:#fff;padding:3px 10px;border-radius:12px;font-size:12px;'
        f'margin:2px 4px 2px 0;">{t}</span>'
        for t in paper["matched_topics"]
    )

    # Authors (show ≤5)
    authors = ", ".join(paper["authors"][:5])
    if len(paper["authors"]) > 5:
        authors += f"  <em>et al.</em> ({len(paper['authors'])} authors)"

    # Keywords line
    kw_html = ""
    if keywords:
        kw_html = (
            f'<p style="color:#e94560;font-size:13px;margin:5px 0;">'
            f'<strong>🔑 Keywords:</strong> {", ".join(keywords[:8])}</p>'
        )

    return f"""
    <div style="background:#fafbfd;border-left:4px solid #e94560;border-radius:8px;
                padding:20px;margin:20px 0;box-shadow:0 1px 4px rgba(0,0,0,0.05);">
      <h3 style="color:#1a1a2e;margin:0 0 8px;font-size:17px;">
        {i}. <a href="{paper['abs_url']}" style="color:#0f3460;text-decoration:none;">
              {paper['title']}</a>
      </h3>
      <p style="color:#888;font-size:13px;margin:4px 0;">👤 {authors}</p>
      <p style="color:#888;font-size:13px;margin:4px 0;">
        📅 {paper['published'][:10]} &nbsp;|&nbsp;
        🏷️ {', '.join(paper['categories'][:4])}
      </p>
      <div style="margin:8px 0;">{tags}</div>
      {kw_html}
      <p style="color:#333;font-size:14px;line-height:1.6;margin:10px 0;">
        {summary}
      </p>
      <div style="margin-top:10px;">
        <a href="{paper['abs_url']}"
           style="display:inline-block;background:#0f3460;color:#fff;
                  padding:6px 16px;border-radius:6px;text-decoration:none;
                  font-size:13px;margin-right:8px;">📄 Abstract</a>
        <a href="{paper['pdf_url']}"
           style="display:inline-block;background:#e94560;color:#fff;
                  padding:6px 16px;border-radius:6px;text-decoration:none;
                  font-size:13px;">📥 PDF</a>
      </div>
    </div>"""


def build_email_html(papers: list[dict], date_str: str) -> str:
    """Assemble the full HTML email body."""

    if not papers:
        return f"""<html><body style="font-family:'Segoe UI',Arial,sans-serif;
        background:#f4f6f9;padding:20px;">
        <div style="max-width:700px;margin:auto;background:#fff;border-radius:12px;
                    padding:30px;box-shadow:0 2px 8px rgba(0,0,0,0.08);">
          <h1 style="color:#1a1a2e;border-bottom:3px solid #e94560;padding-bottom:10px;">
            📚 arXiv Research Digest — {date_str}</h1>
          <p style="color:#666;font-size:16px;">
            No new papers matching your research interests were found today.
            This can happen on weekends or holidays when arXiv announcements pause.</p>
          <p style="color:#999;font-size:13px;margin-top:30px;">
            Generated by arXiv Research Assistant</p>
        </div></body></html>"""

    cards = "".join(_paper_card(i, p) for i, p in enumerate(papers, 1))

    return f"""<html><body style="font-family:'Segoe UI',Arial,sans-serif;
    background:#f4f6f9;padding:20px;">
    <div style="max-width:750px;margin:auto;background:#fff;border-radius:12px;
                padding:30px;box-shadow:0 2px 8px rgba(0,0,0,0.08);">

      <h1 style="color:#1a1a2e;border-bottom:3px solid #e94560;padding-bottom:10px;">
        📚 arXiv Research Digest — {date_str}</h1>

      <p style="color:#666;font-size:15px;">
        Found <strong>{len(papers)}</strong> new paper(s) matching your research interests.</p>

      <div style="background:#f0f4ff;border-radius:8px;padding:12px 16px;margin:15px 0;">
        <strong style="color:#0f3460;">🔬 Your Research Fields:</strong>
        <span style="color:#555;font-size:14px;">
          Cavity QED · Strong Coupling · Vacuum Rabi · Fabry-Perot ·
          Ultracold Molecules · KRb · RbCs · Photoassociation · STIRAP ·
          Feshbach · MOT · Jaynes-Cummings · Molecular Spectroscopy
        </span>
      </div>

      {cards}

      <hr style="border:none;border-top:1px solid #eee;margin:30px 0 15px;">
      <p style="color:#aaa;font-size:12px;text-align:center;">
        🤖 Generated by arXiv Research Assistant<br>
        Papers sourced from <a href="https://arxiv.org" style="color:#0f3460;">arXiv.org</a>
        &nbsp;|&nbsp; Saved to Zotero collection "<em>new papers</em>"
      </p>
    </div></body></html>"""


# ===================================================================
#  4.  Email Sender
# ===================================================================

def send_email(subject: str, html_body: str) -> bool:
    """Send the HTML report via SMTP.  Falls back to saving locally."""
    if not EMAIL_SENDER or not EMAIL_PASSWORD:
        logger.warning("⚠  Email credentials missing — saving report locally instead.")
        _save_report_locally(html_body)
        return False

    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"]    = EMAIL_SENDER
    msg["To"]      = EMAIL_RECIPIENT
    msg.attach(MIMEText(html_body, "html"))

    try:
        ctx = ssl.create_default_context()
        with smtplib.SMTP(SMTP_SERVER, SMTP_PORT) as server:
            server.ehlo()
            server.starttls(context=ctx)
            server.ehlo()
            server.login(EMAIL_SENDER, EMAIL_PASSWORD)
            server.sendmail(EMAIL_SENDER, EMAIL_RECIPIENT, msg.as_string())
        logger.info(f"✅  Email sent to {EMAIL_RECIPIENT}")
        return True
    except Exception as e:
        logger.error(f"❌  Email failed: {e}")
        _save_report_locally(html_body)
        return False


def _save_report_locally(html_body: str):
    """Save report as a local HTML file when email can't be sent."""
    reports_dir = SCRIPT_DIR / "reports"
    reports_dir.mkdir(exist_ok=True)
    path = reports_dir / f"report_{datetime.now().strftime('%Y%m%d_%H%M')}.html"
    path.write_text(html_body, encoding="utf-8")
    logger.info(f"📁  Report saved locally: {path}")


# ===================================================================
#  5.  Zotero Integration
# ===================================================================

def save_to_zotero(papers: list[dict]):
    """Add papers to the 'new papers' Zotero collection, skipping duplicates."""
    if not ZOTERO_API_KEY or not ZOTERO_USER_ID:
        logger.warning("⚠  Zotero credentials missing — skipping Zotero save.")
        return

    try:
        zot = zotero.Zotero(ZOTERO_USER_ID, ZOTERO_LIBRARY_TYPE, ZOTERO_API_KEY)

        # --- find or create collection ---
        collection_key = None
        for col in zot.collections():
            if col["data"]["name"].lower() == ZOTERO_COLLECTION_NAME.lower():
                collection_key = col["data"]["key"]
                logger.info(f"📂  Using existing collection '{ZOTERO_COLLECTION_NAME}' "
                            f"(key={collection_key})")
                break

        if not collection_key:
            logger.info(f"📂  Creating collection '{ZOTERO_COLLECTION_NAME}'…")
            res = zot.create_collections([{"name": ZOTERO_COLLECTION_NAME}])
            collection_key = res["successful"]["0"]["key"]
            logger.info(f"📂  Created (key={collection_key})")

        # --- gather existing URLs to skip duplicates ---
        existing_urls: set[str] = set()
        try:
            for item in zot.collection_items(collection_key, limit=100):
                url = item.get("data", {}).get("url", "")
                if url:
                    existing_urls.add(url)
        except Exception:
            pass  # if collection is new there are no items

        # --- add each paper ---
        added = 0
        for paper in papers:
            if paper["abs_url"] in existing_urls:
                logger.info(f"   ⏭  Already in Zotero: {paper['title'][:55]}…")
                continue

            tmpl = zot.item_template("preprint")
            tmpl["title"]        = paper["title"]
            tmpl["abstractNote"] = paper["abstract"]
            tmpl["repository"]   = "arXiv"
            tmpl["archiveID"]    = f"arXiv:{paper['arxiv_id']}"
            tmpl["url"]          = paper["abs_url"]
            tmpl["date"]         = paper["published"][:10]
            tmpl["collections"]  = [collection_key]

            # Parse author names ("First Last" → firstName / lastName)
            creators = []
            for name in paper["authors"]:
                parts = name.rsplit(" ", 1)
                if len(parts) == 2:
                    creators.append({"creatorType": "author",
                                     "firstName": parts[0], "lastName": parts[1]})
                else:
                    creators.append({"creatorType": "author", "name": name})
            tmpl["creators"] = creators

            # Tags = matched research topics
            tmpl["tags"] = [{"tag": t} for t in paper["matched_topics"]]

            try:
                zot.create_items([tmpl])
                added += 1
                logger.info(f"   ✅  Added: {paper['title'][:55]}…")
            except Exception as e:
                logger.error(f"   ❌  Zotero add failed: {e}")

            time.sleep(1)  # be gentle with API

        logger.info(f"📚  Zotero: added {added} new paper(s) to '{ZOTERO_COLLECTION_NAME}'")

    except Exception as e:
        logger.error(f"❌  Zotero error: {e}")


# ===================================================================
#  6.  Main Pipeline
# ===================================================================

def main():
    parser = argparse.ArgumentParser(description="arXiv Research Assistant")
    parser.add_argument("--test", action="store_true",
                        help="Dry run: search arXiv and save report locally, "
                             "but don't send email or touch Zotero.")
    args = parser.parse_args()

    logger.info("=" * 65)
    logger.info("  arXiv Research Assistant — Daily Run")
    logger.info("=" * 65)

    # 1. Search
    logger.info("Step 1/5 — Searching arXiv across 8 research topics…")
    all_papers = search_all_topics()
    logger.info(f"  → {len(all_papers)} unique papers found (all dates)")

    # 2. Filter recent
    logger.info("Step 2/5 — Filtering to last 48 hours…")
    recent = filter_recent(all_papers, hours=48)
    recent.sort(key=lambda p: p["published"], reverse=True)
    logger.info(f"  → {len(recent)} recent paper(s)")

    # 3. Build report
    date_str = datetime.now().strftime("%B %d, %Y")
    logger.info("Step 3/5 — Building email report…")
    subject = f"📚 arXiv Digest — {date_str} ({len(recent)} papers)"
    html = build_email_html(recent, date_str)

    if args.test:
        logger.info("Step 4/5 — TEST MODE: saving report locally…")
        _save_report_locally(html)
        logger.info("Step 5/5 — TEST MODE: skipping Zotero.")
    else:
        # 4. Send email
        logger.info("Step 4/5 — Sending email…")
        send_email(subject, html)

        # 5. Zotero
        if recent:
            logger.info("Step 5/5 — Saving to Zotero…")
            save_to_zotero(recent)
        else:
            logger.info("Step 5/5 — No papers to save to Zotero.")

    logger.info("=" * 65)
    logger.info("  Done!  🎉")
    logger.info("=" * 65)


if __name__ == "__main__":
    main()
