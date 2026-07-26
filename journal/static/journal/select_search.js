(function () {
    'use strict';

    var SELECTOR = 'select[data-searchable-select]';
    var MINIMUM_OPTIONS = 9;

    function normalize(value) {
        return String(value || '').trim().toLocaleLowerCase('ru');
    }

    function filterOptions(select, query) {
        var needle = normalize(query);
        Array.prototype.forEach.call(select.options, function (option) {
            if (!option.value) {
                option.hidden = false;
                option.disabled = false;
                return;
            }
            var visible = !needle || normalize(option.textContent).includes(needle) || option.selected;
            option.hidden = !visible;
            option.disabled = !visible;
        });
    }

    function enhance(select) {
        if (!select || select.dataset.searchableInitialized === '1') {
            return;
        }
        var count = Array.prototype.filter.call(select.options, function (option) {
            return Boolean(option.value);
        }).length;
        if (count < MINIMUM_OPTIONS) {
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
        input.addEventListener('input', function () { filterOptions(select, input.value); });
        input.addEventListener('keydown', function (event) {
            if (event.key === 'Escape') {
                input.value = '';
                filterOptions(select, '');
            }
        });
        select.addEventListener('journal:options-updated', function () {
            filterOptions(select, input.value);
        });
    }

    function start(root) {
        (root || document).querySelectorAll(SELECTOR).forEach(enhance);
    }

    document.addEventListener('DOMContentLoaded', function () { start(document); });
    document.addEventListener('journal:options-updated', function (event) {
        start(event.target.parentElement || document);
    }, true);
    document.addEventListener('formset:added', function (event) { start(event.target || document); });
}());
