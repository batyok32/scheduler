from __future__ import annotations

from django.db import models


class RetellFunctionCallLog(models.Model):
    """Lightweight audit trail for Retell custom function invocations."""

    created_at = models.DateTimeField(auto_now_add=True)
    function_name = models.CharField(max_length=128)
    request_id = models.CharField(max_length=128, blank=True, default="")
    payload_json = models.JSONField(default=dict)
    response_json = models.JSONField(default=dict, null=True, blank=True)
    success = models.BooleanField(default=False)
    error_message = models.TextField(blank=True, default="")

    class Meta:
        ordering = ["-created_at"]

    def __str__(self) -> str:
        return f"{self.function_name} @ {self.created_at}"
