from __future__ import annotations

from django.db.models import Q

from .models import ContactMessage, Inquiry, Product


def admin_dashboard(request):
    """Supply operational totals only to the Admin landing page."""
    if request.path.rstrip("/") != "/admin" or not request.user.is_authenticated:
        return {}

    permissions = {
        "view_products": request.user.has_perm("catalog.view_product"),
        "add_products": request.user.has_perm("catalog.add_product"),
        "view_inquiries": request.user.has_perm("catalog.view_inquiry"),
        "view_contacts": request.user.has_perm("catalog.view_contactmessage"),
    }
    recent_inquiries = (
        Inquiry.objects.select_related("assigned_to").order_by("-created_at")[:6]
        if permissions["view_inquiries"]
        else Inquiry.objects.none()
    )
    return {
        "admin_permissions": permissions,
        "admin_metrics": {
            "published_products": (
                Product.objects.filter(status=Product.Status.PUBLISHED).count()
                if permissions["view_products"]
                else None
            ),
            "new_inquiries": (
                Inquiry.objects.filter(status=Inquiry.Status.NEW).count()
                if permissions["view_inquiries"]
                else None
            ),
            "high_priority_inquiries": (
                Inquiry.objects.filter(
                    Q(priority=Inquiry.Priority.HIGH) | Q(priority=Inquiry.Priority.URGENT)
                ).exclude(
                    status__in=[Inquiry.Status.WON, Inquiry.Status.LOST, Inquiry.Status.SPAM]
                ).count()
                if permissions["view_inquiries"]
                else None
            ),
            "unresolved_contacts": (
                ContactMessage.objects.filter(is_resolved=False).count()
                if permissions["view_contacts"]
                else None
            ),
            "recent_inquiries": recent_inquiries,
        }
    }
