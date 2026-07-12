import urllib.request
import xml.etree.ElementTree as ET
import urllib.parse
import datetime
import requests

# 1. Your updated research keywords (Exact phrase matching via double quotes)
KEYWORDS = (
    '("cold atom") OR '
    '("ultracold atoms") OR '
    '("ultracold molecules") OR '
    '("cavity QED") OR '
    '("cavity thermodynamics") OR '
    '("Rb K dual species MOT") OR '
    '("K2 molecules") OR '
    '("Rb2 molecules") OR '
    '("Dual species trap") OR '
    '("KRb molecule") OR '
    '("lasing through cavity") OR '
    '("cavity g2 measurement")'
)

# Encode query for URL
query = urllib.parse.quote(KEYWORDS)

# 2. Query arXiv API for papers sorted by submission date
url = f'http://export.arxiv.org/api/query?search_query=all:{query}&sortBy=submittedDate&sortOrder=descending&max_results=15'

# Fetch data
response = urllib.request.urlopen(url)
xml_data = response.read()

# Parse XML
root = ET.fromstring(xml_data)
namespaces = {'atom': 'http://www.w3.org/2005/Atom'}

# Get today's and yesterday's date to filter new uploads
yesterday = (datetime.date.today() - datetime.timedelta(days=1)).strftime('%Y-%m-%d')
today = datetime.date.today().strftime('%Y-%m-%d')

new_papers = []

for entry in root.findall('atom:entry', namespaces):
    published = entry.find('atom:published', namespaces).text[:10]
    # Filter for papers published in the last 48 hours
    if published >= yesterday:
        title = entry.find('atom:title', namespaces).text.strip().replace('\n', ' ')
        link = entry.find('atom:id', namespaces).text
        summary = entry.find('atom:summary', namespaces).text.strip()[:200] + "..."
        new_papers.append(f"📄 *{title}*\n📅 {published}\n🔗 [Link to Paper]({link})\n📝 {summary}\n")

# 3. Send alerts to Telegram if new papers found
if new_papers:
    bot_token = "YOUR_TELEGRAM_BOT_TOKEN"
    chat_id = "@my_arxiv_alerts"
    
    header = f"🚨 *New arXiv papers found for your research:* \n\n"
    full_message = header + "\n---\n".join(new_papers)
    
    # Send message in chunks of 4000 characters if too long
    for i in range(0, len(full_message), 4000):
        chunk = full_message[i:i+4000]
        telegram_url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
        requests.post(telegram_url, data={
            "chat_id": chat_id,
            "text": chunk,
            "parse_mode": "Markdown",
            "disable_web_page_preview": True
        })
    print("Alerts sent successfully.")
else:
    print("No new papers matching keywords today.")
