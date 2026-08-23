from django.urls import path

from domains.assets.api.views import (
    FixedAssetDetailView,
    FixedAssetDepreciationScheduleView,
    FixedAssetListView,
)

urlpatterns = [
    path('fixed-assets/', FixedAssetListView.as_view(), name='assets-fixed-asset-list'),
    path(
        'fixed-assets/<int:pk>/',
        FixedAssetDetailView.as_view(),
        name='assets-fixed-asset-detail',
    ),
    path(
        'fixed-assets/<int:pk>/depreciation-schedule/',
        FixedAssetDepreciationScheduleView.as_view(),
        name='assets-fixed-asset-depreciation-schedule',
    ),
]
