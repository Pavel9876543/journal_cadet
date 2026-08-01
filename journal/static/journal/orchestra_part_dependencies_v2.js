(function () {
    'use strict';

    function findRelatedField(form, partField, fieldName) {
        var suffix = 'orchestra_part';
        var prefix = String(partField.name || '').endsWith(suffix)
            ? partField.name.slice(0, -suffix.length)
            : '';
        return form.querySelector('[name="' + prefix + fieldName + '"]');
    }

    function adminJQuery() {
        if (window.django && window.django.jQuery) {
            return window.django.jQuery;
        }
        return window.jQuery || null;
    }

    function refreshEnhancedSelect(field) {
        var jq = adminJQuery();
        if (jq) {
            // Jazzmin/Django Admin enhances selects with Select2. Updating the
            // native <select> is not enough: Select2 must be explicitly told
            // to redraw its visible options and disabled state.
            jq(field).trigger('change.select2');
        }
        field.dispatchEvent(new Event('journal:options-updated', {bubbles: true}));
    }

    function resetOptions(field, label) {
        while (field.options.length) {
            field.remove(0);
        }
        field.add(new Option(label, ''));
        field.value = '';
    }

    function setDisabled(field, disabled) {
        field.disabled = disabled;
        field.setAttribute('aria-disabled', disabled ? 'true' : 'false');
        if (disabled) {
            field.setAttribute('disabled', 'disabled');
        } else {
            field.removeAttribute('disabled');
        }
        refreshEnhancedSelect(field);
    }

    function buildEndpoint(endpoint, instrumentId) {
        var url = new URL(endpoint, window.location.origin);
        url.searchParams.set('instrument', instrumentId);
        return url.toString();
    }

    function initialisePartField(partField) {
        if (partField.dataset.orchestraPartReady === '1') {
            return;
        }
        var form = partField.closest('form');
        var endpoint = partField.dataset.orchestraPartsUrl;
        var instrumentFieldName = partField.dataset.instrumentField || 'instrument';
        if (!form || !endpoint) {
            return;
        }
        var instrumentField = findRelatedField(form, partField, instrumentFieldName);
        var customField = findRelatedField(form, partField, 'custom_instrument');
        if (!instrumentField) {
            return;
        }

        partField.dataset.orchestraPartReady = '1';
        var requestNumber = 0;
        var abortController = null;

        function syncCustomField() {
            if (!customField) {
                return;
            }
            var useCustom = !instrumentField.value;
            customField.disabled = !useCustom;
            customField.required = useCustom;
            customField.setAttribute('aria-disabled', useCustom ? 'false' : 'true');
            if (useCustom) {
                customField.removeAttribute('disabled');
            } else {
                customField.setAttribute('disabled', 'disabled');
                customField.value = '';
                customField.setCustomValidity('');
            }
        }

        function disable(label) {
            requestNumber += 1;
            if (abortController) {
                abortController.abort();
                abortController = null;
            }
            resetOptions(partField, label);
            delete partField.dataset.loadedInstrument;
            setDisabled(partField, true);
        }

        function renderParts(parts, instrumentId, selectedValue) {
            resetOptions(
                partField,
                parts.length ? 'Не выбрана' : 'Для инструмента партии не заданы'
            );
            parts.forEach(function (part) {
                var selected = selectedValue && String(part.id) === String(selectedValue);
                partField.add(new Option(part.name, String(part.id), selected, selected));
            });
            partField.dataset.loadedInstrument = instrumentId;
            setDisabled(partField, parts.length === 0);
            if (selectedValue && parts.some(function (part) {
                return String(part.id) === String(selectedValue);
            })) {
                partField.value = String(selectedValue);
                refreshEnhancedSelect(partField);
            }
        }

        function loadParts(forceReload) {
            syncCustomField();
            var instrumentId = String(instrumentField.value || '').trim();
            var hasCustomInstrument = Boolean(
                customField && String(customField.value || '').trim()
            );
            if (hasCustomInstrument) {
                disable('Недоступно для собственного инструмента');
                return;
            }
            if (!instrumentId) {
                disable('Сначала выберите инструмент');
                return;
            }

            if (
                !forceReload
                && partField.options.length > 1
                && partField.dataset.loadedInstrument === instrumentId
            ) {
                setDisabled(partField, false);
                return;
            }

            var selectedValue = forceReload ? '' : partField.value;
            var currentRequest = ++requestNumber;
            if (abortController) {
                abortController.abort();
            }
            abortController = typeof AbortController !== 'undefined'
                ? new AbortController()
                : null;

            resetOptions(partField, 'Загрузка партий…');
            setDisabled(partField, true);

            var fetchOptions = {
                credentials: 'same-origin',
                cache: 'no-store',
                headers: {'X-Requested-With': 'XMLHttpRequest'}
            };
            if (abortController) {
                fetchOptions.signal = abortController.signal;
            }

            fetch(buildEndpoint(endpoint, instrumentId), fetchOptions)
                .then(function (response) {
                    if (!response.ok) {
                        throw new Error('Unable to load orchestra parts: ' + response.status);
                    }
                    return response.json();
                })
                .then(function (payload) {
                    if (currentRequest !== requestNumber) {
                        return;
                    }
                    var parts = Array.isArray(payload.parts) ? payload.parts : [];
                    renderParts(parts, instrumentId, selectedValue);
                })
                .catch(function (error) {
                    if (error && error.name === 'AbortError') {
                        return;
                    }
                    if (currentRequest !== requestNumber) {
                        return;
                    }
                    disable('Не удалось загрузить партии');
                });
        }

        if (partField.options.length > 1 && instrumentField.value) {
            partField.dataset.loadedInstrument = String(instrumentField.value);
        }

        instrumentField.addEventListener('change', function () {
            loadParts(true);
        });

        var jq = adminJQuery();
        if (jq) {
            // Native change is normally emitted by Select2, but explicit
            // handlers make the dependency reliable across Jazzmin versions.
            jq(instrumentField).on('select2:select select2:clear', function () {
                loadParts(true);
            });
        }

        if (customField) {
            customField.addEventListener('input', function () {
                loadParts(true);
            });
            customField.addEventListener('change', function () {
                loadParts(true);
            });
        }
        loadParts(false);
    }

    function initialise(scope) {
        (scope || document).querySelectorAll('[data-orchestra-part="1"]').forEach(
            initialisePartField
        );
    }

    function initialiseDocument() {
        initialise(document);
        // Jazzmin may initialise Select2 after DOMContentLoaded. A second pass
        // is harmless because fields are marked as ready and guarantees that
        // dynamically enhanced widgets are synchronised.
        window.setTimeout(function () {
            document.querySelectorAll('[data-orchestra-part="1"]').forEach(function (field) {
                refreshEnhancedSelect(field);
            });
        }, 0);
    }

    document.addEventListener('DOMContentLoaded', initialiseDocument);
    window.addEventListener('load', initialiseDocument);
    document.addEventListener('formset:added', function (event) {
        initialise(event.target || document);
    });
    if (window.django && window.django.jQuery) {
        window.django.jQuery(document).on('formset:added', function (_event, row) {
            initialise(row && row[0] ? row[0] : document);
        });
    }
})();
