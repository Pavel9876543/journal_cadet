(function () {
    'use strict';

    var FIELD_NAMES = ['group', 'student', 'subject', 'teacher'];

    function start() {
        var sources = document.querySelectorAll(
            'form[data-grade-options-url], [data-grade-options-url]:not(form)'
        );
        var initializedForms = new Set();

        sources.forEach(function (source) {
            var form = source.matches('form') ? source : source.closest('form');
            if (!form || initializedForms.has(form)) {
                return;
            }
            initializedForms.add(form);
            initializeForm(form, source.dataset.gradeOptionsUrl || form.dataset.gradeOptionsUrl);
        });
    }

    function initializeForm(form, endpoint) {
        if (!endpoint) {
            return;
        }

        var fields = {};
        FIELD_NAMES.concat(['academic_year']).forEach(function (name) {
            fields[name] = form.querySelector('[name="' + name + '"]');
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
                }
            }
            loadOptions();
        }

        Object.keys(fields).forEach(function (name) {
            if (!fields[name]) {
                return;
            }
            fields[name].addEventListener('change', function () {
                handleChange(name);
            });
        });

        function buildUrl() {
            var url = new URL(endpoint, window.location.origin);
            Object.keys(fields).forEach(function (name) {
                if (fields[name] && fields[name].value) {
                    url.searchParams.set(name, fields[name].value);
                }
            });
            if (!fields.teacher && fixedTeacher) {
                url.searchParams.set('teacher', fixedTeacher);
            }
            if (!fields.subject && fixedSubject) {
                url.searchParams.set('subject', fixedSubject);
            }
            if (!fields.academic_year && fixedAcademicYear) {
                url.searchParams.set('academic_year', fixedAcademicYear);
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

        function replaceOptions(name, items) {
            var select = fields[name];
            if (!select) {
                return false;
            }

            var previousValue = select.value;
            var fragment = document.createDocumentFragment();
            var emptyOption = new Option(
                items.length ? placeholders[name] : 'Нет допустимых вариантов',
                ''
            );
            fragment.appendChild(emptyOption);

            items.forEach(function (item) {
                var option = new Option(item.label, String(item.id));
                if (item.group_id) {
                    option.dataset.groupId = String(item.group_id);
                }
                fragment.appendChild(option);
            });

            select.replaceChildren(fragment);
            var canKeepValue = items.some(function (item) {
                return String(item.id) === previousValue;
            });
            select.value = canKeepValue ? previousValue : '';
            select.disabled = items.length === 0;
            return Boolean(previousValue && !canKeepValue);
        }

        function loadOptions() {
            requestSequence += 1;
            var sequence = requestSequence;
            if (activeRequest) {
                activeRequest.abort();
            }
            activeRequest = new AbortController();
            setLoading(true);

            fetch(buildUrl(), {
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
                        if (replaceOptions(name, payload[name + 's'] || [])) {
                            selectionWasCleared = true;
                        }
                    });
                    setLoading(false);
                    if (selectionWasCleared) {
                        loadOptions();
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
})();
