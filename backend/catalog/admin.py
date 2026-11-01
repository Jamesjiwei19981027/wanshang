from django import forms
from django.contrib import admin, messages
from django.db.models import Q
from django.urls import reverse
from django.utils.html import format_html, format_html_join
from django.utils.safestring import mark_safe

from .file_security import schedule_inquiry_attachment_scan
from .signals import batched_search_updates
from .models import (
    FAQ, AnalyticsEvent, Application, Banner, Category, ContactMessage,
    CompanyPhoto, CompanyProfile, KnowledgeEntry,
    Inquiry, InquiryAttachment, InquiryFollowup, InquiryItem, PageView, Product,
    ProductDocument, ProductImage, ProductSpecValue, NewsArticle, SearchMiss,
    SearchSynonym, SiteSetting, SpecField, VisitSession,
)
from .tasks import enqueue_contact_notification, enqueue_document_text_extraction, enqueue_inquiry_notification
from .units import QUANTITIES, QUANTITY_CHOICES, display_unit, unit_choices


class ProtectedProductDocumentForm(forms.ModelForm):
    """Render a replacement upload without dereferencing private storage.url."""

    class Meta:
        model = ProductDocument
        fields = "__all__"
        widgets = {"file": forms.FileInput()}


class ProtectedProductDocumentAdminMixin:
    form = ProtectedProductDocumentForm

    @admin.display(description="Protected file")
    def protected_file_status(self, obj):
        if not obj or not obj.file or not obj.file.name:
            return "No uploaded file"
        return f"Stored privately: {obj.file.name.rsplit('/', 1)[-1]}"

    @admin.display(description="Controlled delivery")
    def controlled_download_link(self, obj):
        if not obj or not obj.pk or not obj.file:
            return "Available after an uploaded file is saved"
        if (
            not obj.is_active
            or obj.product.status != Product.Status.PUBLISHED
            or not obj.product.category.is_active
        ):
            return "Enable this document and publish its product/category to deliver it"
        url = reverse("document-download", kwargs={"pk": obj.pk})
        return format_html('<a href="{}">Open controlled download</a>', url)


class AuditedAdminActionMixin:
    """Write one Django admin LogEntry for every object touched by an action."""

    def log_admin_action(self, request, objects, message: str) -> None:
        for obj in objects:
            self.log_change(request, obj, message)


class ProductDocumentInline(
    ProtectedProductDocumentAdminMixin,
    admin.TabularInline,
):
    model = ProductDocument
    extra = 0
    fields = (
        "title",
        "document_type",
        "version",
        "external_url",
        "file",
        "protected_file_status",
        "controlled_download_link",
        "text_note",
        "is_active",
    )
    readonly_fields = ("protected_file_status", "controlled_download_link")


class ProductImageInline(admin.TabularInline):
    model = ProductImage
    extra = 0
    fields = (
        "image",
        "external_url",
        "image_type",
        "alt_text",
        "caption",
        "sort_order",
    )
    ordering = ("sort_order", "id")


SPEC_PREFIX = "spec__"


def _spec_form_fields(spec_fields: list[SpecField], system: str) -> dict[str, forms.Field]:
    """One form field per parameter, plus a unit selector for measured numbers."""
    fields: dict[str, forms.Field] = {}
    for field in spec_fields:
        name = f"{SPEC_PREFIX}{field.key}"
        options = {"label": field.label, "required": field.required, "help_text": field.help_text}
        if field.data_type == SpecField.DataType.NUMBER:
            fields[name] = forms.DecimalField(max_digits=20, decimal_places=6, **options)
            if field.quantity in QUANTITIES:
                fields[f"{name}__unit"] = forms.ChoiceField(
                    label=f"{field.label} unit",
                    choices=unit_choices(field.quantity),
                    initial=display_unit(field.quantity, system, field.imperial_unit, field.metric_unit),
                )
        elif field.data_type == SpecField.DataType.CHOICE:
            fields[name] = forms.ChoiceField(choices=[("", "—")] + [(item, item) for item in field.choices], **options)
        elif field.data_type == SpecField.DataType.BOOLEAN:
            fields[name] = forms.NullBooleanField(
                widget=forms.Select(choices=[("unknown", "—"), ("true", "Yes"), ("false", "No")]),
                **{**options, "required": False},
            )
        else:
            fields[name] = forms.CharField(max_length=255, **options)
    return fields


class ProductAdminForm(forms.ModelForm):
    spec_field_objects: list[SpecField] = []

    class Meta:
        model = Product
        fields = "__all__"

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if not self.instance.pk:
            return
        existing = {value.field_id: value for value in self.instance.spec_values.select_related("field")}
        for field in self.spec_field_objects:
            value = existing.get(field.pk)
            if value is None:
                continue
            name = f"{SPEC_PREFIX}{field.key}"
            if field.data_type == SpecField.DataType.NUMBER:
                self.initial[name] = value.number_value
                if value.entered_unit:
                    self.initial[f"{name}__unit"] = value.entered_unit
            elif field.data_type == SpecField.DataType.BOOLEAN:
                self.initial[name] = value.boolean_value
            else:
                self.initial[name] = value.text_value

    def save_specs(self, product: Product) -> None:
        keep: set[int] = set()
        for field in self.spec_field_objects:
            name = f"{SPEC_PREFIX}{field.key}"
            raw = self.cleaned_data.get(name)
            keep.add(field.pk)
            values = {"number_value": None, "entered_unit": "", "text_value": "", "boolean_value": None}
            if field.data_type == SpecField.DataType.NUMBER:
                values["number_value"] = raw
                values["entered_unit"] = self.cleaned_data.get(f"{name}__unit", "") if raw is not None else ""
            elif field.data_type == SpecField.DataType.BOOLEAN:
                values["boolean_value"] = raw
            else:
                values["text_value"] = raw or ""
            if values["number_value"] is None and not values["text_value"] and values["boolean_value"] is None:
                ProductSpecValue.objects.filter(product=product, field=field).delete()
                continue
            spec_value = ProductSpecValue.objects.filter(product=product, field=field).first() or ProductSpecValue(product=product, field=field)
            for attribute, value in values.items():
                setattr(spec_value, attribute, value)
            spec_value.save()
        # Values of parameters that no longer apply (e.g. after a category change).
        ProductSpecValue.objects.filter(product=product).exclude(field_id__in=keep).delete()


@admin.register(Product)
class ProductAdmin(admin.ModelAdmin):
    form = ProductAdminForm
    list_display = ("model", "title", "category", "featured", "status_badge")
    list_filter = (
        "status",
        "category",
        "featured",
        "is_customizable",
        "made_to_order",
    )
    list_editable = ("featured",)
    search_fields = ("title", "model", "sku", "short_description")
    prepopulated_fields = {"slug": ("title",)}
    filter_horizontal = ("applications",)
    inlines = (ProductImageInline, ProductDocumentInline)
    list_select_related = ("category",)
    list_per_page = 25
    base_fieldsets = (
        ("Product identity", {"fields": (("title", "slug"), ("model", "sku"), ("category", "status", "position"))}),
        ("Commercial information", {"fields": (("moq", "lead_time", "loading_port"), "packaging", "image_url")}),
        ("Publishing", {"fields": (("featured", "is_customizable", "oem_available", "made_to_order"), "applications", "short_description", "description")}),
        ("Translations", {"classes": ("collapse",), "description": 'Optional. Example: {"es": {"title": "…", "short_description": "…"}}. Untranslated text shows in English.', "fields": ("translations",)}),
        ("Search metadata", {"classes": ("collapse",), "fields": ("meta_title", "meta_description")} ),
    )

    class Media:
        js = ("catalog/admin/product-category-parameters.js",)

    def _selected_category(self, request, obj=None) -> Category | None:
        cached = getattr(request, "_wanshang_product_category", False)
        if cached is not False:
            return cached
        raw = request.POST.get("category") or request.GET.get("category") or ""
        category = None
        if raw.isdecimal():
            category = Category.objects.filter(pk=int(raw)).first()
        if category is None and obj is not None:
            category = obj.category
        request._wanshang_product_category = category
        return category

    def _spec_fields(self, request, obj=None) -> list[SpecField]:
        category = self._selected_category(request, obj)
        return category.effective_spec_fields(products_only=True) if category else []

    def get_fieldsets(self, request, obj=None):
        spec_fields = self._spec_fields(request, obj)
        if not spec_fields:
            category = self._selected_category(request, obj)
            note = (
                "This category has no parameter list yet. Add parameters under Categories."
                if category
                else "Choose a category; its parameter list appears here."
            )
            return (self.base_fieldsets[0], ("Parameters", {"description": note, "fields": ()}), *self.base_fieldsets[1:])
        rows = []
        for field in spec_fields:
            name = f"{SPEC_PREFIX}{field.key}"
            rows.append((name, f"{name}__unit") if field.data_type == SpecField.DataType.NUMBER and field.quantity in QUANTITIES else name)
        category = self._selected_category(request, obj)
        return (
            self.base_fieldsets[0],
            ("Parameters", {"description": f"Parameter list of {category}. Enter values in any unit; the site converts them.", "fields": tuple(rows)}),
            *self.base_fieldsets[1:],
        )

    def get_form(self, request, obj=None, **kwargs):
        spec_fields = self._spec_fields(request, obj)
        system = SiteSetting.objects.filter(pk=1).values_list("default_unit_system", flat=True).first() or "imperial"
        attributes = _spec_form_fields(spec_fields, system)
        attributes["spec_field_objects"] = spec_fields
        kwargs["form"] = type("ProductParameterForm", (self.form,), attributes)
        return super().get_form(request, obj, **kwargs)

    def changeform_view(self, request, *args, **kwargs):
        # Product, parameters, images, documents and applications are saved in
        # one request; rebuild the product's search text once at the end.
        with batched_search_updates():
            return super().changeform_view(request, *args, **kwargs)

    def save_model(self, request, obj, form, change):
        super().save_model(request, obj, form, change)
        form.save_specs(obj)

    @admin.display(description="Status", ordering="status")
    def status_badge(self, obj):
        return format_html('<span class="status-badge status-{}">{}</span>', obj.status, obj.get_status_display())


class SpecFieldForm(forms.ModelForm):
    quantity = forms.ChoiceField(choices=[("", "— no unit —")] + QUANTITY_CHOICES, required=False)
    choices_text = forms.CharField(
        label="Options",
        required=False,
        widget=forms.Textarea(attrs={"rows": 2, "cols": 28}),
        help_text="One option per line, for list parameters.",
    )

    class Meta:
        model = SpecField
        fields = (
            "key", "label", "data_type", "quantity", "imperial_unit", "metric_unit",
            "choices_text", "required", "filterable", "show_on_card", "ask_in_rfq", "used_by_products", "position", "help_text",
        )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["choices_text"].initial = "\n".join(self.instance.choices or [])

    def clean(self):
        cleaned = super().clean()
        lines = [line.strip() for line in (cleaned.get("choices_text") or "").splitlines()]
        self.instance.choices = [line for line in lines if line]
        return cleaned


class SpecFieldInline(admin.TabularInline):
    model = SpecField
    form = SpecFieldForm
    extra = 0
    ordering = ("position", "label")


@admin.register(Category)
class CategoryAdmin(admin.ModelAdmin):
    list_display = ("__str__", "parent", "parameter_count", "position", "is_active")
    list_editable = ("position", "is_active")
    list_filter = ("is_active", "parent")
    prepopulated_fields = {"slug": ("name",)}
    list_per_page = 25
    inlines = (SpecFieldInline,)
    fields = (
        ("name", "slug"),
        ("parent", "inherit_parent_specs"),
        "description",
        ("icon", "image_url"),
        ("position", "is_active"),
        ("meta_title", "meta_description"),
        "translations",
    )

    @admin.display(description="Parameters")
    def parameter_count(self, obj):
        own = obj.spec_fields.count()
        total = len(obj.effective_spec_fields())
        return f"{own}" if own == total else f"{own} own · {total} total"


@admin.register(Application)
class ApplicationAdmin(admin.ModelAdmin):
    list_display = ("name", "recommended_finish", "position", "is_active")
    list_editable = ("position", "is_active")
    list_filter = ("is_active",)
    prepopulated_fields = {"slug": ("name",)}
    list_per_page = 25


@admin.register(FAQ)
class FAQAdmin(admin.ModelAdmin):
    list_display = ("question", "group", "position", "is_published", "updated_at")
    list_editable = ("position", "is_published")
    list_filter = ("is_published", "group")
    search_fields = ("question", "answer")


@admin.register(KnowledgeEntry)
class KnowledgeEntryAdmin(admin.ModelAdmin):
    list_display = ("title", "is_published", "updated_at")
    list_filter = ("is_published",)
    search_fields = ("title", "body")
    prepopulated_fields = {"slug": ("title",)}


@admin.register(SearchSynonym)
class SearchSynonymAdmin(admin.ModelAdmin):
    list_display = ("terms", "is_active", "updated_at")
    list_editable = ("is_active",)
    search_fields = ("terms",)


@admin.register(SearchMiss)
class SearchMissAdmin(admin.ModelAdmin):
    list_display = ("query", "count", "last_seen_at", "resolved")
    list_editable = ("resolved",)
    list_filter = ("resolved",)
    search_fields = ("query",)
    readonly_fields = ("query", "count", "first_seen_at", "last_seen_at")

    def has_add_permission(self, request):
        return False


class InquiryItemInline(admin.TabularInline):
    model = InquiryItem
    readonly_fields = (
        "product",
        "product_snapshot",
        "quantity",
        "selected_options",
        "requested_specs",
    )
    can_delete = False
    extra = 0


class InquiryAttachmentInline(admin.TabularInline):
    model = InquiryAttachment
    fields = (
        "original_name",
        "size",
        "declared_mime_type",
        "scan_status",
        "scan_detail",
        "scanned_at",
        "created_at",
        "download_link",
    )
    readonly_fields = fields
    extra = 0
    can_delete = False

    @admin.display(description="Private download")
    def download_link(self, obj):
        if not obj.pk:
            return "—"
        if obj.scan_status != InquiryAttachment.ScanStatus.CLEAN:
            return f"Quarantined ({obj.get_scan_status_display()})"
        url = reverse("inquiry-attachment-download", kwargs={"pk": obj.pk})
        return format_html('<a href="{}">Download attachment</a>', url)


class InquiryFollowupInline(admin.StackedInline):
    model = InquiryFollowup
    fields = ("action_type", "content", "next_followup_at", "user", "created_at")
    readonly_fields = ("user", "created_at")
    extra = 1


@admin.register(Inquiry)
class InquiryAdmin(AuditedAdminActionMixin, admin.ModelAdmin):
    list_display = ("reference", "company", "name", "email", "status_badge", "priority_badge", "assigned_to", "next_followup_at", "created_at")
    list_filter = ("status", "priority", "country", "utm_source")
    search_fields = ("reference", "name", "company", "email")
    readonly_fields = ("reference", "assistant_conversations", "next_followup_at", "session", "source_url", "referrer", "first_touch_source", "last_touch_source", "utm_source", "utm_medium", "utm_campaign", "utm_term", "utm_content", "first_landing_page", "last_landing_page", "user_agent", "ip_hash", "notification_queued_at", "notification_claimed_at", "notification_sent_at", "confirmation_claimed_at", "confirmation_sent_at", "notification_error", "created_at", "updated_at")
    inlines = (InquiryItemInline, InquiryAttachmentInline, InquiryFollowupInline)
    list_select_related = ("assigned_to",)
    date_hierarchy = "created_at"
    list_per_page = 30
    actions = (
        "mark_qualified",
        "mark_contacted",
        "mark_spam",
        "retry_notifications",
        "rescan_attachments",
    )
    fieldsets = (
        ("Pipeline", {"fields": (("reference", "status", "priority", "assigned_to"), "next_followup_at", "created_at", "updated_at")}),
        ("Buyer contact", {"fields": (("name", "company"), ("email", "phone"), ("country", "quantity"), "application")}),
        ("Delivery", {"fields": ("destination_port", "target_delivery_date")}),
        ("Requirement", {"fields": ("message", "requested_categories", "specifications", "privacy_consent", "admin_notes", "assistant_conversations")} ),
        ("Attribution & traceability", {"classes": ("collapse",), "fields": ("session", "source_url", "referrer", ("first_touch_source", "last_touch_source"), ("utm_source", "utm_medium", "utm_campaign"), ("utm_term", "utm_content"), ("first_landing_page", "last_landing_page"), "user_agent", "ip_hash")} ),
        ("Email delivery", {"classes": ("collapse",), "fields": ("notification_queued_at", ("notification_claimed_at", "notification_sent_at"), ("confirmation_claimed_at", "confirmation_sent_at"), "notification_error")} ),
    )

    @admin.display(description="Status", ordering="status")
    def status_badge(self, obj):
        return format_html('<span class="status-badge status-{}">{}</span>', obj.status, obj.get_status_display())

    @admin.display(description="Assistant conversation before this RFQ")
    def assistant_conversations(self, obj):
        conversations = list(obj.assistant_conversations.all()) if obj.pk else []
        if not conversations:
            return "—"
        return format_html_join(
            mark_safe("<br>"),
            '<a href="{}">Conversation {} ({} messages)</a>',
            (
                (reverse("admin:assistant_conversation_change", args=[item.pk]), item.public_id[:8], item.message_count)
                for item in conversations
            ),
        )

    @admin.display(description="Priority", ordering="priority")
    def priority_badge(self, obj):
        return format_html('<span class="priority-badge priority-{}">{}</span>', obj.priority, obj.get_priority_display())

    @admin.action(description="Mark selected enquiries as qualified", permissions=["change"])
    def mark_qualified(self, request, queryset):
        objects = list(queryset)
        queryset.update(status=Inquiry.Status.QUALIFIED)
        self.log_admin_action(
            request,
            objects,
            "Admin action: marked enquiry as qualified.",
        )

    @admin.action(description="Mark selected enquiries as contacted", permissions=["change"])
    def mark_contacted(self, request, queryset):
        objects = list(queryset)
        queryset.update(status=Inquiry.Status.CONTACTED)
        self.log_admin_action(
            request,
            objects,
            "Admin action: marked enquiry as contacted.",
        )

    @admin.action(description="Mark selected enquiries as spam", permissions=["change"])
    def mark_spam(self, request, queryset):
        objects = list(queryset)
        queryset.update(status=Inquiry.Status.SPAM)
        self.log_admin_action(
            request,
            objects,
            "Admin action: marked enquiry as spam.",
        )

    @admin.action(description="Retry pending email notifications", permissions=["change"])
    def retry_notifications(self, request, queryset):
        pending = list(
            queryset.filter(
                Q(notification_sent_at__isnull=True)
                | Q(confirmation_sent_at__isnull=True)
            )
        )
        queued = 0
        for inquiry in pending:
            succeeded = enqueue_inquiry_notification(inquiry.pk)
            queued += int(succeeded)
            self.log_admin_action(
                request,
                [inquiry],
                (
                    "Admin action: queued an enquiry notification retry."
                    if succeeded
                    else "Admin action: enquiry notification retry failed to queue."
                ),
            )
        failures = len(pending) - queued
        self.message_user(
            request,
            f"Queued {queued} notification(s); {failures} failed to reach the broker.",
            level=messages.SUCCESS if failures == 0 else messages.WARNING,
        )

    @admin.action(description="Rescan selected RFQ attachments", permissions=["change"])
    def rescan_attachments(self, request, queryset):
        # Scanning runs in the background worker; the page returns at once and
        # each attachment's status updates when its scan finishes.
        queued = 0
        failed = 0
        for inquiry in queryset.prefetch_related("attachments"):
            attachments = list(inquiry.attachments.all())
            for attachment in attachments:
                InquiryAttachment.objects.filter(pk=attachment.pk).update(
                    scan_status=InquiryAttachment.ScanStatus.PENDING,
                    scan_detail="Rescan requested by staff.",
                )
                attachment.scan_status = InquiryAttachment.ScanStatus.PENDING
                status = schedule_inquiry_attachment_scan(attachment)
                if status == InquiryAttachment.ScanStatus.ERROR:
                    failed += 1
                else:
                    queued += 1
            self.log_admin_action(
                request,
                [inquiry],
                f"Admin action: queued a rescan of {len(attachments)} attachment(s).",
            )
        self.message_user(
            request,
            f"Queued {queued} attachment scan(s); results appear when scanning finishes."
            + (f" {failed} could not be queued." if failed else ""),
            level=messages.SUCCESS if failed == 0 else messages.WARNING,
        )

    def save_formset(self, request, form, formset, change):
        instances = formset.save(commit=False)
        for deleted_object in formset.deleted_objects:
            deleted_object.delete()
        for instance in instances:
            if isinstance(instance, InquiryFollowup) and not instance.user_id:
                instance.user = request.user
            instance.save()
        formset.save_m2m()
        if getattr(formset, "model", None) is InquiryFollowup:
            inquiry = getattr(form, "instance", None)
            if inquiry is None:
                inquiry = next(
                    (
                        instance.inquiry
                        for instance in instances
                        if isinstance(instance, InquiryFollowup)
                    ),
                    None,
                )
            if inquiry is None:
                return
            latest_followup = inquiry.followups.order_by("-created_at", "-pk").first()
            inquiry.next_followup_at = (
                latest_followup.next_followup_at if latest_followup else None
            )
            inquiry.save(update_fields=["next_followup_at", "updated_at"])


@admin.register(ContactMessage)
class ContactMessageAdmin(AuditedAdminActionMixin, admin.ModelAdmin):
    list_display = (
        "reference",
        "name",
        "company",
        "email",
        "status_badge",
        "priority",
        "assigned_to",
        "created_at",
    )
    list_filter = ("status", "priority", "country", "utm_source")
    search_fields = ("reference", "name", "company", "email", "message")
    date_hierarchy = "created_at"
    list_per_page = 30
    list_select_related = ("assigned_to",)
    readonly_fields = ("reference", "is_resolved", "session", "source_url", "referrer", "first_touch_source", "last_touch_source", "utm_source", "utm_medium", "utm_campaign", "utm_term", "utm_content", "first_landing_page", "last_landing_page", "user_agent", "ip_hash", "notification_queued_at", "notification_claimed_at", "notification_sent_at", "confirmation_claimed_at", "confirmation_sent_at", "notification_error", "created_at", "updated_at")
    actions = ("mark_resolved", "mark_spam", "retry_notifications")
    fieldsets = (
        (
            "Workflow",
            {
                "fields": (
                    ("reference", "status", "priority", "assigned_to"),
                    ("created_at", "updated_at"),
                    "admin_notes",
                )
            },
        ),
        (
            "Contact",
            {
                "fields": (
                    ("name", "company"),
                    ("email", "phone"),
                    "country",
                    "subject",
                    "message",
                    "privacy_consent",
                )
            },
        ),
        (
            "Attribution & traceability",
            {
                "classes": ("collapse",),
                "fields": (
                    "session",
                    "source_url",
                    "referrer",
                    ("first_touch_source", "last_touch_source"),
                    ("utm_source", "utm_medium", "utm_campaign"),
                    ("utm_term", "utm_content"),
                    ("first_landing_page", "last_landing_page"),
                    "user_agent",
                    "ip_hash",
                ),
            },
        ),
        (
            "Email delivery",
            {
                "classes": ("collapse",),
                "fields": (
                    "notification_queued_at",
                    "notification_claimed_at",
                    "notification_sent_at",
                    "confirmation_claimed_at",
                    "confirmation_sent_at",
                    "notification_error",
                ),
            },
        ),
    )

    @admin.display(description="Status", ordering="status")
    def status_badge(self, obj):
        label = obj.get_status_display()
        css_class = (
            "status-published"
            if obj.status == ContactMessage.Status.RESOLVED
            else f"status-{obj.status}"
        )
        return format_html('<span class="status-badge {}">{}</span>', css_class, label)

    @admin.action(description="Mark selected messages as resolved", permissions=["change"])
    def mark_resolved(self, request, queryset):
        objects = list(queryset)
        queryset.update(
            status=ContactMessage.Status.RESOLVED,
            is_resolved=True,
        )
        self.log_admin_action(
            request,
            objects,
            "Admin action: marked contact message as resolved.",
        )

    @admin.action(description="Mark selected messages as spam", permissions=["change"])
    def mark_spam(self, request, queryset):
        objects = list(queryset)
        queryset.update(
            status=ContactMessage.Status.SPAM,
            is_resolved=False,
        )
        self.log_admin_action(
            request,
            objects,
            "Admin action: marked contact message as spam.",
        )

    @admin.action(description="Retry pending email notifications", permissions=["change"])
    def retry_notifications(self, request, queryset):
        pending = list(
            queryset.filter(
                Q(notification_sent_at__isnull=True)
                | Q(confirmation_sent_at__isnull=True)
            )
        )
        queued = 0
        for contact_message in pending:
            succeeded = enqueue_contact_notification(contact_message.pk)
            queued += int(succeeded)
            self.log_admin_action(
                request,
                [contact_message],
                (
                    "Admin action: queued a contact notification retry."
                    if succeeded
                    else "Admin action: contact notification retry failed to queue."
                ),
            )
        failures = len(pending) - queued
        self.message_user(
            request,
            f"Queued {queued} notification(s); {failures} failed to reach the broker.",
            level=messages.SUCCESS if failures == 0 else messages.WARNING,
        )


@admin.register(ProductDocument)
class ProductDocumentAdmin(
    ProtectedProductDocumentAdminMixin,
    admin.ModelAdmin,
):
    list_display = ("title", "product", "document_type", "version", "is_active", "downloads", "text_status")
    list_filter = ("document_type", "is_active")
    list_editable = ("is_active",)
    search_fields = ("title", "product__title", "product__model")
    list_select_related = ("product",)
    readonly_fields = ("protected_file_status", "controlled_download_link", "extracted_at", "extracted_preview")
    actions = ("extract_text",)

    @admin.display(description="Text for search")
    def text_status(self, obj):
        if obj.extracted_text:
            return "Extracted"
        return "Described" if obj.text_note else "—"

    @admin.display(description="Extracted text (first 2,000 characters)")
    def extracted_preview(self, obj):
        return (obj.extracted_text or "—")[:2000]

    @admin.action(description="Extract text from selected PDF files", permissions=["change"])
    def extract_text(self, request, queryset):
        queued = sum(int(enqueue_document_text_extraction(document.pk)) for document in queryset.filter(file__iendswith=".pdf"))
        self.message_user(request, f"Queued text extraction for {queued} PDF file(s).")


@admin.register(NewsArticle)
class NewsArticleAdmin(admin.ModelAdmin):
    list_display = (
        "title",
        "status",
        "category",
        "is_featured",
        "author",
        "published_at",
        "updated_at",
    )
    list_filter = ("status", "category", "is_featured", "published_at")
    search_fields = ("title", "summary", "author", "category", "seo_keywords")
    prepopulated_fields = {"slug": ("title",)}
    list_editable = ("is_featured",)
    date_hierarchy = "published_at"
    fieldsets = (
        (
            "Article",
            {
                "fields": (
                    ("title", "slug"),
                    ("category", "reading_minutes"),
                    "tags",
                    "summary",
                    "body",
                )
            },
        ),
        (
            "Publishing",
            {
                "fields": (
                    ("status", "is_featured"),
                    ("author", "published_at"),
                    ("cover_image", "cover_image_url"),
                )
            },
        ),
        (
            "Search metadata",
            {
                "classes": ("collapse",),
                "fields": (
                    "seo_title",
                    "seo_keywords",
                    "seo_description",
                ),
            },
        ),
    )


@admin.register(Banner)
class BannerAdmin(admin.ModelAdmin):
    list_display = (
        "title",
        "sort_order",
        "is_active",
        "starts_at",
        "ends_at",
    )
    list_editable = ("sort_order", "is_active")
    list_filter = ("is_active",)
    ordering = ("sort_order", "id")
    fieldsets = (
        ("Copy", {"fields": ("title", "subtitle")}),
        (
            "Images",
            {
                "fields": (
                    ("desktop_image", "desktop_image_url"),
                    ("mobile_image", "mobile_image_url"),
                )
            },
        ),
        (
            "Actions",
            {
                "fields": (
                    ("button_text", "button_link"),
                    ("secondary_button_text", "secondary_button_link"),
                )
            },
        ),
        (
            "Schedule",
            {
                "fields": (
                    ("sort_order", "is_active"),
                    ("starts_at", "ends_at"),
                )
            },
        ),
    )


@admin.register(SiteSetting)
class SiteSettingAdmin(admin.ModelAdmin):
    fieldsets = (
        (
            "Identity",
            {
                "fields": (
                    "site_name",
                    "legal_name",
                    "chinese_name",
                    "tagline",
                    ("site_logo", "site_logo_url"),
                    ("default_og_image", "default_og_image_url"),
                )
            },
        ),
        (
            "Contact",
            {
                "fields": (
                    ("contact_email", "sales_email"),
                    ("contact_person", "contact_title"),
                    "website_url",
                    ("phone_number", "whatsapp_number"),
                    "whatsapp_link",
                    "address",
                    "business_hours",
                    "map_url",
                    "linkedin_url",
                )
            },
        ),
        (
            "Search and measurement",
            {
                "fields": (
                    "global_seo_title",
                    "global_seo_keywords",
                    "global_seo_description",
                    ("google_analytics_id", "google_tag_manager_id"),
                    "default_unit_system",
                )
            },
        ),
        (
            "Email templates",
            {
                "classes": ("collapse",),
                "description": (
                    "Optional templates support simple placeholders such as "
                    "{reference}, {name}, {email}, {company}, and {site_name}. "
                    "Invalid templates automatically fall back to safe defaults."
                ),
                "fields": (
                    "inquiry_sales_subject_template",
                    "inquiry_sales_intro_template",
                    "inquiry_confirmation_subject_template",
                    "inquiry_confirmation_intro_template",
                    "contact_sales_subject_template",
                    "contact_sales_intro_template",
                    "contact_confirmation_subject_template",
                    "contact_confirmation_intro_template",
                ),
            },
        ),
        ("Footer", {"fields": ("footer_text",)}),
    )

    def has_add_permission(self, request):
        return super().has_add_permission(request) and not SiteSetting.objects.exists()

    def has_delete_permission(self, request, obj=None):
        return False


class CompanyPhotoInline(admin.TabularInline):
    model = CompanyPhoto
    extra = 0
    fields = ("image", "alt_text", "caption", "sort_order")


@admin.register(CompanyProfile)
class CompanyProfileAdmin(admin.ModelAdmin):
    list_display = ("__str__", "is_published", "updated_at")
    inlines = (CompanyPhotoInline,)
    fieldsets = (
        ("Publishing", {"fields": ("is_published",), "description": "Keep this as a draft until the company information and photos are ready for public display."}),
        ("Company introduction", {"fields": ("headline", "summary", "business_role", "introduction")}),
        ("Business information", {"fields": ("main_products", "markets", "strengths", "quality_information"), "description": "Use paragraphs or separate lines. Publish only verified business information; leave unused sections blank."}),
        ("Search metadata", {"classes": ("collapse",), "fields": ("meta_title", "meta_description")}),
    )

    def has_add_permission(self, request):
        return super().has_add_permission(request) and not CompanyProfile.objects.exists()

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(VisitSession)
class VisitSessionAdmin(admin.ModelAdmin):
    list_display = (
        "session_key",
        "first_touch_source",
        "last_touch_source",
        "country",
        "device_type",
        "consent_status",
        "last_seen_at",
        "converted_at",
    )
    list_filter = ("consent_status", "country", "device_type", "first_touch_source")
    search_fields = ("session_key", "utm_source", "utm_campaign", "referrer_url")
    date_hierarchy = "started_at"
    readonly_fields = tuple(
        field.name
        for field in VisitSession._meta.fields
        if field.name not in {"id"}
    )

    def has_add_permission(self, request):
        return False


@admin.register(PageView)
class PageViewAdmin(admin.ModelAdmin):
    list_display = ("path", "session", "product", "page_title", "timestamp")
    list_filter = ("timestamp",)
    search_fields = ("path", "page_title", "session__session_key", "product__title")
    date_hierarchy = "timestamp"
    list_select_related = ("session", "product")
    readonly_fields = ("session", "path", "page_title", "product", "timestamp", "created_at", "updated_at")

    def has_add_permission(self, request):
        return False


@admin.register(AnalyticsEvent)
class AnalyticsEventAdmin(admin.ModelAdmin):
    list_display = ("event_name", "path", "product", "session_id", "visit_session", "created_at")
    list_filter = ("event_name", "created_at")
    search_fields = ("path", "session_id")
    date_hierarchy = "created_at"
    readonly_fields = ("event_name", "path", "product", "session_id", "visit_session", "metadata", "created_at", "updated_at")
    list_select_related = ("product", "visit_session")

    def has_add_permission(self, request):
        return False


admin.site.site_header = "WANSHANG Control Center"
admin.site.site_title = "WANSHANG Admin"
admin.site.index_title = "Catalogue, RFQ and content operations"
