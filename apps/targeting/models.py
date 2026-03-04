from django.db import models
from apps.core.models import BaseModel

class Country(BaseModel):
    name = models.CharField(max_length=100)
    code = models.CharField(max_length=5, unique=True)

class City(BaseModel):
    name = models.CharField(max_length=100)
    country = models.ForeignKey(Country, on_delete=models.CASCADE)