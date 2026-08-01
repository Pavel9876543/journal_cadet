(function () {
    'use strict';

    var ADMIN_PATH_RE = /\/admin\//;
    var POPUP_PARAM_RE = /(?:[?&])_popup=1(?:&|$)/;
    var originalWindowOpen = window.open;

    function viewportWidth() {
        return Math.max(
            document.documentElement.clientWidth || 0,
            window.innerWidth || 0
        );
    }

    function viewportHeight() {
        return Math.max(
            document.documentElement.clientHeight || 0,
            window.innerHeight || 0
        );
    }

    function availableScreenWidth() {
        return Math.max(320, (window.screen && window.screen.availWidth) || viewportWidth());
    }

    function availableScreenHeight() {
        return Math.max(480, (window.screen && window.screen.availHeight) || viewportHeight());
    }

    function setViewportVariables() {
        var root = document.documentElement;
        root.style.setProperty('--journal-viewport-width', viewportWidth() + 'px');
        root.style.setProperty('--journal-viewport-height', viewportHeight() + 'px');
    }

    function parseFeatures(features) {
        var featureMap = {};
        String(features || '').split(',').forEach(function (item) {
            var parts = item.split('=');
            var key = (parts[0] || '').trim();
            if (!key) {
                return;
            }
            featureMap[key] = parts.length > 1 ? parts.slice(1).join('=').trim() : 'yes';
        });
        return featureMap;
    }

    function stringifyFeatures(featureMap) {
        return Object.keys(featureMap).map(function (key) {
            return key + '=' + featureMap[key];
        }).join(',');
    }

    function isAdminRelatedUrl(url) {
        var href = String(url || '');
        return ADMIN_PATH_RE.test(href) && (
            POPUP_PARAM_RE.test(href) ||
            href.indexOf('/add/') !== -1 ||
            href.indexOf('/change/') !== -1 ||
            href.indexOf('/delete/') !== -1 ||
            href.indexOf('/history/') !== -1
        );
    }

    function responsivePopupFeatures(features) {
        var screenWidth = availableScreenWidth();
        var screenHeight = availableScreenHeight();
        var compact = (
            window.matchMedia &&
            window.matchMedia('(max-width: 767.98px)').matches
        ) || Math.min(screenWidth, viewportWidth()) < 768;
        var featureMap = parseFeatures(features);
        var width;
        var height;
        var left;
        var top;

        if (compact) {
            width = Math.max(320, screenWidth);
            height = Math.max(480, screenHeight);
            left = 0;
            top = 0;
        } else {
            width = Math.min(
                screenWidth,
                1440,
                Math.max(640, Math.round(screenWidth * 0.92))
            );
            height = Math.min(
                screenHeight,
                980,
                Math.max(520, Math.round(screenHeight * 0.90))
            );
            left = Math.max(0, Math.round((screenWidth - width) / 2));
            top = Math.max(0, Math.round((screenHeight - height) / 2));
        }

        featureMap.width = width;
        featureMap.height = height;
        featureMap.left = left;
        featureMap.top = top;
        featureMap.resizable = 'yes';
        featureMap.scrollbars = 'yes';
        featureMap.status = 'no';

        return stringifyFeatures(featureMap);
    }

    window.open = function (url, target, features) {
        if (isAdminRelatedUrl(url)) {
            features = responsivePopupFeatures(features);
        }
        return originalWindowOpen.call(window, url, target, features);
    };

    function iframeIsAdminPopup(iframe) {
        if (!iframe) {
            return false;
        }
        var src = iframe.getAttribute('src') || '';
        return ADMIN_PATH_RE.test(src) || POPUP_PARAM_RE.test(src);
    }

    function markRelatedModal(modal) {
        if (!modal || !modal.querySelector) {
            return;
        }
        var iframe = modal.querySelector('iframe');
        if (!iframeIsAdminPopup(iframe)) {
            return;
        }
        modal.classList.add('journal-related-modal', 'responsive-admin-modal');
        var dialog = modal.querySelector('.modal-dialog');
        if (dialog) {
            dialog.classList.add('journal-related-modal__dialog');
        }
        iframe.setAttribute('title', iframe.getAttribute('title') || 'Связанная форма');
    }

    function markExistingRelatedModals(scope) {
        (scope || document).querySelectorAll('.modal').forEach(markRelatedModal);
    }

    function markPopupDocument() {
        var params;
        try {
            params = new URLSearchParams(window.location.search);
        } catch (_error) {
            params = null;
        }
        if (params && params.get('_popup') === '1') {
            document.documentElement.classList.add('journal-admin-popup');
            document.body.classList.add('journal-admin-popup');
        }
    }

    function markScrollableTables(scope) {
        (scope || document).querySelectorAll('table').forEach(function (table) {
            var parent = table.parentElement;
            if (!parent || parent.classList.contains('journal-responsive-table')) {
                return;
            }
            if (
                parent.classList.contains('results') ||
                parent.classList.contains('tabular') ||
                parent.classList.contains('table-responsive') ||
                parent.classList.contains('inline-group')
            ) {
                parent.classList.add('journal-responsive-table');
            }
        });
    }

    function initialiseInstrumentFields(scope) {
        (scope || document).querySelectorAll('[data-instrument-reference="1"]').forEach(function (reference) {
            var form = reference.closest('form') || document;
            var prefixMatch = String(reference.name || '').match(/^(.*-\d+)-[^-]+$/);
            var customName = prefixMatch ? prefixMatch[1] + '-custom_instrument' : 'custom_instrument';
            var custom = form.querySelector('[name="' + customName + '"]')
                || form.querySelector('[data-custom-instrument="1"]');
            if (!custom || reference.dataset.instrumentToggleReady === '1') {
                return;
            }
            reference.dataset.instrumentToggleReady = '1';
            function sync() {
                var useCustom = !reference.value;
                custom.disabled = !useCustom;
                custom.required = useCustom;
                custom.setAttribute('aria-disabled', useCustom ? 'false' : 'true');
                if (!useCustom) {
                    custom.value = '';
                    custom.setCustomValidity('');
                }
            }

            reference.addEventListener('change', sync);
            sync();
        });
    }

    function initialise(scope) {
        setViewportVariables();
        markPopupDocument();
        markExistingRelatedModals(scope || document);
        markScrollableTables(scope || document);
        initialiseInstrumentFields(scope || document);
    }

    var adminFormStateKey = 'journal-admin-form-state';

    function saveAdminFormState(event) {
        var submitterName = event && event.submitter ? event.submitter.name : '';
        if (submitterName === '_addanother' || submitterName === '_saveasnew') {
            try {
                sessionStorage.removeItem(adminFormStateKey);
            } catch (_error) {
                // Navigation still works when session storage is unavailable.
            }
            return;
        }
        var activeTab = document.querySelector('.nav-tabs .nav-link.active, [role="tab"].active');
        var form = event.currentTarget;
        var forms = Array.prototype.slice.call(
            document.querySelectorAll('#content-main form[method="post"]')
        );
        var scrollers = [];
        document.querySelectorAll('.journal-responsive-table, .inline-group, .tabular').forEach(function (element, index) {
            if (element.scrollTop || element.scrollLeft) {
                scrollers.push({index: index, top: element.scrollTop, left: element.scrollLeft});
            }
        });
        try {
            sessionStorage.setItem(adminFormStateKey, JSON.stringify({
                path: window.location.pathname,
                scrollY: window.scrollY || 0,
                activeTab: activeTab ? activeTab.getAttribute('href') : '',
                scrollers: scrollers,
                formId: form.id || '',
                formIndex: forms.indexOf(form),
                submitterName: submitterName
            }));
        } catch (_error) {
            // Saving still works when session storage is unavailable.
        }
    }

    function restoreAdminFormState() {
        var rawState;
        try {
            rawState = sessionStorage.getItem(adminFormStateKey);
            sessionStorage.removeItem(adminFormStateKey);
        } catch (_error) {
            return null;
        }
        if (!rawState) {
            return null;
        }
        var state;
        try {
            state = JSON.parse(rawState);
        } catch (_error) {
            return null;
        }
        var currentPath = window.location.pathname;
        var samePath = !state.path || state.path === currentPath;
        var continuedAdd = Boolean(
            state.path
            && state.path.endsWith('/add/')
            && currentPath.startsWith(state.path.slice(0, -4))
            && currentPath.endsWith('/change/')
        );
        if (!samePath && !continuedAdd) {
            return null;
        }
        if (state.activeTab) {
            var tab = document.querySelector('.nav-tabs .nav-link[href="' + state.activeTab + '"]');
            if (tab) {
                tab.click();
            }
        }
        window.requestAnimationFrame(function () {
            window.scrollTo(0, Number(state.scrollY) || 0);
            var elements = document.querySelectorAll('.journal-responsive-table, .inline-group, .tabular');
            (state.scrollers || []).forEach(function (position) {
                var element = elements[position.index];
                if (element) {
                    element.scrollTop = Number(position.top) || 0;
                    element.scrollLeft = Number(position.left) || 0;
                }
            });
        });
        return state;
    }

    function adminFormForState(state, forms) {
        if (!state) {
            return null;
        }
        if (state.formId) {
            var byId = document.getElementById(state.formId);
            if (byId && byId.matches('#content-main form[method="post"]')) {
                return byId;
            }
        }
        return forms[state.formIndex] || forms[0] || null;
    }

    function adminToastAnchor(form, state) {
        if (state && state.submitterName) {
            var submitter = Array.prototype.find.call(
                form.querySelectorAll('[name]'),
                function (element) { return element.name === state.submitterName; }
            );
            if (submitter) {
                return submitter.closest('.submit-row, .actions, .paginator') || submitter.parentElement;
            }
        }
        return form;
    }

    function adminMessageText(element) {
        var copy = element.cloneNode(true);
        copy.querySelectorAll('button, .close, svg').forEach(function (item) { item.remove(); });
        return (copy.textContent || '').trim();
    }

    function adminFieldErrorDetails(form) {
        var details = [];
        form.querySelectorAll(
            '.errorlist li, .help-block.text-red li, .row-form-errors li, .invalid-feedback'
        ).forEach(function (error) {
            var message = adminMessageText(error);
            if (!message) {
                return;
            }
            var container = error.closest('.form-group, td');
            var control = container && container.querySelector('input[id], select[id], textarea[id]');
            var label = control ? form.querySelector('label[for="' + control.id + '"]') : null;
            if (!label && container && container.matches('td[class*="field-"]')) {
                var fieldClass = Array.prototype.find.call(container.classList, function (name) {
                    return name.indexOf('field-') === 0;
                });
                var table = container.closest('table');
                label = fieldClass && table
                    ? table.querySelector('th.column-' + fieldClass.slice(6))
                    : null;
            }
            var detail = label ? adminMessageText(label) + ': ' + message : message;
            if (details.indexOf(detail) === -1) {
                details.push(detail);
            }
        });
        return details;
    }

    function adminValidationMessage(form) {
        var friendly = form && form.querySelector('[data-user-friendly-error-message]');
        if (friendly) {
            var configured = friendly.getAttribute('data-user-friendly-error-message')
                || adminMessageText(friendly);
            if (configured) {
                return configured;
            }
        }
        var details = adminFieldErrorDetails(form);
        if (!details.length) {
            return 'Не удалось сохранить запись. Проверьте выделенные поля и строки во всех вкладках.';
        }
        var visible = details.slice(0, 4);
        var suffix = details.length > visible.length
            ? ' Ещё ошибок: ' + (details.length - visible.length) + '.'
            : '';
        return 'Не удалось сохранить. ' + visible.join('; ') + '.' + suffix;
    }

    function translateAdminErrorSummary(form) {
        if (!form) {
            return null;
        }
        var summary = form.querySelector(
            ':scope > .alert-danger:not(.alert-dismissible), .errornote, .alert-warning[role="alert"]'
        );
        if (!summary) {
            summary = Array.prototype.find.call(
                document.querySelectorAll('.callout-danger'),
                function (item) {
                    return /Please correct the errors? below\./i.test(item.textContent || '');
                }
            ) || null;
        }
        if (summary) {
            summary.textContent = adminValidationMessage(form);
            summary.setAttribute('role', 'alert');
        }
        return summary;
    }

    function revealAdminError(form) {
        var error = form.querySelector(
            '.errorlist li, .help-block.text-red li, .row-form-errors li, .invalid-feedback'
        );
        if (!error) {
            return;
        }
        var pane = error.closest('.tab-pane[id]');
        if (pane) {
            var selector = '[href="#' + pane.id + '"], [data-bs-target="#' + pane.id + '"]';
            var trigger = document.querySelector(selector);
            if (trigger && !trigger.classList.contains('active')) {
                trigger.click();
            }
        }
        window.requestAnimationFrame(function () {
            error.scrollIntoView({behavior: 'smooth', block: 'center'});
            var container = error.closest('.form-group, td, tr') || error.parentElement;
            var control = container && container.querySelector('input:not([type="hidden"]), select, textarea');
            if (control && typeof control.focus === 'function') {
                control.focus({preventScroll: true});
            }
        });
    }

    function createAdminToast(anchor, message, level) {
        if (!anchor || !message) {
            return;
        }
        var isSuccess = level === 'success';
        var toast = document.createElement('div');
        toast.className = 'journal-save-toast journal-save-toast--' + (isSuccess ? 'success' : 'error');
        toast.setAttribute('role', isSuccess ? 'status' : 'alert');
        toast.setAttribute('aria-live', isSuccess ? 'polite' : 'assertive');
        toast.dataset.localSaveToast = '1';

        var symbol = document.createElement('span');
        symbol.className = 'journal-save-toast__symbol';
        symbol.setAttribute('aria-hidden', 'true');
        symbol.textContent = isSuccess ? '\u2713' : '!';

        var text = document.createElement('span');
        text.className = 'journal-save-toast__message';
        text.textContent = message;

        var close = document.createElement('button');
        close.type = 'button';
        close.className = 'journal-save-toast__close';
        close.setAttribute('aria-label', 'Закрыть уведомление');
        close.textContent = '\u00d7';

        toast.appendChild(symbol);
        toast.appendChild(text);
        toast.appendChild(close);
        if (anchor === anchor.closest('form')) {
            anchor.insertBefore(toast, anchor.firstChild);
        } else {
            anchor.parentNode.insertBefore(toast, anchor);
        }

        function removeToast() {
            toast.remove();
        }
        close.addEventListener('click', removeToast);
        window.setTimeout(removeToast, isSuccess ? 6000 : 9000);
    }

    function showAdminSaveNotification(state, forms) {
        var form = adminFormForState(state, forms);
        if (!form) {
            return;
        }
        var anchor = adminToastAnchor(form, state);
        var errorSummary = translateAdminErrorSummary(form);
        var validationErrors = adminFieldErrorDetails(form);
        if (errorSummary || validationErrors.length) {
            createAdminToast(anchor, adminValidationMessage(form), 'error');
            if (errorSummary) {
                errorSummary.remove();
            }
            revealAdminError(form);
            return;
        }
        var flash = document.querySelector(
            '.messagelist li, .alert-success, .alert-danger, .alert-error, .alert-warning'
        );
        if (flash) {
            var level = (
                flash.classList.contains('error')
                || flash.classList.contains('alert-danger')
                || flash.classList.contains('alert-error')
                || flash.classList.contains('warning')
                || flash.classList.contains('alert-warning')
            ) ? 'error' : 'success';
            createAdminToast(anchor, adminMessageText(flash), level);
            flash.remove();
            return;
        }
        var error = form.querySelector('.form-row.errors');
        if (error) {
            createAdminToast(
                anchor,
                adminMessageText(error) || 'Не удалось сохранить изменения. Проверьте поля формы.',
                'error'
            );
        }
    }

    function initialiseAdminFormState() {
        var forms = Array.prototype.slice.call(
            document.querySelectorAll('#content-main form[method="post"]')
        );
        if (!forms.length) {
            return;
        }
        forms.forEach(translateAdminErrorSummary);
        var state = restoreAdminFormState();
        showAdminSaveNotification(state, forms);
        forms.forEach(function (form) {
            form.addEventListener('submit', saveAdminFormState);
        });
    }

    var resizeTimer = null;
    window.addEventListener('resize', function () {
        window.clearTimeout(resizeTimer);
        resizeTimer = window.setTimeout(setViewportVariables, 80);
    });
    window.addEventListener('orientationchange', setViewportVariables);

    document.addEventListener('shown.bs.modal', function (event) {
        markRelatedModal(event.target);
    });
    document.addEventListener('formset:added', function (event) {
        initialise(event.target || document);
    });

    if (window.django && window.django.jQuery) {
        window.django.jQuery(document).on('shown.bs.modal', '.modal', function () {
            markRelatedModal(this);
        });
        window.django.jQuery(document).on('formset:added', function (_event, row) {
            initialise(row && row[0] ? row[0] : document);
        });
    }

    var observer = new MutationObserver(function (mutations) {
        mutations.forEach(function (mutation) {
            mutation.addedNodes.forEach(function (node) {
                if (!node || node.nodeType !== 1) {
                    return;
                }
                if (node.matches && node.matches('.modal')) {
                    markRelatedModal(node);
                }
                markExistingRelatedModals(node);
                markScrollableTables(node);
                initialiseInstrumentFields(node);
            });
        });
    });

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', function () {
            initialise(document);
            initialiseAdminFormState();
            observer.observe(document.body, {childList: true, subtree: true});
        });
    } else {
        initialise(document);
        initialiseAdminFormState();
        observer.observe(document.body, {childList: true, subtree: true});
    }
}());
