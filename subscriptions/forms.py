"""
subscriptions/forms.py — Django forms for the subscriptions application.

Provides forms used in the subscription flow:
  - SubscribeForm: captures an email address and a hidden region_id for
    inline subscribe CTAs embedded on bulletin pages.
  - EmailForm: captures an email address for the standalone manage page
    (unauthenticated entry point).
"""

from django import forms


class SubscribeForm(forms.Form):
    """Form for the inline subscribe CTA on bulletin pages.

    Accepts an email address and a hidden region_id so the subscribe
    partial knows which region to pre-associate on first confirmation.
    """

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
            }
        ),
    )
    region_id = forms.CharField(
        max_length=32,
        required=True,
        widget=forms.HiddenInput(),
    )

    def clean_email(self) -> str:
        """Normalise the email address to lowercase with whitespace stripped."""
        email: str = self.cleaned_data["email"]
        return email.lower().strip()


class EmailForm(forms.Form):
    """Form for capturing the subscriber's email address on the manage page."""

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

    def clean_email(self) -> str:
        """Normalise the email address to lowercase with whitespace stripped."""
        email: str = self.cleaned_data["email"]
        return email.lower().strip()
