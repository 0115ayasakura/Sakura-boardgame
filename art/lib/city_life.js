/* Bounded cosmetic animation. No gameplay writes, realtime lights, shadows or postprocessing. */
import * as T from 'three';
import {manifest,packed} from './city_life_data.js';
export async function createCityLife(scene,container,options={}){
 let decoded=null;
 try{const bytes=Uint8Array.from(atob(packed),c=>c.charCodeAt(0));decoded=await new Response(new Blob([bytes]).stream().pipeThrough(new DecompressionStream('gzip'))).arrayBuffer();}catch(e){console.warn('城市动效数据不可用，保留静态场景',e);}
 const time={value:0},offset={value:0},motion={value:1};let enabled=true,disposed=false,visible=true,attached=0,elapsed=0,lastNow=0,lastUpdate=0,suspended=false,costEMA=0,slow=0,quality='full';
 try{enabled=localStorage.getItem('sakura-city-motion')!=='off';}catch{}
 const reduced=matchMedia('(prefers-reduced-motion: reduce)');
 const button=document.createElement('button');button.type='button';button.className='city-life-toggle';button.style.cssText='position:absolute;right:12px;bottom:38px;z-index:21;border:1px solid #a4c5d9;border-radius:12px;padding:5px 10px;background:rgba(244,250,255,.94);color:#37667e;font:600 11px sans-serif;cursor:pointer;box-shadow:0 2px 6px #41688718';container.appendChild(button);
 let onChange=()=>{};
 function allowed(){return enabled&&!reduced.matches&&visible&&!document.hidden&&!disposed&&!!decoded&&attached>0&&quality!=='static';}
 function label(){button.textContent='城市动效：'+(!enabled?'关':!decoded||!attached?'静态':reduced.matches?'简化':quality==='static'?'省电':quality==='low'?'轻量':'开');button.setAttribute('aria-pressed',String(enabled));button.title='花箱叶片轻摆、电子牌扫线、内圈呼吸灯；交互时暂停装饰动画。点击可完全关闭。';}
 function setEnabled(value){enabled=!!value;try{localStorage.setItem('sakura-city-motion',enabled?'on':'off');}catch{}lastNow=0;if(enabled){quality='full';slow=0;}label();onChange();}
 button.onclick=()=>setEnabled(!enabled);
 function changed(){lastNow=0;label();onChange();}
 reduced.addEventListener('change',changed);
 const observer=new IntersectionObserver(entries=>{visible=entries.some(e=>e.isIntersecting);changed();},{threshold:0});observer.observe(container);
 function attach(geometry,material,sourceName,rawGeometry,worldMatrix){
  if(!decoded||!manifest.meshes.some(r=>r.material===sourceName&&r.vertices===geometry.attributes.position.count))return false;
  const source=rawGeometry?.index?rawGeometry.toNonIndexed():rawGeometry;let signature=2166136261;
  if(source)for(const key of ['position','uv']){const array=source.attributes[key]?.array;if(array)for(const byte of new Uint8Array(array.buffer,array.byteOffset,array.byteLength))signature=Math.imul(signature^byte,16777619)>>>0;}
  if(source&&source!==rawGeometry)source.dispose();
  const row=manifest.meshes.find(r=>r.material===sourceName&&r.vertices===geometry.attributes.position.count&&r.fingerprint===signature&&r.matrices?.some(m=>m.every((v,i)=>Math.abs(v-worldMatrix.elements[i])<1e-6)));
  if(!row||!decoded)return false;
  const all=new Float32Array(decoded,row.offset,row.vertices*8),a=new Float32Array(row.vertices*4),b=new Float32Array(row.vertices*4);
  for(let i=0;i<row.vertices;i++){a.set(all.subarray(i*8,i*8+4),i*4);b.set(all.subarray(i*8+4,i*8+8),i*4);}
  geometry.setAttribute('lifeMotion',new T.BufferAttribute(a,4));geometry.setAttribute('lifeLight',new T.BufferAttribute(b,4));
  if(!material.userData.cityLife){
   material.userData.cityLife=true;
   material.onBeforeCompile=shader=>{
    shader.uniforms.cityTime=time;shader.uniforms.trainOffset=offset;
    shader.vertexShader='attribute vec4 lifeMotion;\nattribute vec4 lifeLight;\nuniform float cityTime;\nuniform float trainOffset;\nvarying vec4 vLifeLight;\nvarying vec3 vLifePosition;\n'+shader.vertexShader;
    shader.vertexShader=shader.vertexShader.replace('#include <begin_vertex>',`#include <begin_vertex>
      vLifeLight=lifeLight;vLifePosition=position;
      if(lifeMotion.x>0.5&&lifeMotion.x<1.5){
       float phase=position.x*.71+position.z*.53;
       transformed.x+=sin(cityTime*1.05+phase)*lifeMotion.y;
       transformed.z+=sin(cityTime*.79+phase)*lifeMotion.y*.48;
      }`);
    shader.fragmentShader='uniform float cityTime;\nvarying vec4 vLifeLight;\nvarying vec3 vLifePosition;\n'+shader.fragmentShader;
    shader.fragmentShader=shader.fragmentShader.replace('#include <color_fragment>',`#include <color_fragment>
      if(vLifeLight.x>0.0){
      float energy=vLifeLight.x;
      float pulse=.78+.22*sin(cityTime*1.25+vLifePosition.x*.2+vLifePosition.z*.3);
      vec3 tint=vec3(.20,.77,1.0);
      if(vLifeLight.y>2.5&&vLifeLight.y<3.5){
       float distance=abs(fract(vLifeLight.w+cityTime*.15)-.5);
       float band=1.0-smoothstep(.035,.105,distance),core=1.0-smoothstep(.012,.032,distance);
       energy=.10+band*.68+core*.28;
      }else if(vLifeLight.y>1.5&&vLifeLight.y<2.5){
       pulse=.65+.35*sin(cityTime*1.1);
      }else if(vLifeLight.y>3.5&&vLifeLight.y<4.5){
       float lamp=step(.76,vLifeLight.w)*step(vLifeLight.w,.93)*(1.0-step(.31,vLifeLight.z)+step(.72,vLifeLight.z));
       energy*=lamp;tint=vec3(1.0,.84,.48);pulse=1.0;
      }else if(vLifeLight.y>6.5){
       tint=vec3(1.0,.84,.48);pulse=1.0;
      }else if(vLifeLight.y>5.5){
       tint=vec3(1.0,.68,.25);pulse=.82+.18*sin(cityTime*.8);
      }else if(vLifeLight.y>4.5){
       float u=vLifeLight.z,v=vLifeLight.w;
       float window=step(.07,u)*step(u,.872)*step(.26,v)*step(v,.74)*step(mod(u-.07,.215),.157);
       float glass=step(.16,diffuseColor.b)*step(diffuseColor.r*1.3,diffuseColor.b);
       energy*=window*glass;tint=vec3(.42,.69,.82);
      }
      diffuseColor.rgb+=tint*energy*pulse;
      }`);
   };material.customProgramCacheKey=()=> 'civic-life-v2';material.needsUpdate=true;
  }
  material.userData.cityUniforms={time,offset};if(options.capture)material.userData.cityCostProbe=observeCost;attached++;label();return true;
 }
 function setPortals(cells){} // Only the real inner ring breathes, no overlay geometry.
 function update(now,busy){
  if(!allowed()||busy){lastNow=now;suspended=!!busy;return false;}
  suspended=false;const interval=quality==='low'?125:50;if(now-lastUpdate<interval-2)return false;
  if(lastNow)elapsed+=Math.min(.15,(now-lastNow)/1000);lastNow=now;lastUpdate=now;time.value=elapsed;offset.value=0;return true;
 }
 function observeCost(ms,late=0){costEMA=costEMA*.92+ms*.08;if(ms>24||late>90)slow++;else slow=Math.max(0,slow-.15);if(slow>15&&quality==='full'){quality='low';label();}if(slow>45&&quality==='low'){quality='static';label();onChange();}}
 label();return{attach,setPortals,update,observeCost,setEnabled,get enabled(){return enabled;},get eligible(){return allowed();},get visible(){return visible;},get interval(){return quality==='low'?125:50;},set onChange(fn){onChange=fn;},getStats(){return{enabled,eligible:allowed(),quality,visible,reducedMotion:reduced.matches,suspended,time:elapsed,trainOffset:offset.value,attachedMeshes:attached,portalParticles:0,trainStatic:true,treesStatic:true,extraDraws:0,costEMA};},dispose(){if(disposed)return;disposed=true;observer.disconnect();reduced.removeEventListener('change',changed);button.remove();}};
}
