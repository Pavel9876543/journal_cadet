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
            if (select.dataset.studentCityInitialized === '1') {
                return;
            }
            select.dataset.studentCityInitialized = '1';
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

(function () {
    'use strict';

    var originalWindowOpen = window.open;

    window.open = function (url, target, features) {
        var href = String(url || '');
        if (href.indexOf('/admin/journal/student/') !== -1 && window.innerWidth >= 992) {
            var width = Math.min(1280, Math.max(1050, window.screen.availWidth - 120));
            var height = Math.min(900, Math.max(720, window.screen.availHeight - 120));
            var featureMap = {};
            String(features || '').split(',').forEach(function (item) {
                var parts = item.split('=');
                if (parts[0]) {
                    featureMap[parts[0].trim()] = parts.length > 1 ? parts[1].trim() : 'yes';
                }
            });
            featureMap.width = width;
            featureMap.height = height;
            featureMap.resizable = 'yes';
            featureMap.scrollbars = 'yes';
            features = Object.keys(featureMap).map(function (key) {
                return key + '=' + featureMap[key];
            }).join(',');
        }
        return originalWindowOpen.call(window, url, target, features);
    };

    document.addEventListener('shown.bs.modal', function (event) {
        var modal = event.target;
        var iframe = modal && modal.querySelector('iframe');
        if (!iframe || iframe.src.indexOf('/admin/journal/student/') === -1) {
            return;
        }
        modal.classList.add('journal-student-related-modal');
    });
})();
