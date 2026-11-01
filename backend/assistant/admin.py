from django.conf import settings
from django.contrib import admin, messages
from django.urls import reverse
from django.utils.html import format_html

from .limits import cost_today
from .models import AssistantSetting, Conversation, Message, QuickPrompt


@admin.register(AssistantSetting)
class AssistantSettingAdmin(admin.ModelAdmin):
    readonly_fields = ("service_status", "today_cost")
    fieldsets = (
        ("Switch", {"fields": ("service_status", "enabled", "model_name")}),
        ("Limits", {"fields": (("visitor_daily_limit", "ip_daily_limit"), ("max_question_chars", "max_lookups"), "idle_timeout_seconds")}),
        (
            "Cost control",
            {
                "description": "Enter the provider's current prices so the daily cap can be enforced.",
                "fields": ("today_cost", ("daily_cost_cap", "currency"), ("price_input_cache_hit", "price_input_cache_miss", "price_output")),
            },
        ),
        ("Conversation records", {"fields": (("conversation_retention_days", "lead_retention_days"),)}),
        ("Texts", {"fields": ("welcome_message", "privacy_notice", "extra_instructions")}),
    )

    @admin.display(description="Status")
    def service_status(self, obj):
        if not settings.DEEPSEEK_API_KEY:
            return "Not configured: set DEEPSEEK_API_KEY on the server."
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
        if obj.daily_cost_cap is not None and not obj.prices_configured:
            self.message_user(request, "Prices are zero, so the daily cost cap cannot take effect.", level=messages.WARNING)


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

    list_display = ("created_at", "short_content", "rating", "cost", "error", "conversation")
    list_filter = ("rating", "role", "created_at")
    list_editable = ("rating",)
    search_fields = ("content", "rating_note")
    readonly_fields = ("conversation", "role", "content", "citations", "lookups", "rfq_draft", "prompt_cache_hit_tokens", "prompt_cache_miss_tokens", "completion_tokens", "cost", "error", "created_at")
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
