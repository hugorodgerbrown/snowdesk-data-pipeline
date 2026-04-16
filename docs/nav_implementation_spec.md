# Navigation implementation spec

## Structure

Single Django template partial at `templates/includes/nav.html`, included on every page.

## Template

This is sample code - rewrite using existing design specs and CSS classes

```html
<nav style="padding: 10px 14px; border-bottom: 0.5px solid rgba(0,0,0,0.12); display: flex; align-items: center;">
  <div style="display: flex; align-items: center; gap: 12px;">
    {% if back_url %}
      <a href="{{ back_url }}" style="display: flex; align-items: center; gap: 5px; color: #5f5e5a; font-size: 13px; text-decoration: none;">
        <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="m15 18-6-6 6-6"/></svg>
        {{ back_label|default:"Back" }}
      </a>
      <div style="width: 0.5px; height: 16px; background: rgba(0,0,0,0.12);"></div>
    {% endif %}
    <a href="/" style="font-family: Georgia, serif; font-size: {% if back_url %}15{% else %}18{% endif %}px; line-height: 1; text-decoration: none; color: inherit;">SnowDesk</a>
  </div>
</nav>
```

## Usage per page

```html
{# Homepage — logo only, no back link #}
{% include "includes/nav.html" %}

{# Map — logo only, logo links home #}
{% include "includes/nav.html" %}

{# Bulletin — back arrow to map, logo links home #}
{% include "includes/nav.html" with back_url="/map/" back_label="Map" %}
```

## Behaviour

- Logo always links to `/` (homepage).
- Logo renders at 18px when standalone, 15px when sharing the row with a back link.
- Back link: chevron-left SVG icon + text label. Only rendered when `back_url` is passed.
- A thin vertical divider separates the back link from the logo.
- No right-side nav elements on any page.
- No hamburger menu, no secondary links.
