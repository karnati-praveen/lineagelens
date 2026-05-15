'use strict';

let _jsonMode = false;

function setJsonMode(val) { _jsonMode = !!val; }
function isJsonMode() { return _jsonMode; }

function out(data) {
  if (_jsonMode) {
    console.log(JSON.stringify(data, null, 2));
  } else if (typeof data === 'string') {
    console.log(data);
  } else {
    console.log(JSON.stringify(data, null, 2));
  }
}

function err(data) {
  if (_jsonMode) {
    if (typeof data === 'string') {
      console.error(JSON.stringify({ error: data }, null, 2));
    } else {
      const msg = data.message ?? JSON.stringify(data);
      console.error(JSON.stringify({ error: msg, ...data }, null, 2));
    }
  } else {
    if (typeof data === 'string') {
      console.error(data);
    } else {
      console.error(data.message ?? JSON.stringify(data));
    }
  }
}

module.exports = { setJsonMode, isJsonMode, out, err };
