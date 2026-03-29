from django.contrib import admin

from booking.models import RetellFunctionCallLog


@admin.register(RetellFunctionCallLog)
class RetellFunctionCallLogAdmin(admin.ModelAdmin):
    list_display = ("created_at", "function_name", "success", "request_id")
    list_filter = ("success", "function_name")
    readonly_fields = ("created_at", "function_name", "request_id", "payload_json", "response_json", "success", "error_message")
    search_fields = ("request_id", "function_name")
