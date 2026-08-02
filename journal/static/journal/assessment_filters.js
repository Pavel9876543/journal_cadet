(function () {
    'use strict';

    var FIELD_CONFIG = {
        academic_year: 'academic_years',
        assessment_teacher: 'teachers',
        assessment_subject: 'subjects',
        assessment_group: 'assessment_groups',
        assessment_item: 'items',
        assessment_student: 'students'
    };

    function availableJQueries() {
        var instances = [];
        [window.jQuery, window.django && window.django.jQuery].forEach(function (jq) {
            if (jq && instances.indexOf(jq) === -1) {
                instances.push(jq);
            }
        });
        return instances;
    }

    function start() {
        document.querySelectorAll('form[data-assessment-filter-url]').forEach(function (form) {
            if (form.dataset.assessmentFiltersInitialized === '1') {
                return;
            }
            form.dataset.assessmentFiltersInitialized = '1';
            initialize(form);
        });
    }

    function initialize(form) {
        var fields = {};
        var placeholders = {};
        Object.keys(FIELD_CONFIG).forEach(function (name) {
            var field = form.elements[name];
            fields[name] = field && field.tagName === 'SELECT' ? field : null;
            if (fields[name]) {
                var empty = Array.prototype.find.call(fields[name].options, function (option) {
                    return option.value === '';
                });
                placeholders[name] = empty ? empty.textContent : 'Выберите значение';
            }
        });

        var requestSequence = 0;
        var controller = null;
        var pendingChange = null;

        function queueLoad(name) {
            if (pendingChange) {
                window.clearTimeout(pendingChange);
            }
            pendingChange = window.setTimeout(function () {
                pendingChange = null;
                load(name);
            }, 0);
        }

        Object.keys(fields).forEach(function (name) {
            if (!fields[name]) {
                return;
            }
            fields[name].addEventListener('change', function () {
                queueLoad(name);
            });
            availableJQueries().forEach(function (jq) {
                jq(fields[name])
                    .off('.journalAssessmentFilters')
                    .on(
                        'change.journalAssessmentFilters '
                        + 'select2:select.journalAssessmentFilters '
                        + 'select2:clear.journalAssessmentFilters',
                        function () { queueLoad(name); }
                    );
            });
        });

        function buildUrl(changedField) {
            var url = new URL(form.dataset.assessmentFilterUrl, window.location.origin);
            Object.keys(FIELD_CONFIG).forEach(function (name) {
                var field = fields[name];
                if (field && field.value) {
                    url.searchParams.set(name, field.value);
                }
            });
            if (!fields.assessment_teacher && form.dataset.fixedTeacher) {
                url.searchParams.set('assessment_teacher', form.dataset.fixedTeacher);
            }
            if (!fields.academic_year && form.dataset.fixedAcademicYear) {
                url.searchParams.set('academic_year', form.dataset.fixedAcademicYear);
            }
            if (changedField) {
                url.searchParams.set('changed', changedField);
            }
            if (form.dataset.assessmentEditableOnly === '1') {
                url.searchParams.set('editable', '1');
            }
            return url;
        }

        function setStatus(message, isError) {
            var status = form.querySelector('[data-assessment-filter-status]');
            if (!status) {
                return;
            }
            status.textContent = message || '';
            status.classList.toggle('journal-admin-field-status--error', Boolean(isError));
        }

        function setMetadata(option, item) {
            if (item.subject_id) {
                option.dataset.subjectId = String(item.subject_id);
            }
            if (item.assessment_group_id) {
                option.dataset.assessmentGroupId = String(item.assessment_group_id);
            }
        }

        function replaceOptions(name, items, preserveMissing) {
            var select = fields[name];
            if (!select) {
                return false;
            }
            var previousValue = select.value;
            var previousOption = select.selectedIndex >= 0 ? select.options[select.selectedIndex] : null;
            var previousLabel = previousOption ? previousOption.textContent : previousValue;
            var fragment = document.createDocumentFragment();
            if (placeholders[name]) {
                fragment.appendChild(new Option(placeholders[name], ''));
            }

            var retained = false;
            items.forEach(function (item) {
                var value = String(item.id);
                var option = new Option(item.label, value, false, value === previousValue);
                setMetadata(option, item);
                fragment.appendChild(option);
                retained = retained || value === previousValue;
            });
            if (previousValue && !retained && preserveMissing) {
                fragment.appendChild(new Option(previousLabel || previousValue, previousValue, false, true));
                retained = true;
            }
            select.replaceChildren(fragment);
            select.value = retained ? previousValue : '';
            select.disabled = false;
            select.removeAttribute('disabled');
            select.setAttribute('aria-disabled', 'false');
            select.dispatchEvent(new CustomEvent('journal:options-updated', {bubbles: true}));
            return Boolean(previousValue && !retained);
        }

        function load(changedField) {
            requestSequence += 1;
            var currentSequence = requestSequence;
            if (controller) {
                controller.abort();
            }
            controller = new AbortController();
            setStatus('Обновляем доступные варианты...', false);

            fetch(buildUrl(changedField), {
                credentials: 'same-origin',
                headers: {'X-Requested-With': 'XMLHttpRequest'},
                signal: controller.signal
            })
                .then(function (response) {
                    if (!response.ok) {
                        throw new Error('HTTP ' + response.status);
                    }
                    return response.json();
                })
                .then(function (payload) {
                    if (currentSequence !== requestSequence) {
                        return;
                    }
                    var reload = false;
                    Object.keys(FIELD_CONFIG).forEach(function (name) {
                        reload = replaceOptions(
                            name,
                            payload[FIELD_CONFIG[name]] || [],
                            !changedField || changedField === name
                        ) || reload;
                    });
                    if (payload.existing_change) {
                        form.dataset.changeConfirmationFields = JSON.stringify(
                            payload.existing_change
                        );
                    } else {
                        delete form.dataset.changeConfirmationFields;
                    }
                    setStatus('', false);
                    if (reload) {
                        load(changedField);
                    }
                })
                .catch(function (error) {
                    if (error.name !== 'AbortError') {
                        setStatus('Не удалось обновить фильтры. Повторите попытку.', true);
                    }
                });
        }

        load('');
    }

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', start);
    } else {
        start();
    }
}());
