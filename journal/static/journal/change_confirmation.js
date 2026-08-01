(function () {
    'use strict';

    var snapshots = new WeakMap();
    var bypassOnce = new WeakSet();
    var activeConfirmation = null;

    function inlinePrefix(name) {
        var match = String(name || '').match(/^(.*-\d+)-[^-]+$/);
        return match ? match[1] : '';
    }

    function isExistingControl(form, control) {
        var prefix = inlinePrefix(control.name);
        if (!prefix) {
            return form.dataset.confirmExistingChanges === '1';
        }
        var identity = form.elements.namedItem(prefix + '-id');
        return Boolean(identity && String(identity.value || '').trim());
    }

    function isRelevantControl(form, control) {
        if (!control || !control.name || control.form !== form) {
            return false;
        }
        if (control.closest && control.closest('.empty-form')) {
            return false;
        }
        if (
            control.type === 'hidden'
            || control.type === 'submit'
            || control.type === 'button'
            || control.type === 'reset'
            || control.type === 'file'
            || control.readOnly
            || control.disabled
        ) {
            return false;
        }
        if (
            control.name === 'csrfmiddlewaretoken'
            || control.name === '_confirm_existing_changes'
            || /-(TOTAL_FORMS|INITIAL_FORMS|MIN_NUM_FORMS|MAX_NUM_FORMS)$/.test(control.name)
        ) {
            return false;
        }
        return isExistingControl(form, control);
    }

    function optionLabel(option) {
        return option ? String(option.textContent || '').trim() : '';
    }

    function readControl(form, name) {
        var group = form.elements.namedItem(name);
        if (!group) {
            return null;
        }
        var controls = typeof group.length === 'number' && !group.tagName
            ? Array.prototype.slice.call(group)
            : [group];
        var first = controls[0];
        if (!first) {
            return null;
        }
        if (first.type === 'radio') {
            var selected = controls.find(function (control) { return control.checked; });
            var radioLabel = selected ? labelForControl(form, selected) : '';
            return {
                raw: selected ? String(selected.value) : '',
                label: radioLabel || (selected ? String(selected.value) : '')
            };
        }
        if (first.type === 'checkbox') {
            return {
                raw: first.checked ? '1' : '0',
                label: first.checked ? 'Да' : 'Нет'
            };
        }
        if (first.tagName === 'SELECT' && first.multiple) {
            var selectedOptions = Array.prototype.filter.call(
                first.options,
                function (option) { return option.selected && option.value; }
            );
            return {
                raw: selectedOptions.map(function (option) { return String(option.value); }).join('\u001f'),
                label: selectedOptions.map(optionLabel).join(', ')
            };
        }
        if (first.tagName === 'SELECT') {
            var option = first.selectedIndex >= 0 ? first.options[first.selectedIndex] : null;
            return {
                raw: String(first.value || ''),
                label: optionLabel(option)
            };
        }
        return {
            raw: String(first.value || ''),
            label: String(first.value || '').trim()
        };
    }

    function cleanLabel(value) {
        return String(value || '')
            .replace(/\s+/g, ' ')
            .replace(/[:*]\s*$/, '')
            .trim();
    }

    function labelForControl(form, control) {
        var explicit = cleanLabel(control.getAttribute('aria-label'));
        if (explicit) {
            return explicit;
        }
        if (control.id) {
            var label = form.querySelector('label[for="' + cssEscape(control.id) + '"]');
            if (label) {
                var labelled = cleanLabel(label.textContent);
                if (labelled) {
                    return labelled;
                }
            }
        }
        var field = control.closest && control.closest('.field, .form-row, td, .form-group');
        if (field) {
            var localLabel = field.querySelector('label, .field-label');
            if (localLabel) {
                var localText = cleanLabel(localLabel.textContent);
                if (localText) {
                    return localText;
                }
            }
            if (field.tagName === 'TD') {
                var row = field.closest('tr');
                var table = field.closest('table');
                if (row && table) {
                    var cells = Array.prototype.slice.call(row.children);
                    var index = cells.indexOf(field);
                    var headers = table.querySelectorAll('thead tr:last-child th');
                    if (index >= 0 && headers[index]) {
                        var headerText = cleanLabel(headers[index].textContent);
                        if (headerText) {
                            return headerText;
                        }
                    }
                }
            }
        }
        var suffix = String(control.name || '').split('-').pop().split('__')[0];
        return cleanLabel(suffix.replace(/_/g, ' ')) || 'Значение';
    }

    function cssEscape(value) {
        if (window.CSS && typeof window.CSS.escape === 'function') {
            return window.CSS.escape(value);
        }
        return String(value).replace(/([ #;?%&,.+*~\':"!^$[\]()=>|/@])/g, '\\$1');
    }

    function snapshotForm(form) {
        if (snapshots.has(form)) {
            return;
        }
        var values = new Map();
        Array.prototype.forEach.call(form.elements, function (control) {
            if (!isRelevantControl(form, control) || values.has(control.name)) {
                return;
            }
            var current = readControl(form, control.name);
            if (!current) {
                return;
            }
            values.set(control.name, {
                raw: current.raw,
                label: current.label,
                fieldLabel: labelForControl(form, control)
            });
        });
        snapshots.set(form, values);
    }

    function displayValue(value) {
        var text = String(value || '').trim();
        return text || 'Пусто';
    }

    function collectSnapshotChanges(form) {
        snapshotForm(form);
        var changes = [];
        var values = snapshots.get(form) || new Map();
        values.forEach(function (original, name) {
            // Filling a previously empty value is an addition and deliberately
            // does not require confirmation.
            if (original.raw === '') {
                return;
            }
            var current = readControl(form, name);
            if (!current || current.raw === original.raw) {
                return;
            }
            changes.push({
                key: name,
                label: original.fieldLabel,
                oldValue: displayValue(original.label),
                newValue: displayValue(current.label)
            });
        });
        return changes;
    }

    function parsePreview(node) {
        try {
            return JSON.parse(node.dataset.changeConfirmationFields || '{}');
        } catch (_error) {
            return {};
        }
    }

    function collectPreviewChanges(form) {
        var changes = [];
        var nodes = [form].concat(Array.prototype.slice.call(
            form.querySelectorAll('[data-change-confirmation-fields]')
        ));
        nodes.forEach(function (node) {
            var preview = parsePreview(node);
            var fields = preview.fields || {};
            Object.keys(fields).forEach(function (key) {
                var specification = fields[key] || {};
                var oldRaw = String(specification.old_raw || '');
                if (!oldRaw && !specification.confirm_when_empty) {
                    return;
                }
                var current = null;
                if (specification.field_name) {
                    current = readControl(form, specification.field_name);
                }
                if (!current) {
                    current = {
                        raw: String(specification.current_raw || ''),
                        label: String(specification.current_label || '')
                    };
                }
                if (current.raw === oldRaw) {
                    return;
                }
                changes.push({
                    key: specification.field_name || key,
                    label: specification.label || key,
                    oldValue: displayValue(specification.old_label),
                    newValue: displayValue(current.label)
                });
            });
        });
        return changes;
    }

    function collectChanges(form) {
        var combined = collectSnapshotChanges(form).concat(collectPreviewChanges(form));
        var unique = new Map();
        combined.forEach(function (change) {
            unique.set(change.key + '\u001f' + change.label, change);
        });
        return Array.from(unique.values());
    }

    function ensureConfirmationField(form) {
        var field = form.elements.namedItem('_confirm_existing_changes');
        if (!field) {
            field = document.createElement('input');
            field.type = 'hidden';
            field.name = '_confirm_existing_changes';
            form.appendChild(field);
        }
        field.value = '1';
    }

    function continueSubmission(form, submitter) {
        ensureConfirmationField(form);
        bypassOnce.add(form);
        if (
            typeof form.requestSubmit === 'function'
            && (!submitter || submitter.form === form)
        ) {
            form.requestSubmit(submitter || undefined);
            return;
        }
        if (submitter && submitter.name) {
            var submittedAction = document.createElement('input');
            submittedAction.type = 'hidden';
            submittedAction.name = submitter.name;
            submittedAction.value = submitter.value;
            form.appendChild(submittedAction);
        }
        HTMLFormElement.prototype.submit.call(form);
    }

    function fallbackConfirmation(form, submitter, changes) {
        var lines = changes.map(function (change) {
            return change.label + ': «' + change.oldValue + '» → «' + change.newValue + '»';
        });
        if (window.confirm('Будут изменены существующие данные:\n\n' + lines.join('\n') + '\n\nСохранить изменения?')) {
            continueSubmission(form, submitter);
        }
    }

    function createDialog() {
        var dialog = document.createElement('dialog');
        dialog.className = 'journal-change-confirmation';
        dialog.setAttribute('aria-labelledby', 'journal-change-confirmation-title');
        dialog.innerHTML = ''
            + '<div class="journal-change-confirmation__panel">'
            + '<div class="journal-change-confirmation__head">'
            + '<h2 id="journal-change-confirmation-title">Подтвердите изменение данных</h2>'
            + '<button type="button" class="journal-change-confirmation__close" data-confirm-cancel aria-label="Отменить и закрыть">×</button>'
            + '</div>'
            + '<p>Будут изменены уже существующие значения:</p>'
            + '<div class="journal-change-confirmation__list" data-confirm-change-list></div>'
            + '<p class="journal-change-confirmation__hint">Проверьте изменения. После подтверждения они будут сохранены.</p>'
            + '<div class="journal-change-confirmation__actions">'
            + '<button type="button" class="journal-change-confirmation__cancel" data-confirm-cancel>Отмена</button>'
            + '<button type="button" class="journal-change-confirmation__submit" data-confirm-submit>Сохранить изменения</button>'
            + '</div>'
            + '</div>';
        document.body.appendChild(dialog);
        dialog.querySelectorAll('[data-confirm-cancel]').forEach(function (button) {
            button.addEventListener('click', function () { dialog.close('cancel'); });
        });
        dialog.querySelector('[data-confirm-submit]').addEventListener('click', function () {
            dialog.close('confirm');
        });
        dialog.addEventListener('click', function (event) {
            if (event.target === dialog) {
                dialog.close('cancel');
            }
        });
        dialog.addEventListener('close', function () {
            var confirmation = activeConfirmation;
            activeConfirmation = null;
            if (!confirmation) {
                return;
            }
            if (dialog.returnValue === 'confirm') {
                continueSubmission(confirmation.form, confirmation.submitter);
            } else if (confirmation.submitter) {
                confirmation.submitter.focus();
            }
        });
        return dialog;
    }

    function showDialog(form, submitter, changes) {
        if (!window.HTMLDialogElement || typeof HTMLDialogElement.prototype.showModal !== 'function') {
            fallbackConfirmation(form, submitter, changes);
            return;
        }
        var dialog = document.querySelector('dialog.journal-change-confirmation') || createDialog();
        var list = dialog.querySelector('[data-confirm-change-list]');
        list.replaceChildren();
        changes.forEach(function (change) {
            var row = document.createElement('div');
            row.className = 'journal-change-confirmation__change';
            var label = document.createElement('strong');
            label.textContent = change.label;
            var values = document.createElement('div');
            values.className = 'journal-change-confirmation__values';
            var oldValue = document.createElement('span');
            oldValue.className = 'journal-change-confirmation__old';
            oldValue.textContent = change.oldValue;
            var arrow = document.createElement('span');
            arrow.className = 'journal-change-confirmation__arrow';
            arrow.setAttribute('aria-hidden', 'true');
            arrow.textContent = '→';
            var newValue = document.createElement('span');
            newValue.className = 'journal-change-confirmation__new';
            newValue.textContent = change.newValue;
            values.append(oldValue, arrow, newValue);
            row.append(label, values);
            list.appendChild(row);
        });
        activeConfirmation = {form: form, submitter: submitter};
        dialog.returnValue = '';
        dialog.showModal();
        dialog.querySelector('[data-confirm-submit]').focus();
    }

    function handleSubmit(event) {
        var form = event.target;
        if (!(form instanceof HTMLFormElement)) {
            return;
        }
        if (bypassOnce.has(form)) {
            bypassOnce.delete(form);
            return;
        }
        var confirmed = form.elements.namedItem('_confirm_existing_changes');
        if (confirmed && confirmed.value === '1') {
            return;
        }
        var changes = collectChanges(form);
        if (!changes.length) {
            return;
        }
        event.preventDefault();
        showDialog(form, event.submitter || null, changes);
    }

    function initialize() {
        document.querySelectorAll('form[data-confirm-existing-changes="1"]').forEach(snapshotForm);
        document.addEventListener('submit', handleSubmit, true);
    }

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', initialize);
    } else {
        initialize();
    }
}());
