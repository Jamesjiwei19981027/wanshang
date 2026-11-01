from django import forms
from django.contrib import admin, messages
from django.db.models import Q
from django.urls import reverse
from django.utils.html import format_html

from .file_security import scan_inquiry_attachment
from .models import (
    AnalyticsEvent, Application, AxleSpec, Banner, Category, ContactMessage,
    CompanyPhoto, CompanyProfile,
    Inquiry, InquiryAttachment, InquiryFollowup, InquiryItem, PageView, Product,
    ProductDocument, ProductImage, NewsArticle, SiteSetting, VisitSession,
)
from .tasks import enqueue_contact_notification, enqueue_inquiry_notification


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


class AxleSpecInline(admin.StackedInline):
    model = AxleSpec
    extra = 0
    classes = ("collapse",)


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


@admin.register(Product)
class ProductAdmin(admin.ModelAdmin):
    list_display = ("model", "title", "category", "capacity_display", "brake_type", "featured", "status_badge")
    list_filter = (
        "status",
        "category",
        "axle_type",
        "brake_type",
        "featured",
        "is_customizable",
        "made_to_order",
    )
    list_editable = ("featured",)
    search_fields = ("title", "model", "sku", "short_description")
    prepopulated_fields = {"slug": ("title",)}
    filter_horizontal = ("applications",)
    inlines = (AxleSpecInline, ProductImageInline, ProductDocumentInline)
    list_select_related = ("category",)
    list_per_page = 25
    fieldsets = (
        ("Product identity", {"fields": (("title", "slug"), ("model", "sku"), ("category", "status", "position"))}),
        ("Commercial configuration", {"fields": (("rated_capacity", "capacity_unit", "dimension_unit"), ("axle_type", "brake_type"), ("bolt_pattern", "stud_size"), ("grease_cap_type", "tube_size"), ("hub_face_length", "spring_center"), ("surface_finish", "packaging"), "image_url")}),
        ("Publishing", {"fields": (("featured", "is_customizable", "oem_available", "made_to_order"), "applications", "short_description", "description")}),
        ("Search metadata", {"classes": ("collapse",), "fields": ("meta_title", "meta_description")} ),
    )

    @admin.display(description="Capacity", ordering="rated_capacity")
    def capacity_display(self, obj):
        return f"{obj.rated_capacity:,} {obj.capacity_unit}" if obj.rated_capacity else "—"

    @admin.display(description="Status", ordering="status")
    def status_badge(self, obj):
        return format_html('<span class="status-badge status-{}">{}</span>', obj.status, obj.get_status_display())


@admin.register(Category)
class CategoryAdmin(admin.ModelAdmin):
    list_display = ("name", "position", "is_active")
    list_editable = ("position", "is_active")
    prepopulated_fields = {"slug": ("name",)}
    list_per_page = 25


@admin.register(Application)
class ApplicationAdmin(admin.ModelAdmin):
    list_display = ("name", "recommended_finish", "position", "is_active")
    list_editable = ("position", "is_active")
    list_filter = ("is_active",)
    prepopulated_fields = {"slug": ("name",)}
    list_per_page = 25


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
    readonly_fields = ("reference", "next_followup_at", "session", "source_url", "referrer", "first_touch_source", "last_touch_source", "utm_source", "utm_medium", "utm_campaign", "utm_term", "utm_content", "first_landing_page", "last_landing_page", "user_agent", "ip_hash", "notification_queued_at", "notification_claimed_at", "notification_sent_at", "confirmation_claimed_at", "confirmation_sent_at", "notification_error", "created_at", "updated_at")
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
        ("Requirement", {"fields": ("message", "specifications", "privacy_consent", "admin_notes")} ),
        ("Attribution & traceability", {"classes": ("collapse",), "fields": ("session", "source_url", "referrer", ("first_touch_source", "last_touch_source"), ("utm_source", "utm_medium", "utm_campaign"), ("utm_term", "utm_content"), ("first_landing_page", "last_landing_page"), "user_agent", "ip_hash")} ),
        ("Email delivery", {"classes": ("collapse",), "fields": ("notification_queued_at", ("notification_claimed_at", "notification_sent_at"), ("confirmation_claimed_at", "confirmation_sent_at"), "notification_error")} ),
    )

    @admin.display(description="Status", ordering="status")
    def status_badge(self, obj):
        return format_html('<span class="status-badge status-{}">{}</span>', obj.status, obj.get_status_display())

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
        scanned = 0
        clean = 0
        for inquiry in queryset.prefetch_related("attachments"):
            inquiry_scanned = 0
            inquiry_clean = 0
            for attachment in inquiry.attachments.all():
                scanned += 1
                inquiry_scanned += 1
                if scan_inquiry_attachment(attachment) == InquiryAttachment.ScanStatus.CLEAN:
                    clean += 1
                    inquiry_clean += 1
            self.log_admin_action(
                request,
                [inquiry],
                (
                    "Admin action: rescanned "
                    f"{inquiry_scanned} attachment(s); {inquiry_clean} reported clean."
                ),
            )
        self.message_user(
            request,
            f"Scanned {scanned} attachment(s); {clean} reported clean.",
            level=messages.SUCCESS if scanned == clean else messages.WARNING,
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
    list_display = ("title", "product", "document_type", "version", "is_active", "downloads")
    list_filter = ("document_type", "is_active")
    list_editable = ("is_active",)
    search_fields = ("title", "product__title", "product__model")
    list_select_related = ("product",)
    readonly_fields = ("protected_file_status", "controlled_download_link")


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


admin.site.site_header = "AxleForge Control Center"
admin.site.site_title = "AxleForge Admin"
admin.site.index_title = "Catalogue, RFQ and content operations"
