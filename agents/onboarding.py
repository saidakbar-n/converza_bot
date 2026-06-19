import os
import uuid
import logging
import httpx
from db.supabase_client import sb
from models.schemas import TelegramUpdate
from agents.parser import (
    process_document,
    process_freeform_text,
    merge_passports,
)
from services.brand_passport import upsert_passport, fetch_passport_by_org
from services.org_resolver import owner_org_id
from agents.closer import select_invoice_item, send_invoice
from agents.searcher import get_organization
from services.payments import (
    get_payment_provider_token,
    is_configured_provider_token,
    payment_setup_message,
)
from services.brand_passport import get_org_context
from agents.admin_access import handle_admin_command, is_admin_command
from services.access_requests import is_user_approved
from services.config import is_admin_telegram_id, is_production
from services.telegram_send import send_message

TELEGRAM_BOT_TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
TELEGRAM_API = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}"
logger = logging.getLogger(__name__)

WEB_APP_URL = os.getenv("WEB_APP_URL", "").strip()
if is_production() and not WEB_APP_URL:
    raise RuntimeError("WEB_APP_URL is required when ENV=production")


def _web_app_url() -> str:
    return WEB_APP_URL or "http://localhost:8001"

# In-memory simple state machine for onboarding
# In production, use Redis or a DB table for persistent state.
ONBOARDING_STATE = {}

# In-memory progress for the guided chat-based passport fill.
# chat_id -> {"step": int, "answers": {field: value}}
GUIDED_FLOW = {}

# Guided fill questions (Uzbek). Each entry: (passport_field, question, optional).
GUIDED_QUESTIONS = [
    ("brand_name", "1/6 — Brendingiz yoki kompaniyangiz nomi nima?", False),
    ("industry", "2/6 — Qaysi sohada ishlaysiz? (masalan: go'zallik saloni, IT, savdo)", False),
    ("target_audience", "3/6 — Mijozlaringiz kimlar? (maqsadli auditoriya)", False),
    ("core_offer", "4/6 — Asosiy mahsulot yoki xizmatingiz nima?", False),
    (
        "pricing",
        "5/6 — Narx/tariflaringiz qanday? (ixtiyoriy — bilmasangiz \"yo'q\" deb yozing)",
        True,
    ),
    (
        "tone",
        "6/6 — Mijozlar bilan qanday ohangda gaplashasiz? "
        "(masalan: do'stona, rasmiy) — ixtiyoriy, \"yo'q\" deb yozsangiz ham bo'ladi",
        True,
    ),
]

# Words a user can type to start / skip the guided flow.
_FILL_TRIGGERS = ("/fill", "to'ldirish", "toldirish", "qo'lda", "qolda", "savol")
_SKIP_WORDS = ("yo'q", "yoq", "skip", "-", "keyin")
# Minimum length of a free-text message we treat as a business description.
_FREEFORM_MIN_LEN = 40

def _welcome_text(chat_id: int | None = None) -> str:
    lines = [
        "Assalomu alaykum! Men Converza botiman.",
        "",
        "Savollaringizga javob beraman, suhbatni davom ettiraman va kerak bo'lsa Click invoice yuboraman.",
        "",
        "/profile - biznes profilingiz va brend pasporti",
        "/help - nimalar qila olishim",
        "/status - bot va Business ulanish holati",
        "/fill - brend pasportini to'ldirish",
    ]
    if not is_production():
        lines.append("/test_invoice - test to'lovni tekshirish")
    if chat_id and is_admin_telegram_id(chat_id):
        lines += ["", "🛠 Admin: /admin — kirish so'rovlarini boshqarish"]
    return "\n".join(lines)


def _profile_text(passport: dict, name: str = "") -> str:
    p = passport or {}
    greeting = f"Assalomu alaykum, {name}!" if name else "Assalomu alaykum!"
    lines = [greeting, "", "Mana sizning biznes profilingiz:", ""]
    lines.append(f"🏢 Brend: {p.get('brand_name') or '—'}")
    if p.get("industry"):
        lines.append(f"🏷 Soha: {p['industry']}")
    if p.get("target_location"):
        lines.append(f"📍 Hudud: {p['target_location']}")
    if p.get("target_audience"):
        lines.append(f"🎯 Auditoriya: {p['target_audience']}")
    if p.get("core_offer"):
        lines.append(f"💡 Asosiy taklif: {p['core_offer']}")
    if p.get("tone"):
        lines.append(f"🗣 Ohang: {p['tone']}")
    pricing = p.get("pricing") or []
    if pricing:
        lines.append(f"💰 Tariflar: {len(pricing)} ta")
    faq = p.get("faq") or []
    if faq:
        lines.append(f"❓ FAQ: {len(faq)} ta savol")
    objections = p.get("objections") or []
    if objections:
        lines.append(f"🛡 E'tirozlar: {len(objections)} ta")
    lines += [
        "",
        f"Ma'lumotni yangilash: {_web_app_url()}",
        "Yoki shu yerga yangi PDF yuboring — avtomatik yangilanadi.",
    ]
    return "\n".join(lines)


def _no_profile_text(name: str = "") -> str:
    greeting = f"Assalomu alaykum, {name}!" if name else "Assalomu alaykum!"
    return (
        f"{greeting}\n\n"
        "Hozircha siz uchun brend pasport topilmadi.\n\n"
        "Uni to'ldirish uchun uch yo'l bor:\n"
        f"1️⃣ Veb-sahifa orqali: {_web_app_url()}\n"
        "2️⃣ Shu yerga biznesingiz haqidagi PDF fayl(lar)ni yuboring — "
        "men avtomatik tahlil qilib, pasport yarataman.\n"
        "3️⃣ /fill buyrug'i bilan savol-javob orqali to'ldiring.\n\n"
        "Tayyor bo'lgach, /profile buyrug'i bilan ko'rishingiz mumkin."
    )


async def _send_profile_or_prompt(chat_id: int, name: str = "") -> None:
    """Show the owner's saved brand passport, or ask them to set one up."""
    try:
        passport = fetch_passport_by_org(owner_org_id(chat_id))
    except Exception:
        passport = None

    if passport and passport.get("brand_name"):
        ONBOARDING_STATE[chat_id] = "READY"
        await send_message(chat_id, _profile_text(passport, name))
    else:
        ONBOARDING_STATE[chat_id] = "AWAITING_PASSPORT"
        await send_message(chat_id, _no_profile_text(name))

async def _download_telegram_file(file_id: str) -> bytes:
    """Resolve a Telegram file_id to its bytes via getFile + file download."""
    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.get(f"{TELEGRAM_API}/getFile?file_id={file_id}")
        resp.raise_for_status()
        file_path = resp.json()["result"]["file_path"]

        file_url = f"https://api.telegram.org/file/bot{TELEGRAM_BOT_TOKEN}/{file_path}"
        file_resp = await client.get(file_url)
        file_resp.raise_for_status()
        return file_resp.content


def _is_pdf(document) -> bool:
    """A document is a PDF by extension or mime type."""
    name = (document.file_name or "").lower()
    mime = (document.mime_type or "").lower()
    return name.endswith(".pdf") or mime == "application/pdf"


async def _save_passport_merged(chat_id: int, new_data: dict) -> dict:
    """Merge new passport data onto any existing passport and persist it.

    Returns the saved passport. Raises on persistence failure so callers can
    surface a clear Uzbek error to the user.
    """
    org_id = owner_org_id(chat_id)
    try:
        existing = fetch_passport_by_org(org_id)
    except Exception as exc:
        logger.warning("Could not fetch existing passport for %s: %s", org_id, exc)
        existing = None

    merged = merge_passports(existing, new_data)
    saved = upsert_passport(org_id, merged)
    return saved


async def _handle_document(chat_id: int, document) -> None:
    """Process an uploaded PDF into the brand passport (supports multiple PDFs)."""
    if not _is_pdf(document):
        await send_message(
            chat_id,
            "Hozircha faqat PDF fayllarni qabul qila olaman. "
            "Iltimos, biznesingiz haqidagi ma'lumotni PDF ko'rinishida yuboring.",
        )
        return

    await send_message(chat_id, "📄 Hujjat qabul qilindi. Ma'lumotlarni o'qimoqdaman... ⏳")

    # 1. Download the file from Telegram.
    try:
        file_bytes = await _download_telegram_file(document.file_id)
    except Exception as exc:
        logger.exception("Failed to download Telegram file for chat %s", chat_id)
        await send_message(
            chat_id,
            "Faylni yuklab olishda muammo bo'ldi. Iltimos, biroz kutib, faylni qayta yuboring.",
        )
        return

    # 2. Extract + structure the passport.
    try:
        passport_data = await process_document(file_bytes)
    except Exception as exc:
        logger.exception("Failed to parse PDF for chat %s", chat_id)
        await send_message(
            chat_id,
            "Faylni o'qishda xatolik yuz berdi. PDF matnli (skaner emas) ekaniga "
            "ishonch hosil qiling yoki /fill bilan qo'lda to'ldiring.",
        )
        return

    has_content = any(
        (passport_data.get(k) or "").strip()
        for k in ("brand_name", "industry", "core_offer", "target_audience", "raw_notes")
        if isinstance(passport_data.get(k), str)
    )
    if not has_content:
        await send_message(
            chat_id,
            "Bu fayldan foydali ma'lumot topa olmadim. Iltimos, matnli PDF yuboring "
            "yoki /fill bilan savol-javob orqali to'ldiring.",
        )
        return

    # 3. Persist (merging with any earlier PDFs / answers).
    try:
        saved = await _save_passport_merged(chat_id, passport_data)
    except Exception as exc:
        logger.exception("Failed to save passport for chat %s", chat_id)
        await send_message(
            chat_id,
            "Ma'lumotni saqlashda xatolik yuz berdi. Iltimos, birozdan so'ng qayta urinib ko'ring. "
            "(Texnik sabab tizimga qayd etildi.)",
        )
        return

    ONBOARDING_STATE[chat_id] = "READY"
    GUIDED_FLOW.pop(chat_id, None)
    brand_name = saved.get("brand_name") or "kompaniyangiz"
    await send_message(
        chat_id,
        f"✅ Zo'r! {brand_name} uchun brend pasport saqlandi.\n"
        "Yana PDF yuborsangiz, ma'lumotlar ustiga qo'shaman.\n\n"
        "Profilingiz quyida 👇",
    )
    await _send_profile_or_prompt(chat_id)


async def _start_guided_fill(chat_id: int) -> None:
    """Begin the step-by-step chat fill flow."""
    GUIDED_FLOW[chat_id] = {"step": 0, "answers": {}}
    ONBOARDING_STATE[chat_id] = "AWAITING_PASSPORT"
    await send_message(
        chat_id,
        "Keling, brend pasportingizni savol-javob orqali to'ldiramiz. "
        "Har bir savolga javob yozing (bekor qilish uchun /cancel).",
    )
    await send_message(chat_id, GUIDED_QUESTIONS[0][1])


async def _handle_guided_answer(chat_id: int, text: str) -> None:
    """Record an answer for the active guided fill and advance or finish."""
    flow = GUIDED_FLOW.get(chat_id)
    if not flow:
        return

    if text.strip().lower() in ("/cancel", "bekor"):
        GUIDED_FLOW.pop(chat_id, None)
        await send_message(chat_id, "To'ldirish bekor qilindi. Xohlagan vaqtingiz /fill yoki PDF yuboring.")
        return

    step = flow["step"]
    field, _question, optional = GUIDED_QUESTIONS[step]
    answer = text.strip()

    skipped = answer.lower() in _SKIP_WORDS
    if not skipped:
        if field == "pricing":
            flow["answers"]["pricing"] = [
                {"tier": "Asosiy", "price": answer, "features": []}
            ]
        else:
            flow["answers"][field] = answer

    # Advance.
    flow["step"] = step + 1
    if flow["step"] < len(GUIDED_QUESTIONS):
        await send_message(chat_id, GUIDED_QUESTIONS[flow["step"]][1])
        return

    # All questions answered — build + save.
    answers = flow.get("answers", {})
    GUIDED_FLOW.pop(chat_id, None)

    if not answers.get("brand_name"):
        await send_message(chat_id, "Brend nomi kiritilmadi. /fill bilan qaytadan urinib ko'ring.")
        ONBOARDING_STATE[chat_id] = "AWAITING_PASSPORT"
        return

    await send_message(chat_id, "Rahmat! Ma'lumotlarni saqlayapman... ⏳")
    try:
        saved = await _save_passport_merged(chat_id, answers)
    except Exception:
        logger.exception("Failed to save guided passport for chat %s", chat_id)
        await send_message(
            chat_id,
            "Ma'lumotni saqlashda xatolik yuz berdi. Iltimos, birozdan so'ng qayta urinib ko'ring.",
        )
        ONBOARDING_STATE[chat_id] = "AWAITING_PASSPORT"
        return

    ONBOARDING_STATE[chat_id] = "READY"
    brand_name = saved.get("brand_name") or "kompaniyangiz"
    await send_message(chat_id, f"✅ {brand_name} uchun brend pasport saqlandi. Profilingiz 👇")
    await _send_profile_or_prompt(chat_id)


async def _handle_freeform_description(chat_id: int, text: str) -> None:
    """Structure a free-text business description into a passport and save it."""
    await send_message(chat_id, "Ma'lumotni tahlil qilyapman... ⏳")
    try:
        passport_data = await process_freeform_text(text)
    except Exception:
        logger.exception("Failed to structure free text for chat %s", chat_id)
        await send_message(
            chat_id,
            "Matnni tahlil qilishda xatolik bo'ldi. /fill bilan savol-javob orqali urinib ko'ring.",
        )
        return

    if not (passport_data.get("brand_name") or passport_data.get("core_offer")):
        # Keep whatever we extracted as notes, but tell the user we need more.
        passport_data.setdefault("raw_notes", text)

    try:
        saved = await _save_passport_merged(chat_id, passport_data)
    except Exception:
        logger.exception("Failed to save free-text passport for chat %s", chat_id)
        await send_message(
            chat_id,
            "Ma'lumotni saqlashda xatolik yuz berdi. Iltimos, birozdan so'ng qayta urinib ko'ring.",
        )
        return

    ONBOARDING_STATE[chat_id] = "READY"
    brand_name = saved.get("brand_name") or "kompaniyangiz"
    await send_message(
        chat_id,
        f"✅ {brand_name} uchun brend pasport saqlandi. "
        "Aniqroq bo'lishi uchun PDF ham yuborishingiz mumkin. Profilingiz 👇",
    )
    await _send_profile_or_prompt(chat_id)


async def handle_onboarding_message(update: TelegramUpdate) -> None:
    msg = update.message
    if not msg:
        return

    chat_id = msg.chat.id
    text = msg.text or ""
    user_name = (msg.from_.first_name if msg.from_ else None) or msg.chat.first_name or ""
    sender = msg.from_
    sender_id = sender.id if sender else chat_id
    sender_username = sender.username if sender else None

    if is_admin_command(text):
        await handle_admin_command(chat_id, sender_id, text)
        return

    if not is_admin_telegram_id(sender_id) and not is_user_approved(sender_id, sender_username):
        await send_message(
            chat_id,
            "Kirish uchun admin tasdig'i kerak.\n\n"
            f"Avval veb-sahifada ({_web_app_url()}) kirish so'rovini yuboring. "
            "Tasdiqlangach, bu yerda ham brend pasportini sozlashingiz mumkin bo'ladi.",
        )
        return

    # Document uploads (PDFs) — now read from the typed schema field so they are
    # reliably detected for single and multiple files.
    if msg.document:
        await _handle_document(chat_id, msg.document)
        return

    state = ONBOARDING_STATE.get(chat_id)

    # An active guided fill consumes plain-text answers (but not slash commands
    # other than /cancel, handled inside the flow).
    if chat_id in GUIDED_FLOW and not (text.startswith("/") and not text.startswith("/cancel")):
        await _handle_guided_answer(chat_id, text)
        return

    if text.startswith("/fill") or text.strip().lower() in _FILL_TRIGGERS:
        await _start_guided_fill(chat_id)
        return

    if text.startswith("/start"):
        await send_message(chat_id, _welcome_text(chat_id))
        await _send_profile_or_prompt(chat_id, user_name)
        return

    if text.startswith("/profile") or text.startswith("/passport"):
        await _send_profile_or_prompt(chat_id, user_name)
        return

    if text.startswith("/help"):
        await send_message(
            chat_id,
            "Men Converza haqida qisqa javob beraman, mijoz holatini kuzataman va xaridga tayyor mijozga test invoice yubora olaman.\n\n"
            "Oddiy savol yozib ko'ring yoki /test_invoice bilan to'lov oqimini tekshiring."
        )
        return

    if text.startswith("/status"):
        org_id = owner_org_id(chat_id)
        ctx = get_org_context(org_id)
        passport = fetch_passport_by_org(org_id)
        has_passport = bool(passport and passport.get("brand_name"))
        conn_id = ctx.get("business_connection_id")
        lines = [
            "📊 Converza holati",
            "",
            f"✅ Bot ishlayapti",
            f"{'✅' if has_passport else '❌'} Brend pasporti: "
            + (passport.get("brand_name") if has_passport else "to'ldirilmagan"),
            f"{'✅' if conn_id else '❌'} Telegram Business ulanishi: "
            + ("faol" if conn_id else "ulanmagan"),
        ]
        if not conn_id:
            lines += [
                "",
                "Mijozlar xabarlarini qabul qilish uchun:",
                "Telegram → Sozlamalar → Business → Chatbots → "
                f"@{os.getenv('TELEGRAM_BOT_USERNAME', 'ConverzaSales_bot')} ni qo'shing.",
            ]
        lines.append(f"\nVeb: {_web_app_url()}")
        await send_message(chat_id, "\n".join(lines))
        return

    if text.startswith("/test_invoice"):
        if is_production():
            await send_message(chat_id, "Test invoice ishlab chiqarishda o'chirilgan.")
            return
        org = await get_organization(owner_org_id(chat_id))
        provider_token = get_payment_provider_token(org)
        if not is_configured_provider_token(provider_token):
            await send_message(chat_id, payment_setup_message())
            return

        invoice_item = select_invoice_item(org.get("brand_context", {}))
        resp = await send_invoice(chat_id, provider_token, f"test_{chat_id}", invoice_item)
        if resp.is_success:
            await send_message(chat_id, "Test invoice yuborildi.")
        else:
            await send_message(chat_id, f"Invoice yuborilmadi: {resp.text[:300]}")
        return

    # Default fallback
    if state == "AWAITING_DOCUMENTS":
        await send_message(chat_id, "Kompaniyangiz haqidagi PDF faylni kutmoqdaman. Iltimos, fayl yuklang.")
    elif state == "AWAITING_PASSPORT":
        # A substantial free-text message is treated as a business description
        # and structured into a passport via the LLM.
        if text and not text.startswith("/") and len(text.strip()) >= _FREEFORM_MIN_LEN:
            await _handle_freeform_description(chat_id, text)
            return
        await send_message(
            chat_id,
            "Brend pasportingiz hali to'ldirilmagan. Uni tayyorlash uchun:\n"
            "📄 Biznesingiz haqidagi PDF fayl(lar)ni yuboring,\n"
            "✍️ /fill bilan savol-javob orqali to'ldiring,\n"
            "yoki biznesingizni shu yerda bir-ikki gap bilan ta'riflab yozing.\n"
            f"Veb-sahifa: {_web_app_url()}",
        )
    else:
        cmds = "/profile, /help, /status"
        if not is_production():
            cmds += ", /test_invoice"
        await send_message(chat_id, f"Savolingizni yozing yoki buyruqlardan birini tanlang: {cmds}.")

async def handle_business_connection(update: TelegramUpdate) -> None:
    conn = update.business_connection
    if not conn:
        return

    # conn['user']['id'] is the business owner
    org_id = str(conn["user"]["id"])
    connection_id = conn["id"]
    is_enabled = conn.get("is_enabled", False)
    owner_chat_id = conn.get("user", {}).get("id")

    from services.brand_passport import sync_organization

    sync_organization(org_id)
    try:
        sb.table("organizations").upsert({
            "id": org_id,
            "business_connection_id": connection_id if is_enabled else None,
        }).execute()
    except Exception as e:
        logger.warning("business_connection upsert skipped for %s: %s", org_id, e)

    if owner_chat_id:
        if is_enabled:
            await send_message(
                int(owner_chat_id),
                "✅ Telegram Business ulanishi faollashtirildi!\n\n"
                "Endi mijozlar biznes hisobingizga yozganda DM Closer avtomatik javob beradi.",
            )
        else:
            await send_message(
                int(owner_chat_id),
                "⚠️ Telegram Business ulanishi o'chirildi.\n\n"
                "Mijozlar xabarlarini qayta qabul qilish uchun botni Business → Chatbots orqali qayta ulang.",
            )
