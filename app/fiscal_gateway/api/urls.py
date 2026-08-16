from django.urls import path

from fiscal_gateway.api.views.as4_inbound import As4InboundPushView

urlpatterns = [
    path('as4/inbound/', As4InboundPushView.as_view(), name='fiscal_as4_inbound'),
]
