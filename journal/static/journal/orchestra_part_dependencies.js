(function () {
    'use strict';

    function findRelatedField(form, partField, fieldName) {
        var suffix = 'orchestra_part';
        var prefix = String(partField.name || '').endsWith(suffix)
            ? partField.name.slice(0, -suffix.length)
            : '';
        return form.querySelector('[name="' + prefix + fieldName + '"]');
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

        function syncCustomField() {
            if (!customField) {
                return;
            }
            var useCustom = !instrumentField.value;
            customField.disabled = !useCustom;
            customField.required = useCustom;
            customField.setAttribute('aria-disabled', useCustom ? 'false' : 'true');
            if (!useCustom) {
                customField.value = '';
                customField.setCustomValidity('');
            }
        }

        function disable(label) {
            requestNumber += 1;
            resetOptions(partField, label);
            setDisabled(partField, true);
        }

        function loadParts(forceReload) {
            syncCustomField();
            var instrumentId = instrumentField.value;
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
            resetOptions(partField, 'Загрузка партий…');
            setDisabled(partField, true);

            var separator = endpoint.indexOf('?') === -1 ? '?' : '&';
            fetch(endpoint + separator + 'instrument=' + encodeURIComponent(instrumentId), {
                credentials: 'same-origin',
                headers: {'X-Requested-With': 'XMLHttpRequest'}
            })
                .then(function (response) {
                    if (!response.ok) {
                        throw new Error('Unable to load orchestra parts');
                    }
                    return response.json();
                })
                .then(function (payload) {
                    if (currentRequest !== requestNumber) {
                        return;
                    }
                    var parts = Array.isArray(payload.parts) ? payload.parts : [];
                    resetOptions(
                        partField,
                        parts.length ? 'Не выбрана' : 'Для инструмента партии не заданы'
                    );
                    parts.forEach(function (part) {
                        partField.add(new Option(part.name, String(part.id)));
                    });
                    if (selectedValue && parts.some(function (part) {
                        return String(part.id) === String(selectedValue);
                    })) {
                        partField.value = selectedValue;
                    }
                    partField.dataset.loadedInstrument = instrumentId;
                    setDisabled(partField, parts.length === 0);
                })
                .catch(function () {
                    if (currentRequest !== requestNumber) {
                        return;
                    }
                    disable('Не удалось загрузить партии');
                });
        }

        if (partField.options.length > 1 && instrumentField.value) {
            partField.dataset.loadedInstrument = instrumentField.value;
        }
        instrumentField.addEventListener('change', function () {
            loadParts(true);
        });
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

    document.addEventListener('DOMContentLoaded', function () {
        initialise(document);
    });
    document.addEventListener('formset:added', function (event) {
        initialise(event.target || document);
    });
    if (window.django && window.django.jQuery) {
        window.django.jQuery(document).on('formset:added', function (_event, row) {
            initialise(row && row[0] ? row[0] : document);
        });
    }
})();
