// scorm.js — SCORM 1.2 adapter, auto-generated, do not edit by hand
(function () {
  'use strict';

  // --- Find the SCORM 1.2 API by traversing parent frames ---
  function findAPI(win) {
    var depth = 0;
    while (depth < 7) {
      if (win.API) return win.API;
      if (!win.parent || win.parent === win) break;
      win = win.parent;
      depth++;
    }
    return null;
  }

  var API = findAPI(window);
  if (!API) return; // Not in a SCORM LMS — exit silently

  // --- Initialize the session ---
  API.LMSInitialize('');

  // --- Identify the current page ---
  var metaTitleId = document.querySelector('meta[name="title-id"]');
  var pageId = metaTitleId ? metaTitleId.getAttribute('content') : '';

  // All activity IDs in this ADT (embedded at generation time)
  var ALL_ACTIVITY_IDS = [];
  var hasActivities = ALL_ACTIVITY_IDS.length > 0;

  // --- Record where the learner is ---
  API.LMSSetValue('cmi.core.lesson_location', pageId);

  // --- Set lesson status ---
  if (hasActivities) {
    applyStatus();
    watchForCompletions();
  } else {
    // Content-only ADT — mark as passed on first visit
    var existingStatus = API.LMSGetValue('cmi.core.lesson_status') || '';
    if (existingStatus !== 'passed') {
      API.LMSSetValue('cmi.core.lesson_status', 'passed');
      API.LMSSetValue('cmi.core.score.raw', '100');
      API.LMSSetValue('cmi.core.score.min', '0');
      API.LMSSetValue('cmi.core.score.max', '100');
    }
  }

  API.LMSCommit('');

  // --- Session close ---
  window.addEventListener('beforeunload', function () {
    if (hasActivities) applyStatus();
    API.LMSCommit('');
    API.LMSFinish('');
  });

  // -------------------------------------------------------
  // Helpers
  // -------------------------------------------------------

  function getCompletedIds() {
    var completed = [];
    try {
      completed = JSON.parse(localStorage.getItem('completedActivities') || '[]');
    } catch (e) { /* ignore */ }

    var ids = {};
    for (var i = 0; i < completed.length; i++) {
      if (typeof completed[i] === 'string') {
        var dashIdx = completed[i].indexOf('-');
        var actId = dashIdx > -1 ? completed[i].substring(0, dashIdx) : completed[i];
        ids[actId] = true;
      }
    }
    return ids;
  }

  function applyStatus() {
    var completedIds = getCompletedIds();
    var completedCount = 0;
    for (var i = 0; i < ALL_ACTIVITY_IDS.length; i++) {
      if (completedIds[ALL_ACTIVITY_IDS[i]]) completedCount++;
    }

    var score = Math.round((completedCount / ALL_ACTIVITY_IDS.length) * 100);
    API.LMSSetValue('cmi.core.score.raw', String(score));
    API.LMSSetValue('cmi.core.score.min', '0');
    API.LMSSetValue('cmi.core.score.max', '100');

    if (completedCount === ALL_ACTIVITY_IDS.length) {
      API.LMSSetValue('cmi.core.lesson_status', 'passed');
    } else {
      var existingStatus = API.LMSGetValue('cmi.core.lesson_status') || '';
      if (existingStatus !== 'passed') {
        API.LMSSetValue('cmi.core.lesson_status', 'incomplete');
      }
    }
  }

  function watchForCompletions() {
    var _origSetItem = localStorage.setItem.bind(localStorage);
    localStorage.setItem = function (key, value) {
      _origSetItem(key, value);
      if (key === 'completedActivities') {
        applyStatus();
        API.LMSCommit('');
      }
    };

    window.addEventListener('storage', function (e) {
      if (e.key === 'completedActivities') {
        applyStatus();
        API.LMSCommit('');
      }
    });
  }
})();
