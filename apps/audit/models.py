from django.db import models
from apps.core.models import BaseModel
from django.conf import settings

class AuditLog(BaseModel):
    actor = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True)
    action = models.CharField(max_length=50)
    target = models.CharField(max_length=100)