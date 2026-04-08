/*
 * static/js/main.js — Application JavaScript.
 *
 * Minimal JS — HTMX handles most dynamic behaviour declaratively.
 * Add progressive enhancements here; keep logic in the server.
 */

// Log HTMX errors to the console so they are visible during development.
document.body.addEventListener("htmx:responseError", function (evt) {
  console.error("HTMX response error", evt.detail);
});

document.body.addEventListener("htmx:sendError", function (evt) {
  console.error("HTMX send error", evt.detail);
});
