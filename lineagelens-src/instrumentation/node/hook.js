const http = require('http');
const https = require('https');

const LLM_DOMAINS = new Set([
  'api.openai.com',
  'api.anthropic.com',
  'generativelanguage.googleapis.com'
]);
const PROXY_URL = (process.env.LINEAGELENS_LLM_PROXY_URL || 'http://127.0.0.1:8080').replace(/\/$/, '');
const ENABLED = !['0', 'false', 'no'].includes(String(process.env.LINEAGELENS_AUTO_INSTRUMENT || '1').toLowerCase());

function hostnameFromOptions(options) {
  const raw = options.hostname || options.host;
  if (!raw) {
    return '';
  }
  return String(raw).replace(/^\[/, '').replace(/\]$/, '').replace(/:\d+$/, '').toLowerCase();
}

function normalizeHttpsArgs(args) {
  let options = {};
  let callback;

  if (typeof args[0] === 'string' || args[0] instanceof URL) {
    const parsed = new URL(args[0]);
    options = {
      protocol: parsed.protocol,
      hostname: parsed.hostname,
      port: parsed.port,
      path: `${parsed.pathname}${parsed.search}`
    };
    if (args[1] && typeof args[1] === 'object') {
      options = { ...options, ...args[1] };
      callback = args[2];
    } else {
      callback = args[1];
    }
  } else {
    options = { ...(args[0] || {}) };
    callback = args[1];
  }

  return { options, callback };
}

function proxiedHttpOptions(options) {
  const proxy = new URL(PROXY_URL);
  const protocol = options.protocol || 'https:';
  const hostname = hostnameFromOptions(options);
  const targetPort = options.port ? `:${options.port}` : '';
  const targetAuthority = `${hostname}${targetPort}`;
  const targetPath = options.path || '/';

  return {
    ...options,
    protocol: 'http:',
    hostname: proxy.hostname,
    host: proxy.hostname,
    port: proxy.port || 80,
    path: `${protocol}//${targetAuthority}${targetPath}`,
    headers: {
      ...(options.headers || {}),
      Host: targetAuthority,
      'X-Forwarded-Proto': protocol.replace(':', '') || 'https'
    },
    servername: undefined
  };
}

function headersObject(headers) {
  const result = {};
  if (!headers) {
    return result;
  }
  new Headers(headers).forEach((value, key) => {
    result[key] = value;
  });
  return result;
}

function fetchViaProxy(target, init) {
  return new Promise((resolve, reject) => {
    const proxy = new URL(PROXY_URL);
    const body = init && init.body;
    const request = http.request(
      {
        protocol: 'http:',
        hostname: proxy.hostname,
        port: proxy.port || 80,
        method: (init && init.method) || 'GET',
        path: target.href,
        headers: {
          ...headersObject(init && init.headers),
          Host: target.host,
          'X-Forwarded-Proto': target.protocol.replace(':', '') || 'https'
        }
      },
      response => {
        const chunks = [];
        response.on('data', chunk => chunks.push(Buffer.isBuffer(chunk) ? chunk : Buffer.from(chunk)));
        response.on('end', () => {
          resolve(new Response(Buffer.concat(chunks), {
            status: response.statusCode || 200,
            statusText: response.statusMessage,
            headers: response.headers
          }));
        });
      }
    );
    request.on('error', reject);
    if (body) {
      request.write(body);
    }
    request.end();
  });
}

if (ENABLED) {
  const originalHttpsRequest = https.request;
  const originalHttpsGet = https.get;

  https.request = function lineagelensHttpsRequest(...args) {
    const { options, callback } = normalizeHttpsArgs(args);
    const hostname = hostnameFromOptions(options);
    if (!LLM_DOMAINS.has(hostname)) {
      return originalHttpsRequest.apply(this, args);
    }
    return http.request(proxiedHttpOptions(options), callback);
  };

  https.get = function lineagelensHttpsGet(...args) {
    const request = https.request.apply(this, args);
    request.end();
    return request;
  };

  if (typeof global.fetch === 'function') {
    const originalFetch = global.fetch;
    global.fetch = async function lineagelensFetch(input, init) {
      const rawUrl = typeof input === 'string' || input instanceof URL ? String(input) : input && input.url;
      if (!rawUrl) {
        return originalFetch.apply(this, arguments);
      }

      const target = new URL(rawUrl);
      if (!LLM_DOMAINS.has(target.hostname.toLowerCase())) {
        return originalFetch.apply(this, arguments);
      }

      try {
        return await fetchViaProxy(target, init || {});
      } catch (_error) {
        return originalFetch.apply(this, arguments);
      }
    };
  }
}
