(function () {
    'use strict';

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
            select.addEventListener('change', function () {
                updateCityChurch(select);
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
})();
