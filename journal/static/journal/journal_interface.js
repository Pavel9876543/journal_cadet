(function () {
    'use strict';

    var SEARCHABLE_SELECTOR = 'select[data-searchable-select]';
    var SEARCH_THRESHOLD = 8;

    function normalize(value) {
        return String(value || '').trim().toLocaleLowerCase('ru');
    }

    function searchableText(node) {
        var values = Array.prototype.map.call(
            node.querySelectorAll('input:not([type="hidden"]), select, textarea'),
            function (control) { return control.value || ''; }
        );
        return normalize((node.innerText || '') + ' ' + values.join(' '));
    }

    function filterSelect(select, query) {
        var normalizedQuery = normalize(query);
        Array.prototype.forEach.call(select.options, function (option) {
            if (!option.value) {
                option.hidden = false;
                option.disabled = false;
                return;
            }
            var matches = !normalizedQuery || normalize(option.textContent).includes(normalizedQuery);
            var keepSelected = option.value === select.value;
            option.hidden = !matches && !keepSelected;
            option.disabled = !matches && !keepSelected;
        });
    }

    function enhanceSelect(select) {
        if (!select || select.dataset.searchableInitialized === '1') {
            return;
        }
        var optionCount = Array.prototype.filter.call(select.options, function (option) {
            return Boolean(option.value);
        }).length;
        if (optionCount <= SEARCH_THRESHOLD) {
            return;
        }

        select.dataset.searchableInitialized = '1';
        var input = document.createElement('input');
        input.type = 'search';
        input.className = 'select-search-input';
        input.placeholder = select.dataset.searchPlaceholder || 'Поиск в списке';
        input.setAttribute('aria-label', input.placeholder);
        input.autocomplete = 'off';
        select.parentNode.insertBefore(input, select);
        input.addEventListener('input', function () {
            filterSelect(select, input.value);
        });
        input.addEventListener('keydown', function (event) {
            if (event.key === 'Escape') {
                input.value = '';
                filterSelect(select, '');
            }
        });
        select.addEventListener('journal:options-updated', function () {
            filterSelect(select, input.value);
        });
    }

    function enhanceSearchableSelects(root) {
        (root || document).querySelectorAll(SEARCHABLE_SELECTOR).forEach(enhanceSelect);
    }

    function initializeWorkspaceSearch() {
        var input = document.querySelector('[data-workspace-search]');
        if (!input) {
            return;
        }
        var status = document.querySelector('[data-workspace-search-status]');
        var cards = Array.prototype.slice.call(
            document.querySelectorAll('.table-card, .table-panel')
        );

        function applySearch() {
            var query = normalize(input.value);
            var visibleCards = 0;
            cards.forEach(function (card) {
                var header = card.querySelector('.table-card-header, .table-panel-head');
                var headerMatches = !query || (header && searchableText(header).includes(query));
                var matchingRows = 0;
                card.querySelectorAll('tbody tr').forEach(function (row) {
                    var matches = headerMatches || !query || searchableText(row).includes(query);
                    row.hidden = !matches;
                    if (matches) {
                        matchingRows += 1;
                    }
                });
                var hasRows = card.querySelectorAll('tbody tr').length > 0;
                var visible = !query || headerMatches || !hasRows || matchingRows > 0;
                card.hidden = !visible;
                if (visible) {
                    visibleCards += 1;
                    if (query && card.tagName === 'DETAILS') {
                        card.open = true;
                    }
                }
            });
            if (status) {
                status.textContent = query
                    ? 'Найдено разделов: ' + visibleCards
                    : '';
            }
        }

        var searchFrame = null;
        function scheduleSearch() {
            if (searchFrame !== null) {
                window.cancelAnimationFrame(searchFrame);
            }
            searchFrame = window.requestAnimationFrame(function () {
                searchFrame = null;
                applySearch();
            });
        }

        input.addEventListener('input', scheduleSearch);
        input.addEventListener('keydown', function (event) {
            if (event.key === 'Escape') {
                input.value = '';
                applySearch();
            }
        });
    }

    function initializeCollapseControls() {
        document.querySelectorAll('[data-collapse-target]').forEach(function (button) {
            var target = document.getElementById(button.dataset.collapseTarget);
            if (!target) {
                return;
            }
            button.addEventListener('click', function () {
                var collapse = button.dataset.collapseAction === 'collapse';
                target.hidden = collapse;
                if (!collapse) {
                    target.querySelectorAll('details').forEach(function (details) {
                        details.open = true;
                    });
                }
                document.querySelectorAll(
                    '[data-collapse-target="' + target.id + '"]'
                ).forEach(function (control) {
                    control.disabled = (
                        collapse
                        ? control.dataset.collapseAction === 'collapse'
                        : control.dataset.collapseAction === 'expand'
                    );
                });
            });
        });
    }

    function start() {
        enhanceSearchableSelects(document);
        initializeWorkspaceSearch();
        initializeCollapseControls();
    }

    document.addEventListener('journal:options-updated', function (event) {
        enhanceSearchableSelects(event.target.parentElement || document);
    }, true);
    document.addEventListener('formset:added', function (event) {
        enhanceSearchableSelects(event.target || document);
    });

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', start);
    } else {
        start();
    }
}());
