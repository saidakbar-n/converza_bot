from pydantic import BaseModel, Field
from typing import Optional


# ── Telegram wire types (minimal subset) ────────────────────────────────────

class TelegramUser(BaseModel):
    id: int
    is_bot: bool = False
    first_name: str
    username: Optional[str] = None
    language_code: Optional[str] = None


class TelegramChat(BaseModel):
    id: int
    type: str                       # private | group | supergroup | channel
    username: Optional[str] = None
    first_name: Optional[str] = None


class TelegramMessage(BaseModel):
    message_id: int
    # `from` is a Python keyword — use alias and populate_by_name so Pydantic
    # accepts the raw Telegram payload AND lets us reference the field as `from_`.
    from_: Optional[TelegramUser] = Field(default=None, alias="from")
    chat: TelegramChat
    date: int
    text: Optional[str] = None

    model_config = {"populate_by_name": True}


class TelegramUpdate(BaseModel):
    update_id: int
    message: Optional[TelegramMessage] = None


# ── Internal domain models ───────────────────────────────────────────────────

class ProspectCreate(BaseModel):
    org_id: str
    platform: str = "telegram"
    external_id: str
    metadata: dict = {}


class MessageCreate(BaseModel):
    org_id: str
    prospect_id: Optional[str] = None
    direction: str                  # inbound | outbound
    content: str
    sent_by: str = "ai"             # ai | human | system
    agent_model: Optional[str] = None
    conversation_id: Optional[str] = None


class DraftCreate(BaseModel):
    org_id: str
    prospect_id: Optional[str] = None
    conversation_id: str
    draft_content: str
    context_summary: Optional[str] = None
    status: str = "pending"


class HITLApprovalRequest(BaseModel):
    prospect_id: str
    conversation_id: str
    draft_reply: str
    context_summary: str


class HITLDecision(BaseModel):
    conversation_id: str
    approved: bool
    edited_reply: Optional[str] = None
