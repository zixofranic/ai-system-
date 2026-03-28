"""Split n8n workflows — separate Wisdom and Gibran plan generators."""

import requests, json, os
from dotenv import load_dotenv
load_dotenv("C:/AI/.env")

N8N_URL = "http://107.173.231.158:5678"
N8N_API_KEY = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJzdWIiOiI4ZDdmNzUwYS03YThhLTQwM2UtYTBiOS0zZjRkMzM3ZDIwNTMiLCJpc3MiOiJuOG4iLCJhdWQiOiJwdWJsaWMtYXBpIiwiaWF0IjoxNzc0NjI0NjY2fQ.8z07X3gQck7IvIVxdEk-GfxK3yYCG6JKILL9YFzBBE4"
SUPABASE_KEY = os.environ.get("SUPABASE_SERVICE_KEY", "")
ANTHROPIC_KEY = os.environ.get("ANTHROPIC_API_KEY", "")
WISDOM_ID = "1b3ba813-31c5-42b3-a270-67e85fcc7123"
GIBRAN_ID = "ff18bcb2-21db-4320-89ad-c24d04f0dad3"

headers = {"X-N8N-API-KEY": N8N_API_KEY, "Content-Type": "application/json"}

# ============================================================
# 1. UPDATE Wisdom workflow — EXCLUDE Gibran
# ============================================================
WF_ID = "mJBXFcE9QkTgDlk0"
resp = requests.get(f"{N8N_URL}/api/v1/workflows/{WF_ID}", headers=headers)
wf = resp.json()

for node in wf["nodes"]:
    if node["name"] == "Claude Haiku":
        body = json.loads(node["parameters"]["jsonBody"])
        body["messages"][0]["content"] = (
            "Generate a 7-day content plan for a philosophy YouTube channel called Deep Echoes of Wisdom. "
            "For each day Monday-Sunday suggest a topic about human struggles. "
            "Return ONLY a valid JSON array with no other text. "
            "Each object: day, title, philosopher, tone. "
            "Philosophers to use (pick the BEST match): "
            "Marcus Aurelius, Seneca, Epictetus, Rumi, Lao Tzu, Sun Tzu, Nietzsche, "
            "Emerson, Thoreau, Wilde, Dostoevsky, Musashi, Vivekananda, Confucius, Franklin, Da Vinci, Tesla. "
            "DO NOT use Gibran. He belongs to a different channel. "
            "Tones: contemplative, urgent, challenging, mystical, witty. "
            "Focus on: anxiety, relationships, purpose, discipline, anger, grief, loneliness."
        )
        node["parameters"]["jsonBody"] = json.dumps(body)

for node in wf["nodes"]:
    if node["name"] == "Parse Plan":
        code = node["parameters"]["jsCode"]
        code = code.replace(
            'item.channel === "gibran" ? GIBRAN_ID : WISDOM_ID',
            "WISDOM_ID"
        )
        node["parameters"]["jsCode"] = code

allowed = {"name", "nodes", "connections", "settings"}
clean = {k: v for k, v in wf.items() if k in allowed}
clean["name"] = "Wisdom - Weekly Plan Generator"

resp = requests.put(f"{N8N_URL}/api/v1/workflows/{WF_ID}", headers=headers, json=clean)
print(f"1. Updated Wisdom workflow: {resp.status_code}")

# ============================================================
# 2. CREATE Gibran-only workflow
# ============================================================
gibran_code = (
    "const response = items[0].json;\n"
    "const text = response.content[0].text;\n"
    "let plan;\n"
    "try {\n"
    '  const match = text.match(/\\[[\\s\\S]*\\]/);\n'
    "  plan = JSON.parse(match[0]);\n"
    "} catch(e) {\n"
    '  plan = [{day: "Monday", title: "On Love and Loss", tone: "contemplative"}];\n'
    "}\n"
    f'const GIBRAN_ID = "{GIBRAN_ID}";\n'
    "return plan.map(item => ({json: {\n"
    '  philosopher: "Gibran",\n'
    '  quote_text: "Pending generation",\n'
    "  topic: item.title,\n"
    "  title: item.title,\n"
    '  status: "queued",\n'
    "  channel_id: GIBRAN_ID,\n"
    '  format: "short_portrait",\n'
    "  is_system_generated: true\n"
    "}}));"
)

gibran_prompt = (
    "Generate a 7-day content plan for a YouTube channel dedicated to Gibran Khalil Gibran. "
    "For each day Monday-Sunday suggest a topic inspired by Gibran's philosophy. "
    "Return ONLY a valid JSON array with no other text. "
    "Each object: day, title, tone. "
    "The philosopher is ALWAYS Gibran Khalil Gibran. "
    "Draw from his themes: love, pain, children, freedom, beauty, death, the soul, nature, "
    "longing, exile, the prophet, the madman, work, marriage, joy and sorrow. "
    "Tones: contemplative, mystical, warm, poetic, bittersweet. "
    "Titles should be evocative and poetic."
)

gibran_wf = {
    "name": "Gibran - Weekly Plan Generator",
    "nodes": [
        {
            "parameters": {"rule": {"interval": [{"field": "cronExpression", "expression": "0 22 * * 6"}]}},
            "name": "Saturday 10 PM",
            "type": "n8n-nodes-base.scheduleTrigger",
            "typeVersion": 1.2,
            "position": [240, 300],
        },
        {
            "parameters": {
                "method": "POST",
                "url": "https://api.anthropic.com/v1/messages",
                "sendHeaders": True,
                "headerParameters": {"parameters": [
                    {"name": "x-api-key", "value": ANTHROPIC_KEY},
                    {"name": "anthropic-version", "value": "2023-06-01"},
                ]},
                "sendBody": True,
                "specifyBody": "json",
                "jsonBody": json.dumps({
                    "model": "claude-haiku-4-5-20251001",
                    "max_tokens": 2000,
                    "messages": [{"role": "user", "content": gibran_prompt}],
                }),
            },
            "name": "Claude Haiku (Gibran)",
            "type": "n8n-nodes-base.httpRequest",
            "typeVersion": 4.2,
            "position": [480, 300],
        },
        {
            "parameters": {"jsCode": gibran_code},
            "name": "Parse Gibran Plan",
            "type": "n8n-nodes-base.code",
            "typeVersion": 2,
            "position": [720, 300],
        },
        {
            "parameters": {
                "method": "POST",
                "url": "https://kwyqaewdvvdhodxieqrh.supabase.co/rest/v1/content",
                "sendHeaders": True,
                "headerParameters": {"parameters": [
                    {"name": "apikey", "value": SUPABASE_KEY},
                    {"name": "Authorization", "value": "Bearer " + SUPABASE_KEY},
                    {"name": "Content-Type", "value": "application/json"},
                    {"name": "Prefer", "value": "return=representation"},
                ]},
                "sendBody": True,
                "specifyBody": "keypair",
                "bodyParameters": {"parameters": [
                    {"name": "philosopher", "value": "={{ $json.philosopher }}"},
                    {"name": "quote_text", "value": "={{ $json.quote_text }}"},
                    {"name": "topic", "value": "={{ $json.topic }}"},
                    {"name": "title", "value": "={{ $json.title }}"},
                    {"name": "status", "value": "={{ $json.status }}"},
                    {"name": "channel_id", "value": "={{ $json.channel_id }}"},
                    {"name": "format", "value": "={{ $json.format }}"},
                    {"name": "is_system_generated", "value": "={{ $json.is_system_generated }}"},
                ]},
            },
            "name": "Queue in Supabase",
            "type": "n8n-nodes-base.httpRequest",
            "typeVersion": 4.2,
            "position": [960, 300],
        },
    ],
    "connections": {
        "Saturday 10 PM": {"main": [[{"node": "Claude Haiku (Gibran)", "type": "main", "index": 0}]]},
        "Claude Haiku (Gibran)": {"main": [[{"node": "Parse Gibran Plan", "type": "main", "index": 0}]]},
        "Parse Gibran Plan": {"main": [[{"node": "Queue in Supabase", "type": "main", "index": 0}]]},
    },
    "settings": {"executionOrder": "v1"},
}

resp = requests.post(f"{N8N_URL}/api/v1/workflows", headers=headers, json=gibran_wf)
data = resp.json()
print(f"2. Created Gibran workflow: {resp.status_code} — id: {data.get('id', '?')}")

print("\nDone! Two separate workflows on VPS n8n:")
print("  - Wisdom: excludes Gibran, 17 other philosophers")
print("  - Gibran: ONLY Gibran themes, Gibran channel")
