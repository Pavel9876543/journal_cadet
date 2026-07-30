(function () {
    'use strict';

    document.addEventListener('DOMContentLoaded', function () {
        var scrollKey = 'journal-form-state:' + window.location.pathname;

        function savePosition() {
            var scrollers = [];
            document.querySelectorAll('.table-scroll').forEach(function (element, index) {
                if (element.scrollTop || element.scrollLeft) {
                    scrollers.push({index: index, top: element.scrollTop, left: element.scrollLeft});
                }
            });
            sessionStorage.setItem(scrollKey, JSON.stringify({
                scrollY: window.scrollY || 0,
                scrollers: scrollers
            }));
        }

        function restorePosition(rawState) {
            var state;
            try {
                state = JSON.parse(rawState);
            } catch (_error) {
                state = {scrollY: Number(rawState) || 0, scrollers: []};
            }
            window.requestAnimationFrame(function () {
                window.scrollTo(0, Number(state.scrollY) || 0);
                var elements = document.querySelectorAll('.table-scroll');
                (state.scrollers || []).forEach(function (position) {
                    var element = elements[position.index];
                    if (element) {
                        element.scrollTop = Number(position.top) || 0;
                        element.scrollLeft = Number(position.left) || 0;
                    }
                });
            });
        }

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
                restorePosition(savedY);
                sessionStorage.removeItem(scrollKey);
            }
        }

        document.querySelectorAll('main form[method="post"]').forEach(function (form) {
            form.addEventListener('submit', savePosition);
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
