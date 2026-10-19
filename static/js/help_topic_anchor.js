// @ts-check
/**
 * static/js/help_topic_anchor.js — open the /help/ topic a link lands on
 * (SNOW-1119).
 *
 * Every topic on /help/ is a collapsed <details>. The map's help mode links
 * each control to its topic (/help/#help-topic-layers), and a link that
 * scrolled to a closed panel would land on its title with the answer still
 * folded away. This opens the targeted panel — on load and when the hash
 * changes on the page — and brings it into view once open.
 */

(function helpTopicAnchorInit() {
  'use strict';

  /** Open the <details> the address names, if it names one. */
  const openTarget = () => {
    const id = decodeURIComponent(location.hash.slice(1));
    if (!id) return;
    const target = document.getElementById(id);
    if (!target) return;
    const details = target instanceof HTMLDetailsElement ? target : target.querySelector('details');
    if (!details) return;
    details.open = true;
    details.scrollIntoView({ block: 'start' });
  };

  openTarget();
  window.addEventListener('hashchange', openTarget);
}());
