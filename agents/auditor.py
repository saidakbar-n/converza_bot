import os
import httpx
from db.supabase_client import sb

GROQ_API_KEY = os.environ.get("GROQ_API_KEY", "")
GROQ_MODEL = "llama-3.3-70b-versatile"
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_API = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}"

async def run_nightly_audit():
    """Runs a nightly audit for all organizations and sends a Telegram report."""
    # 1. Fetch all active organizations
    orgs_resp = sb.table("organizations").select("*").execute()
    organizations = orgs_resp.data or []

    for org in organizations:
        org_id = org.get("id")
        
        # 2. Fetch today's metrics
        # For a production system, use proper timestamp filtering for "today"
        # Since this MVP lacks precise timezone logic on DB, we'll fetch recent messages
        # Here we just fetch the last 100 messages and group by prospect
        msgs_resp = sb.table("messages").select("*").eq("org_id", org_id).order("created_at", desc=True).limit(100).execute()
        messages = msgs_resp.data or []

        prospects_resp = sb.table("prospects").select("*").eq("org_id", org_id).execute()
        prospects = prospects_resp.data or []

        # Count conditions
        conditions = {"cold": 0, "warm": 0, "purchasing": 0, "closed": 0}
        for p in prospects:
            cond = p.get("client_condition", "cold")
            if cond in conditions:
                conditions[cond] += 1

        total_messages_today = len(messages)
        
        # 3. Generate summary via Groq
        system_prompt = (
            "Siz biznes egasiga kunlik hisobot (audit) tayyorlab beruvchi AI yordamchisisiz. "
            "Sizga bugungi statistika beriladi, siz uni qisqa, tushunarli va professional O'zbek tilida "
            "hisobot shaklida yozib berishingiz kerak. Hisobot faqat matnli bo'lsin."
        )
        
        stats_text = (
            f"Bugun yuborilgan va qabul qilingan xabarlar (taxminiy): {total_messages_today}\n"
            f"Mijozlar holati:\n"
            f"- Sovuq (Cold): {conditions['cold']}\n"
            f"- Iliq (Warm): {conditions['warm']}\n"
            f" - Xarid jarayonida (Purchasing): {conditions['purchasing']}\n"
            f"- Yopilgan (Closed): {conditions['closed']}"
        )

        try:
            async with httpx.AsyncClient(timeout=30) as client:
                resp = await client.post(
                    "https://api.groq.com/openai/v1/chat/completions",
                    headers={"Authorization": f"Bearer {GROQ_API_KEY}"},
                    json={
                        "model": GROQ_MODEL, 
                        "messages": [
                            {"role": "system", "content": system_prompt},
                            {"role": "user", "content": stats_text}
                        ], 
                        "max_tokens": 500
                    },
                )
                resp.raise_for_status()

            report = resp.json()["choices"][0]["message"]["content"].strip()
            
            # Send to business owner (org_id is their Telegram chat_id)
            async with httpx.AsyncClient(timeout=10) as tg_client:
                await tg_client.post(
                    f"{TELEGRAM_API}/sendMessage",
                    json={"chat_id": org_id, "text": f"📊 KUNLIK HISOBOT\n\n{report}"},
                )
        except Exception as e:
            print(f"Failed to run audit for {org_id}: {e}")
