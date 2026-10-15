const {test}=require('node:test');
const assert=require('node:assert/strict'),fs=require('node:fs');
const css=fs.readFileSync('tam/web/styles.css','utf8');
test('one token system provides light and dark colors',()=>{
 for(const token of ['--accent:','--glass:','--text:','--r-xl:'])assert.ok(css.includes(token));
 assert.match(css,/:root\[data-theme="dark"\]/);
 assert.doesNotMatch(css,/TAO visual polish layer|ReactBits-inspired refinement/);
});
test('animated background remains pointer-transparent and reduced-motion safe',()=>{
 assert.match(css,/body::before\{[\s\S]*?pointer-events:none/);
 assert.match(css,/body::before\{[\s\S]*?animation:fx-aurora/);
 assert.match(css,/@layer prefs\s*\{[\s\S]*?prefers-reduced-motion:reduce[\s\S]*?body::before[\s\S]*?animation:none(?!\s*!important)/);
 assert.match(css,/radial-gradient\(/);
});
test('focus and reduced motion apply to all controls and overlays',()=>{
 assert.match(css,/:focus-visible/); assert.match(css,/prefers-reduced-motion:reduce/);
 assert.match(css,/animation-duration:.001ms!important/);
 assert.match(css,/\.tao-motion-overlay\{[^}]*animation:none!important/);
});
test('business surfaces have responsive styling',()=>{
 for(const cls of ['.accounts-panel','.taskrow','.ui-task-card','.modal','.drawer','.ai-panel'])assert.ok(css.includes(cls),cls);
 assert.match(css,/@media \(max-width:720px\)/);
 assert.match(css,/grid-template-areas:"c n s"/);
});
