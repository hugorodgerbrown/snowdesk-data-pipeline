"""
apps/accounts/forms.py — Django forms for the accounts application.

Provides forms used in the account flow:
  - EmailForm: captures an email address for the standalone manage page
    (unauthenticated entry point).
  - RegisterForm: captures an email address (required) and an optional
    display name for the standalone registration page (SNOW-430).
  - SnowdeskSetPasswordForm: Django's SetPasswordForm restyled with the
    account design tokens, for the credential-setup card (SNOW-431).
  - PasswordSignInForm: email + password for the password sign-in path
    (SNOW-431).
"""

from django import forms
from django.contrib.auth.forms import SetPasswordForm

from apps.public.templatetags.components import input_classes

# Tailwind classes for the text inputs used across the account forms.
# SNOW-672: the string used to be defined here, which meant the staff panels
# and the debug pages carried their own near-copies with no way to tell they
# had drifted. It lives with the rest of the design system's class logic now,
# and the templates reach the same string through {% input_classes %}.
_INPUT_CLASSES = input_classes(size="standard")


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


class SnowdeskSetPasswordForm(SetPasswordForm):
    """Django's SetPasswordForm restyled with the account design tokens.

    Used for the optional "set a password" card on the credential-setup page
    (SNOW-431) and the password-reset confirm page (SNOW-432).  Validation
    (mismatch + ``AUTH_PASSWORD_VALIDATORS``) is inherited unchanged.
    """

    def __init__(self, *args: object, **kwargs: object) -> None:
        """Apply the shared input styling to both password fields."""
        super().__init__(*args, **kwargs)
        for field_name in ("new_password1", "new_password2"):
            self.fields[field_name].widget.attrs["class"] = _INPUT_CLASSES


class PasswordSignInForm(forms.Form):
    """Email + password for the password sign-in path (SNOW-431).

    ``username == email`` (per ``get_or_create_for_email``), so the email is
    the authentication username.  Errors are surfaced generically by the view
    (no user enumeration), so this form only normalises the email.
    """

    email = forms.EmailField(
        max_length=254,
        widget=forms.EmailInput(
            attrs={"placeholder": "your@email.com", "class": _INPUT_CLASSES}
        ),
    )
    password = forms.CharField(
        widget=forms.PasswordInput(
            attrs={"placeholder": "Your password", "class": _INPUT_CLASSES}
        ),
    )

    def clean_email(self) -> str:
        """Normalise the email address to lowercase with whitespace stripped."""
        email: str = self.cleaned_data["email"]
        return email.lower().strip()


class ChangeEmailForm(forms.Form):
    """Form for requesting an account email change (SNOW-433).

    A single field for the new address; the view handles the silent
    uniqueness check and verification flow.
    """

    email = forms.EmailField(
        max_length=254,
        label="New email address",
        widget=forms.EmailInput(
            attrs={
                "placeholder": "new@email.com",
                "class": _INPUT_CLASSES,
                "autofocus": True,
                "autocomplete": "email",
            }
        ),
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
                    "w-full px-4 py-2.5 rounded-tag border border-text-3/30 "
                    "bg-card text-text-1 placeholder:text-text-3 "
                    "focus:outline-none focus:ring-2 focus:ring-text-1/30"
                ),
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
