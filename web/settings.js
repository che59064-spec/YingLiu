/* Connection settings stay on this computer and outside canvas exports. */
(() => {
  'use strict';
  const dialog = document.querySelector('#settings-dialog');
  if (!dialog) return;
  const el = id => document.getElementById(id);
  const defaults = {
    openai: {baseUrl:'https://api.openai.com/v1',model:'',vision:false},
    anthropic: {baseUrl:'https://api.anthropic.com',model:'',vision:false},
    ollama: {baseUrl:'http://127.0.0.1:11434',model:'',vision:false},
    agnes: {baseUrl:'https://apihub.agnes-ai.com',model:'agnes-2.5-flash',vision:true}
  };
  let loaded = null, busy = false;
  const status = (message, error=false) => {el('settings-status').textContent=message;el('settings-status').classList.toggle('error',error);};
  const keyHint = () => {
    const same=loaded&&el('ai-provider').value===loaded.provider&&el('ai-base').value.trim().replace(/\/$/,'')===loaded.baseUrl.replace(/\/$/,'');
    el('ai-key').placeholder=same&&loaded.hasKey?'已保存，留空保留':'填写此服务的密钥';
    el('ai-key-state').textContent=same&&loaded.hasKey?'本机已保存密钥，界面不会回显。':'Ollama 或无鉴权的本机服务可留空。';
    el('ai-clear-key').disabled=busy||!same||!loaded?.hasKey;
  };
  function fillAI(config) {
    loaded=config;el('ai-provider').value=config.provider;el('ai-base').value=config.baseUrl;
    el('ai-model').value=config.model;el('ai-format').value=config.responseFormat;
    el('ai-vision').checked=config.vision;el('ai-key').value='';keyHint();
  }
  function lock(value) {
    busy=value;
    dialog.querySelectorAll('input,select,button[data-save],#ai-test').forEach(control=>control.disabled=value);
    keyHint();
  }
  const aiBody = () => ({provider:el('ai-provider').value,baseUrl:el('ai-base').value.trim(),model:el('ai-model').value.trim(),
    apiKey:el('ai-key').value.trim(),responseFormat:el('ai-format').value,vision:el('ai-vision').checked});
  async function saveAI(test=false) {
    if(busy)return;
    if(!el('ai-base').reportValidity()||!el('ai-model').reportValidity())return;
    lock(true);status(test?'保存并测试连接…':'正在保存 AI 接口…');
    try {
      fillAI(await api('/api/ai-provider',{method:'PUT',body:JSON.stringify(aiBody())}));
      if(test){const result=await api('/api/ai-provider/test',{method:'POST',body:'{}',timeoutMs:90000});status(result.message||'连接成功，可以优化提示词。');}
      else status('AI 接口已保存。下次优化使用这组设置，保存不会发送请求。');
    }catch(error){status(error.message||'保存失败，请检查连接设置。',true);}
    finally{lock(false);}
  }
  async function openSettings(tab='ai') {
    if(!dialog.open)dialog.showModal();
    dialog.querySelectorAll('[data-settings-tab]').forEach(button=>{const active=button.dataset.settingsTab===tab;button.setAttribute('aria-selected',String(active));});
    el('ai-settings-panel').hidden=tab!=='ai';el('engine-settings-panel').hidden=tab!=='engine';
    if(busy)return;
    lock(true);status('正在读取本机设置…');
    try {
      const [config,engine]=await Promise.all([api('/api/ai-provider'),api('/api/settings')]);
      fillAI(config);el('setup-engine-url').value=engine.comfy_url==='auto'?'http://127.0.0.1:8188':engine.comfy_url;
      el('setup-engine-root').value=engine.comfy_root||'';el('setup-engine-python').value=engine.comfy_python||'';
      el('setup-engine-port').value=engine.comfy_port||8188;el('setup-engine-auto').checked=engine.auto_start!==false;
      status(config.configured?'设置保存在本机。':'先填写 AI 接口，或切换到生成引擎连接 ComfyUI。');
    }catch(error){status(`设置读取失败：${error.message}`,true);}
    finally{lock(false);}
  }
  el('app-settings').onclick=()=>openSettings();
  el('prompt-settings').onclick=()=>openSettings();
  el('engine-settings-link').onclick=()=>{el('connection-dialog').close();openSettings('engine');};
  el('settings-close').onclick=()=>{el('ai-key').value='';dialog.close();};
  dialog.addEventListener('close',()=>{el('ai-key').value='';});
  dialog.querySelectorAll('[data-settings-tab]').forEach(button=>button.onclick=()=>{
    dialog.querySelectorAll('[data-settings-tab]').forEach(item=>item.setAttribute('aria-selected',String(item===button)));
    el('ai-settings-panel').hidden=button.dataset.settingsTab!=='ai';el('engine-settings-panel').hidden=button.dataset.settingsTab!=='engine';
  });
  el('ai-provider').onchange=()=>{const config=el('ai-provider').value===loaded?.provider?loaded:defaults[el('ai-provider').value];
    el('ai-base').value=config.baseUrl;el('ai-model').value=config.model;el('ai-vision').checked=config.vision;
    el('ai-format').value=config.responseFormat||'prompt';el('ai-key').value='';keyHint();};
  el('ai-base').addEventListener('input',keyHint);
  el('ai-save').onclick=()=>saveAI();el('ai-test').onclick=()=>saveAI(true);
  el('ai-clear-key').onclick=async()=>{if(busy)return;lock(true);try{fillAI(await api('/api/ai-provider',{method:'PUT',body:JSON.stringify({...aiBody(),apiKey:'',clearKey:true})}));status('本机密钥已清除。');}catch(error){status(error.message,true);}finally{lock(false);}};
  el('engine-save').onclick=async()=>{
    if(busy||!el('setup-engine-url').reportValidity()||!el('setup-engine-port').reportValidity())return;
    lock(true);
    try{
      const body={comfy_url:el('setup-engine-url').value.trim(),comfy_root:el('setup-engine-root').value.trim(),
        comfy_python:el('setup-engine-python').value.trim(),comfy_port:Number(el('setup-engine-port').value),auto_start:el('setup-engine-auto').checked};
      await api('/api/settings',{method:'PUT',body:JSON.stringify(body)});
      if(window.CanvasApp){window.CanvasApp.state.engineUrl=body.comfy_url;await window.CanvasApp.refreshStatus();}
      status('引擎设置已保存，下次启动生效。当前生成任务继续运行。');
    }catch(error){status(error.message,true);}finally{lock(false);}
  };
  window.CanvasSettings={open:openSettings};
})();
