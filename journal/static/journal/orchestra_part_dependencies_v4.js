(function () {
    'use strict';

    var PART_SELECTOR = '[data-orchestra-part="1"]';
    var INSTRUMENT_SELECTOR = '[data-instrument-reference="1"]';

    function adminJQuery() {
        if (window.django && window.django.jQuery) {
            return window.django.jQuery;
        }
        return window.jQuery || null;
    }

    function fieldPrefix(field, suffix) {
        var name = String(field.name || '');
        return name.endsWith(suffix) ? name.slice(0, -suffix.length) : '';
    }

    function findRelatedField(form, partField, fieldName) {
        var prefix = fieldPrefix(partField, 'orchestra_part');
        var escapedName = prefix + fieldName;
        var byName = form.querySelector('[name="' + CSS.escape(escapedName) + '"]');
        if (byName) {
            return byName;
        }

        var partId = String(partField.id || '');
        if (partId.endsWith('orchestra_part')) {
            return document.getElementById(
                partId.slice(0, -'orchestra_part'.length) + fieldName
            );
        }
        return null;
    }

    function parsePartsMap(field) {
        try {
            var parsed = JSON.parse(field.dataset.orchestraPartsMap || '{}');
            return parsed && typeof parsed === 'object' ? parsed : {};
        } catch (_error) {
            return {};
        }
    }

    function normaliseParts(parts) {
        if (!Array.isArray(parts)) {
            return [];
        }
        return parts
            .filter(function (part) {
                return part && part.id !== undefined && part.name !== undefined;
            })
            .map(function (part) {
                return {id: String(part.id), name: String(part.name)};
            });
    }

    function emitOptionsChanged(field) {
        var jq = adminJQuery();
        if (jq) {
            var wrapped = jq(field);
            wrapped.prop('disabled', field.disabled);
            // A plain change event is intentional: Jazzmin/Select2 listens to
            // it and rebuilds the visible selection from the native options.
            wrapped.trigger('change');
        } else {
            field.dispatchEvent(new Event('change', {bubbles: true}));
        }
        field.dispatchEvent(new CustomEvent('journal:options-updated', {
            bubbles: true,
            detail: {disabled: field.disabled}
        }));
    }

    function setDisabled(field, disabled) {
        field.disabled = Boolean(disabled);
        field.setAttribute('aria-disabled', disabled ? 'true' : 'false');
        if (disabled) {
            field.setAttribute('disabled', 'disabled');
        } else {
            field.removeAttribute('disabled');
        }
    }

    function replaceOptions(field, parts, selectedValue, instrumentId, emptyLabel) {
        var fragment = document.createDocumentFragment();
        fragment.appendChild(new Option(emptyLabel, ''));
        parts.forEach(function (part) {
            var selected = Boolean(
                selectedValue && String(part.id) === String(selectedValue)
            );
            var option = new Option(part.name, part.id, selected, selected);
            option.dataset.instrumentId = String(instrumentId || '');
            fragment.appendChild(option);
        });

        field.replaceChildren(fragment);
        field.dataset.loadedInstrument = String(instrumentId || '');
        setDisabled(field, parts.length === 0);
        if (selectedValue && parts.some(function (part) {
            return String(part.id) === String(selectedValue);
        })) {
            field.value = String(selectedValue);
        } else {
            field.value = '';
        }
        emitOptionsChanged(field);
    }

    function endpointFor(endpoint, instrumentId) {
        var url = new URL(endpoint, window.location.origin);
        url.searchParams.set('instrument', instrumentId);
        return url.toString();
    }

    function initialisePartField(partField) {
        if (!(partField instanceof HTMLSelectElement)) {
            return;
        }
        if (partField.dataset.orchestraPartReady === '1') {
            return;
        }

        var form = partField.closest('form');
        if (!form) {
            return;
        }
        var instrumentFieldName = partField.dataset.instrumentField || 'instrument';
        var instrumentField = findRelatedField(form, partField, instrumentFieldName);
        var customField = findRelatedField(form, partField, 'custom_instrument');
        if (!(instrumentField instanceof HTMLSelectElement)) {
            return;
        }

        partField.dataset.orchestraPartReady = '1';
        var partsMap = parsePartsMap(partField);
        var endpoint = partField.dataset.orchestraPartsUrl || '';
        var initialValue = String(
            partField.dataset.selectedOrchestraPart || partField.value || ''
        );
        var requestSerial = 0;
        var controller = null;

        function syncCustomInstrument() {
            if (!customField) {
                return;
            }
            var referenceSelected = Boolean(String(instrumentField.value || '').trim());
            customField.disabled = referenceSelected;
            customField.required = !referenceSelected;
            customField.setAttribute('aria-disabled', referenceSelected ? 'true' : 'false');
            if (referenceSelected) {
                customField.setAttribute('disabled', 'disabled');
                customField.value = '';
                customField.setCustomValidity('');
            } else {
                customField.removeAttribute('disabled');
            }
        }

        function abortPending() {
            requestSerial += 1;
            if (controller) {
                controller.abort();
                controller = null;
            }
        }

        function disableWith(label) {
            abortPending();
            replaceOptions(partField, [], '', '', label);
            delete partField.dataset.loadedInstrument;
        }

        function renderInstrumentParts(instrumentId, parts, selectedValue) {
            var normalised = normaliseParts(parts);
            replaceOptions(
                partField,
                normalised,
                selectedValue,
                instrumentId,
                normalised.length ? 'Не выбрана' : 'Для инструмента партии не заданы'
            );
        }

        function refreshFromServer(instrumentId, selectedValue, serial) {
            if (!endpoint || typeof window.fetch !== 'function') {
                return;
            }
            controller = typeof AbortController !== 'undefined'
                ? new AbortController()
                : null;
            var options = {
                credentials: 'same-origin',
                cache: 'no-store',
                headers: {'X-Requested-With': 'XMLHttpRequest'}
            };
            if (controller) {
                options.signal = controller.signal;
            }

            window.fetch(endpointFor(endpoint, instrumentId), options)
                .then(function (response) {
                    if (!response.ok) {
                        throw new Error('HTTP ' + response.status);
                    }
                    return response.json();
                })
                .then(function (payload) {
                    if (serial !== requestSerial) {
                        return;
                    }
                    var parts = normaliseParts(payload.parts);
                    partsMap[String(instrumentId)] = parts;
                    renderInstrumentParts(instrumentId, parts, selectedValue);
                })
                .catch(function (error) {
                    if (error && error.name === 'AbortError') {
                        return;
                    }
                    if (serial !== requestSerial) {
                        return;
                    }
                    // Embedded options are already usable. Only replace the
                    // label when there were no local choices at all.
                    if (!normaliseParts(partsMap[String(instrumentId)]).length) {
                        disableWith('Не удалось загрузить партии');
                    }
                });
        }

        function loadParts(keepSelection) {
            syncCustomInstrument();
            var instrumentId = String(instrumentField.value || '').trim();
            var customValue = String(customField ? customField.value || '' : '').trim();
            if (!instrumentId) {
                disableWith(customValue
                    ? 'Недоступно для собственного инструмента'
                    : 'Сначала выберите инструмент');
                return;
            }

            abortPending();
            var selectedValue = keepSelection
                ? String(partField.value || initialValue || '')
                : '';
            initialValue = '';
            var localParts = normaliseParts(partsMap[instrumentId]);
            var serial = requestSerial;

            if (localParts.length) {
                renderInstrumentParts(instrumentId, localParts, selectedValue);
            } else {
                replaceOptions(partField, [], '', instrumentId, 'Загрузка партий…');
            }
            refreshFromServer(instrumentId, selectedValue, serial);
        }

        function queueLoad(keepSelection) {
            window.setTimeout(function () {
                loadParts(Boolean(keepSelection));
            }, 0);
        }

        instrumentField.addEventListener('change', function () {
            queueLoad(false);
        });
        instrumentField.addEventListener('input', function () {
            queueLoad(false);
        });

        var jq = adminJQuery();
        if (jq) {
            jq(instrumentField)
                .off('.journalOrchestraParts')
                .on(
                    'change.journalOrchestraParts '
                    + 'select2:select.journalOrchestraParts '
                    + 'select2:clear.journalOrchestraParts',
                    function () { queueLoad(false); }
                );
        }

        if (customField) {
            customField.addEventListener('input', function () { queueLoad(false); });
            customField.addEventListener('change', function () { queueLoad(false); });
        }

        partField._journalLoadOrchestraParts = function (keepSelection) {
            queueLoad(Boolean(keepSelection));
        };
        queueLoad(true);
    }

    function initialise(scope) {
        var root = scope && scope.querySelectorAll ? scope : document;
        if (root.matches && root.matches(PART_SELECTOR)) {
            initialisePartField(root);
        }
        root.querySelectorAll(PART_SELECTOR).forEach(initialisePartField);
    }

    function findPartFieldForInstrument(instrumentField) {
        var form = instrumentField.closest('form');
        if (!form) {
            return null;
        }
        var name = String(instrumentField.name || '');
        var suffix = name.endsWith('instrument_reference')
            ? 'instrument_reference'
            : 'instrument';
        var prefix = name.endsWith(suffix)
            ? name.slice(0, -suffix.length)
            : '';
        return form.querySelector(
            '[name="' + CSS.escape(prefix + 'orchestra_part') + '"]'
        );
    }

    // Delegated handling survives Jazzmin replacing or moving Select2 nodes.
    document.addEventListener('change', function (event) {
        var instrument = event.target && event.target.closest
            ? event.target.closest(INSTRUMENT_SELECTOR)
            : null;
        if (!instrument) {
            return;
        }
        var partField = findPartFieldForInstrument(instrument);
        if (partField) {
            initialisePartField(partField);
            if (typeof partField._journalLoadOrchestraParts === 'function') {
                partField._journalLoadOrchestraParts(false);
            }
        }
    });

    document.addEventListener('DOMContentLoaded', function () { initialise(document); });
    window.addEventListener('load', function () { initialise(document); });
    document.addEventListener('formset:added', function (event) {
        initialise(event.target || document);
    });

    var observer = new MutationObserver(function (records) {
        records.forEach(function (record) {
            record.addedNodes.forEach(function (node) {
                if (node instanceof Element) {
                    initialise(node);
                }
            });
        });
    });
    if (document.documentElement) {
        observer.observe(document.documentElement, {childList: true, subtree: true});
    }
})();
