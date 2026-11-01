from django.apps import AppConfig


class CatalogConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "catalog"
    verbose_name = "Catalogue & Leads"

    def ready(self):
        from django.contrib.auth.signals import user_logged_in

        from .middleware import mark_admin_login_succeeded

        user_logged_in.connect(
            mark_admin_login_succeeded,
            dispatch_uid="catalog.admin_login_rate_limit.login_succeeded",
            weak=False,
        )
