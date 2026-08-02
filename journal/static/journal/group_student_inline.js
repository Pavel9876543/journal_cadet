(function () {
    'use strict';

    function availableJQueries() {
        var instances = [];
        [window.jQuery, window.django && window.django.jQuery].forEach(function (jq) {
            if (jq && instances.indexOf(jq) === -1) {
                instances.push(jq);
            }
        });
        return instances;
    }

    function closestRow(element) {
        return element.closest('tr') || element.closest('.dynamic-students');
    }

    function cityInputFor(select) {
        var row = closestRow(select);
        if (!row) {
            return null;
        }
        return row.querySelector('[data-student-city-target="1"]');
    }

    function updateCityChurch(select) {
        var input = cityInputFor(select);
        if (!input) {
            return;
        }

        var selectedOption = select.options[select.selectedIndex];
        input.value = selectedOption ? (selectedOption.dataset.cityChurch || '') : '';
    }

    function initialize(scope) {
        var root = scope || document;
        root.querySelectorAll('[data-student-city-source="1"]').forEach(function (select) {
            if (select.dataset.studentCityInitialized === '1') {
                return;
            }
            select.dataset.studentCityInitialized = '1';
            select.addEventListener('change', function () {
                updateCityChurch(select);
            });
            availableJQueries().forEach(function (jq) {
                jq(select)
                    .off('.journalStudentCity')
                    .on(
                        'change.journalStudentCity '
                        + 'select2:select.journalStudentCity '
                        + 'select2:clear.journalStudentCity',
                        function () { updateCityChurch(select); }
                    );
            });
        });
    }

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', function () {
            initialize(document);
        });
    } else {
        initialize(document);
    }

    document.addEventListener('formset:added', function (event) {
        initialize(event.target);
    });
    availableJQueries().forEach(function (jq) {
        jq(document)
            .off('formset:added.journalStudentCity')
            .on('formset:added.journalStudentCity', function (_event, row) {
                initialize(row && row[0] ? row[0] : document);
            });
    });
})();
