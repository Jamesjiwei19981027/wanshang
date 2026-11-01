from urllib.parse import urlsplit

from django import forms
from django.contrib import admin, messages
from django.urls import reverse
from django.utils.html import format_html

from .harness import check_provider, has_usable_provider
from .limits import cost_today
from .models import AIProvider, AssistantSetting, Conversation, Message, QuickPrompt


@admin.register(AssistantSetting)
class AssistantSettingAdmin(admin.ModelAdmin):
    readonly_fields = ("service_status", "today_cost")
    fieldsets = (
        ("Switch", {"fields": ("service_status", "enabled")}),
        ("Limits", {"fields": (("visitor_daily_limit", "ip_daily_limit"), ("max_question_chars", "max_lookups"), "idle_timeout_seconds")}),
        (
            "Cost control",
            {
                "description": "Each AI provider's prices are entered on its own page under AI providers.",
                "fields": ("today_cost", ("daily_cost_cap", "currency")),
            },
        ),
        ("Conversation records", {"fields": (("conversation_retention_days", "lead_retention_days"),)}),
        ("Texts", {"fields": ("welcome_message", "privacy_notice", "extra_instructions")}),
    )

    @admin.display(description="Status")
    def service_status(self, obj):
        if not has_usable_provider():
            return "Not configured: add an active AI provider with an API key under AI providers."
        if not obj.enabled:
            return "Switched off"
        return "Running"

    @admin.display(description="Cost today")
    def today_cost(self, obj):
        return f"{cost_today():.4f} {obj.currency}"

    def has_add_permission(self, request):
        return super().has_add_permission(request) and not AssistantSetting.objects.exists()

    def has_delete_permission(self, request, obj=None):
        return False

    def save_model(self, request, obj, form, change):
        super().save_model(request, obj, form, change)
        unpriced = [provider.name for provider in AIProvider.objects.filter(is_active=True) if not provider.prices_configured]
        if obj.daily_cost_cap is not None and unpriced:
            self.message_user(
                request,
                f"Prices are zero for {', '.join(unpriced)}, so answers from them do not count toward the daily cost cap.",
                level=messages.WARNING,
            )


class AIProviderForm(forms.ModelForm):
    api_key = forms.CharField(
        label="API key",
        required=False,
        strip=True,
        widget=forms.PasswordInput(render_value=False, attrs={"autocomplete": "new-password"}),
        help_text="Saved encrypted and never shown again. Leave blank to keep the saved key.",
    )
    clear_api_key = forms.BooleanField(label="Remove saved key", required=False)

    class Meta:
        model = AIProvider
        fields = (
            "name", "base_url", "model_name", "priority", "is_active", "extra_parameters",
            "price_input_cache_hit", "price_input_cache_miss", "price_output",
        )

    def clean_base_url(self):
        url = self.cleaned_data["base_url"].strip().rstrip("/")
        parsed = urlsplit(url)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username or parsed.query or parsed.fragment:
            raise forms.ValidationError("Enter an address such as https://api.deepseek.com or http://model-server:8000/v1.")
        if url.endswith("/chat/completions"):
            url = url[: -len("/chat/completions")]
        return url

    def clean_extra_parameters(self):
        value = self.cleaned_data.get("extra_parameters") or {}
        if not isinstance(value, dict):
            raise forms.ValidationError('Enter a JSON object, e.g. {"max_completion_tokens": 1200}.')
        protected = {"model", "messages", "tools", "stream"} & value.keys()
        if protected:
            raise forms.ValidationError(f"These are set by the website and cannot be changed here: {', '.join(sorted(protected))}.")
        return value

    def save(self, commit=True):
        provider = super().save(commit=False)
        if self.cleaned_data.get("clear_api_key"):
            provider.set_api_key("")
        elif self.cleaned_data.get("api_key"):
            provider.set_api_key(self.cleaned_data["api_key"])
        if commit:
            provider.save()
        return provider


@admin.register(AIProvider)
class AIProviderAdmin(admin.ModelAdmin):
    """Several providers can be active; the assistant tries them by priority
    and switches to the next one automatically when one fails."""

    form = AIProviderForm
    list_display = ("name", "model_name", "priority", "is_active", "key_status", "health")
    list_editable = ("priority", "is_active")
    list_display_links = ("name",)
    readonly_fields = ("key_status", "health", "last_error")
    fieldsets = (
        (None, {"fields": ("name", "base_url", "model_name", ("priority", "is_active"))}),
        ("Compatibility", {"classes": ("collapse",), "fields": ("extra_parameters",)}),
        ("API key", {"fields": ("key_status", "api_key", "clear_api_key")}),
        (
            "Prices",
            {
                "description": "Per million tokens, in the currency set under Assistant settings. Used for the daily cost cap.",
                "fields": (("price_input_cache_hit", "price_input_cache_miss", "price_output"),),
            },
        ),
        ("Health", {"fields": ("health", "last_error")}),
    )
    actions = ("test_connection",)

    def save_model(self, request, obj, form, change):
        super().save_model(request, obj, form, change)
        if obj.base_url.startswith("http://"):
            self.message_user(
                request,
                "This address uses plain http, so the API key travels unencrypted. Use it only for a model inside your own network.",
                level=messages.WARNING,
            )

    @admin.display(description="API key")
    def key_status(self, obj):
        if not obj.encrypted_api_key:
            return "Not set"
        key = obj.get_api_key()
        if key is None:
            return "Unreadable (the server secret key changed). Enter the key again."
        return f"Saved (ends with {key[-4:]})" if len(key) >= 12 else "Saved"

    @admin.display(description="Last result")
    def health(self, obj):
        if obj.last_failure_at and (not obj.last_success_at or obj.last_failure_at > obj.last_success_at):
            return f"Failed at {obj.last_failure_at:%Y-%m-%d %H:%M}"
        if obj.last_success_at:
            return f"OK at {obj.last_success_at:%Y-%m-%d %H:%M}"
        return "Not used yet"

    @admin.action(description="Test connection", permissions=["change"])
    def test_connection(self, request, queryset):
        for provider in queryset:
            ok, detail = check_provider(provider)
            level = messages.SUCCESS if ok else messages.ERROR
            self.message_user(request, f"{provider.name}: {detail}", level=level)


@admin.register(QuickPrompt)
class QuickPromptAdmin(admin.ModelAdmin):
    list_display = ("text", "page", "position", "is_active")
    list_editable = ("page", "position", "is_active")
    list_filter = ("page", "is_active")


class MessageInline(admin.TabularInline):
    model = Message
    fields = ("created_at", "role", "content", "citation_links", "rating", "rating_note", "cost", "error")
    readonly_fields = ("created_at", "role", "content", "citation_links", "cost", "error")
    extra = 0
    can_delete = False

    def has_add_permission(self, request, obj=None):
        return False

    @admin.display(description="Pages read")
    def citation_links(self, obj):
        if not obj.citations:
            return "—"
        return format_html(
            "<br>".join("{}" for _ in obj.citations),
            *(f"{citation.get('title', '')} ({citation.get('url', '')})" for citation in obj.citations),
        )


@admin.register(Conversation)
class ConversationAdmin(admin.ModelAdmin):
    list_display = ("public_id_short", "kind", "contact_details", "inquiry_link", "message_count", "total_cost", "last_message_at")
    list_filter = ("kind", "last_message_at")
    search_fields = ("contact_details", "messages__content", "inquiry__reference")
    readonly_fields = ("public_id", "kind", "contact_details", "inquiry", "page_path", "message_count", "total_cost", "created_at", "last_message_at")
    fields = readonly_fields
    inlines = (MessageInline,)
    date_hierarchy = "last_message_at"

    def has_add_permission(self, request):
        return False

    @admin.display(description="Conversation")
    def public_id_short(self, obj):
        return obj.public_id[:8]

    @admin.display(description="RFQ")
    def inquiry_link(self, obj):
        if not obj.inquiry_id:
            return "—"
        return format_html('<a href="{}">{}</a>', reverse("admin:catalog_inquiry_change", args=[obj.inquiry_id]), obj.inquiry.reference)


@admin.register(Message)
class MessageAdmin(admin.ModelAdmin):
    """Review answers; incorrect ones are listed together for fixing content."""

    list_display = ("created_at", "short_content", "rating", "provider_name", "cost", "error", "conversation")
    list_filter = ("rating", "role", "created_at")
    list_editable = ("rating",)
    search_fields = ("content", "rating_note")
    readonly_fields = ("conversation", "role", "content", "citations", "lookups", "rfq_draft", "provider_name", "prompt_cache_hit_tokens", "prompt_cache_miss_tokens", "completion_tokens", "cost", "error", "created_at")
    fields = ("rating", "rating_note", *readonly_fields)
    actions = ("mark_correct", "mark_incorrect")
    list_select_related = ("conversation",)

    def get_queryset(self, request):
        return super().get_queryset(request).filter(role=Message.Role.ASSISTANT)

    def has_add_permission(self, request):
        return False

    @admin.display(description="Answer")
    def short_content(self, obj):
        return obj.content[:120]

    @admin.action(description="Mark as correct", permissions=["change"])
    def mark_correct(self, request, queryset):
        queryset.update(rating=Message.Rating.CORRECT)

    @admin.action(description="Mark as incorrect", permissions=["change"])
    def mark_incorrect(self, request, queryset):
        queryset.update(rating=Message.Rating.INCORRECT)
