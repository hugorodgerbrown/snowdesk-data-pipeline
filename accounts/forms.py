"""
accounts/forms.py — Django forms for the accounts application.

Provides forms used in the subscription flow:
  - SubscribeForm: captures an email address and a hidden region_id for
    inline subscribe CTAs embedded on bulletin pages.
  - EmailForm: captures an email address for the standalone manage page
    (unauthenticated entry point).
  - RegisterForm: captures an email address (required) and an optional
    display name for the standalone registration page (SNOW-430).
"""

from django import forms

# Shared Tailwind classes for the text inputs used across the account forms.
_INPUT_CLASSES = (
    "w-full px-4 py-2.5 rounded-tag border border-text-3/30 "
    "bg-card text-text-1 placeholder:text-text-3 "
    "focus:outline-none focus:ring-2 focus:ring-text-1/30"
)


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
                    "w-full px-4 py-2.5 rounded-tag border border-text-3/30 "
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


class RegisterForm(forms.Form):
    """Form for the standalone registration page (SNOW-430).

    Email is the only mandatory field; ``name`` is optional and, when
    supplied, is stored on ``Account.display_name``.
    """

    email = forms.EmailField(
        max_length=254,
        widget=forms.EmailInput(
            attrs={
                "placeholder": "your@email.com",
                "class": _INPUT_CLASSES,
                "autofocus": True,
                "autocomplete": "email",
            }
        ),
    )
    name = forms.CharField(
        max_length=150,
        required=False,
        widget=forms.TextInput(
            attrs={
                "placeholder": "Your name (optional)",
                "class": _INPUT_CLASSES,
                "autocomplete": "name",
            }
        ),
    )

    def clean_email(self) -> str:
        """Normalise the email address to lowercase with whitespace stripped."""
        email: str = self.cleaned_data["email"]
        return email.lower().strip()

    def clean_name(self) -> str:
        """Strip surrounding whitespace from the optional display name."""
        name: str = self.cleaned_data.get("name", "")
        return name.strip()


class EmailForm(forms.Form):
    """Form for capturing the subscriber's email address on the manage page."""

    email = forms.EmailField(
        max_length=254,
        widget=forms.EmailInput(
            attrs={
                "placeholder": "your@email.com",
                "class": (
                    "w-full px-4 py-2.5 rounded-tag border border-text-3/30 "
                    "bg-card text-text-1 placeholder:text-text-3 "
                    "focus:outline-none focus:ring-2 focus:ring-text-1/30"
                ),
                "autofocus": True,
                # Required for WebAuthn conditional UI (passkey autofill).
                # The browser surfaces registered passkeys inline in this field.
                "autocomplete": "username webauthn",
            }
        ),
    )

    def clean_email(self) -> str:
        """Normalise the email address to lowercase with whitespace stripped."""
        email: str = self.cleaned_data["email"]
        return email.lower().strip()
