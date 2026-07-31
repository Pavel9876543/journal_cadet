(() => {
    'use strict';

    const WRAPPER_CLASS = 'journal-responsive-table';
    const EXISTING_CONTAINERS = [
        '.journal-responsive-table',
        '.table-scroll',
        '.table-responsive',
        '.results',
        '.tabular',
    ].join(',');

    function tableLabel(table) {
        const caption = table.querySelector('caption');
        if (caption && caption.textContent.trim()) {
            return caption.textContent.trim();
        }

        const section = table.closest('details, .module, .panel, section, article');
        const heading = section && section.querySelector(
            'summary, h1, h2, h3, h4, .module-title, .panel-title'
        );
        return heading && heading.textContent.trim()
            ? heading.textContent.trim()
            : 'Прокручиваемая таблица';
    }

    function makeScrollable(container, table) {
        container.classList.add(WRAPPER_CLASS);
        if (!container.hasAttribute('tabindex')) {
            container.tabIndex = 0;
        }
        if (!container.hasAttribute('role')) {
            container.setAttribute('role', 'region');
        }
        if (!container.hasAttribute('aria-label')) {
            container.setAttribute('aria-label', tableLabel(table));
        }
        container.dataset.responsiveTable = 'ready';
    }

    function enhanceTable(table) {
        if (!(table instanceof HTMLTableElement) || table.dataset.noResponsiveTable !== undefined) {
            return;
        }

        const existing = table.closest(EXISTING_CONTAINERS);
        if (existing) {
            makeScrollable(existing, table);
            return;
        }

        const wrapper = document.createElement('div');
        table.parentNode.insertBefore(wrapper, table);
        wrapper.appendChild(table);
        makeScrollable(wrapper, table);
    }

    function enhanceTables(root = document) {
        if (root instanceof HTMLTableElement) {
            enhanceTable(root);
        }
        root.querySelectorAll?.('table').forEach(enhanceTable);
    }

    document.addEventListener('DOMContentLoaded', () => enhanceTables());
    document.addEventListener('formset:added', (event) => enhanceTables(event.target));

    const observer = new MutationObserver((records) => {
        records.forEach((record) => {
            record.addedNodes.forEach((node) => {
                if (node instanceof HTMLElement) {
                    enhanceTables(node);
                }
            });
        });
    });

    if (document.documentElement) {
        observer.observe(document.documentElement, {childList: true, subtree: true});
    }
})();
