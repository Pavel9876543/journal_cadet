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
            var customRow = custom.closest('.form-row, .fieldBox, .field-custom_instrument, .form-group') || custom.parentElement;

            function sync() {
                var useCustom = !reference.value;
                if (customRow) {
                    customRow.hidden = !useCustom;
                }
                custom.disabled = !useCustom;
                custom.required = useCustom;
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
            observer.observe(document.body, {childList: true, subtree: true});
        });
    } else {
        initialise(document);
        observer.observe(document.body, {childList: true, subtree: true});
    }
}());
