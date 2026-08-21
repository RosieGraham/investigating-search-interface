/*
Bounded same-page matching retry. CommonJS for Node tests; attaches to self
in the extension content script.
*/
(function (root, factory) {
  var api = factory();
  if (typeof module === 'object' && module.exports) {
    module.exports = api;
  }
  if (typeof root !== 'undefined') {
    root.ISIRequestLifecycle = api;
  }
}(typeof self !== 'undefined' ? self : this, function () {
  var FIRST_ATTEMPT_TIMEOUT_MS = 12000;
  var RETRY_DELAY_MS = 350;
  var MAX_ATTEMPTS = 2;
  var RETRY_STATUSES = { 502: true, 503: true, 504: true };

  function isTimeoutError(err) {
    return !!(err && (err.name === 'AbortError' || err.message === 'timeout'));
  }

  function shouldRetryStatus(status) {
    return !!RETRY_STATUSES[status];
  }

  function createRequestMachine(opts) {
    opts = opts || {};
    var fetchImpl = opts.fetchImpl;
    var delay = opts.delay || function (ms) {
      return new Promise(function (resolve) { setTimeout(resolve, ms); });
    };
    var now = opts.now || function () { return Date.now(); };
    var timeoutMs = opts.timeoutMs || FIRST_ATTEMPT_TIMEOUT_MS;
    var retryDelayMs = opts.retryDelayMs || RETRY_DELAY_MS;
    var generation = 0;
    var acknowledged = false;
    var abortWaiters = [];

    function acknowledge(version) {
      if (version === 'season-2026-v2') acknowledged = true;
    }

    function withdrawAcknowledgement() {
      acknowledged = false;
      abort();
    }

    function abort() {
      generation += 1;
      abortWaiters.splice(0).forEach(function (wake) { wake(); });
    }

    function raceAbort(promise, gen) {
      return new Promise(function (resolve, reject) {
        var settled = false;
        function wake() {
          if (settled) return;
          settled = true;
          resolve({ aborted: true });
        }
        abortWaiters.push(wake);
        if (gen !== generation) {
          wake();
          return;
        }
        promise.then(function (value) {
          if (settled) return;
          settled = true;
          resolve({ aborted: false, value: value });
        }, function (err) {
          if (settled) return;
          settled = true;
          reject(err);
        });
      });
    }

    async function requestMatch(req) {
      void now();
      if (!acknowledged) return null;
      var gen = generation + 1;
      generation = gen;
      var lastError = null;
      for (var attempt = 0; attempt < MAX_ATTEMPTS; attempt += 1) {
        if (gen !== generation || !acknowledged) return null;
        if (attempt > 0) {
          await delay(retryDelayMs);
          if (gen !== generation || !acknowledged) return null;
        }
        try {
          var raced = await raceAbort(Promise.resolve(fetchImpl(req, {
            attempt: attempt,
            timeoutMs: timeoutMs,
            signal: null,
          })), gen);
          if (raced.aborted || gen !== generation) return null;
          var resp = raced.value;
          if (!resp) return null;
          if (shouldRetryStatus(resp.status) && attempt === 0) {
            lastError = resp.status;
            continue;
          }
          if (!resp.ok) return { ok: false, status: resp.status, data: null };
          var data = typeof resp.json === 'function' ? await resp.json() : resp.data;
          if (gen !== generation) return null;
          return { ok: true, status: resp.status || 200, data: data };
        } catch (err) {
          if (gen !== generation) return null;
          lastError = err;
          if (isTimeoutError(err) && attempt === 0) continue;
          return null;
        }
      }
      return lastError && lastError.ok === false ? lastError : null;
    }

    return {
      acknowledge: acknowledge,
      withdrawAcknowledgement: withdrawAcknowledgement,
      abort: abort,
      requestMatch: requestMatch,
      FIRST_ATTEMPT_TIMEOUT_MS: FIRST_ATTEMPT_TIMEOUT_MS,
      RETRY_DELAY_MS: RETRY_DELAY_MS,
    };
  }

  return {
    createRequestMachine: createRequestMachine,
    FIRST_ATTEMPT_TIMEOUT_MS: FIRST_ATTEMPT_TIMEOUT_MS,
    RETRY_DELAY_MS: RETRY_DELAY_MS,
  };
}));
