from fastapi import APIRouter, File, UploadFile, HTTPException
from pydantic import BaseModel
from agents.parser import process_document
from services.access_requests import is_user_approved
from services.brand_passport import sync_organization, upsert_passport
from services.config import is_admin_telegram_id
from services.telegram_auth import verify_telegram_auth

router = APIRouter(prefix="/api/web", tags=["web_api"])

class TelegramAuthData(BaseModel):
    id: int
    first_name: str
    last_name: str | None = None
    username: str | None = None
    photo_url: str | None = None
    auth_date: int
    hash: str


class SaveOnboardData(BaseModel):
    org_id: str
    brand_name: str = ""
    industry: str = ""
    target_location: str = "Uzbekistan"
    target_audience: str = ""
    core_offer: str = ""
    tone: str = "Friendly, confident, and concise"
    pricing: list[dict] = []
    faq: list[dict] = []
    objections: list[dict] = []
    raw_notes: str = ""
    click_token: str = ""


@router.post("/auth/telegram")
async def telegram_auth(auth_data: TelegramAuthData):
    data_dict = auth_data.model_dump(exclude_none=True)
    if not verify_telegram_auth(data_dict.copy()):
        raise HTTPException(status_code=403, detail="Invalid Telegram Auth Hash")

    org_id = str(auth_data.id)
    if not is_admin_telegram_id(auth_data.id) and not is_user_approved(
        auth_data.id, auth_data.username
    ):
        raise HTTPException(
            status_code=403,
            detail="Kirish uchun admin tasdig'i kerak. Avval kirish so'rovini yuboring.",
        )

    sync_organization(org_id)
    return {
        "ok": True,
        "org_id": org_id,
        "first_name": auth_data.first_name,
        "username": auth_data.username,
    }


@router.post("/onboard/upload-pdf")
async def upload_pdf(file: UploadFile = File(...)):
    if not file.filename.endswith(".pdf"):
        raise HTTPException(status_code=400, detail="Only PDF files are allowed")

    file_bytes = await file.read()
    brand_passport = await process_document(file_bytes)
    return {"brand_passport": brand_passport}


@router.post("/onboard/save")
async def save_onboard_data(data: SaveOnboardData):
    if not is_admin_telegram_id(data.org_id) and not is_user_approved(data.org_id):
        raise HTTPException(
            status_code=403,
            detail="Brend pasportini saqlash uchun admin tasdig'i kerak.",
        )

    saved = upsert_passport(data.org_id, data.model_dump())
    return {
        "ok": True,
        "brand_id": saved["id"],
        "org_id": saved["org_id"],
        "brand_name": saved.get("brand_name"),
    }
