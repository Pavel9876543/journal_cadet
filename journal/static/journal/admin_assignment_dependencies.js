(function () {
    'use strict';

    var SELECT_FIELDS = ['group', 'student', 'subject', 'teacher'];

    function inlinePrefix(name) {
        var match = String(name || '').match(/^(.*-\d+)-[^-]+$/);
        return match ? match[1] : '';
    }

    function markerName(type) {
        return 'assignmentOptionsInitialized' + String(type || '').replace(/[^a-z0-9]/gi, '');
    }

    function start(root) {
        var container = root || document;
        container.querySelectorAll('[data-assignment-options-url]').forEach(function (source) {
            var form = source.closest('form');
            if (!form) {
                return;
            }

            var assignmentType = source.dataset.assignmentType || form.dataset.assignmentType || '';
            var prefix = inlinePrefix(source.getAttribute('name'));
            var scope = prefix ? (source.closest('tr') || source.closest('[class*="dynamic-"]') || form) : form;
            var marker = markerName(assignmentType || 'default');
            if (scope.dataset[marker] === '1') {
                return;
            }
            scope.dataset[marker] = '1';

            initializeScope(
                scope,
                form,
                prefix,
                source.dataset.assignmentOptionsUrl,
                assignmentType,
                source
            );
        });
    }

    function initializeScope(scope, form, prefix, endpoint, assignmentType, source) {
        if (!endpoint || !assignmentType) {
            return;
        }

        function fieldSelector(name) {
            return prefix ? '[name="' + prefix + '-' + name + '"]' : '[name="' + name + '"]';
        }

        var fields = {};
        SELECT_FIELDS.concat(['academic_year', 'is_specialty', 'sort_order']).forEach(function (name) {
            fields[name] = scope.querySelector(fieldSelector(name));
        });

        var placeholders = {};
        SELECT_FIELDS.forEach(function (name) {
            var select = fields[name];
            if (!select || select.tagName !== 'SELECT') {
                return;
            }
            var emptyOption = Array.prototype.find.call(select.options, function (option) {
                return option.value === '';
            });
            placeholders[name] = emptyOption ? emptyOption.textContent : 'Выберите значение';
        });

        var activeRequest = null;
        var requestSequence = 0;

        SELECT_FIELDS.forEach(function (name) {
            var field = fields[name];
            if (!field) {
                return;
            }
            field.addEventListener('change', function () {
                applyLocalDefaults(name);
                loadOptions(name);
            });
        });

        function buildUrl(changedField) {
            var url = new URL(endpoint, window.location.origin);
            url.searchParams.set('type', assignmentType);
            SELECT_FIELDS.concat(['academic_year', 'sort_order']).forEach(function (name) {
                if (fields[name] && fields[name].value) {
                    url.searchParams.set(name, fields[name].value);
                }
            });
            if (changedField) {
                url.searchParams.set('changed', changedField);
                url.searchParams.set('strict', '1');
            }
            return url;
        }

        function syncSelectWidget(select) {
            if (
                window.django
                && window.django.jQuery
                && select
                && select.classList.contains('admin-autocomplete')
            ) {
                window.django.jQuery(select).trigger('change.select2');
            }
        }

        function selectedOption(select) {
            if (!select || select.selectedIndex < 0) {
                return null;
            }
            return select.options[select.selectedIndex] || null;
        }

        function setStatus(message, isError) {
            var status = scope.querySelector('[data-assignment-options-status="1"]');
            if (!status) {
                status = document.createElement('span');
                status.dataset.assignmentOptionsStatus = '1';
                status.className = 'journal-admin-field-status';
                var wrapper = source.closest('.related-widget-wrapper') || source.parentElement;
                if (wrapper && wrapper.parentNode) {
                    wrapper.parentNode.insertBefore(status, wrapper.nextSibling);
                }
            }
            if (status) {
                status.textContent = message || '';
                status.classList.toggle('journal-admin-field-status--error', Boolean(isError && message));
            }
        }

        function optionMetadata(item, option) {
            if (item.group_id) {
                option.dataset.groupId = String(item.group_id);
            }
            if (item.academic_year_id) {
                option.dataset.academicYearId = String(item.academic_year_id);
            }
            if (typeof item.default_is_specialty === 'boolean') {
                option.dataset.defaultIsSpecialty = item.default_is_specialty ? '1' : '0';
            }
            if (typeof item.is_individual === 'boolean') {
                option.dataset.isIndividual = item.is_individual ? '1' : '0';
            }
        }

        function replaceOptions(name, items, preserveMissing) {
            var select = fields[name];
            if (!select || select.tagName !== 'SELECT') {
                return false;
            }

            var previousValue = select.value;
            var previousOption = selectedOption(select);
            var previousLabel = previousOption ? previousOption.textContent : previousValue;
            var previousDataset = previousOption ? {
                groupId: previousOption.dataset.groupId || '',
                academicYearId: previousOption.dataset.academicYearId || '',
                defaultIsSpecialty: previousOption.dataset.defaultIsSpecialty || '',
                isIndividual: previousOption.dataset.isIndividual || '',
            } : {};
            var fragment = document.createDocumentFragment();
            fragment.appendChild(new Option(
                items.length || previousValue ? placeholders[name] : 'Нет допустимых вариантов',
                ''
            ));

            var canKeepValue = false;
            items.forEach(function (item) {
                var itemValue = String(item.id);
                var option = new Option(item.label, itemValue, false, itemValue === previousValue);
                optionMetadata(item, option);
                fragment.appendChild(option);
                if (itemValue === previousValue) {
                    canKeepValue = true;
                }
            });

            if (previousValue && !canKeepValue && preserveMissing) {
                var preservedOption = new Option(previousLabel || previousValue, previousValue, false, true);
                Object.keys(previousDataset).forEach(function (datasetKey) {
                    if (previousDataset[datasetKey]) {
                        preservedOption.dataset[datasetKey] = previousDataset[datasetKey];
                    }
                });
                fragment.appendChild(preservedOption);
                canKeepValue = true;
            }

            select.replaceChildren(fragment);
            select.value = canKeepValue ? previousValue : '';
            var valueChanged = Boolean(previousValue && !canKeepValue);
            if (!select.value && items.length === 1) {
                select.value = String(items[0].id);
                valueChanged = true;
            }
            select.disabled = items.length === 0 && !select.value;
            syncSelectWidget(select);
            return valueChanged;
        }

        function selectedSubjectDefault() {
            var option = selectedOption(fields.subject);
            if (!option || !option.value) {
                return null;
            }
            if (option.dataset.defaultIsSpecialty === '1') {
                return true;
            }
            if (option.dataset.defaultIsSpecialty === '0') {
                return false;
            }
            return null;
        }

        function setFieldValue(field, value, force) {
            if (!field || value === null || typeof value === 'undefined') {
                return false;
            }
            var nextValue = String(value);
            if (!force && field.value) {
                return false;
            }
            if (field.value === nextValue) {
                return false;
            }
            field.value = nextValue;
            syncSelectWidget(field);
            return true;
        }

        function applyLocalDefaults(changedField) {
            var changed = false;
            if (changedField === 'student' && fields.student) {
                var studentOption = selectedOption(fields.student);
                if (studentOption && studentOption.dataset.groupId) {
                    changed = setFieldValue(fields.group, studentOption.dataset.groupId, true) || changed;
                }
                if (studentOption && studentOption.dataset.academicYearId) {
                    changed = setFieldValue(fields.academic_year, studentOption.dataset.academicYearId, true) || changed;
                }
            }
            if (changedField === 'group' && fields.group) {
                var groupOption = selectedOption(fields.group);
                if (groupOption && groupOption.dataset.academicYearId) {
                    changed = setFieldValue(fields.academic_year, groupOption.dataset.academicYearId, true) || changed;
                }
            }
            if (changedField === 'subject' && fields.is_specialty) {
                var defaultSpecialty = selectedSubjectDefault();
                if (defaultSpecialty !== null) {
                    fields.is_specialty.checked = defaultSpecialty;
                }
            }
            return changed;
        }

        function applyServerDefaults(defaults, changedField) {
            var changed = false;
            if (!defaults) {
                return false;
            }
            if (defaults.group_id && (changedField === 'student' || !fields.group || !fields.group.value)) {
                changed = setFieldValue(fields.group, defaults.group_id, changedField === 'student') || changed;
            }
            if (defaults.academic_year_id && (
                changedField === 'group'
                || changedField === 'student'
                || !fields.academic_year
                || !fields.academic_year.value
            )) {
                changed = setFieldValue(
                    fields.academic_year,
                    defaults.academic_year_id,
                    changedField === 'group' || changedField === 'student'
                ) || changed;
            }
            if (typeof defaults.is_specialty === 'boolean' && fields.is_specialty) {
                fields.is_specialty.checked = defaults.is_specialty;
            }
            if (defaults.sort_order && fields.sort_order && !fields.sort_order.value) {
                fields.sort_order.value = defaults.sort_order;
            }
            if (defaults.teacher_id && fields.teacher && !fields.teacher.value) {
                changed = setFieldValue(fields.teacher, defaults.teacher_id, false) || changed;
            }
            return changed;
        }

        function loadOptions(changedField) {
            requestSequence += 1;
            var sequence = requestSequence;
            if (activeRequest) {
                activeRequest.abort();
            }
            activeRequest = new AbortController();
            setStatus('Обновляем связанные поля...', false);

            fetch(buildUrl(changedField), {
                credentials: 'same-origin',
                headers: {'X-Requested-With': 'XMLHttpRequest'},
                signal: activeRequest.signal,
            })
                .then(function (response) {
                    if (!response.ok) {
                        throw new Error('Request failed: ' + response.status);
                    }
                    return response.json();
                })
                .then(function (payload) {
                    if (sequence !== requestSequence) {
                        return;
                    }

                    var shouldReload = false;
                    SELECT_FIELDS.forEach(function (name) {
                        var preserveMissing = !changedField || changedField === name;
                        if (replaceOptions(name, payload[name + 's'] || [], preserveMissing)) {
                            shouldReload = true;
                        }
                    });
                    shouldReload = applyServerDefaults(payload.defaults || {}, changedField) || shouldReload;
                    applyLocalDefaults(changedField);
                    setStatus('', false);
                    if (shouldReload) {
                        loadOptions(changedField);
                    }
                })
                .catch(function (error) {
                    if (error.name === 'AbortError') {
                        return;
                    }
                    setStatus(
                        'Не удалось обновить связанные поля. Проверьте выбранные значения и попробуйте еще раз.',
                        true
                    );
                });
        }

        applyLocalDefaults('');
        loadOptions();
    }

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', function () {
            start(document);
        });
    } else {
        start(document);
    }

    document.addEventListener('formset:added', function (event) {
        start(event.target);
    });
})();
