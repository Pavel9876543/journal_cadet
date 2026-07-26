(function () {
    'use strict';

    document.addEventListener('DOMContentLoaded', function () {
        var scrollKey = 'journal-scroll-y';
        var firstError = document.querySelector('[data-error-for]');
        if (firstError) {
            sessionStorage.removeItem(scrollKey);
            var form = firstError.closest('form') || document.querySelector('#grade-create-form');
            var field = form && firstError.dataset.errorFor ? form.elements[firstError.dataset.errorFor] : null;
            var target = field || firstError;
            target.scrollIntoView({behavior: 'smooth', block: 'center'});
            if (field && typeof field.focus === 'function') {
                field.focus({preventScroll: true});
            }
        } else {
            var savedY = sessionStorage.getItem(scrollKey);
            if (savedY !== null) {
                window.scrollTo(0, Number(savedY) || 0);
                sessionStorage.removeItem(scrollKey);
            }
        }

        document.querySelectorAll('main form[method="post"]').forEach(function (form) {
            form.addEventListener('submit', function () {
                sessionStorage.setItem(scrollKey, String(window.scrollY));
            });
        });

        var dirtyForms = new Set();
        document.querySelectorAll('.table-form').forEach(function (form) {
            var saveButton = form.querySelector('.table-save-button');
            var state = form.querySelector('.save-state');
            var stateText = state ? state.querySelector('span') : null;
            var controls = form.querySelectorAll('input:not([type="hidden"]), select');
            if (!saveButton || !state || !stateText || controls.length === 0) {
                return;
            }
            saveButton.disabled = true;
            function markDirty() {
                dirtyForms.add(form);
                saveButton.disabled = false;
                state.classList.add('is-dirty');
                stateText.textContent = 'Есть несохранённые изменения';
            }
            controls.forEach(function (control) {
                control.addEventListener('input', markDirty, {once: true});
                control.addEventListener('change', markDirty, {once: true});
            });
            form.addEventListener('submit', function () { dirtyForms.delete(form); });
        });

        window.addEventListener('beforeunload', function (event) {
            if (dirtyForms.size) {
                event.preventDefault();
                event.returnValue = '';
            }
        });
    });
}());
