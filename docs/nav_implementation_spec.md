# Navigation implementation spec

## Structure

Single Django template partial at `templates/includes/nav.html`, included on
every public page. The partial renders the Snowdesk wordmark (always linking
home), an optional chevron-back link, and — on bulletin pages — an optional
right-aligned calendar toggle button.

## Partial

Tailwind-styled version of the partial as implemented. Tokens come from
`src/css/main.css` (`text-text-1`, `text-text-2`, `border-border`) so the
bar picks up theme changes for free.

```html
<nav class="border-b border-border">
  <div class="mx-auto max-w-[640px] flex items-center gap-3 px-4 py-2.5">
    {% if back_url %}
      <a
        href="{{ back_url }}"
        class="flex items-center gap-1.5 text-text-2 text-sm hover:text-text-1"
      >
        <svg width="14" height="14" viewBox="0 0 24 24" fill="none"
             stroke="currentColor" stroke-width="2"
             stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
          <path d="m15 18-6-6 6-6" />
        </svg>
        {{ back_label|default:"Back" }}
      </a>
      <div class="h-4 w-px bg-border" aria-hidden="true"></div>
    {% endif %}
    <a
      href="{% url 'public:home' %}"
      class="text-text-1 leading-none font-semibold tracking-tight hover:opacity-80
             {% if back_url %}text-[15px]{% else %}text-[18px]{% endif %}"
    >
      Snowdesk
    </a>
    {% if calendar_region_id and calendar_partial_url %}
      <button
        type="button"
        class="ml-auto p-1.5 text-text-2 hover:text-text-1 rounded"
        hx-get="{{ calendar_partial_url }}"
        hx-target="#bulletin-calendar-host"
        hx-swap="innerHTML"
        hx-indicator="#calendar-spinner"
        aria-label='{% trans "Show monthly calendar" %}'
      >
        {# calendar SVG glyph #}
      </button>
      <span id="calendar-spinner" class="htmx-indicator text-text-3 text-xs"
            aria-live="polite">{% trans "Loading…" %}</span>
    {% endif %}
  </div>
</nav>
```

## Parameters

| Parameter              | Type | Description                                                                              |
|------------------------|------|------------------------------------------------------------------------------------------|
| `back_url`             | str  | Optional. URL for the chevron-back link. Omit on pages where "back" has no obvious target (home, map). |
| `back_label`           | str  | Optional. Label shown next to the chevron. Defaults to "Back" — prefer a destination-specific label ("Map", "Season"). |
| `calendar_region_id`   | str  | Optional. SLF region identifier (e.g. `CH-4115`). When set with `calendar_partial_url`, a calendar glyph button is rendered right-aligned. Bulletin pages only. |
| `calendar_partial_url` | str  | Optional. HTMX endpoint URL for the calendar fragment (required when `calendar_region_id` is set). Clicking the button issues an `hx-get` and swaps the response into `#bulletin-calendar-host`. |

## Usage per page

```html
{# Homepage — logo only, no back link #}
{% include "includes/nav.html" %}

{# Map — logo only, logo links home #}
{% include "includes/nav.html" %}

{# random_bulletins / season_bulletins — back to map #}
{% url 'public:map' as map_url %}
{% include "includes/nav.html" with back_url=map_url back_label="Map" %}

{# Bulletin page — back to map + calendar toggle #}
{% include "includes/nav.html" with back_url=map_url back_label="Map" calendar_region_id=region.region_id calendar_partial_url=calendar_partial_url %}
```

## Behaviour

- Logo always links to `/` (homepage).
- Logo renders at 18px when standalone, 15px when sharing the row with a back link.
- Back link: chevron-left SVG icon + text label. Only rendered when `back_url` is passed.
- A thin vertical divider separates the back link from the logo.
- Calendar button: rendered right-aligned (`ml-auto`) when both
  `calendar_region_id` and `calendar_partial_url` are passed. Bulletin
  pages only — the map, homepage, season and history pages don't host a
  calendar.
- `<nav>` spans the full page width so its `border-bottom` forms an
  edge-to-edge rule; content sits inside a `max-w-[640px]` inner container
  that matches the bulletin-family page width.
- No hamburger menu, no secondary links.
