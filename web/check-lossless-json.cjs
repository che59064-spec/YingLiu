// Offline JSON round trips: no browser, service, credentials or model tasks.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const source = fs.readFileSync(__dirname + '/lossless-json.js', 'utf8');
function codec(deterministic=false) {
  const context = {window:{}};
  vm.createContext(context);
  if(deterministic)vm.runInContext('Date.now=()=>0;Math.random=()=>0.5;',context);
  vm.runInContext(source,context);
  return context.window.CanvasJSON;
}
const json = codec(), prototypeBefore = Object.getOwnPropertyNames(BigInt.prototype);
const text = '{"seed":18446744073709551615,"negative":-9223372036854775808,"boundary":9007199254740991,"unsafe":9007199254740992,"small":42,"fraction":1.25,"exp":1e3,"string":"seed 18446744073709551615","escaped":"\\\"18446744073709551615\\\"\\n\\u4f60"}';
const parsed = json.parse(text);
assert.equal(parsed.seed,18446744073709551615n);
assert.equal(parsed.negative,-9223372036854775808n);
assert.equal(parsed.boundary,9007199254740991);
assert.equal(parsed.unsafe,9007199254740992n);
assert.equal(parsed.small,42);
assert.equal(parsed.fraction,1.25);
assert.equal(parsed.exp,1000);
assert.equal(parsed.string,'seed 18446744073709551615');
assert.equal(parsed.escaped,'"18446744073709551615"\n你');
const output = json.stringify(parsed,null,2);
assert.match(output,/"seed": 18446744073709551615/);
assert.ok(!output.includes('__yingliu_json_'));
assert.equal(json.parse(output).seed,parsed.seed);
assert.equal(json.parse(json.stringify(parsed)).negative,parsed.negative);
assert.equal(json.stringify(18446744073709551615n),'18446744073709551615');
assert.equal(json.parse('-18446744073709551615'),-18446744073709551615n);
assert.equal(json.stringify(undefined),undefined);
assert.equal(json.stringify([1n,undefined,NaN]),'[1,null,null]');

// Match projectContent's sorting replacer, including nested opaque seed fields.
const project = {z:[{workflow:{'9':{inputs:{seed:parsed.seed,text:'unchanged'}}}}],a:1};
const sort = (_,value)=>value&&typeof value==='object'&&!Array.isArray(value)
  ?Object.fromEntries(Object.entries(value).sort(([a],[b])=>a.localeCompare(b))):value;
const sorted = json.stringify(project,sort,2);
assert.ok(sorted.indexOf('"a"')<sorted.indexOf('"z"'));
assert.equal(json.parse(sorted).z[0].workflow['9'].inputs.seed,parsed.seed);
const transformed=json.stringify({a:1n,b:2n},function(key,value){
  if(key==='a'){assert.equal(this.a,1n);return value+1n;}
  if(key==='b')return undefined;
  return value;
});
assert.equal(transformed,'{"a":2}');
let reviverKeys=[];
const revived=json.parse('{"seed":18446744073709551615,"drop":1}',function(key,value){
  reviverKeys.push(key);
  return key==='drop'?undefined:key==='seed'?value+1n:value;
});
assert.equal(revived.seed,18446744073709551616n);
assert.equal('drop' in revived,false);
assert.deepEqual(reviverKeys,['seed','drop','']);

// Force the first nonce and embed both literal and escaped collisions.
const marker='__yingliu_json_0_1_i';
const collision={normal:{[marker]:'18446744073709551615'},seed:18446744073709551615n};
const collisionText=codec(true).stringify(collision);
assert.equal(JSON.parse(collisionText).normal[marker],'18446744073709551615');
assert.equal(json.parse(collisionText).seed,18446744073709551615n);
const literal=codec(true).parse('{"'+marker+'":"18446744073709551615","seed":18446744073709551615}');
assert.equal(literal[marker],'18446744073709551615');
assert.equal(literal.seed,18446744073709551615n);
const escaped=codec(true).parse('{"\\u005f_yingliu_json_0_1_i":"18446744073709551615","seed":18446744073709551615}');
assert.equal(escaped[marker],'18446744073709551615');
assert.equal(escaped.seed,18446744073709551615n);

const cyclic={};cyclic.self=cyclic;
assert.throws(()=>json.stringify(cyclic),/circular/i);
for(const invalid of ['{"seed":18446744073709551615,}','[018446744073709551615]','{"x":+18446744073709551615}','{"x":18446744073709551615e}'])
  assert.throws(()=>json.parse(invalid));
assert.deepEqual(Object.getOwnPropertyNames(BigInt.prototype),prototypeBefore);
console.log('Lossless JSON: 64-bit seeds, bilingual strings, marker collisions, clone and replacers passed.');
