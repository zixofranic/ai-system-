"""Import Wisdom workflows to VPS n8n at 107.173.231.158:5678"""

import requests
import json
import os
from dotenv import load_dotenv

load_dotenv("C:/AI/.env")

N8N_URL = "http://107.173.231.158:5678"
N8N_API_KEY = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJzdWIiOiI4ZDdmNzUwYS03YThhLTQwM2UtYTBiOS0zZjRkMzM3ZDIwNTMiLCJpc3MiOiJuOG4iLCJhdWQiOiJwdWJsaWMtYXBpIiwiaWF0IjoxNzc0NjI0NjY2fQ.8z07X3gQck7IvIVxdEk-GfxK3yYCG6JKILL9YFzBBE4"
SUPABASE_KEY = os.environ.get("SUPABASE_SERVICE_KEY", "")
ANTHROPIC_KEY = os.environ.get("ANTHROPIC_API_KEY", "")

headers = {"X-N8N-API-KEY": N8N_API_KEY, "Content-Type": "application/json"}

# Get channel IDs
ch_resp = requests.get(
    "https://kwyqaewdvvdhodxieqrh.supabase.co/rest/v1/channels?select=id,slug",
    headers={"apikey": SUPABASE_KEY, "Authorization": f"Bearer {SUPABASE_KEY}"},
)
channels = {c["slug"]: c["id"] for c in ch_resp.json()}
print(f"Channels: {channels}")

WISDOM_ID = channels.get("wisdom", "")
GIBRAN_ID = channels.get("gibran", "")


def import_workflow(workflow):
    resp = requests.post(f"{N8N_URL}/api/v1/workflows", headers=headers, json=workflow)
    data = resp.json()
    name = data.get("name", "?")
    wf_id = data.get("id", "?")
    print(f"  {resp.status_code}: {name} (id: {wf_id})")
    return wf_id


# 1. Weekly Plan Generator
print("\n1. Weekly Plan Generator...")
import_workflow({
    "name": "Wisdom - Weekly Plan Generator",
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
                    "messages": [{"role": "user", "content": "Generate a 7-day content plan for a philosophy YouTube channel called Deep Echoes of Wisdom. For each day Monday-Sunday suggest: topic title about human struggles, best philosopher from (Marcus Aurelius, Seneca, Epictetus, Gibran, Rumi, Lao Tzu, Sun Tzu, Nietzsche, Emerson, Thoreau, Wilde, Dostoevsky, Musashi, Vivekananda, Confucius, Franklin, Da Vinci, Tesla), and tone (contemplative/urgent/challenging/mystical/witty). Return ONLY a JSON array with: day, title, philosopher, tone."}],
                }),
            },
            "name": "Claude Haiku Plan",
            "type": "n8n-nodes-base.httpRequest",
            "typeVersion": 4.2,
            "position": [480, 300],
        },
        {
            "parameters": {"jsCode": "const r=$input.first().json;const t=r.content[0].text;let p;try{p=JSON.parse(t.match(/\\[.*\\]/s)[0])}catch(e){p=[{day:'Monday',title:'Inner Peace',philosopher:'Marcus Aurelius',tone:'contemplative'}]}return p.map(i=>({json:i}));"},
            "name": "Parse",
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
                    {"name": "Authorization", "value": f"Bearer {SUPABASE_KEY}"},
                    {"name": "Content-Type", "value": "application/json"},
                    {"name": "Prefer", "value": "return=representation"},
                ]},
                "sendBody": True,
                "specifyBody": "json",
                "jsonBody": '{"philosopher":"={{ $json.philosopher }}","quote_text":"Pending generation","topic":"={{ $json.title }}","title":"={{ $json.title }}","status":"queued","channel_id":"' + WISDOM_ID + '","format":"short_portrait","is_system_generated":true}',
            },
            "name": "Queue in Supabase",
            "type": "n8n-nodes-base.httpRequest",
            "typeVersion": 4.2,
            "position": [960, 300],
        },
    ],
    "connections": {
        "Saturday 10 PM": {"main": [[{"node": "Claude Haiku Plan", "type": "main", "index": 0}]]},
        "Claude Haiku Plan": {"main": [[{"node": "Parse", "type": "main", "index": 0}]]},
        "Parse": {"main": [[{"node": "Queue in Supabase", "type": "main", "index": 0}]]},
    },
    "settings": {"executionOrder": "v1"},
    })

# 2. Daily Publisher
print("\n2. Daily Publisher...")
import_workflow({
    "name": "Wisdom - Daily Publisher",
    "nodes": [
        {
            "parameters": {"rule": {"interval": [{"field": "cronExpression", "expression": "0 9 * * *"}]}},
            "name": "Daily 9 AM",
            "type": "n8n-nodes-base.scheduleTrigger",
            "typeVersion": 1.2,
            "position": [240, 300],
        },
        {
            "parameters": {
                "method": "GET",
                "url": "https://kwyqaewdvvdhodxieqrh.supabase.co/rest/v1/content?status=eq.approved&deleted_at=is.null&limit=10",
                "sendHeaders": True,
                "headerParameters": {"parameters": [
                    {"name": "apikey", "value": SUPABASE_KEY},
                    {"name": "Authorization", "value": f"Bearer {SUPABASE_KEY}"},
                ]},
            },
            "name": "Get Approved",
            "type": "n8n-nodes-base.httpRequest",
            "typeVersion": 4.2,
            "position": [480, 300],
        },
        {
            "parameters": {"jsCode": "const items=$input.first().json;if(!Array.isArray(items)||items.length===0)return[{json:{msg:'No items',count:0}}];return[{json:{msg:items.length+' ready to publish',count:items.length}}];"},
            "name": "Log",
            "type": "n8n-nodes-base.code",
            "typeVersion": 2,
            "position": [720, 300],
        },
    ],
    "connections": {
        "Daily 9 AM": {"main": [[{"node": "Get Approved", "type": "main", "index": 0}]]},
        "Get Approved": {"main": [[{"node": "Log", "type": "main", "index": 0}]]},
    },
    "settings": {"executionOrder": "v1"},
    })

# 3. Health Check
print("\n3. Health Check...")
import_workflow({
    "name": "Wisdom - Health Check",
    "nodes": [
        {
            "parameters": {"rule": {"interval": [{"field": "cronExpression", "expression": "*/30 * * * *"}]}},
            "name": "Every 30 min",
            "type": "n8n-nodes-base.scheduleTrigger",
            "typeVersion": 1.2,
            "position": [240, 300],
        },
        {
            "parameters": {
                "method": "POST",
                "url": "https://wisdom-dashboard-weld.vercel.app/api/machine/status",
                "sendHeaders": True,
                "headerParameters": {"parameters": [
                    {"name": "Authorization", "value": f"Bearer {SUPABASE_KEY}"},
                    {"name": "Content-Type", "value": "application/json"},
                ]},
                "sendBody": True,
                "specifyBody": "json",
                "jsonBody": '{"source":"vps_n8n","status":"healthy"}',
                "options": {"timeout": 10000},
            },
            "name": "Ping Dashboard",
            "type": "n8n-nodes-base.httpRequest",
            "typeVersion": 4.2,
            "position": [480, 300],
        },
    ],
    "connections": {
        "Every 30 min": {"main": [[{"node": "Ping Dashboard", "type": "main", "index": 0}]]},
    },
    "settings": {"executionOrder": "v1"},
    })

# 4. Manual Trigger Webhook
print("\n4. Manual Trigger Webhook...")
import_workflow({
    "name": "Wisdom - Manual Content Trigger",
    "nodes": [
        {
            "parameters": {"httpMethod": "POST", "path": "wisdom-generate", "responseMode": "responseNode"},
            "name": "Webhook",
            "type": "n8n-nodes-base.webhook",
            "typeVersion": 2,
            "position": [240, 300],
            "webhookId": "wisdom-generate",
        },
        {
            "parameters": {
                "method": "POST",
                "url": "https://kwyqaewdvvdhodxieqrh.supabase.co/rest/v1/content",
                "sendHeaders": True,
                "headerParameters": {"parameters": [
                    {"name": "apikey", "value": SUPABASE_KEY},
                    {"name": "Authorization", "value": f"Bearer {SUPABASE_KEY}"},
                    {"name": "Content-Type", "value": "application/json"},
                    {"name": "Prefer", "value": "return=representation"},
                ]},
                "sendBody": True,
                "specifyBody": "json",
                "jsonBody": '{"philosopher":"={{ $json.body.philosopher }}","quote_text":"Pending","topic":"={{ $json.body.topic }}","title":"={{ $json.body.title }}","status":"queued","channel_id":"={{ $json.body.channel_id }}","format":"={{ $json.body.format || \'short_portrait\' }}","is_system_generated":true}',
            },
            "name": "Create in Supabase",
            "type": "n8n-nodes-base.httpRequest",
            "typeVersion": 4.2,
            "position": [480, 300],
        },
        {
            "parameters": {"respondWith": "json", "responseBody": '={"status":"queued","id":"{{ $json[0].id }}"}'},
            "name": "Respond",
            "type": "n8n-nodes-base.respondToWebhook",
            "typeVersion": 1.1,
            "position": [720, 300],
        },
    ],
    "connections": {
        "Webhook": {"main": [[{"node": "Create in Supabase", "type": "main", "index": 0}]]},
        "Create in Supabase": {"main": [[{"node": "Respond", "type": "main", "index": 0}]]},
    },
    "settings": {"executionOrder": "v1"},
    })

print("\n" + "=" * 60)
print("All 4 workflows imported to VPS n8n!")
print(f"Go to: {N8N_URL}")
print("=" * 60)
