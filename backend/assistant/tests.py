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
from .models import AIProvider, AssistantSetting, Conversation, Message, QuickPrompt

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
        self.hosts: list[str] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(json.loads(request.content))
        self.hosts.append(request.url.host)
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


def add_provider(name: str, host: str, priority: int = 10, **prices) -> AIProvider:
    provider = AIProvider(name=name, base_url=f"https://{host}", model_name=f"{name.lower()}-chat", priority=priority, **prices)
    provider.set_api_key(f"sk-{name.lower()}-0123456789abcdef")
    provider.save()
    return provider


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
        AssistantSetting.load().save()
        self.provider = add_provider(
            "DeepSeek", "deepseek.test",
            price_input_cache_hit=Decimal("0.5"), price_input_cache_miss=Decimal("2"), price_output=Decimal("8"),
        )

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
        self.assertEqual(message.provider_name, "DeepSeek")
        self.assertEqual(script.requests[0]["model"], "deepseek-chat")
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
        self.provider.set_api_key("")
        self.provider.save()
        self.assertEqual(self.chat("hello", ScriptedDeepSeek()).status_code, 503)
        self.assertFalse(self.client.get("/api/assistant/config/").json()["enabled"])
        add_provider("Qwen", "qwen.test", is_active=False)
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

    def test_failed_provider_switches_to_the_next_one_without_the_visitor_noticing(self):
        add_provider("Qwen", "qwen.test", priority=20, price_output=Decimal("4"))
        script = ScriptedDeepSeek(
            httpx.Response(401, content=b"invalid key"),
            sse(
                {"choices": [{"index": 0, "delta": {"content": "Hello from the backup."}, "finish_reason": "stop"}]},
                {"choices": [], "usage": {"prompt_tokens": 300, "prompt_tokens_details": {"cached_tokens": 100}, "completion_tokens": 50}},
            ),
        )
        stream = events(self.chat("hello", script))
        self.assertEqual(script.hosts, ["deepseek.test", "qwen.test"])
        self.assertEqual(script.requests[1]["model"], "qwen-chat")
        self.assertFalse(any(event["type"] == "error" for event in stream))
        self.assertEqual("".join(event["text"] for event in stream if event["type"] == "delta"), "Hello from the backup.")
        message = Message.objects.get(role=Message.Role.ASSISTANT)
        self.assertEqual(message.provider_name, "Qwen")
        # OpenAI-style usage: 100 cached, 200 uncached, priced with the backup's prices.
        self.assertEqual((message.prompt_cache_hit_tokens, message.prompt_cache_miss_tokens), (100, 200))
        self.assertEqual(message.cost, Decimal("0.000200"))
        self.provider.refresh_from_db()
        self.assertIn("HTTP 401", self.provider.last_error)
        self.assertIsNotNone(self.provider.last_failure_at)

        # The failed provider is tried last for a while.
        follow_up = ScriptedDeepSeek(answer("Still the backup."))
        self.chat("again", follow_up)
        self.assertEqual(follow_up.hosts, ["qwen.test"])

    def test_switch_happens_mid_answer_after_lookups(self):
        add_provider("Qwen", "qwen.test", priority=20)
        script = ScriptedDeepSeek(
            tool_call("search_site", {"query": "axle"}),
            httpx.Response(503, content=b"overloaded"),
            answer("Found the EA-3500."),
        )
        stream = events(self.chat("Show me axles", script))
        self.assertEqual(script.hosts, ["deepseek.test", "deepseek.test", "qwen.test"])
        # The backup continues the same conversation, including the search result.
        self.assertEqual(script.requests[2]["messages"][-1]["role"], "tool")
        self.assertEqual(stream[-1]["type"], "done")
        self.assertEqual(Message.objects.get(role=Message.Role.ASSISTANT).provider_name, "DeepSeek, Qwen")

    def test_no_switch_once_text_was_shown(self):
        add_provider("Qwen", "qwen.test", priority=20)

        class BreaksAfterFirstChunk(httpx.AsyncByteStream):
            async def __aiter__(self):
                yield f"data: {json.dumps({'choices': [{'index': 0, 'delta': {'content': 'Partial'}, 'finish_reason': None}]})}\n\n".encode()
                raise httpx.ReadTimeout("silent")

        script = ScriptedDeepSeek(httpx.Response(200, stream=BreaksAfterFirstChunk(), headers={"content-type": "text/event-stream"}))
        stream = events(self.chat("hello", script))
        self.assertEqual(script.hosts, ["deepseek.test"])
        error = next(event for event in stream if event["type"] == "error")
        self.assertEqual(error["code"], "idle_timeout")
        self.assertTrue(error["partial"])
        # The visitor saw part of an answer: it is saved and the question is not refunded.
        self.assertEqual(Message.objects.get(role=Message.Role.ASSISTANT).content, "Partial")
        self.assertEqual(stream[-1]["remaining"], 9)

    def test_all_providers_failing_reports_the_last_error(self):
        add_provider("Qwen", "qwen.test", priority=20)
        script = ScriptedDeepSeek(httpx.Response(500, content=b"down"), httpx.Response(429, content=b"busy"))
        stream = events(self.chat("hello", script))
        self.assertEqual(next(event for event in stream if event["type"] == "error")["code"], "rate_limited")
        self.assertEqual(stream[-1]["remaining"], 10)

    def test_unreadable_key_is_skipped(self):
        backup = add_provider("Qwen", "qwen.test", priority=20)
        AIProvider.objects.filter(pk=self.provider.pk).update(encrypted_api_key="gAAAA-not-a-valid-token")
        script = ScriptedDeepSeek(answer("Hi."))
        self.chat("hello", script)
        self.assertEqual(script.hosts, ["qwen.test"])
        self.assertEqual(Message.objects.get(role=Message.Role.ASSISTANT).provider_name, backup.name)

    def test_extra_parameters_adapt_requests_to_models_that_reject_defaults(self):
        self.provider.extra_parameters = {"max_tokens": None, "temperature": None, "max_completion_tokens": 900, "reasoning_effort": "low"}
        self.provider.save()
        script = ScriptedDeepSeek(answer("Hi."))
        self.chat("hello", script)
        body = script.requests[0]
        self.assertNotIn("max_tokens", body)
        self.assertNotIn("temperature", body)
        self.assertEqual((body["max_completion_tokens"], body["reasoning_effort"]), (900, "low"))
        self.assertEqual(body["model"], "deepseek-chat")
        self.assertTrue(body["stream"])

    def test_error_inside_a_stream_switches_provider(self):
        add_provider("Qwen", "qwen.test", priority=20)
        script = ScriptedDeepSeek(sse({"error": {"message": "model overloaded"}}), answer("From the backup."))
        stream = events(self.chat("hello", script))
        self.assertEqual(script.hosts, ["deepseek.test", "qwen.test"])
        self.assertFalse(any(event["type"] == "error" for event in stream))
        self.provider.refresh_from_db()
        self.assertIn("model overloaded", self.provider.last_error)


class AIProviderAdminTests(TestCase):
    def setUp(self):
        from django.contrib.auth import get_user_model

        self.admin_user = get_user_model().objects.create_superuser("boss", "boss@example.com", "pw-0123456789")
        self.client.force_login(self.admin_user)

    def post(self, url, follow=False, **fields):
        data = {
            "name": "DeepSeek", "base_url": "https://api.deepseek.com/", "model_name": "deepseek-chat", "priority": 10,
            "is_active": "on", "price_input_cache_hit": "0.5", "price_input_cache_miss": "2", "price_output": "8", **fields,
        }
        return self.client.post(url, data, follow=follow)

    def test_key_is_stored_encrypted_kept_when_blank_and_never_shown(self):
        response = self.post("/admin/assistant/aiprovider/add/", api_key="sk-live-secret-abcdef123456")
        self.assertEqual(response.status_code, 302)
        provider = AIProvider.objects.get()
        self.assertEqual(provider.base_url, "https://api.deepseek.com")
        self.assertNotIn("sk-live-secret", provider.encrypted_api_key)
        self.assertEqual(provider.get_api_key(), "sk-live-secret-abcdef123456")

        change_url = f"/admin/assistant/aiprovider/{provider.pk}/change/"
        page = self.client.get(change_url).content.decode()
        self.assertNotIn("sk-live-secret-abcdef123456", page)
        self.assertNotIn(provider.encrypted_api_key, page)
        self.assertIn("ends with 3456", page)

        self.post(change_url, model_name="deepseek-reasoner")
        provider.refresh_from_db()
        self.assertEqual(provider.model_name, "deepseek-reasoner")
        self.assertEqual(provider.get_api_key(), "sk-live-secret-abcdef123456")

        self.post(change_url, clear_api_key="on")
        provider.refresh_from_db()
        self.assertEqual(provider.encrypted_api_key, "")

    def test_plain_http_address_is_allowed_with_a_warning(self):
        response = self.post("/admin/assistant/aiprovider/add/", base_url="http://ollama:11434/v1", api_key="sk-x", follow=True)
        self.assertEqual(AIProvider.objects.get().base_url, "http://ollama:11434/v1")
        self.assertTrue(any("plain http" in str(message) for message in response.context["messages"]))
        for bad in ("ftp://example.com", "api.deepseek.com", "https://user:pw@example.com", "https://example.com/v1?x=1"):
            self.post("/admin/assistant/aiprovider/add/", name="Bad", base_url=bad)
        self.assertEqual(AIProvider.objects.count(), 1)

    def test_extra_parameters_must_be_an_object_and_cannot_override_core_fields(self):
        self.post("/admin/assistant/aiprovider/add/", extra_parameters='["x"]')
        self.post("/admin/assistant/aiprovider/add/", extra_parameters='{"messages": []}')
        self.assertFalse(AIProvider.objects.exists())
        self.post("/admin/assistant/aiprovider/add/", extra_parameters='{"max_tokens": null, "max_completion_tokens": 1200}')
        self.assertEqual(AIProvider.objects.get().extra_parameters, {"max_tokens": None, "max_completion_tokens": 1200})

    def test_blank_extra_parameters_are_saved_as_empty(self):
        self.post("/admin/assistant/aiprovider/add/", extra_parameters="")
        self.assertEqual(AIProvider.objects.get().extra_parameters, {})

    def test_full_endpoint_address_is_trimmed(self):
        self.post("/admin/assistant/aiprovider/add/", base_url="https://dashscope.example.com/compatible-mode/v1/chat/completions")
        self.assertEqual(AIProvider.objects.get().base_url, "https://dashscope.example.com/compatible-mode/v1")

    def test_connection_test_action_reports_each_provider(self):
        good = add_provider("Good", "good.test")
        bad = add_provider("Bad", "bad.test")

        no_tools = add_provider("NoTools", "notools.test")
        sent = []

        def reply(request):
            sent.append(json.loads(request.content))
            if request.url.host == "good.test":
                return httpx.Response(200, content=answer("OK"), headers={"content-type": "text/event-stream"})
            if request.url.host == "notools.test":
                return httpx.Response(400, json={"error": "tools are not supported by this model"})
            return httpx.Response(401, json={"error": "invalid api key"})

        with patch("assistant.harness.default_sync_transport", return_value=httpx.MockTransport(reply)):
            response = self.client.post(
                "/admin/assistant/aiprovider/",
                {"action": "test_connection", "_selected_action": [good.pk, bad.pk, no_tools.pk]},
                follow=True,
            )
        notes = [str(message) for message in response.context["messages"]]
        self.assertIn("Good: OK (streaming and tools accepted)", notes)
        self.assertTrue(any(note.startswith("Bad: HTTP 401") for note in notes))
        self.assertTrue(any(note.startswith("NoTools: HTTP 400") and "tools" in note for note in notes))
        self.assertTrue(all(body["stream"] and body["tools"] for body in sent))

    def test_migration_style_provider_without_key_disables_the_assistant(self):
        AIProvider.objects.create(name="DeepSeek")
        self.assertFalse(self.client.get("/api/assistant/config/").json()["enabled"])


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
