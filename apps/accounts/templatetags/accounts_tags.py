"""
apps/accounts/templatetags/accounts_tags.py — the sign-in link a page hands out.

One tag, ``{% sign_in_href %}``, which builds ``/account/sign-in/?next=<this
page>`` so a visitor who signs in from a button lands back where they were
rather than on the map (SNOW-825 established the contract; SNOW-826 gave it
one definition).

Why a tag and not a partial.  Three templates need this string — the nav's
"Sign in" button and the two trips save buttons — and each of them built it
inline as a ``{% url %}`` captured into a variable, a ``{% with %}`` holding
``request.get_full_path|urlencode``, and an ``|add:`` chain joining the two.
That is four template constructs to express one concatenation, copied per
call site.  It carries no markup and has no variants, so it is a string
helper rather than a component: no ``/_components/`` registry entry and no
fixture.

Registered in ``apps.accounts`` because the URL it reverses is this app's.
Django's tag registry is global, so any template may ``{% load accounts_tags %}``.

**Only use this in a template rendered by a FULL-PAGE response.**  The tag
names ``request.get_full_path()``, and in a template served by an HTMX
fragment endpoint that is the FRAGMENT's own URL, not the page the visitor
is looking at.  Sending someone to ``?next=/routes/partials/list/?variant=map``
lands them on a ``require_htmx``-guarded endpoint, which answers 400 to the
plain GET a redirect makes — so the return trip becomes an error page.

Verified on the current call sites: the nav and the region-pin roundel render
only in full pages, and the two trips partials ARE also rendered as fragments
(``_saved_fragment`` / ``_save_route_fragment`` in ``apps.trips.views``) but
only for a visitor who has just saved — the anonymous branch holding this tag
is unreachable down that path.  A new call site needs the same check; the
question to ask is not "is this file a partial" but "can the signed-out branch
render in a fragment response".
"""

from urllib.parse import quote

from django import template
from django.template import Context
from django.urls import reverse

register = template.Library()


@register.simple_tag(takes_context=True)
def sign_in_href(context: Context) -> str:
    """Return the sign-in URL carrying the current page as ``?next=``.

    Args:
        context: The template context, read for the ``request`` a
            ``RequestContext`` provides.

    Returns:
        ``/account/sign-in/?next=<encoded current path>``, or the bare
        sign-in URL when there is no request to name a destination.

    """
    request = context.get("request")
    path = getattr(request, "get_full_path", None)
    if path is None:
        # No request in the context — a plain ``Context``, or a caller
        # rendering the partial outside the request cycle.  The nav is
        # included by every page, so a tag that raised here would take down
        # the site rather than one button; the bare sign-in URL is a
        # working link, just without the return trip.
        return reverse("accounts:sign_in")

    # ``safe="/"`` matches Django's ``urlencode`` template filter, whose own
    # default is ``safe="/"``.  The three call sites all built this string
    # through that filter before SNOW-826, so keeping the same safe set
    # keeps their rendered output byte-for-byte identical — this is a
    # refactor for them, and narrowing to ``safe=""`` would silently change
    # every emitted href.
    return f"{reverse('accounts:sign_in')}?next={quote(path(), safe='/')}"
