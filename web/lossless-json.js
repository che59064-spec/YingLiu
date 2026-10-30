// Preserve integer seeds while keeping files and HTTP bodies ordinary JSON.
(function (root) {
  'use strict';
  const nativeParse = JSON.parse, nativeStringify = JSON.stringify;
  const tokens = /"(?:\\[\s\S]|[^"\\])*"|-?(?:0|[1-9]\d*)(?:\.\d+)?(?:[eE][+-]?\d+)?/g;
  const maxInteger = 9007199254740991n;
  let sequence = 0;

  function newMarker() {
    return '__yingliu_json_' + Date.now().toString(36) + '_' + (++sequence).toString(36)
      + '_' + Math.random().toString(36).slice(2);
  }

  function parse(text, reviver) {
    const source = String(text);
    let marker, transformed, collision;
    do {
      marker = newMarker();
      collision = source.includes(marker);
      if (collision) continue;
      transformed = source.replace(tokens, token => {
        if (token[0] === '"') {
          // Escaped property names must not impersonate our temporary objects.
          if (token.includes('\\') && nativeParse(token) === marker) collision = true;
          return token;
        }
        if (!/[.eE]/.test(token)) {
          const integer = BigInt(token);
          if (integer > maxInteger || integer < -maxInteger)
            return '{"' + marker + '":"' + token + '"}';
        }
        return token;
      });
    } while (collision);
    let result = nativeParse(transformed, function (key, value) {
      return value && typeof value === 'object' && Object.keys(value).length === 1
        && Object.hasOwn(value, marker) ? BigInt(value[marker]) : value;
    });
    if (typeof reviver === 'function') {
      const visit = (holder, key) => {
        const value = holder[key];
        if (value && typeof value === 'object') {
          for (const child of Object.keys(value)) {
            const replacement = visit(value, child);
            if (replacement === undefined) delete value[child];
            else Object.defineProperty(value, child, {value:replacement, writable:true, enumerable:true, configurable:true});
          }
        }
        return reviver.call(holder, key, value);
      };
      result = visit({'':result}, '');
    }
    return result;
  }

  function hasKey(value, key) {
    const pending = [value], seen = new WeakSet();
    while (pending.length) {
      const item = pending.pop();
      if (!item || typeof item !== 'object' || seen.has(item)) continue;
      seen.add(item);
      const descriptors = Object.getOwnPropertyDescriptors(item);
      if (Object.hasOwn(descriptors, key)) return true;
      for (const descriptor of Object.values(descriptors))
        if (descriptor.enumerable && Object.hasOwn(descriptor, 'value')) pending.push(descriptor.value);
    }
    return false;
  }

  function stringify(value, replacer, space) {
    if (Array.isArray(replacer)) throw new TypeError('CanvasJSON.stringify 仅支持函数形式的字段筛选。');
    let marker;
    do { marker = newMarker(); } while (hasKey(value, marker));
    const placeholders = new WeakSet();
    let expected = 0;
    const serialized = nativeStringify(value, function (key, item) {
      if (placeholders.has(this)) return item;
      if (key === marker) throw new TypeError('JSON 字段与临时整数标记冲突，请重试。');
      if (typeof replacer === 'function') item = replacer.call(this, key, item);
      if (typeof item === 'bigint') {
        const placeholder = {[marker]:item.toString()};
        placeholders.add(placeholder);
        expected++;
        return placeholder;
      }
      return item;
    }, space);
    if (serialized === undefined || !expected) return serialized;
    const expression = new RegExp('\\{\\s*"' + marker + '"\\s*:\\s*"(-?(?:0|[1-9]\\d*))"\\s*\\}', 'g');
    let found = 0;
    const result = serialized.replace(expression, (match, integer) => {found++; return integer;});
    if (found !== expected) throw new TypeError('整数未能完整保存，请重试。');
    return result;
  }

  root.CanvasJSON = Object.freeze({parse, stringify});
})(typeof window === 'undefined' ? globalThis : window);
