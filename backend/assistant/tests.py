import json
from datetime import timedelta
from decimal import Decimal
from unittest.mock import patch

import httpx
from asgiref.sync import async_to_sync
from django.core.cache import cache
from django.test import TestCase, override_settings
from django.utils import timezone

from catalog.models import Category, Inquiry, KnowledgeEntry, Product, ProductSpecValue
from catalog.spec_templates import TRAILER_AXLE_PARAMETERS, apply_template
from catalog.specs import refresh_search_document

from . import knowledge
from .leads import build_rfq_draft, detect_contact, purge_expired_conversations
from .models import AssistantSetting, Conversation, Message, QuickPrompt

VISITOR = "visitor-0123456789abcdef"


def sse(*chunks) -> bytes:
    body = "".join(f"data: {json.dumps(chunk)}\n\n" for chunk in chunks) + "data: [DONE]\n\n"
    return body.encode()


def tool_call(name: str, arguments: dict, call_id: str = "call_1") -> bytes:
    return sse(
        {"choices": [{"index": 0, "delta": {"tool_calls": [{"index": 0, "id": call_id, "type": "function", "function": {"name": name, "arguments": json.dumps(arguments)}}]}, "finish_reason": None}]},
        {"choices": [{"index": 0, "delta": {}, "finish_reason": "tool_calls"}]},
        {"choices": [], "usage": {"prompt_cache_hit_tokens": 100, "prompt_cache_miss_tokens": 50, "completion_tokens": 10}},
    )


def answer(text: str) -> bytes:
    return sse(
        {"choices": [{"index": 0, "delta": {"content": text[: len(text) // 2]}, "finish_reason": None}]},
        {"choices": [{"index": 0, "delta": {"content": text[len(text) // 2 :]}, "finish_reason": "stop"}]},
        {"choices": [], "usage": {"prompt_cache_hit_tokens": 200, "prompt_cache_miss_tokens": 20, "completion_tokens": 40}},
    )


class ScriptedDeepSeek:
    """Mock transport answering each chat-completions call from a script."""

    def __init__(self, *responses):
        self.responses = list(responses)
        self.requests: list[dict] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(json.loads(request.content))
        body = self.responses.pop(0)
        if isinstance(body, httpx.Response):
            return body
        return httpx.Response(200, content=body, headers={"content-type": "text/event-stream"})

    def transport(self):
        return httpx.MockTransport(self)


def events(response) -> list[dict]:
    return response.events


async def _collect(iterator) -> bytes:
    return b"".join([chunk async for chunk in iterator])


def consume(response):
    """Read a streamed answer while the mock transport is still installed."""
    if getattr(response, "streaming", False):
        raw = async_to_sync(_collect)(response.streaming_content).decode()
        response.events = [json.loads(block[6:]) for block in raw.split("\n\n") if block.startswith("data: ")]
    return response


@override_settings(DEEPSEEK_API_KEY="test-key", DEEPSEEK_BASE_URL="https://deepseek.test")
class AssistantChatTests(TestCase):
    def setUp(self):
        cache.clear()
        self.category = Category.objects.create(name="Trailer Axles", slug="trailer-axles")
        apply_template(self.category, TRAILER_AXLE_PARAMETERS)
        self.product = Product.objects.create(
            category=self.category, title="3,500 lb Electric Axle", slug="ea-3500", model="EA-3500", sku="EA-3500",
            short_description="Electric brake axle", status=Product.Status.PUBLISHED,
        )
        field = self.category.spec_fields.get(key="rated_capacity")
        ProductSpecValue.objects.create(product=self.product, field=field, number_value=Decimal("3500"), entered_unit="lb")
        self.draft = Product.objects.create(
            category=self.category, title="Unreleased prototype", slug="secret-proto", model="PROTO", sku="PROTO",
            short_description="Electric secret", status=Product.Status.DRAFT,
        )
        refresh_search_document(self.product.pk)
        refresh_search_document(self.draft.pk)
        setting = AssistantSetting.load()
        setting.price_input_cache_hit = Decimal("0.5")
        setting.price_input_cache_miss = Decimal("2")
        setting.price_output = Decimal("8")
        setting.save()

    def chat(self, message: str, script: ScriptedDeepSeek, **extra):
        with patch("assistant.harness.default_transport", return_value=script.transport()):
            return consume(self.client.post(
                "/api/assistant/chat/",
                json.dumps({"visitor_id": VISITOR, "message": message, "page": "/products", **extra}),
                content_type="application/json",
            ))

    def test_answer_streams_with_citations_from_pages_actually_read(self):
        script = ScriptedDeepSeek(
            tool_call("search_site", {"query": "3500 lb electric"}),
            tool_call("read_page", {"ref": "product:ea-3500"}, "call_2"),
            answer("The EA-3500 is rated 3,500 lb (1,588 kg)."),
        )
        response = self.chat("Which axle carries 3500 lb with electric brakes?", script)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Type"], "text/event-stream; charset=utf-8")
        stream = events(response)
        self.assertEqual(stream[0]["type"], "conversation")
        text = "".join(event["text"] for event in stream if event["type"] == "delta")
        self.assertEqual(text, "The EA-3500 is rated 3,500 lb (1,588 kg).")
        done = stream[-1]
        self.assertEqual(done["type"], "done")
        self.assertEqual(done["citations"], [{"title": "3,500 lb Electric Axle", "url": "/products/trailer-axles/ea-3500"}])
        self.assertEqual(done["remaining"], 9)

        # The search result handed to the model excludes the draft product.
        search_result = script.requests[1]["messages"][-1]["content"]
        self.assertIn("product:ea-3500", search_result)
        self.assertNotIn("secret-proto", search_result)
        # The page read contains both unit systems.
        page = script.requests[2]["messages"][-1]["content"]
        self.assertIn("3,500 lb | 1,588 kg", page)
        # The system prompt is identical between rounds so the prefix cache applies.
        self.assertEqual(script.requests[0]["messages"][0], script.requests[2]["messages"][0])

        message = Message.objects.get(role=Message.Role.ASSISTANT)
        self.assertEqual(message.prompt_cache_hit_tokens, 400)
        self.assertEqual(message.completion_tokens, 60)
        # (400 * 0.5 + 120 * 2 + 60 * 8) / 1e6
        self.assertEqual(message.cost, Decimal("0.000920"))
        self.assertEqual([lookup["tool"] for lookup in message.lookups], ["search_site", "read_page"])

    def test_read_page_never_exposes_unpublished_content(self):
        self.assertIsNone(knowledge.read_page("product:secret-proto"))
        self.assertIsNone(knowledge.read_page("knowledge:missing"))
        KnowledgeEntry.objects.create(title="Packing", slug="packing", body="Steel frames", is_published=False)
        self.assertIsNone(knowledge.read_page("knowledge:packing"))
        self.assertIsNone(knowledge.read_page("../../etc/passwd"))
        self.assertEqual(knowledge.search("prototype"), [])

    def test_lookup_limit_stops_tools_and_forces_an_answer(self):
        setting = AssistantSetting.load()
        setting.max_lookups = 1
        setting.save()
        script = ScriptedDeepSeek(
            tool_call("search_site", {"query": "axle"}),
            answer("Here is what I found."),
        )
        stream = events(self.chat("Show me axles", script))
        self.assertEqual(stream[-1]["type"], "done")
        tools_offered = [tool["function"]["name"] for tool in script.requests[1].get("tools", [])]
        self.assertNotIn("search_site", tools_offered)
        self.assertNotIn("read_page", tools_offered)

    def test_rfq_draft_is_validated_and_never_submitted(self):
        script = ScriptedDeepSeek(
            tool_call(
                "prepare_rfq",
                {
                    "products": ["ea-3500", "secret-proto"],
                    "categories": ["trailer-axles", "unknown"],
                    "specifications": {"trailer-axles": {"rated_capacity": "3500 lb", "inner_bearing": "x"}},
                    "quantity": 40,
                    "email": "buyer@example.com",
                },
            ),
            answer("Please review the RFQ draft and submit it."),
        )
        stream = events(self.chat("Quote 40 axles, email buyer@example.com", script))
        draft = next(event["draft"] for event in stream if event["type"] == "rfq_draft")
        self.assertEqual([item["slug"] for item in draft["products"]], ["ea-3500"])
        self.assertEqual(draft["requested_categories"], ["trailer-axles"])
        self.assertEqual(draft["specifications"], {"trailer-axles": {"rated_capacity": "3500 lb"}})
        self.assertEqual(draft["quantity"], 40)
        self.assertFalse(Inquiry.objects.exists())
        conversation = Conversation.objects.get()
        self.assertEqual(conversation.kind, Conversation.Kind.LEAD)
        self.assertIn("buyer@example.com", conversation.contact_details)

    def test_daily_visitor_limit_and_question_length(self):
        setting = AssistantSetting.load()
        setting.visitor_daily_limit = 1
        setting.save()
        self.assertEqual(self.chat("hello", ScriptedDeepSeek(answer("Hi."))).status_code, 200)
        blocked = self.chat("again", ScriptedDeepSeek())
        self.assertEqual(blocked.status_code, 429)
        self.assertEqual(blocked.json()["code"], "visitor_limit")
        too_long = self.chat("x" * 501, ScriptedDeepSeek())
        self.assertEqual(too_long.status_code, 400)

    def test_ip_limit_applies_across_visitor_ids(self):
        setting = AssistantSetting.load()
        setting.ip_daily_limit = 1
        setting.save()
        self.assertEqual(self.chat("hello", ScriptedDeepSeek(answer("Hi."))).status_code, 200)
        with patch("assistant.harness.default_transport", return_value=ScriptedDeepSeek().transport()):
            response = self.client.post(
                "/api/assistant/chat/",
                json.dumps({"visitor_id": "another-visitor-0123456789", "message": "hi"}),
                content_type="application/json",
            )
        self.assertEqual(response.status_code, 429)
        self.assertEqual(response.json()["code"], "ip_limit")

    def test_daily_cost_cap_pauses_the_assistant(self):
        setting = AssistantSetting.load()
        setting.daily_cost_cap = Decimal("0.0001")
        setting.save()
        conversation = Conversation.objects.create(visitor_hash="x")
        Message.objects.create(conversation=conversation, role=Message.Role.ASSISTANT, content="earlier", cost=Decimal("0.0002"))
        response = self.chat("hello", ScriptedDeepSeek())
        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.json()["code"], "daily_budget")
        self.assertFalse(self.client.get("/api/assistant/config/").json()["enabled"])

    def test_master_switch_and_missing_key_disable_the_assistant(self):
        setting = AssistantSetting.load()
        setting.enabled = False
        setting.save()
        self.assertEqual(self.chat("hello", ScriptedDeepSeek()).status_code, 503)
        self.assertFalse(self.client.get("/api/assistant/config/").json()["enabled"])
        setting.enabled = True
        setting.save()
        with override_settings(DEEPSEEK_API_KEY=""):
            self.assertEqual(self.chat("hello", ScriptedDeepSeek()).status_code, 503)

    def test_provider_failure_returns_a_clear_message_and_refunds_the_question(self):
        script = ScriptedDeepSeek(httpx.Response(500, content=b"upstream down"))
        stream = events(self.chat("hello", script))
        error = next(event for event in stream if event["type"] == "error")
        self.assertEqual(error["code"], "provider_error")
        self.assertIn("RFQ form", error["message"])
        self.assertEqual(stream[-1]["remaining"], 10)
        self.assertEqual(Message.objects.get(role=Message.Role.ASSISTANT).error, "provider_error")

    def test_empty_answer_is_reported_as_unavailable(self):
        stream = events(self.chat("hello", ScriptedDeepSeek(sse({"choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}]}))))
        self.assertEqual(next(event for event in stream if event["type"] == "error")["code"], "provider_error")
        self.assertEqual(stream[-1]["remaining"], 10)

    def test_idle_timeout_is_reported(self):
        def silent(request):
            raise httpx.ReadTimeout("no data", request=request)

        with patch("assistant.harness.default_transport", return_value=httpx.MockTransport(silent)):
            response = consume(self.client.post(
                "/api/assistant/chat/",
                json.dumps({"visitor_id": VISITOR, "message": "hello"}),
                content_type="application/json",
            ))
        error = next(event for event in events(response) if event["type"] == "error")
        self.assertEqual(error["code"], "idle_timeout")

    def test_cross_site_browser_posts_are_rejected(self):
        response = self.client.post(
            "/api/assistant/chat/",
            json.dumps({"visitor_id": VISITOR, "message": "hello"}),
            content_type="application/json",
            HTTP_ORIGIN="https://evil.example",
        )
        self.assertEqual(response.status_code, 403)

    def test_conversation_continues_with_history_for_the_same_visitor_only(self):
        first = events(self.chat("My trailer weighs 3 tons", ScriptedDeepSeek(answer("Noted."))))
        conversation_id = first[0]["id"]
        script = ScriptedDeepSeek(answer("Then choose a tandem setup."))
        self.chat("What should I choose?", script, conversation_id=conversation_id)
        roles = [message["role"] for message in script.requests[0]["messages"]]
        self.assertEqual(roles, ["system", "user", "assistant", "user"])
        with patch("assistant.harness.default_transport", return_value=ScriptedDeepSeek(answer("Hello.")).transport()):
            other = consume(self.client.post(
                "/api/assistant/chat/",
                json.dumps({"visitor_id": "someone-else-0123456789", "message": "hi", "conversation_id": conversation_id}),
                content_type="application/json",
            ))
        self.assertNotEqual(events(other)[0]["id"], conversation_id)

    def test_config_returns_quick_prompts_by_page(self):
        QuickPrompt.objects.create(page="product", text="Compare this product")
        QuickPrompt.objects.create(page="any", text="Help me choose")
        QuickPrompt.objects.create(page="rfq", text="Hidden", is_active=False)
        config = self.client.get("/api/assistant/config/").json()
        self.assertTrue(config["enabled"])
        self.assertEqual(config["quick_prompts"], {"product": ["Compare this product"], "any": ["Help me choose"]})
        self.assertEqual(config["max_question_chars"], 500)


class AssistantLeadTests(TestCase):
    def test_contact_detection(self):
        self.assertEqual(detect_contact("mail me at a.b@example.co.uk"), "a.b@example.co.uk")
        self.assertEqual(detect_contact("WhatsApp +1 (555) 123-4567"), "+1 (555) 123-4567")
        self.assertEqual(detect_contact("I need 3500 lb axles, 20 sets"), "")

    def test_rfq_draft_drops_unknown_values(self):
        draft = build_rfq_draft({"quantity": True, "email": "x" * 300, "application": "missing", "products": "not-a-list"})
        self.assertNotIn("quantity", draft)
        self.assertEqual(len(draft["email"]), 254)
        self.assertNotIn("application", draft)

    def test_retention_keeps_rfq_conversations_and_leads_longer(self):
        now = timezone.now()
        general = Conversation.objects.create(visitor_hash="a", last_message_at=now - timedelta(days=91))
        fresh = Conversation.objects.create(visitor_hash="b", last_message_at=now - timedelta(days=10))
        lead = Conversation.objects.create(visitor_hash="c", kind=Conversation.Kind.LEAD, last_message_at=now - timedelta(days=200))
        old_lead = Conversation.objects.create(visitor_hash="d", kind=Conversation.Kind.LEAD, last_message_at=now - timedelta(days=400))
        inquiry = Inquiry.objects.create(name="Buyer", email="b@example.com", privacy_consent=True)
        linked = Conversation.objects.create(visitor_hash="e", kind=Conversation.Kind.INQUIRY, inquiry=inquiry, last_message_at=now - timedelta(days=900))
        purge_expired_conversations(now)
        remaining = set(Conversation.objects.values_list("pk", flat=True))
        self.assertEqual(remaining, {fresh.pk, lead.pk, linked.pk})
        self.assertNotIn(general.pk, remaining)
        self.assertNotIn(old_lead.pk, remaining)

    @override_settings(TURNSTILE_REQUIRED=False)
    @patch("catalog.views.send_inquiry_notification.delay")
    def test_rfq_submitted_from_the_panel_links_the_conversation(self, mocked_task):
        from catalog.attribution import hash_client_ip
        from django.test import RequestFactory

        ip_hash = hash_client_ip(RequestFactory().get("/"))
        conversation = Conversation.objects.create(visitor_hash="v", ip_hash=ip_hash, kind=Conversation.Kind.LEAD)
        response = self.client.post(
            "/api/inquiries/",
            {"name": "Buyer", "email": "buyer@example.com", "privacy_consent": True, "assistant_conversation": conversation.public_id},
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 201, response.content)
        conversation.refresh_from_db()
        self.assertEqual(conversation.kind, Conversation.Kind.INQUIRY)
        self.assertEqual(conversation.inquiry.reference, response.json()["reference"])
