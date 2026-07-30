(function () {
    'use strict';

    var OPTION_FIELDS = ['group', 'student', 'subject', 'teacher'];
    var ALL_FIELDS = ['academic_year'].concat(OPTION_FIELDS);

    function inlinePrefix(name) {
        var match = String(name || '').match(/^(.*-\d+)-[^-]+$/);
        return match ? match[1] : '';
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

    function start(root) {
        (root || document).querySelectorAll(
            'form[data-grade-options-url], [data-grade-options-url]:not(form)'
        ).forEach(function (source) {
            var form = source.matches('form') ? source : source.closest('form');
            if (!form) {
                return;
            }
            var prefix = source.matches('form') ? '' : inlinePrefix(source.name);
            var scope = prefix ? (source.closest('tr') || source.closest('[class*="dynamic-"]') || form) : form;
            if (scope.dataset.gradeOptionsInitialized === '1') {
                return;
            }
            scope.dataset.gradeOptionsInitialized = '1';
            initialize(scope, form, source, prefix);
        });
    }

    function initialize(scope, form, source, prefix) {
        var endpoint = source.dataset.gradeOptionsUrl || form.dataset.gradeOptionsUrl;
        var mode = source.dataset.gradeDependencyMode || form.dataset.gradeDependencyMode || 'legacy';
        if (!endpoint) {
            return;
        }

        function selector(name) {
            return prefix ? '[name="' + prefix + '-' + name + '"]' : '[name="' + name + '"]';
        }

        var fields = {};
        var placeholders = {};
        ALL_FIELDS.forEach(function (name) {
            fields[name] = scope.querySelector(selector(name));
            if (fields[name] && fields[name].tagName === 'SELECT') {
                var empty = Array.prototype.find.call(fields[name].options, function (option) {
                    return option.value === '';
                });
                placeholders[name] = empty ? empty.textContent : 'Выберите значение';
            }
        });

        var fixedStudent = source.dataset.fixedStudent || form.dataset.fixedStudent || '';
        var fixedTeacher = source.dataset.fixedTeacher || form.dataset.fixedTeacher || '';
        var fixedSubject = source.dataset.fixedSubject || form.dataset.fixedSubject || '';
        var fixedYear = source.dataset.fixedAcademicYear || form.dataset.fixedAcademicYear || '';
        var yearFilterSelector = form.dataset.gradeYearFilter || '';
        var yearFilter = yearFilterSelector ? document.querySelector(yearFilterSelector) : null;
        var requestSequence = 0;
        var activeRequest = null;

        function setStatus(message, isError) {
            var status = scope.querySelector('[data-grade-options-status]')
                || form.querySelector('[data-grade-options-status]');
            if (!status && source.parentNode) {
                status = document.createElement('span');
                status.dataset.gradeOptionsStatus = '1';
                status.className = 'journal-admin-field-status';
                var wrapper = source.closest('.related-widget-wrapper') || source.parentElement;
                if (wrapper && wrapper.parentNode) {
                    wrapper.parentNode.insertBefore(status, wrapper.nextSibling);
                }
            }
            if (status) {
                status.textContent = message || '';
                status.classList.toggle('journal-admin-field-status--error', Boolean(isError));
            }
        }

        function setBusy(isBusy) {
            form.setAttribute('aria-busy', isBusy ? 'true' : 'false');
            form.querySelectorAll('button[type="submit"], input[type="submit"]').forEach(function (button) {
                if (isBusy) {
                    if (typeof button.dataset.gradeWasDisabled === 'undefined') {
                        button.dataset.gradeWasDisabled = button.disabled ? '1' : '0';
                    }
                    button.disabled = true;
                } else {
                    if (button.dataset.gradeWasDisabled === '0') {
                        button.disabled = false;
                    }
                    delete button.dataset.gradeWasDisabled;
                }
            });
        }

        function buildUrl(changedField) {
            var url = new URL(endpoint, window.location.origin);
            ALL_FIELDS.forEach(function (name) {
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
            if (!fields.academic_year && fixedYear) {
                url.searchParams.set('academic_year', fixedYear);
            }
            url.searchParams.set('mode', mode);
            if (changedField) {
                url.searchParams.set('changed', changedField);
                url.searchParams.set('strict', '1');
            }
            return url;
        }

        function replaceOptions(name, items, preserveMissing) {
            var select = fields[name];
            if (!select || select.tagName !== 'SELECT') {
                return false;
            }
            var oldValue = select.value;
            var oldOption = select.selectedIndex >= 0 ? select.options[select.selectedIndex] : null;
            var oldLabel = oldOption ? oldOption.textContent : oldValue;
            var fragment = document.createDocumentFragment();
            fragment.appendChild(new Option(
                items.length ? (placeholders[name] || 'Выберите значение') : 'Нет допустимых вариантов',
                ''
            ));
            var retained = false;
            items.forEach(function (item) {
                var value = String(item.id);
                var option = new Option(item.label, value, false, value === oldValue);
                if (item.group_id) {
                    option.dataset.groupId = String(item.group_id);
                }
                if (item.academic_year_id) {
                    option.dataset.academicYearId = String(item.academic_year_id);
                }
                fragment.appendChild(option);
                retained = retained || value === oldValue;
            });
            if (oldValue && !retained && preserveMissing) {
                fragment.appendChild(new Option(oldLabel || oldValue, oldValue, false, true));
                retained = true;
            }
            select.replaceChildren(fragment);
            select.value = retained ? oldValue : '';
            if (!select.value && items.length === 1 && name === 'teacher') {
                select.value = String(items[0].id);
            }
            select.disabled = items.length === 0 && !select.value;
            syncSelectWidget(select);
            select.dispatchEvent(new CustomEvent('journal:options-updated', {bubbles: true}));
            return Boolean(oldValue && !retained);
        }

        function clearField(name) {
            var select = fields[name];
            if (!select || select.tagName !== 'SELECT') {
                return;
            }
            select.value = '';
            syncSelectWidget(select);
        }

        function clearDescendants(changedField) {
            if (mode !== 'grade') {
                return;
            }
            if (changedField === 'academic_year') {
                ['group', 'student', 'subject', 'teacher'].forEach(clearField);
            } else if (changedField === 'group') {
                ['student', 'subject'].forEach(clearField);
            } else if (changedField === 'teacher') {
                ['student', 'subject'].forEach(clearField);
            }
        }

        function updateFormActionYear(yearId) {
            if (!yearId || !form.dataset.gradeYearFilter) {
                return;
            }
            var action = new URL(form.getAttribute('action') || window.location.href, window.location.href);
            action.searchParams.set('academic_year', yearId);
            form.action = action.pathname + action.search + action.hash;
        }

        function loadOptions(changedField) {
            requestSequence += 1;
            var sequence = requestSequence;
            if (activeRequest) {
                activeRequest.abort();
            }
            activeRequest = new AbortController();
            setBusy(true);
            setStatus('Обновляем доступные варианты…', false);

            fetch(buildUrl(changedField), {
                credentials: 'same-origin',
                headers: {'X-Requested-With': 'XMLHttpRequest'},
                signal: activeRequest.signal
            })
                .then(function (response) {
                    if (!response.ok) {
                        throw new Error('HTTP ' + response.status);
                    }
                    return response.json();
                })
                .then(function (payload) {
                    if (sequence !== requestSequence) {
                        return;
                    }
                    OPTION_FIELDS.forEach(function (name) {
                        var preserveMissing = mode !== 'grade' && (!changedField || changedField === name);
                        replaceOptions(name, payload[name + 's'] || [], preserveMissing);
                    });
                    setBusy(false);
                    setStatus('', false);
                })
                .catch(function (error) {
                    if (error.name === 'AbortError') {
                        return;
                    }
                    setBusy(false);
                    setStatus('Не удалось обновить связанные поля. Повторите попытку.', true);
                });
        }

        ALL_FIELDS.forEach(function (name) {
            if (!fields[name]) {
                return;
            }
            fields[name].addEventListener('change', function () {
                if (name === 'academic_year' && fields[name].dataset.gradeYearAutoSubmit === '1') {
                    if (typeof form.requestSubmit === 'function') {
                        form.requestSubmit();
                    } else {
                        form.submit();
                    }
                    return;
                }
                clearDescendants(name);
                loadOptions(name);
            });
        });

        if (yearFilter && fields.academic_year) {
            yearFilter.addEventListener('change', function () {
                fields.academic_year.value = yearFilter.value;
                fixedYear = yearFilter.value;
                syncSelectWidget(fields.academic_year);
                updateFormActionYear(yearFilter.value);
                clearDescendants('academic_year');
                if (yearFilter.dataset.gradeYearAutoSubmit === '1') {
                    return;
                }
                loadOptions('academic_year');
            });
            if (yearFilter.value && fields.academic_year.value !== yearFilter.value) {
                fields.academic_year.value = yearFilter.value;
            }
            updateFormActionYear(yearFilter.value);
        }

        loadOptions('');
    }

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', function () { start(document); });
    } else {
        start(document);
    }
    document.addEventListener('formset:added', function (event) { start(event.target || document); });
    if (window.django && window.django.jQuery) {
        window.django.jQuery(document).on('formset:added', function (_event, row) {
            start(row && row[0] ? row[0] : document);
        });
    }
}());
