(function () {
    'use strict';

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

    document.addEventListener('DOMContentLoaded', function () {
        var input = document.querySelector('[data-workspace-search]');
        if (!input) {
            return;
        }
        var status = document.querySelector('[data-workspace-search-status]');
        var cards = Array.prototype.slice.call(document.querySelectorAll('.table-card, .table-panel'));
        var frame = null;

        function applySearch() {
            var query = normalize(input.value);
            var visibleCards = 0;
            cards.forEach(function (card) {
                var heading = card.querySelector('.table-card-header, .table-panel-head');
                var headingMatches = !query || (heading && searchableText(heading).includes(query));
                var matchingRows = 0;
                card.querySelectorAll('tbody tr').forEach(function (row) {
                    var visible = headingMatches || !query || searchableText(row).includes(query);
                    row.hidden = !visible;
                    matchingRows += visible ? 1 : 0;
                });
                var hasRows = card.querySelectorAll('tbody tr').length > 0;
                var visible = !query || headingMatches || !hasRows || matchingRows > 0;
                card.hidden = !visible;
                if (visible) {
                    visibleCards += 1;
                    if (query && card.tagName === 'DETAILS') {
                        card.open = true;
                    }
                }
            });
            if (status) {
                status.textContent = query ? 'Найдено разделов: ' + visibleCards : '';
            }
        }

        input.addEventListener('input', function () {
            if (frame !== null) {
                window.cancelAnimationFrame(frame);
            }
            frame = window.requestAnimationFrame(function () {
                frame = null;
                applySearch();
            });
        });
        input.addEventListener('keydown', function (event) {
            if (event.key === 'Escape') {
                input.value = '';
                applySearch();
            }
        });
    });
}());
