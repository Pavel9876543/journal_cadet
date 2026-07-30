(function () {
    'use strict';

    document.addEventListener('DOMContentLoaded', function () {
        var scrollKey = 'journal-form-state:' + window.location.pathname;

        function savePosition(event) {
            var scrollers = [];
            document.querySelectorAll('.table-scroll').forEach(function (element, index) {
                if (element.scrollTop || element.scrollLeft) {
                    scrollers.push({index: index, top: element.scrollTop, left: element.scrollLeft});
                }
            });
            try {
                sessionStorage.setItem(scrollKey, JSON.stringify({
                    scrollY: window.scrollY || 0,
                    scrollers: scrollers,
                    saveContext: event.currentTarget.dataset.saveContext || ''
                }));
            } catch (_error) {
                // Saving still works when session storage is unavailable.
            }
        }

        function readSavedState() {
            var rawState;
            try {
                rawState = sessionStorage.getItem(scrollKey);
                sessionStorage.removeItem(scrollKey);
            } catch (_error) {
                return null;
            }
            if (!rawState) {
                return null;
            }
            try {
                return JSON.parse(rawState);
            } catch (_error) {
                return {scrollY: Number(rawState) || 0, scrollers: [], saveContext: ''};
            }
        }

        function restorePosition(state) {
            if (!state) {
                return;
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

        function findSavedForm(state) {
            if (!state || !state.saveContext) {
                return null;
            }
            return Array.prototype.find.call(
                document.querySelectorAll('main form[data-save-context]'),
                function (form) {
                    return form.dataset.saveContext === state.saveContext;
                }
            ) || null;
        }

        function toastHost(form) {
            var previous = form.previousElementSibling;
            if (previous && previous.classList.contains('save-toast-stack')) {
                return previous;
            }
            var host = document.createElement('div');
            host.className = 'save-toast-stack';
            host.dataset.saveToastHost = '1';
            form.parentNode.insertBefore(host, form);
            return host;
        }

        function showToast(form, message, level) {
            if (!form || !message) {
                return false;
            }
            var isSuccess = level === 'success';
            var toast = document.createElement('div');
            toast.className = 'save-toast save-toast--' + (isSuccess ? 'success' : 'error');
            toast.setAttribute('role', isSuccess ? 'status' : 'alert');
            toast.setAttribute('aria-live', isSuccess ? 'polite' : 'assertive');
            toast.dataset.localSaveToast = '1';

            var symbol = document.createElement('span');
            symbol.className = 'save-toast__symbol';
            symbol.setAttribute('aria-hidden', 'true');
            symbol.textContent = isSuccess ? '\u2713' : '!';

            var text = document.createElement('span');
            text.className = 'save-toast__message';
            text.textContent = message.trim();

            var close = document.createElement('button');
            close.type = 'button';
            close.className = 'save-toast__close';
            close.setAttribute('aria-label', 'Закрыть уведомление');
            close.textContent = '\u00d7';

            toast.appendChild(symbol);
            toast.appendChild(text);
            toast.appendChild(close);
            var host = toastHost(form);
            host.appendChild(toast);

            function removeToast() {
                toast.remove();
                if (!host.children.length) {
                    host.remove();
                }
            }
            close.addEventListener('click', removeToast);
            window.setTimeout(removeToast, isSuccess ? 6000 : 9000);
            return true;
        }

        function localizeFlashMessages(form) {
            if (!form) {
                return false;
            }
            var localized = false;
            document.querySelectorAll('[data-flash-message]').forEach(function (flash) {
                var level = flash.dataset.messageLevel === 'success' ? 'success' : 'error';
                if (showToast(form, flash.textContent || '', level)) {
                    var stack = flash.parentElement;
                    flash.remove();
                    if (stack && !stack.children.length) {
                        stack.remove();
                    }
                    localized = true;
                }
            });
            return localized;
        }

        var savedState = readSavedState();
        var savedForm = findSavedForm(savedState);
        var localizedFlash = localizeFlashMessages(savedForm);
        var firstError = document.querySelector('[data-error-for], .grade-form .field-error');
        if (firstError) {
            var form = firstError.closest('form') || document.querySelector('#grade-create-form');
            if (!localizedFlash) {
                showToast(form, firstError.textContent || 'Не удалось сохранить изменения.', 'error');
            }
            var field = form && firstError.dataset.errorFor ? form.elements[firstError.dataset.errorFor] : null;
            var target = field || firstError;
            target.scrollIntoView({behavior: 'smooth', block: 'center'});
            if (field && typeof field.focus === 'function') {
                field.focus({preventScroll: true});
            }
        } else {
            restorePosition(savedState);
        }

        document.querySelectorAll('main form[method="post"], main form[data-preserve-scroll]').forEach(function (form) {
            form.addEventListener('submit', savePosition);
        });

        document.querySelectorAll('main a[data-preserve-scroll]').forEach(function (link) {
            link.addEventListener('click', savePosition);
        });

        document.querySelectorAll('main [data-filter-auto-submit="1"]').forEach(function (field) {
            field.addEventListener('change', function () {
                var form = field.form;
                if (!form) {
                    return;
                }
                if (typeof form.requestSubmit === 'function') {
                    form.requestSubmit();
                } else {
                    savePosition({currentTarget: form});
                    form.submit();
                }
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
