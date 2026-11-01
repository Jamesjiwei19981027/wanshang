from django.conf import settings
from django.core.files.storage import FileSystemStorage
from django.utils.deconstruct import deconstructible


@deconstructible
class PrivateMediaStorage(FileSystemStorage):
    """Filesystem storage without a public URL for protected files."""

    def __init__(self) -> None:
        super().__init__(location=settings.PRIVATE_MEDIA_ROOT, base_url=None)

    def url(self, name: str) -> str:
        raise ValueError("Protected files do not have public URLs.")


private_media_storage = PrivateMediaStorage()
