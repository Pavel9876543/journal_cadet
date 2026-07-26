(function () {
    'use strict';

    document.addEventListener('DOMContentLoaded', function () {
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
                document.querySelectorAll('[data-collapse-target="' + target.id + '"]').forEach(function (control) {
                    control.disabled = collapse
                        ? control.dataset.collapseAction === 'collapse'
                        : control.dataset.collapseAction === 'expand';
                });
            });
        });
    });
}());
