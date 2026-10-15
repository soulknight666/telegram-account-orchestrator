const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const vm=require('node:vm');

const src=fs.readFileSync('tam/web/app.js','utf8');
const css=fs.readFileSync('tam/web/styles.css','utf8');
const block=src.slice(src.indexOf('const TAO_MOTION'),src.indexOf('function setAccounts'));

function harness({missing=false,reduced=false}={}) {
  const observers=[], animations=[], events={}, deferred=[];
  const mq={matches:reduced,addEventListener(_,fn){this.change=fn}};
  function element(kind='modal') {
    const names=new Set(kind.split(' ').filter(Boolean));
    const el={nodeType:1,isConnected:true,children:[],id:'',
      classList:{
        add:n=>names.add(n), remove:n=>names.delete(n), contains:n=>names.has(n),
        toggle(n,on){if(on) names.add(n); else names.delete(n)}
      },
      matches:selector=>selector.split(',').some(s=>names.has(s.replace(/^\./,''))),
      animate:missing?undefined:(keyframes,options)=>{
        const anim={keyframes,options,cancelled:0,onfinish:null,cancel(){this.cancelled++}};
        animations.push({el,anim});
        return anim;
      }
    };
    return el;
  }
  class Observer {
    constructor(fn){this.fn=fn;this.disconnected=false;observers.push(this)}
    observe(el,options){this.el=el;this.options=options}
    disconnect(){this.disconnected=true}
  }
  const root=element('html'), body=element('body'); body.children=[];
  const window={matchMedia:()=>mq,addEventListener:(n,fn)=>events[n]=fn,requestAnimationFrame:fn=>deferred.push(fn)};
  const document={body,documentElement:root,hidden:false,querySelectorAll:()=>[],addEventListener:(n,fn)=>events[n]=fn};
  const context=vm.createContext({window,document,MutationObserver:Observer,queueMicrotask:fn=>deferred.push(fn),setTimeout:fn=>deferred.push(fn)});
  vm.runInContext(block,context);
  const motion=vm.runInContext('TAO_MOTION',context);
  return {motion,body,root,element,animations,observers,mq,events,flush(){while(deferred.length) deferred.shift()()}};
}

test('open uses native Web Animations and close cancels/restores marker',()=>{
  const h=harness(), modal=h.element('modal'); h.motion.open(modal);
  assert.equal(h.animations.length,1);
  assert.equal(JSON.stringify(h.animations[0].anim.keyframes),JSON.stringify([{opacity:.6},{opacity:1}]));
  assert.equal(h.animations[0].anim.options.duration,180);
  assert.equal(modal.classList.contains('tao-motion-overlay'),true);
  h.motion.close(modal);
  assert.equal(h.animations[0].anim.cancelled,1);
  assert.equal(modal.classList.contains('tao-motion-overlay'),false);
});

test('animation completion clears ownership without restarting',()=>{
  const h=harness(), modal=h.element('modal'); h.motion.open(modal);
  const run=h.animations[0].anim; run.onfinish(); h.flush();
  assert.equal(run.cancelled,1); assert.equal(modal.classList.contains('tao-motion-overlay'),false); assert.equal(h.animations.length,1);
});

test('rapid replacement cancels stale view animation and keeps latest',()=>{
  const h=harness(), modal=h.element('modal'); h.motion.view(modal);
  h.motion.view(modal);
  assert.equal(h.animations[0].anim.cancelled,1); assert.equal(h.animations.length,2);
  h.animations[0].anim.onfinish(); h.flush(); assert.equal(h.animations[1].anim.cancelled,0);
});

test('reduced motion cancels current animation and starts none',()=>{
  const h=harness(), modal=h.element('modal'); h.motion.open(modal);
  h.mq.matches=true; h.mq.change(); assert.equal(h.animations[0].anim.cancelled,1);
  h.motion.open(modal); assert.equal(h.animations.length,1);
});

test('missing Web Animations and reduced motion fall back without animation',()=>{
  for(const opts of [{missing:true},{reduced:true}]){const h=harness(opts),modal=h.element('modal');h.motion.open(modal);h.motion.view(modal);h.flush();assert.equal(h.animations.length,0);assert.equal(modal.classList.contains('tao-motion-overlay'),false)}
});

test('observer watches direct body children only, never account subtree',()=>{const h=harness();assert.ok(h.observers.some(o=>o.el===h.body&&o.options.childList&&!o.options.subtree));assert.ok(h.observers.every(o=>!o.options.subtree));});

test('dynamic modal insertion starts animation and removal disconnects it',()=>{
  const h=harness(),modal=h.element('modal on'),bodyObserver=h.observers.find(o=>o.el===h.body);
  bodyObserver.fn([{addedNodes:[modal],removedNodes:[]}]); assert.equal(h.animations.length,1);
  modal.isConnected=false; bodyObserver.fn([{addedNodes:[],removedNodes:[modal]}]);
  assert.equal(h.animations[0].anim.cancelled,1); assert.ok(h.observers.some(o=>o.el===modal&&o.disconnected));
});

test('pagehide cleans animations and pageshow restores observer',()=>{
  const h=harness(),modal=h.element('modal');h.motion.open(modal);h.events.pagehide();assert.equal(h.animations[0].anim.cancelled,1);assert.ok(h.observers.every(o=>o.disconnected));h.events.pageshow();assert.ok(h.observers.some(o=>!o.disconnected&&o.el===h.body));
});

test('unchanged class mutation does not restart animation',()=>{
  const h=harness(),modal=h.element('modal on'),bodyObserver=h.observers.find(o=>o.el===h.body);bodyObserver.fn([{addedNodes:[modal],removedNodes:[]}]);const observer=h.observers.find(o=>o.el===modal);const count=h.animations.length;observer.fn([{type:'attributes',attributeName:'class',target:modal}]);assert.equal(h.animations.length,count);
});

test('deferred view keeps only the latest navigation frame',()=>{const h=harness(),modal=h.element('modal');h.motion.view(modal,{defer:true});h.motion.view(modal,{defer:true});h.flush();assert.equal(h.animations.length,1);});

test('drawer and AI panel use a short translate entrance',()=>{const h=harness(),drawer=h.element('drawer');h.motion.open(drawer);assert.equal(JSON.stringify(h.animations[0].anim.keyframes),JSON.stringify([{opacity:.6,translate:'18px 0'},{opacity:1,translate:'0 0'}]));assert.equal(h.animations[0].anim.options.duration,180);});

test('CSS fallback and native ownership selectors match implementation',()=>{
  assert.match(src,/原生 Web Animations API/); assert.match(css,/\.tao-motion-overlay\{[^}]*animation:none!important/); assert.match(css,/.tao-motion-ready \.modal\.on\{animation:none\}/); assert.match(css,/.tao-motion-ready \.drawer\.on,.tao-motion-ready \.ai-panel\.on\{transition:none\}/); assert.doesNotMatch(src,/window\.gsap|tao-gsap-ready/); assert.doesNotMatch(css,/tao-gsap-ready/);
});
