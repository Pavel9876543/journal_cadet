(function () {
    'use strict';

    var FIELD_NAMES = ['group', 'student', 'subject', 'teacher'];

    function inlinePrefix(name) {
        var match = String(name || '').match(/^(.*-\d+)-[^-]+$/);
        return match ? match[1] : '';
    }

    function start(root) {
        var container = root || document;
        var sources = container.querySelectorAll(
            'form[data-grade-options-url], [data-grade-options-url]:not(form)'
        );
        var initializedKeys = new Set();

        sources.forEach(function (source) {
            var form = source.matches('form') ? source : source.closest('form');
            if (!form) {
                return;
            }

            var prefix = source.matches('form') ? '' : inlinePrefix(source.getAttribute('name'));
            var scope = prefix ? (source.closest('tr') || source.closest('.dynamic-subject_results') || form) : form;
            var key = prefix || 'form';
            if (initializedKeys.has(key)) {
                return;
            }
            initializedKeys.add(key);
            initializeForm(scope, source.dataset.gradeOptionsUrl || form.dataset.gradeOptionsUrl, prefix, form, source);
        });
    }

    function initializeForm(scope, endpoint, prefix, form, source) {
        if (!endpoint) {
            return;
        }

        form = form || scope.closest('form') || scope;
        source = source || scope;

        function fieldSelector(name) {
            return prefix ? '[name="' + prefix + '-' + name + '"]' : '[name="' + name + '"]';
        }

        var fields = {};
        FIELD_NAMES.concat(['academic_year']).forEach(function (name) {
            fields[name] = scope.querySelector(fieldSelector(name));
        });

        var placeholders = {};
        FIELD_NAMES.forEach(function (name) {
            var select = fields[name];
            if (!select) {
                return;
            }
            var emptyOption = Array.prototype.find.call(select.options, function (option) {
                return option.value === '';
            });
            placeholders[name] = emptyOption ? emptyOption.textContent : 'Выберите значение';
        });

        var fixedStudent = scope.dataset.fixedStudent || form.dataset.fixedStudent || source.dataset.fixedStudent || '';
        var fixedTeacher = form.dataset.fixedTeacher || '';
        var fixedSubject = form.dataset.fixedSubject || '';
        var fixedAcademicYear = form.dataset.fixedAcademicYear || '';
        var activeRequest = null;
        var requestSequence = 0;

        function handleChange(name) {
            if (name === 'student') {
                var option = fields.student.options[fields.student.selectedIndex];
                if (option && option.dataset.groupId && fields.group && !fields.group.value) {
                    fields.group.value = option.dataset.groupId;
                    syncSelectWidget(fields.group);
                }
            }
            loadOptions(name);
        }

        Object.keys(fields).forEach(function (name) {
            if (!fields[name]) {
                return;
            }
            fields[name].addEventListener('change', function () {
                handleChange(name);
            });
        });

        function buildUrl(changedField) {
            var url = new URL(endpoint, window.location.origin);
            Object.keys(fields).forEach(function (name) {
                if (fields[name] && fields[name].value) {
                    url.searchParams.set(name, fields[name].value);
                }
            });
            if (!fields.teacher && fixedTeacher) {
                url.searchParams.set('teacher', fixedTeacher);
            }
            if (!fields.student && fixedStudent) {
                url.searchParams.set('student', fixedStudent);
            }
            if (!fields.subject && fixedSubject) {
                url.searchParams.set('subject', fixedSubject);
            }
            if (!fields.academic_year && fixedAcademicYear) {
                url.searchParams.set('academic_year', fixedAcademicYear);
            }
            if (changedField) {
                url.searchParams.set('changed', changedField);
                url.searchParams.set('strict', '1');
            }
            return url;
        }

        function setLoading(isLoading) {
            form.setAttribute('aria-busy', isLoading ? 'true' : 'false');
            var status = form.querySelector('[data-grade-options-status]');
            if (status) {
                status.textContent = isLoading ? 'Обновляем доступные варианты...' : '';
            }
        }

        function selectedOption(select) {
            if (!select || select.selectedIndex < 0) {
                return null;
            }
            return select.options[select.selectedIndex] || null;
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

        function replaceOptions(name, items, preserveMissing) {
            var select = fields[name];
            if (!select) {
                return false;
            }

            var previousValue = select.value;
            var previousOption = selectedOption(select);
            var previousLabel = previousOption ? previousOption.textContent : previousValue;
            var previousGroupId = previousOption && previousOption.dataset.groupId
                ? previousOption.dataset.groupId
                : '';
            var fragment = document.createDocumentFragment();
            var emptyOption = new Option(
                items.length || previousValue ? placeholders[name] : 'Нет допустимых вариантов',
                ''
            );
            fragment.appendChild(emptyOption);

            var canKeepValue = false;
            items.forEach(function (item) {
                var itemValue = String(item.id);
                var option = new Option(
                    item.label,
                    itemValue,
                    false,
                    itemValue === previousValue
                );
                if (item.group_id) {
                    option.dataset.groupId = String(item.group_id);
                }
                fragment.appendChild(option);
                if (itemValue === previousValue) {
                    canKeepValue = true;
                }
            });

            if (previousValue && !canKeepValue && preserveMissing) {
                var preservedOption = new Option(previousLabel || previousValue, previousValue, false, true);
                if (previousGroupId) {
                    preservedOption.dataset.groupId = previousGroupId;
                }
                fragment.appendChild(preservedOption);
                canKeepValue = true;
            }

            select.replaceChildren(fragment);
            select.value = canKeepValue ? previousValue : '';
            select.disabled = items.length === 0 && !previousValue;
            syncSelectWidget(select);
            return Boolean(previousValue && !canKeepValue);
        }

        function loadOptions(changedField) {
            requestSequence += 1;
            var sequence = requestSequence;
            if (activeRequest) {
                activeRequest.abort();
            }
            activeRequest = new AbortController();
            setLoading(true);

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
                    var selectionWasCleared = false;
                    FIELD_NAMES.forEach(function (name) {
                        var preserveMissing = !changedField || changedField === name;
                        if (replaceOptions(name, payload[name + 's'] || [], preserveMissing)) {
                            selectionWasCleared = true;
                        }
                    });
                    setLoading(false);
                    if (selectionWasCleared) {
                        loadOptions(changedField);
                    }
                })
                .catch(function (error) {
                    if (error.name === 'AbortError') {
                        return;
                    }
                    setLoading(false);
                    var status = form.querySelector('[data-grade-options-status]');
                    if (status) {
                        status.textContent = 'Не удалось обновить доступные варианты.';
                    }
                });
        }

        loadOptions();
    }

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', start);
    } else {
        start();
    }

    document.addEventListener('formset:added', function (event) {
        start(event.target);
    });
})();
