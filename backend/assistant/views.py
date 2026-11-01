from __future__ import annotations

import json
import logging
import re

from asgiref.sync import sync_to_async
from django.conf import settings
from django.db.models import F
from django.http import JsonResponse, StreamingHttpResponse
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_GET, require_POST

from catalog.attribution import hash_client_ip

from . import limits
from .harness import AssistantUnavailable, TurnResult, history_messages, run_turn
from .leads import purge_once_a_day, remember_contact
from .models import AssistantSetting, Conversation, Message, QuickPrompt

logger = logging.getLogger(__name__)

VISITOR_ID_PATTERN = re.compile(r"^[A-Za-z0-9_-]{16,64}$")
CONVERSATION_ID_PATTERN = re.compile(r"^[0-9a-f]{32}$")
PAGE_TYPES = {choice for choice, _ in QuickPrompt.Page.choices} - {"any"}
MAX_BODY_BYTES = 16_384
ERROR_MESSAGES = {
    "disabled": "The assistant is not available right now. Please use the RFQ form or contact our sales team.",
    "not_configured": "The assistant is not available right now. Please use the RFQ form or contact our sales team.",
    "daily_budget": "The assistant has reached today's usage limit and will be back tomorrow. Please use the RFQ form or contact our sales team.",
    "visitor_limit": "You have reached today's question limit. Please use the RFQ form or contact our sales team, and we will reply personally.",
    "ip_limit": "Today's question limit for your network has been reached. Please use the RFQ form or contact our sales team.",
    "idle_timeout": "The AI service stopped responding. Please try again, or use the RFQ form to reach our sales team.",
    "rate_limited": "The AI service is busy. Please try again in a moment, or use the RFQ form.",
    "provider_error": "The AI service is temporarily unavailable. Please try again later, or use the RFQ form to reach our sales team.",
}


def _error(code: str, status: int) -> JsonResponse:
    return JsonResponse({"code": code, "message": ERROR_MESSAGES.get(code, ERROR_MESSAGES["provider_error"])}, status=status)


def _available(setting: AssistantSetting) -> bool:
    return setting.enabled and bool(settings.DEEPSEEK_API_KEY)


@require_GET
def config_view(request):
    setting = AssistantSetting.load()
    prompts: dict[str, list[str]] = {}
    for prompt in QuickPrompt.objects.filter(is_active=True):
        prompts.setdefault(prompt.page, []).append(prompt.text)
    response = JsonResponse(
        {
            "enabled": _available(setting) and not limits.cost_cap_reached(setting),
            "welcome": setting.welcome_message,
            "privacy_notice": setting.privacy_notice,
            "max_question_chars": setting.max_question_chars,
            "quick_prompts": prompts,
        }
    )
    response["Cache-Control"] = "no-store"
    return response


def _prepare(payload: dict, request) -> tuple[AssistantSetting, Conversation, list[dict], str] | JsonResponse:
    setting = AssistantSetting.load()
    if not _available(setting):
        return _error("disabled", 503)
    visitor_id = payload.get("visitor_id")
    question = payload.get("message")
    if not isinstance(visitor_id, str) or not VISITOR_ID_PATTERN.fullmatch(visitor_id):
        return JsonResponse({"code": "invalid", "message": "Missing visitor identifier."}, status=400)
    if not isinstance(question, str) or not question.strip():
        return JsonResponse({"code": "invalid", "message": "Type a question."}, status=400)
    question = question.strip()
    if len(question) > setting.max_question_chars:
        return JsonResponse(
            {"code": "too_long", "message": f"Please keep questions under {setting.max_question_chars} characters."},
            status=400,
        )
    if "\x00" in question or any(0xD800 <= ord(character) <= 0xDFFF for character in question):
        return JsonResponse({"code": "invalid", "message": "The question contains unsupported characters."}, status=400)
    if limits.cost_cap_reached(setting):
        return _error("daily_budget", 503)
    visitor = limits.visitor_hash(visitor_id)
    ip_hash = hash_client_ip(request)
    exhausted = limits.consume_question(setting, visitor, ip_hash)
    if exhausted:
        return _error(f"{exhausted}_limit", 429)
    purge_once_a_day()

    page_path = payload.get("page") if isinstance(payload.get("page"), str) else ""
    conversation = None
    conversation_id = payload.get("conversation_id")
    if isinstance(conversation_id, str) and CONVERSATION_ID_PATTERN.fullmatch(conversation_id):
        conversation = Conversation.objects.filter(public_id=conversation_id, visitor_hash=visitor).first()
    if conversation is None:
        conversation = Conversation.objects.create(visitor_hash=visitor, ip_hash=ip_hash, page_path=page_path[:500])
    rows = list(
        conversation.messages.filter(error="").order_by("-created_at").values_list("role", "content")[: 8]
    )
    history = history_messages(list(reversed(rows)))
    Message.objects.create(conversation=conversation, role=Message.Role.USER, content=question)
    remember_contact(conversation, question)
    Conversation.objects.filter(pk=conversation.pk).update(
        message_count=F("message_count") + 1,
        last_message_at=timezone.now(),
    )
    conversation._limit_identity = (visitor, ip_hash)
    return setting, conversation, history, question


def _finish(setting: AssistantSetting, conversation: Conversation, result: TurnResult, error: str) -> tuple[int, int]:
    cost = limits.message_cost(setting, result.cache_hit_tokens, result.cache_miss_tokens, result.completion_tokens)
    message = Message.objects.create(
        conversation=conversation,
        role=Message.Role.ASSISTANT,
        content=result.text or ERROR_MESSAGES.get(error, ""),
        citations=result.citations,
        lookups=result.lookups,
        rfq_draft=result.rfq_draft,
        prompt_cache_hit_tokens=result.cache_hit_tokens,
        prompt_cache_miss_tokens=result.cache_miss_tokens,
        completion_tokens=result.completion_tokens,
        cost=cost,
        error=error[:300],
    )
    Conversation.objects.filter(pk=conversation.pk).update(
        message_count=F("message_count") + 1,
        total_cost=F("total_cost") + cost,
        last_message_at=timezone.now(),
    )
    visitor, _ = conversation._limit_identity
    return message.pk, limits.remaining_questions(setting, visitor)


prepare = sync_to_async(_prepare, thread_sensitive=True)
finish = sync_to_async(_finish, thread_sensitive=True)
release = sync_to_async(limits.release_question, thread_sensitive=True)


def _event(data: dict) -> bytes:
    return f"data: {json.dumps(data, ensure_ascii=False)}\n\n".encode()


@csrf_exempt
@require_POST
async def chat_view(request):
    if len(request.body) > MAX_BODY_BYTES:
        return JsonResponse({"code": "invalid", "message": "Request is too large."}, status=413)
    try:
        payload = json.loads(request.body or b"{}")
    except (json.JSONDecodeError, UnicodeDecodeError):
        return JsonResponse({"code": "invalid", "message": "Send JSON."}, status=400)
    if not isinstance(payload, dict):
        return JsonResponse({"code": "invalid", "message": "Send a JSON object."}, status=400)
    prepared = await prepare(payload, request)
    if isinstance(prepared, JsonResponse):
        return prepared
    setting, conversation, history, question = prepared

    async def stream():
        result = TurnResult()
        error = ""
        yield _event({"type": "conversation", "id": conversation.public_id})
        try:
            async for event in run_turn(setting, history, question, result):
                yield _event(event)
        except AssistantUnavailable as exc:
            error = exc.code
            logger.warning("Assistant turn failed", extra={"code": exc.code, "detail": str(exc)[:300]})
        except Exception:
            error = "provider_error"
            logger.exception("Assistant turn crashed")
        if error and not result.text:
            await release(*conversation._limit_identity)
        message_id, remaining = await finish(setting, conversation, result, error)
        if error:
            yield _event({"type": "error", "code": error, "message": ERROR_MESSAGES.get(error, ERROR_MESSAGES["provider_error"]), "partial": bool(result.text)})
        yield _event({"type": "done", "message_id": message_id, "citations": result.citations, "remaining": remaining})

    response = StreamingHttpResponse(stream(), content_type="text/event-stream; charset=utf-8")
    response["Cache-Control"] = "no-store"
    response["X-Accel-Buffering"] = "no"
    return response
