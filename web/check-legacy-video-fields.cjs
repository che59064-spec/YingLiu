// Saved workflow fields refresh without uploading media or mutating the graph.
'use strict';
const assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm');
const source=fs.readFileSync(__dirname+'/app.js','utf8').split('boot().catch')[0];
const element={classList:{add(){},remove(){},toggle(){}},dataset:{},style:{},setAttribute(){}};
const document={activeElement:null,querySelector:()=>element,querySelectorAll:()=>[]};
const context={window:{},document,console,URL,Blob,AbortController,location:{origin:'http://127.0.0.1:8766'},setTimeout:()=>0,clearTimeout(){},requestAnimationFrame(){},localStorage:{setItem(){}}};
vm.createContext(context);
vm.runInContext(source+`\nwindow.check={state,refreshLegacyVideoFields};projectChanged=()=>{state.dirty=true;};renderSingleCard=()=>{};renderInspector=()=>{};`,context);
const c=context.window.check;
const graph={nodes:[{id:202,type:'H3ReferenceVideoLoader',widgets_values:['motion.mp4',24]}],links:[]};
const field={id:'nodes/0:video',nodeType:'H3ReferenceVideoLoader',type:'video',key:'video',editable:true,value:'motion.mp4'};
const card={id:'h3',type:'workflow',workflow:graph,fields:[{id:'nodes/1:image',type:'image'}],values:{'nodes/1:image':'kept.png'}};
const project={id:'a',cards:[card],edges:[{id:'edge',from:'ref',to:'h3'}]};c.state.project=project;c.state.selected='h3';
const snapshot=JSON.stringify({graph,values:card.values,edges:project.edges});let calls=0,release;
context.mockApi=(path,options)=>{calls++;assert.equal(path,'/api/workflow-fields');const body=JSON.parse(options.body);assert.deepEqual(body.values,{});assert.deepEqual(body.workflow,graph);return new Promise(resolve=>release=resolve);};
vm.runInContext('api=mockApi;',context);
async function run(){
  const first=c.refreshLegacyVideoFields(card),second=c.refreshLegacyVideoFields(card);assert.equal(calls,1,'concurrent selections share one read-only request');
  release({fields:[field]});await Promise.all([first,second]);assert.equal(card.fields.length,2);assert.equal(card.fields[1].id,field.id);
  assert.equal(JSON.stringify({graph,values:card.values,edges:project.edges}),snapshot,'original inputs, graph, and connections remain unchanged');assert.equal(c.state.dirty,true);
  await c.refreshLegacyVideoFields(card);assert.equal(calls,1,'an existing video field prevents duplicate reads');
  card.fields.pop();c.state.dirty=false;const pending=c.refreshLegacyVideoFields(card);c.state.project={id:'b',cards:[],edges:[]};release({fields:[field]});await pending;assert.equal(card.fields.length,1,'late response cannot modify the prior canvas');
  c.state.project=project;const graphPending=c.refreshLegacyVideoFields(card);graph.nodes[0].widgets_values[0]='new.mp4';release({fields:[field]});await graphPending;assert.equal(card.fields.length,1,'late response cannot modify a changed workflow');
  console.log('Legacy video field refresh passed: read-only mapping, existing values preserved, concurrent and late replies isolated.');
}
run().catch(error=>{console.error(error);process.exitCode=1;});
