"""
subscriptions/forms.py — Django forms for the subscriptions application.

Provides two forms used in the magic-link subscription flow:
  - EmailForm: captures the subscriber's email address to send a magic link.
  - RegionSelectionForm: lets a subscriber choose which SLF warning regions
    they want to follow.
"""

import logging

from django import forms

from pipeline.models import Region

logger = logging.getLogger(__name__)


class EmailForm(forms.Form):
    """Form for capturing the subscriber's email address."""

    email = forms.EmailField(
        max_length=254,
        widget=forms.EmailInput(
            attrs={
                "placeholder": "your@email.com",
                "class": (
                    "w-full px-4 py-2.5 rounded-[8px] border border-text-3/30 "
                    "bg-card text-text-1 placeholder:text-text-3 "
                    "focus:outline-none focus:ring-2 focus:ring-text-1/30"
                ),
                "autofocus": True,
            }
        ),
    )


class RegionSelectionForm(forms.Form):
    """Form for selecting bulletin regions to subscribe to."""

    regions = forms.ModelMultipleChoiceField(
        queryset=Region.objects.all(),
        widget=forms.CheckboxSelectMultiple,
        required=False,
    )
