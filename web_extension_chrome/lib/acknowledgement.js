/*
Workshop notice acknowledgement. CommonJS for Node tests; attaches to self
in the extension (content script and popup).
*/
(function (root, factory) {
  var api = factory();
  if (typeof module === 'object' && module.exports) {
    module.exports = api;
  }
  if (typeof root !== 'undefined') {
    root.ISIAcknowledgement = api;
  }
}(typeof self !== 'undefined' ? self : this, function () {
  var ACK_PREFIX = 'isi_notice_';
  var DECLINE_PREFIX = 'isi_notice_declined_';

  function createAcknowledgement(opts) {
    opts = opts || {};
    var noticeVersion = opts.noticeVersion || 'season-2026-v1';
    var ackKey = ACK_PREFIX + noticeVersion;
    var declineKey = DECLINE_PREFIX + noticeVersion;
    var send = opts.send || function () {};
    var storage = opts.storage || null;
    var allowPreAck = opts.allowPreAck === true;
    var acknowledged = false;
    var declined = false;

    function readStored(key, value) {
      if (value && typeof value === 'object' && !Array.isArray(value) && key in value) {
        return value[key];
      }
      return value;
    }

    function boot(seed) {
      seed = seed || {};
      acknowledged = !!seed[ackKey];
      declined = !!seed[declineKey];
      if (storage && typeof storage.get === 'function') {
        return Promise.resolve(storage.get(declineKey)).then(function (value) {
          if (readStored(declineKey, value)) declined = true;
          return storage.get(ackKey);
        }).then(function (value) {
          if (readStored(ackKey, value)) acknowledged = true;
        });
      }
    }

    function persist(obj) {
      if (storage && typeof storage.set === 'function') {
        return storage.set(obj);
      }
    }

    function acknowledge() {
      acknowledged = true;
      declined = false;
      persist({ isi_enabled: true, [ackKey]: true, [declineKey]: false });
    }

    function decline() {
      declined = true;
      acknowledged = false;
      persist({ isi_enabled: false, [ackKey]: false, [declineKey]: true });
    }

    function canSend() {
      if (allowPreAck) return true;
      return acknowledged && !declined;
    }

    function isAcknowledged() {
      return acknowledged && !declined;
    }

    function isDeclined() {
      return declined;
    }

    function sendIfAllowed(msg) {
      if (!canSend()) return false;
      send(msg);
      return true;
    }

    return {
      boot: boot,
      acknowledge: acknowledge,
      decline: decline,
      canSend: canSend,
      isAcknowledged: isAcknowledged,
      isDeclined: isDeclined,
      sendIfAllowed: sendIfAllowed,
      ackKey: ackKey,
      declineKey: declineKey,
    };
  }

  return { createAcknowledgement: createAcknowledgement };
}));
