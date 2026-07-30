(function () {
    'use strict';

    var storagePrefix = 'journal-collapse-state:';

    function storageKey(details) {
        return storagePrefix + (details.dataset.collapseKey || details.id || '');
    }

    function readState(details) {
        if (!details.dataset.collapseKey && !details.id) {
            return null;
        }
        try {
            return window.localStorage.getItem(storageKey(details));
        } catch (_error) {
            return null;
        }
    }

    function writeState(details) {
        if (!details.dataset.collapseKey && !details.id) {
            return;
        }
        try {
            window.localStorage.setItem(storageKey(details), details.open ? 'open' : 'closed');
        } catch (_error) {
            // The controls still work when browser storage is unavailable.
        }
    }

    function detailsFor(target) {
        return Array.prototype.slice.call(
            target.querySelectorAll('details.collapsible-card')
        );
    }

    function updateControls(target) {
        var details = detailsFor(target);
        var allClosed = details.length > 0 && details.every(function (item) { return !item.open; });
        document.querySelectorAll('[data-collapse-target="' + target.id + '"]').forEach(function (control) {
            var action = allClosed ? 'expand' : 'collapse';
            control.dataset.collapseAction = action;
            control.disabled = details.length === 0;
            control.textContent = action === 'expand'
                ? (control.dataset.expandLabel || 'Развернуть все таблицы')
                : (control.dataset.collapseLabel || 'Свернуть все таблицы');
        });
    }

    function initializeGroup(target) {
        var details = detailsFor(target);
        details.forEach(function (item) {
            var savedState = readState(item);
            if (savedState === 'open') {
                item.open = true;
            } else if (savedState === 'closed') {
                item.open = false;
            }
            item.addEventListener('toggle', function () {
                writeState(item);
                updateControls(target);
            });
        });

        document.querySelectorAll('[data-collapse-target="' + target.id + '"]').forEach(function (button) {
            button.addEventListener('click', function () {
                var open = button.dataset.collapseAction === 'expand';
                details.forEach(function (item) {
                    item.open = open;
                    writeState(item);
                });
                updateControls(target);
            });
        });
        updateControls(target);
    }

    function start() {
        document.querySelectorAll('[data-collapsible-group][id]').forEach(initializeGroup);
    }

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', start);
    } else {
        start();
    }
}());
