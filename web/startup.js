/* One painted surface hands the brand directly into the real canvas header. */
(() => {
  'use strict';
  const bridge=window.chrome?.webview, root=document.documentElement;
  const attempt=new URLSearchParams(location.search).get('canvas_startup');
  const layer=document.getElementById('startup-layer');
  if(window.top!==window||!bridge?.postMessage||!layer||!/^[1-9]\d*$/.test(attempt||''))return;
  const symbol=layer.querySelector('.startup-symbol'),wash=layer.querySelector('.startup-wash');
  const caption=layer.querySelector('.startup-caption'),status=layer.querySelector('[role="status"]');
  let painted=false,ready=false,failed=false,done=false,exitStarted=false;
  const animations=[];
  const reduced=()=>window.matchMedia?.('(prefers-reduced-motion: reduce), (forced-colors: active)').matches;
  const post=(state,detail={})=>{try{bridge.postMessage({type:'startup',state,...detail});}catch{}};
  const frame=()=>new Promise(resolve=>requestAnimationFrame(resolve));
  const finish=()=>{
    if(done)return;done=true;animations.forEach(animation=>animation.cancel());
    layer.hidden=true;delete root.dataset.startup;window.removeEventListener('resize',resize);
  };
  const resize=()=>{if(exitStarted)finish();};
  const play=(element,keyframes,duration)=>{
    const animation=element.animate(keyframes,{duration,easing:'cubic-bezier(.16,1,.3,1)',fill:'forwards'});
    animations.push(animation);return animation.finished.catch(()=>{});
  };
  async function reveal(){
    if(!painted||!ready||failed||done||exitStarted)return;
    exitStarted=true;root.dataset.startup='entering';
    const target=document.querySelector('.brand-mark img');
    const from=symbol.getBoundingClientRect(),to=target?.getBoundingClientRect();
    if(reduced()||!to||!from.width||!to.width||typeof symbol.animate!=='function'){finish();return;}
    const x=to.left+to.width/2-(from.left+from.width/2),y=to.top+to.height/2-(from.top+from.height/2);
    try{
      await Promise.all([
        play(symbol,[{transform:'translate(-50%,-50%) scale(1)'},{transform:`translate(calc(-50% + ${x}px),calc(-50% + ${y}px)) scale(${to.width/from.width})`}],300),
        play(wash,[{opacity:1},{opacity:0}],240),
        play(caption,[{opacity:1},{opacity:0}],150)
      ]);
    }catch{}finally{finish();}
  }
  function notify(state,message){
    if(!['ready','error'].includes(state))return false;
    if(done||ready||failed)return true;
    if(state==='error'){
      failed=true;root.dataset.startup='error';
      const detail=String(message||'画布读取失败，请重新打开映流。').replace(/\s+/g,' ').slice(0,400);
      status.textContent=detail;post('error',{message:detail});return true;
    }
    ready=true;
    // Real data must be ready; the first-paint handoff alone never reports success.
    if(painted){post('ready');reveal();}
    return true;
  }
  window.CanvasStartup={notify};
  root.dataset.startup='pending';layer.hidden=false;
  window.addEventListener('resize',resize);
  document.addEventListener('visibilitychange',()=>{if(document.hidden&&exitStarted)finish();});
  layer.addEventListener('mousedown',event=>{
    if(event.button===0&&!exitStarted&&!event.target.closest('button,a,input')){
      event.preventDefault();try{bridge.postMessage({type:'window',action:'drag'});}catch{}
    }
  });
  (async()=>{
    await frame();await frame();
    if(done||failed)return;
    painted=true;
    const background=getComputedStyle(root).getPropertyValue('--bg').trim();
    post('painted',/^#[\da-f]{6}$/i.test(background)?{background}:{});
    if(ready){post('ready');reveal();}
  })().catch(()=>{if(ready)finish();});
})();
