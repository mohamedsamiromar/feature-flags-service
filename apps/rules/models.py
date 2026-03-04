from django.db import models
from apps.core.models import BaseModel
from apps.flags.models import FeatureFlag

class Rule(BaseModel):
    feature_flag = models.ForeignKey(FeatureFlag, on_delete=models.CASCADE)
    field = models.CharField(max_length=100)
    operator = models.CharField(max_length=50)
    value = models.CharField(max_length=255)