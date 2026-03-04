from django.db import models
from apps.core.models import BaseModel

class Environment(BaseModel):
    name = models.CharField(max_length=50, unique=True)

class FeatureFlag(BaseModel):
    key = models.CharField(max_length=150, unique=True)
    description = models.TextField(blank=True)
    is_active = models.BooleanField(default=False)
    environment = models.ForeignKey(Environment, on_delete=models.CASCADE)