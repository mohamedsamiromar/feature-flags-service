from django.db import models

from apps.core.models import BaseModel


class SDKKey(BaseModel):
    """
    Stores SDK key metadata. Key generation and hashing live in KeyGenerator;
    this class is a pure data model.
    """

    class KeyType(models.TextChoices):
        SERVER = "server", "Server"
        CLIENT = "client", "Client"

    name = models.CharField(max_length=100)
    # First 16 chars of the raw key — shown in list responses so users can
    # identify which key is which without exposing the full secret.
    prefix = models.CharField(max_length=16)
    # SHA-256 hex of the full key. Never store the raw key after creation.
    hashed_key = models.CharField(max_length=64, unique=True, db_index=True)
    environment = models.ForeignKey(
        "environment.Environment",
        on_delete=models.CASCADE,
        related_name="sdk_keys",
    )
    key_type = models.CharField(max_length=10, choices=KeyType.choices, default=KeyType.SERVER)
    is_active = models.BooleanField(default=True, db_index=True)
    last_used_at = models.DateTimeField(null=True, blank=True)

    def __str__(self):
        return f"{self.name} ({self.prefix}...)"
