(function () {
    'use strict';

    var CONFIG = {
        item: {
            fields: ['group', 'element', 'responsible_teacher'],
            payload: {group: 'groups', element: 'elements', responsible_teacher: 'teachers'}
        },
        student_group: {
            fields: ['student', 'assessment_group'],
            payload: {student: 'students', assessment_group: 'groups'}
        },
        rule: {
            fields: ['subject', 'academic_year', 'assessment_group'],
            payload: {subject: 'subjects', academic_year: 'academic_years', assessment_group: 'groups'}
        },
        result: {
            fields: ['student', 'item', 'assessed_by'],
            payload: {student: 'students', item: 'items', assessed_by: 'teachers'}
        }
    };

    function inlinePrefix(name) {
        var match = String(name || '').match(/^(.*-\d+)-[^-]+$/);
        return match ? match[1] : '';
    }

    function start(root) {
        (root || document).querySelectorAll('[data-assessment-options-url]').forEach(function (source) {
            var type = source.dataset.assessmentType || '';
            var config = CONFIG[type];
            var form = source.closest('form');
            if (!config || !form) {
                return;
            }
            var prefix = inlinePrefix(source.name);
            var scope = prefix ? (source.closest('tr') || source.closest('[class*="dynamic-"]') || form) : form;
            var marker = 'assessmentDeps' + type.replace(/[^a-z0-9]/gi, '');
            if (scope.dataset[marker] === '1') {
                return;
            }
            scope.dataset[marker] = '1';
            initialize(scope, form, prefix, source.dataset.assessmentOptionsUrl, type, config, source);
        });
    }

    function initialize(scope, form, prefix, endpoint, type, config, source) {
        function selector(name) {
            return prefix ? '[name="' + prefix + '-' + name + '"]' : '[name="' + name + '"]';
        }

        var fields = {};
        var placeholders = {};
        config.fields.forEach(function (name) {
            fields[name] = scope.querySelector(selector(name));
            if (fields[name] && fields[name].tagName === 'SELECT') {
                var empty = Array.prototype.find.call(fields[name].options, function (option) {
                    return option.value === '';
                });
                placeholders[name] = empty ? empty.textContent : 'Выберите значение';
            }
        });

        var sequence = 0;
        var controller = null;

        config.fields.forEach(function (name) {
            if (!fields[name]) {
                return;
            }
            fields[name].addEventListener('change', function () {
                load(name, true);
            });
        });

        function selectedOption(select) {
            return select && select.selectedIndex >= 0 ? select.options[select.selectedIndex] : null;
        }

        function sync(select) {
            if (window.django && window.django.jQuery && select && select.classList.contains('admin-autocomplete')) {
                window.django.jQuery(select).trigger('change.select2');
            }
        }

        function status(message, isError) {
            var node = scope.querySelector('[data-assessment-options-status="1"]');
            if (!node) {
                node = document.createElement('span');
                node.dataset.assessmentOptionsStatus = '1';
                node.className = 'journal-admin-field-status';
                var wrapper = source.closest('.related-widget-wrapper') || source.parentElement;
                if (wrapper && wrapper.parentNode) {
                    wrapper.parentNode.insertBefore(node, wrapper.nextSibling);
                }
            }
            node.textContent = message || '';
            node.classList.toggle('journal-admin-field-status--error', Boolean(isError && message));
        }

        function buildUrl(changed) {
            var url = new URL(endpoint, window.location.origin);
            url.searchParams.set('type', type);
            config.fields.forEach(function (name) {
                var field = fields[name];
                if (field && field.value) {
                    url.searchParams.set(name, field.value);
                }
            });
            if (source.dataset.parentStudentId && !url.searchParams.has('student')) {
                url.searchParams.set('student', source.dataset.parentStudentId);
            }
            if (source.dataset.parentSubjectId && !url.searchParams.has('subject')) {
                url.searchParams.set('subject', source.dataset.parentSubjectId);
            }
            if (source.dataset.parentAssessmentGroupId && !url.searchParams.has('assessment_group')) {
                url.searchParams.set('assessment_group', source.dataset.parentAssessmentGroupId);
            }
            if (source.dataset.parentAssessmentItemId && !url.searchParams.has('item')) {
                url.searchParams.set('item', source.dataset.parentAssessmentItemId);
            }
            if (source.dataset.parentAcademicYearId && !url.searchParams.has('academic_year')) {
                url.searchParams.set('academic_year', source.dataset.parentAcademicYearId);
            }
            if (source.dataset.currentAssessmentItemId) {
                url.searchParams.set('current_item', source.dataset.currentAssessmentItemId);
            }
            if (changed) {
                url.searchParams.set('changed', changed);
                url.searchParams.set('strict', '1');
            }
            return url;
        }

        function setMetadata(option, item) {
            ['subject_id', 'academic_year_id'].forEach(function (name) {
                if (item[name]) {
                    option.dataset[name.replace('_id', 'Id')] = String(item[name]);
                }
            });
        }

        function replaceOptions(fieldName, items, preserveCurrent) {
            var select = fields[fieldName];
            if (!select || select.tagName !== 'SELECT') {
                return false;
            }
            var oldValue = select.value;
            var oldOption = selectedOption(select);
            var oldLabel = oldOption ? oldOption.textContent : oldValue;
            var fragment = document.createDocumentFragment();
            fragment.appendChild(new Option(items.length ? placeholders[fieldName] : 'Нет допустимых вариантов', ''));
            var retained = false;
            items.forEach(function (item) {
                var value = String(item.id);
                var option = new Option(item.label, value, false, value === oldValue);
                setMetadata(option, item);
                fragment.appendChild(option);
                retained = retained || value === oldValue;
            });
            if (oldValue && !retained && preserveCurrent) {
                fragment.appendChild(new Option(oldLabel || oldValue, oldValue, false, true));
                retained = true;
            }
            select.replaceChildren(fragment);
            select.value = retained ? oldValue : '';
            var changed = Boolean(oldValue && !retained);
            if (!select.value && items.length === 1) {
                select.value = String(items[0].id);
                changed = true;
            }
            select.disabled = false;
            select.removeAttribute('disabled');
            select.setAttribute('aria-disabled', 'false');
            sync(select);
            select.dispatchEvent(new CustomEvent('journal:options-updated', {bubbles: true}));
            return changed;
        }

        function setValue(name, value, force) {
            var field = fields[name];
            if (!field || value === null || typeof value === 'undefined') {
                return false;
            }
            if (!force && field.value) {
                return false;
            }
            var next = String(value);
            if (field.value === next) {
                return false;
            }
            field.value = next;
            sync(field);
            return true;
        }

        function applyLocalDefaults(changed) {
            if (changed !== 'group' && changed !== 'assessment_group') {
                return false;
            }
            var field = fields[changed];
            var option = selectedOption(field);
            if (!option || !option.value) {
                return false;
            }
            var changedAny = false;
            if (option.dataset.subjectId) {
                changedAny = setValue('subject', option.dataset.subjectId, true) || changedAny;
            }
            if (option.dataset.academicYearId) {
                changedAny = setValue('academic_year', option.dataset.academicYearId, true) || changedAny;
            }
            return changedAny;
        }

        function applyDefaults(defaults, changed) {
            var changedAny = false;
            changedAny = setValue('academic_year', defaults.academic_year_id, false) || changedAny;
            changedAny = setValue('student', defaults.student_id, false) || changedAny;
            changedAny = setValue('subject', defaults.subject_id, changed === 'group' || changed === 'assessment_group') || changedAny;
            changedAny = setValue('responsible_teacher', defaults.responsible_teacher_id, false) || changedAny;
            changedAny = setValue('assessed_by', defaults.assessed_by_id, false) || changedAny;
            return changedAny;
        }

        function load(changed, strict) {
            sequence += 1;
            var currentSequence = sequence;
            if (controller) {
                controller.abort();
            }
            controller = new AbortController();
            status('Обновляем связанные поля…', false);

            fetch(buildUrl(strict ? changed : ''), {
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
                    if (currentSequence !== sequence) {
                        return;
                    }
                    var reload = false;
                    config.fields.forEach(function (name) {
                        var payloadName = config.payload[name];
                        var preserve = !strict || changed === name;
                        if (type === 'item' && name === 'element' && !source.dataset.currentAssessmentItemId) {
                            preserve = false;
                        }
                        reload = replaceOptions(name, payload[payloadName] || [], preserve) || reload;
                    });
                    reload = applyDefaults(payload.defaults || {}, changed) || reload;
                    reload = applyLocalDefaults(changed) || reload;
                    status('', false);
                    if (reload) {
                        load(changed, true);
                    }
                })
                .catch(function (error) {
                    if (error.name !== 'AbortError') {
                        status('Не удалось обновить связанные поля. Сервер проверит значения при сохранении.', true);
                    }
                });
        }

        load('', false);
    }

    function refreshItemInlineUniqueness(form) {
        if (!form) {
            return;
        }
        var rows = [];
        form.querySelectorAll('select[data-assessment-type="item"][name$="-element"]').forEach(function (elementField) {
            var prefix = inlinePrefix(elementField.name);
            if (!prefix) {
                return;
            }
            var groupField = form.querySelector('[name="' + prefix + '-group"]');
            if (!groupField) {
                return;
            }
            rows.push({groupField: groupField, element: elementField});
        });

        var claimed = new Set();
        rows.forEach(function (row) {
            var groupValue = String(row.groupField.value || '');
            var selected = String(row.element.value || '');
            var key = groupValue && selected ? groupValue + ':' + selected : '';
            if (key && claimed.has(key)) {
                row.element.value = '';
                syncSelect2(row.element);
                setInlineConflict(
                    row.element,
                    'Это произведение уже выбрано выше для той же группы.'
                );
            } else {
                if (key) {
                    claimed.add(key);
                }
                setInlineConflict(row.element, '');
            }
        });

        rows.forEach(function (row) {
            var ownGroup = String(row.groupField.value || '');
            var ownValue = String(row.element.value || '');
            var occupied = new Set();
            rows.forEach(function (other) {
                var otherGroup = String(other.groupField.value || '');
                if (other === row || !ownGroup || otherGroup !== ownGroup) {
                    return;
                }
                var value = String(other.element.value || '');
                if (value) {
                    occupied.add(value);
                }
            });
            Array.prototype.forEach.call(row.element.options, function (option) {
                option.disabled = Boolean(
                    option.value
                    && option.value !== ownValue
                    && occupied.has(String(option.value))
                );
            });
            syncSelect2(row.element);

            var occupiedGroups = new Set();
            if (ownValue) {
                rows.forEach(function (other) {
                    if (other === row || String(other.element.value || '') !== ownValue) {
                        return;
                    }
                    var otherGroup = String(other.groupField.value || '');
                    if (otherGroup) {
                        occupiedGroups.add(otherGroup);
                    }
                });
            }
            Array.prototype.forEach.call(row.groupField.options, function (option) {
                option.disabled = Boolean(
                    option.value
                    && option.value !== ownGroup
                    && occupiedGroups.has(String(option.value))
                );
            });
            syncSelect2(row.groupField);
        });
    }

    function setInlineConflict(select, message) {
        var wrapper = select.closest('.related-widget-wrapper') || select.parentElement;
        if (!wrapper || !wrapper.parentNode) {
            return;
        }
        var node = wrapper.parentNode.querySelector('[data-inline-assessment-conflict="1"]');
        if (!node && message) {
            node = document.createElement('div');
            node.dataset.inlineAssessmentConflict = '1';
            node.className = 'errornote journal-admin-field-status journal-admin-field-status--error';
            wrapper.parentNode.insertBefore(node, wrapper.nextSibling);
        }
        if (node) {
            node.textContent = message || '';
            node.hidden = !message;
        }
    }

    function syncSelect2(select) {
        if (window.django && window.django.jQuery && select) {
            window.django.jQuery(select).trigger('change.select2');
        }
    }

    document.addEventListener('change', function (event) {
        var target = event.target;
        if (!target || target.dataset.assessmentType !== 'item') {
            return;
        }
        refreshItemInlineUniqueness(target.closest('form'));
    });

    document.addEventListener('journal:options-updated', function (event) {
        refreshItemInlineUniqueness(event.target && event.target.closest('form'));
    });

    document.addEventListener('DOMContentLoaded', function () {
        start(document);
        refreshItemInlineUniqueness(document.querySelector('form'));
    });
    document.addEventListener('formset:added', function (event) {
        start(event.target || document);
        refreshItemInlineUniqueness((event.target && event.target.closest('form')) || document.querySelector('form'));
    });
    if (window.django && window.django.jQuery) {
        window.django.jQuery(document).on('formset:added', function (_event, row) {
            var root = row && row[0] ? row[0] : document;
            start(root);
            refreshItemInlineUniqueness((root.closest && root.closest('form')) || document.querySelector('form'));
        });
    }
}());
